"""Biaya Lainnya on sales invoices: storage, journal, ledger, and PDF."""
from __future__ import annotations

import io
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_charges_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.general_ledger import get_general_ledger
from services.journal_core import JOURNAL_LINES, ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_posting import SOURCE_TYPE
from services.sales_invoices import generate_sales_invoice_pdf_bytes

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-charges",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Charges",
        "role": "admin",
    }


def _pdf_text(pdf_bytes: bytes) -> str:
    chunks = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


async def _coa(prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure(rec, rev, tax):
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "receivable_account_id": rec["id"],
            "revenue_account_id": rev["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )


async def _seed_po(*, sales="Sari Penjualan", qty=100, unit_price=7000):
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/CHG/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Uji Biaya",
        "outlet": "Gudang Utara",
        "recipient": "Rina",
        "address": "Jl. Uji 1",
        "delivery_zone": "Jakarta",
        "sales": sales,
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-0",
            "name": "Dus Uji",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": unit_price,
        }],
        "total_quantity": qty,
        "current_stage": 5,
        "cancelled": False,
        "created_at": "2026-09-01T00:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


def _lines(quantity, unit_price=1):
    return [{"line_idx": 0, "line_id": "line-0", "quantity": quantity, "unit_price": unit_price}]


async def _journals(invoice_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": invoice_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(10)


async def _lines_for(journal_id: str):
    return await server.db[JOURNAL_LINES].find({"journal_id": journal_id}, {"_id": 0}).to_list(20)


@pytest.mark.anyio
async def test_old_invoice_without_charges_keeps_goods_total_and_price():
    po = await _seed_po(unit_price=7000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-02",
                "discount_amount": 0,
                "ppn_percent": 0,
                "lines": _lines(10, 1),
            })
            assert res.status_code == 200, res.text
            inv = res.json()["invoice"]
            assert inv["lines"][0]["unit_price"] == 7000
            assert inv["subtotal"] == 70000
            assert inv["grand_total"] == 70000
            assert inv["other_charges"] == []
            assert inv["other_charges_total"] == 0
            assert inv["sales"] == "Sari Penjualan"
            assert await _journals(inv["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_two_charges_round_trip_post_ledger_retry_reverse_and_pdf():
    po = await _seed_po(qty=100, unit_price=7000, sales="Sari Penjualan")
    shipping = await _coa("71", "Biaya Kirim Uji", "EXPS")
    packing = await _coa("72", "Pendapatan Packing Uji", "OINC")
    rec = await _coa("11", "Piutang Uji", "AREC")
    rev = await _coa("41", "Penjualan Uji", "REVE")
    tax = await _coa("21", "PPN Uji", "OCLY")
    await _configure(rec, rev, tax)
    _admin()
    charges = [
        {"account_id": shipping["id"], "description": "Ongkos kirim", "amount": 15000},
        {"account_id": packing["id"], "description": "Packing pelanggan", "amount": 5000},
    ]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            options = await client.get("/api/sales-invoices/charge-accounts", params={"q": shipping["code"]})
            assert options.status_code == 200, options.text
            assert any(row["id"] == shipping["id"] for row in options.json()["items"])
            assert all(row["account_type"] in {"EXPS", "OEXP", "OINC", "REVE"} for row in options.json()["items"])

            created = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-03",
                "discount_amount": 0,
                "ppn_percent": 0,
                "notes": "Kirim ke gudang pelanggan",
                "lines": _lines(100, 1),
                "other_charges": charges,
            })
            assert created.status_code == 200, created.text
            inv = created.json()["invoice"]
            assert inv["lines"][0]["unit_price"] == 7000
            assert inv["subtotal"] == 700000
            assert inv["after_discount"] == 700000
            assert inv["ppn_amount"] == 0
            assert inv["other_charges_total"] == 20000
            assert inv["grand_total"] == 720000
            assert inv["outstanding_amount"] == 720000
            assert [(row["account_id"], row["description"], row["amount"]) for row in inv["other_charges"]] == [
                (shipping["id"], "Ongkos kirim", 15000),
                (packing["id"], "Packing pelanggan", 5000),
            ]
            assert await _journals(inv["id"]) == []

            again = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert again.status_code == 200
            stored = again.json()["invoice"]
            assert [(row["account_id"], row["description"], row["amount"]) for row in stored["other_charges"]] == [
                (shipping["id"], "Ongkos kirim", 15000),
                (packing["id"], "Packing pelanggan", 5000),
            ]
            assert stored["grand_total"] == 720000

            partial = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-04",
                "lines": _lines(1, 1),
            })
            assert partial.status_code == 400

            second = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-04",
                "lines": _lines(0, 1),
            })
            # Remaining qty is 0 because the first invoice took 100.
            assert second.status_code == 400

            over = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": _lines(101, 1),
            })
            assert over.status_code == 400

            fin = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert fin.status_code == 200, fin.text
            blocked = await client.put(f"/api/sales-invoices/{inv['id']}", json={"notes": "ubah", "other_charges": charges})
            assert blocked.status_code == 409

            posted = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            journal_id = posted.json()["invoice"]["accounting_journal_id"]
            lines = await _lines_for(journal_id)
            debit = sum(Decimal(str(row["debit"])) for row in lines)
            credit = sum(Decimal(str(row["credit"])) for row in lines)
            assert debit == credit == Decimal("720000.00")
            by_account = {}
            for row in lines:
                by_account.setdefault(row["account_id"], {"debit": Decimal("0"), "credit": Decimal("0")})
                by_account[row["account_id"]]["debit"] += Decimal(str(row["debit"]))
                by_account[row["account_id"]]["credit"] += Decimal(str(row["credit"]))
            assert by_account[rec["id"]]["debit"] == Decimal("720000.00")
            assert by_account[rev["id"]]["credit"] == Decimal("700000.00")
            assert by_account[shipping["id"]]["credit"] == Decimal("15000.00")
            assert by_account[packing["id"]]["credit"] == Decimal("5000.00")
            assert tax["id"] not in by_account
            assert by_account[rev["id"]]["credit"] != Decimal("720000.00")

            retry = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert retry.status_code == 200
            assert retry.json()["invoice"]["accounting_journal_id"] == journal_id
            assert len(await _journals(inv["id"])) == 1

            ledger = await get_general_ledger(
                server.db, account_id=shipping["id"], start_date="2026-09-01", end_date="2026-09-30",
            )
            ship_rows = [row for row in ledger["items"] if row["source_id"] == inv["id"]]
            assert len(ship_rows) == 1
            assert ship_rows[0]["credit"] == 15000
            assert ship_rows[0]["debit"] == 0
            assert ledger["account_id"] == shipping["id"]

            receivable = await get_general_ledger(
                server.db, account_id=rec["id"], start_date="2026-09-01", end_date="2026-09-30",
            )
            ar_rows = [row for row in receivable["items"] if row["source_id"] == inv["id"]]
            assert ar_rows[0]["debit"] == 720000

            pdf_res = await client.get(f"/api/sales-invoices/{inv['id']}/pdf")
            assert pdf_res.status_code == 200
            assert pdf_res.content.startswith(b"%PDF-")
            text = _pdf_text(pdf_res.content)
            assert "Sari Penjualan" in text
            assert "Ongkos kirim" in text
            assert "Packing pelanggan" in text
            assert "Kirim ke gudang pelanggan" in text
            assert "720.000" in text
            assert shipping["code"] not in text
            assert packing["code"] not in text
            assert "Biaya Kirim Uji" not in text
            assert stored["grand_total"] == posted.json()["invoice"]["grand_total"]

            reversed_res = await client.post(
                f"/api/sales-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "Koreksi biaya kirim"},
            )
            assert reversed_res.status_code == 200, reversed_res.text
            assert len(await _journals(inv["id"])) == 1
            reversal = await server.db.journal_entries.find_one(
                {"reversal_of": journal_id}, {"_id": 0},
            )
            assert reversal
            rev_lines = await _lines_for(reversal["id"])
            rev_by = {}
            for row in rev_lines:
                rev_by.setdefault(row["account_id"], {"debit": Decimal("0"), "credit": Decimal("0")})
                rev_by[row["account_id"]]["debit"] += Decimal(str(row["debit"]))
                rev_by[row["account_id"]]["credit"] += Decimal(str(row["credit"]))
            assert rev_by[shipping["id"]]["debit"] == Decimal("15000.00")
            assert rev_by[packing["id"]]["debit"] == Decimal("5000.00")
            assert rev_by[rec["id"]]["credit"] == Decimal("720000.00")
            assert rev_by[rev["id"]]["debit"] == Decimal("700000.00")

            ledger_after = await get_general_ledger(
                server.db, account_id=shipping["id"], start_date="2026-09-01", end_date="2026-09-30",
            )
            assert ledger_after["ending_balance"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invalid_accounts_and_amounts_are_rejected_and_not_stored():
    po = await _seed_po(qty=10, unit_price=1000)
    expense = await _coa("73", "Beban Aktif", "EXPS")
    inactive = await _coa("74", "Beban Nonaktif", "EXPS")
    await server.db.ledger_accounts.update_one({"id": inactive["id"]}, {"$set": {"status": "inactive"}})
    header = await _coa("75", "Beban Induk", "EXPS")
    await server.db.ledger_accounts.update_one({"id": header["id"]}, {"$set": {"is_header": True}})
    bank = await _coa("76", "Kas Tidak Layak", "BANK")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            options = await client.get("/api/sales-invoices/charge-accounts")
            ids = {row["id"] for row in options.json()["items"]}
            assert expense["id"] in ids
            assert inactive["id"] not in ids
            assert header["id"] not in ids
            assert bank["id"] not in ids

            async def reject(charges):
                before = await server.db.sales_invoices.count_documents({"po_id": po["id"]})
                res = await client.post("/api/sales-invoices", json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-05",
                    "lines": _lines(1, 1),
                    "other_charges": charges,
                })
                assert res.status_code == 400, res.text
                assert await server.db.sales_invoices.count_documents({"po_id": po["id"]}) == before
                return res.json()["detail"]

            assert "tidak ditemukan" in await reject([
                {"account_id": "missing-account", "description": "Ongkir", "amount": 1000},
            ])
            assert "tidak aktif" in await reject([
                {"account_id": inactive["id"], "description": "Ongkir", "amount": 1000},
            ])
            assert "tidak dapat diposting" in await reject([
                {"account_id": header["id"], "description": "Ongkir", "amount": 1000},
            ])
            assert "tidak diizinkan" in await reject([
                {"account_id": bank["id"], "description": "Ongkir", "amount": 1000},
            ])
            assert "wajib diisi" in await reject([
                {"account_id": expense["id"], "description": "Ongkir", "amount": ""},
            ])
            assert "negatif" in await reject([
                {"account_id": expense["id"], "description": "Ongkir", "amount": -5},
            ])
            assert "tidak valid" in await reject([
                {"account_id": expense["id"], "description": "Ongkir", "amount": "abc"},
            ])
            assert "belum lengkap" in await reject([
                {"account_id": "", "description": "", "amount": ""},
            ])
            assert await _journals("none") == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_partial_invoice_does_not_copy_charges_and_goods_tax_stays_on_goods():
    po = await _seed_po(qty=100, unit_price=1000, sales="")
    expense = await _coa("77", "Ongkir Parsial", "EXPS")
    other = await _coa("78", "Beban Lain Parsial", "OEXP")
    rec = await _coa("12", "Piutang Parsial", "AREC")
    rev = await _coa("42", "Penjualan Parsial", "REVE")
    tax = await _coa("22", "PPN Parsial", "OCLY")
    await _configure(rec, rev, tax)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-06",
                "discount_amount": 10000,
                "ppn_percent": 11,
                "lines": _lines(40, 999),
                "other_charges": [
                    {"account_id": expense["id"], "description": "Ongkir pertama", "amount": 20000},
                ],
            })
            assert first.status_code == 200, first.text
            inv = first.json()["invoice"]
            assert inv["lines"][0]["unit_price"] == 1000
            assert inv["lines"][0]["quantity"] == 40
            assert inv["subtotal"] == 40000
            assert inv["discount_amount"] == 10000
            assert inv["after_discount"] == 30000
            assert inv["ppn_amount"] == 3300
            assert inv["other_charges_total"] == 20000
            assert inv["grand_total"] == 53300

            second = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-09-07",
                "lines": _lines(60, 1),
            })
            assert second.status_code == 200, second.text
            other_inv = second.json()["invoice"]
            assert other_inv["other_charges"] == []
            assert other_inv["grand_total"] == 60000
            reloaded = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert len(reloaded.json()["invoice"]["other_charges"]) == 1

            kept = await client.put(f"/api/sales-invoices/{other_inv['id']}", json={
                "invoice_date": "2026-09-07",
                "notes": "tanpa biaya",
            })
            assert kept.status_code == 200, kept.text
            assert kept.json()["invoice"]["other_charges"] == []

            edited = await client.put(f"/api/sales-invoices/{inv['id']}", json={
                "discount_amount": 10000,
                "ppn_percent": 11,
                "other_charges": [
                    {"id": inv["other_charges"][0]["id"], "account_id": expense["id"], "description": "Ongkir pertama", "amount": 20000},
                    {"account_id": other["id"], "description": "Asuransi", "amount": 2500},
                ],
            })
            assert edited.status_code == 200, edited.text
            body = edited.json()["invoice"]
            assert body["other_charges_total"] == 22500
            assert body["grand_total"] == 55800
            assert body["ppn_amount"] == 3300
            assert [row["description"] for row in body["other_charges"]] == ["Ongkir pertama", "Asuransi"]

            fin = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert fin.status_code == 200, fin.text
            posted = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            lines = await _lines_for(posted.json()["invoice"]["accounting_journal_id"])
            credits = {row["account_id"]: Decimal(str(row["credit"])) for row in lines}
            assert credits[rev["id"]] == Decimal("30000.00")
            assert credits[tax["id"]] == Decimal("3300.00")
            assert credits[expense["id"]] == Decimal("20000.00")
            assert credits[other["id"]] == Decimal("2500.00")
            debits = {row["account_id"]: Decimal(str(row["debit"])) for row in lines if Decimal(str(row["debit"])) > 0}
            assert debits[rec["id"]] == Decimal("55800.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_pdf_uses_order_sales_customer_note_and_charge_description_only():
    blank = generate_sales_invoice_pdf_bytes({
        "invoice_number": "INV-BLANK",
        "po_number": "PO-BLANK",
        "customer": "PT Bukan Sales",
        "sales": "",
        "notes": "Catatan pelanggan",
        "invoice_date": "2026-09-08",
        "due_date": "2026-09-08",
        "payment_status": "BELUM DIBAYAR",
        "subtotal": 700000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "grand_total": 720000,
        "paid_total": 0,
        "outstanding_amount": 720000,
        "lines": [{"item_name": "Dus", "quantity": 100, "unit": "pcs", "unit_price": 7000, "line_subtotal": 700000}],
        "other_charges": [{
            "description": "Ongkos kirim",
            "amount": 20000,
            "account_code": "700031",
            "account_name": "Biaya Pengiriman Internal",
        }],
    })
    text = _pdf_text(blank)
    assert "PT Bukan Sales" in text
    assert "Catatan pelanggan" in text
    assert "Ongkos kirim" in text
    assert "720.000" in text
    assert "700031" not in text
    assert "Biaya Pengiriman Internal" not in text
    assert "PT Bukan Sales" in text
    sales_line = next(line for line in text.splitlines() if line.strip().startswith("Sales"))
    assert "PT Bukan Sales" not in sales_line
