"""Sales receipt explicit accounting posting — Phase 1A."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_receipt_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.general_ledger import SOURCE_LABELS
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_receipt_posting import (
    INVOICE_NOT_POSTED_MESSAGE,
    INACTIVE_CASH_BANK_MESSAGE,
    PERM_POST,
    PERM_REVERSE,
    POSTED_DELETE_MESSAGE,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    post_sales_receipt_accounting,
)
from fastapi import HTTPException

POST_USER = {
    "id": "admin-receipt-posting",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Receipt Posting",
    "role": "admin",
}

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
    await ensure_cb(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(POST_USER)


def _user(role: str, perms: dict):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "email": f"{role}@example.com",
        "name": role,
        "role": role,
        "permissions_override": perms,
    }


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    po_number = f"PO/RCPT/{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": "Customer ABC",
        "outlet": "Outlet Receipt",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup Receipt", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": extra.pop("current_stage", 4),
        "status_label": "Approval Payment",
        "payment_status": extra.pop("payment_status", "Waiting"),
        "packed_qty": extra.pop("packed_qty", 40),
        "loaded_qty": extra.pop("loaded_qty", 10),
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": extra.pop("shipments", []),
        "created_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings(rec=None, rev=None, tax=None):
    rec = rec or await _coa("11", "Piutang Usaha", "AREC")
    rev = rev or await _coa("41", "Pendapatan", "REVE")
    tax = tax or await _coa("21", "PPN Keluaran", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "receivable_account_id": rec["id"],
                "revenue_account_id": rev["id"],
                "output_tax_account_id": tax["id"],
            }
        },
        upsert=True,
    )
    return rec, rev, tax


async def _cash_bank(name: str = "BCA Operasional", *, active: bool = True) -> dict:
    bank_coa = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank_coa["id"], "active": active},
        user=POST_USER,
    )


async def _create_invoice(client, po_id, **extra) -> dict:
    payload = {
        "po_id": po_id,
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "discount_amount": extra.pop("discount_amount", 0),
        "ppn_percent": extra.pop("ppn_percent", 0),
        "lines": [{"line_idx": 0, "quantity": extra.pop("quantity", 1), "unit_price": extra.pop("unit_price", 10_000_000)}],
    }
    payload.update(extra)
    res = await client.post("/api/sales-invoices", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _finalize(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/sales-invoices/{invoice_id}/finalize")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _post_invoice(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/sales-invoices/{invoice_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _create_receipt(client, invoice_id: str, amount, **extra) -> dict:
    payload = {
        "invoice_id": invoice_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-05"),
        "amount": amount,
        "notes": extra.pop("notes", "DP"),
    }
    if "cash_bank_id" in extra:
        payload["cash_bank_id"] = extra.pop("cash_bank_id")
    payload.update(extra)
    res = await client.post("/api/sales-receipts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _source_journals(receipt_id: str, source_type: str = SOURCE_TYPE):
    return await server.db.journal_entries.find(
        {"source_type": source_type, "source_id": receipt_id},
        {"_id": 0},
    ).to_list(20)


async def _journal_lines(journal_id: str):
    return await server.db.journal_lines.find({"journal_id": journal_id}, {"_id": 0}).to_list(20)


async def _account_net(account_id: str) -> Decimal:
    journals = await server.db.journal_entries.find({}, {"_id": 0, "id": 1, "status": 1}).to_list(500)
    ids = [j["id"] for j in journals if j.get("id") and str(j.get("status") or "posted") in ("posted", "reversed")]
    if not ids:
        return Decimal("0.00")
    lines = await server.db.journal_lines.find(
        {"account_id": account_id, "journal_id": {"$in": ids}},
        {"_id": 0},
    ).to_list(500)
    debit = sum((Decimal(str(ln.get("debit") or 0)) for ln in lines), Decimal("0.00"))
    credit = sum((Decimal(str(ln.get("credit") or 0)) for ln in lines), Decimal("0.00"))
    return (debit - credit).quantize(Decimal("0.01"))


async def _unlocked_post(receipt_id: str, cash_bank_id: Optional[str] = None):
    try:
        return await post_sales_receipt_accounting(
            server.db,
            receipt_id,
            POST_USER,
            cash_bank_id=cash_bank_id,
            serialize_in_process=False,
        )
    except HTTPException as exc:
        return exc


@pytest.mark.anyio
async def test_permissions_not_auto_granted_and_gl_labels():
    assert PERM_POST in ALL_PERMISSIONS
    assert PERM_REVERSE in ALL_PERMISSIONS
    assert SOURCE_LABELS["sales_invoice"] == "Faktur Penjualan"
    assert SOURCE_LABELS["sales_receipt"] == "Penerimaan Penjualan"
    assert SOURCE_LABELS["sales_receipt_reversal"] == "Reversal Penerimaan Penjualan"
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert PERM_POST not in perms
        assert PERM_REVERSE not in perms


@pytest.mark.anyio
async def test_matrix_a_b_m_post_idempotent_and_receipt_date():
    rec, _rev, _tax = await _configure_settings()
    bca = await _cash_bank("BCA Operasional")
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price=10_000_000)
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(
                client, inv["id"], 4_000_000, cash_bank_id=bca["id"], receipt_date="2026-08-15"
            )
            assert receipt["accounting_status"] == "not_posted"
            assert receipt["accounting_posted"] is False
            assert receipt["can_post_accounting"] is True
            assert receipt["cash_bank_name"] == "BCA Operasional"

            posted = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            body = posted.json()["receipt"]
            assert body["accounting_status"] == "posted"
            assert body["accounting_posted"] is True
            assert body["accounting_journal_id"]
            assert body["can_delete"] is False
            assert body["can_reverse_accounting"] is True

            journals = await _source_journals(receipt["id"])
            assert len(journals) == 1
            assert journals[0]["date"] == "2026-08-15"
            assert journals[0]["source_type"] == SOURCE_TYPE
            assert "PAY" in journals[0]["description"] or receipt["receipt_number"] in journals[0]["description"]
            assert "Customer ABC" in journals[0]["description"]

            lines = await _journal_lines(journals[0]["id"])
            by_account = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_account[bca["account_id"]]["debit"])) == Decimal("4000000.00")
            assert Decimal(str(by_account[bca["account_id"]]["credit"])) == Decimal("0.00")
            assert Decimal(str(by_account[rec["id"]]["credit"])) == Decimal("4000000.00")
            assert Decimal(str(by_account[rec["id"]]["debit"])) == Decimal("0.00")
            debit = sum(Decimal(str(ln["debit"])) for ln in lines)
            credit = sum(Decimal(str(ln["credit"])) for ln in lines)
            assert debit == credit == Decimal("4000000.00")

            retry = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert retry.status_code == 200
            assert retry.json()["receipt"]["accounting_journal_id"] == journals[0]["id"]
            assert len(await _source_journals(receipt["id"])) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_c_concurrent_post_one_journal():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])

        results = await asyncio.gather(
            _unlocked_post(receipt["id"]),
            _unlocked_post(receipt["id"]),
        )
        ok = [r for r in results if not isinstance(r, HTTPException)]
        assert len(ok) >= 1
        journals = await _source_journals(receipt["id"])
        assert len(journals) == 1
        ids = {r["accounting_journal_id"] for r in ok}
        assert ids == {journals[0]["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_d_invoice_not_posted_blocks_receipt_post_not_create():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            fin = await _finalize(client, inv["id"])
            receipt = await _create_receipt(client, fin["id"], 4_000_000, cash_bank_id=bca["id"])
            assert receipt["accounting_status"] == "not_posted"
            assert receipt["can_post_accounting"] is False

            blocked = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == INVOICE_NOT_POSTED_MESSAGE
            assert await _source_journals(receipt["id"]) == []

            await _post_invoice(client, fin["id"])
            ok = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert ok.status_code == 200, ok.text
            assert ok.json()["receipt"]["accounting_status"] == "posted"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_e_invalid_and_inactive_cash_bank_blocked():
    await _configure_settings()
    inactive = await _cash_bank("Kas Nonaktif", active=False)
    active = await _cash_bank("Mandiri")
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])

            missing = await _create_receipt(client, inv["id"], 1_000_000)
            bad_missing = await client.post(f"/api/sales-receipts/{missing['id']}/post-accounting")
            assert bad_missing.status_code == 400

            later_inactive = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=active["id"])
            await server.db[CB_COLLECTION].update_one({"id": active["id"]}, {"$set": {"active": False}})
            bad_inactive = await client.post(f"/api/sales-receipts/{later_inactive['id']}/post-accounting")
            assert bad_inactive.status_code == 400
            assert INACTIVE_CASH_BANK_MESSAGE in str(bad_inactive.json()["detail"])

            created_inactive = await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 500_000,
                "cash_bank_id": inactive["id"],
                "receipt_date": "2026-09-05",
            })
            assert created_inactive.status_code == 400
            assert await _source_journals(missing["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_f_g_unposted_delete_kept_posted_delete_blocked():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            unposted = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bca["id"])
            posted = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bca["id"])
            ok_post = await client.post(f"/api/sales-receipts/{posted['id']}/post-accounting")
            assert ok_post.status_code == 200

            deleted = await client.delete(f"/api/sales-receipts/{unposted['id']}")
            assert deleted.status_code == 200
            assert deleted.json()["deleted"] is True
            gone = await server.db.sales_receipts.find_one({"id": unposted["id"]})
            assert gone is None

            blocked = await client.delete(f"/api/sales-receipts/{posted['id']}")
            assert blocked.status_code == 409
            assert POSTED_DELETE_MESSAGE in str(blocked.json()["detail"])
            still = await server.db.sales_receipts.find_one({"id": posted["id"]})
            assert still is not None
            assert len(await _source_journals(posted["id"])) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_h_i_j_reverse_idempotent_and_outstanding():
    rec, _rev, _tax = await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
            posted = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert posted.status_code == 200
            invoice_paid = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert Decimal(str(invoice_paid["outstanding_amount"])) == Decimal("6000000")
            assert Decimal(str(invoice_paid["paid_total"])) == Decimal("4000000")

            rev = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            assert rev.status_code == 200, rev.text
            body = rev.json()["receipt"]
            assert body["accounting_status"] == "reversed"
            assert body["financial_status"] == "cancelled"
            assert body["can_delete"] is False
            assert body["cancelled"] is True

            rev_journals = await _source_journals(receipt["id"], SOURCE_REVERSAL)
            assert len(rev_journals) == 1
            lines = await _journal_lines(rev_journals[0]["id"])
            by_account = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_account[rec["id"]]["debit"])) == Decimal("4000000.00")
            assert Decimal(str(by_account[bca["account_id"]]["credit"])) == Decimal("4000000.00")

            invoice_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert Decimal(str(invoice_after["paid_total"])) == Decimal("0")
            assert Decimal(str(invoice_after["outstanding_amount"])) == Decimal("10000000")
            assert invoice_after["payment_status"] == "BELUM DIBAYAR"

            again = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            assert again.status_code == 200
            assert len(await _source_journals(receipt["id"], SOURCE_REVERSAL)) == 1
            still = await server.db.sales_receipts.find_one({"id": receipt["id"]})
            assert still is not None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_k_and_balance_then_reverse_parity():
    rec, _rev, _tax = await _configure_settings()
    bca = await _cash_bank("BCA Operasional")
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price=10_000_000)
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            assert await _account_net(rec["id"]) == Decimal("10000000.00")

            r1 = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
            p1 = await client.post(f"/api/sales-receipts/{r1['id']}/post-accounting")
            assert p1.status_code == 200
            assert await _account_net(rec["id"]) == Decimal("6000000.00")
            assert await _account_net(bca["account_id"]) == Decimal("4000000.00")

            r2 = await _create_receipt(client, inv["id"], 6_000_000, cash_bank_id=bca["id"])
            p2 = await client.post(f"/api/sales-receipts/{r2['id']}/post-accounting")
            assert p2.status_code == 200
            assert await _account_net(rec["id"]) == Decimal("0.00")
            assert await _account_net(bca["account_id"]) == Decimal("10000000.00")

            invoice = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert Decimal(str(invoice["outstanding_amount"])) == Decimal("0")
            assert invoice["payment_status"] == "LUNAS"

            credits = Decimal("0.00")
            for rid in (r1["id"], r2["id"]):
                journals = await _source_journals(rid)
                lines = await _journal_lines(journals[0]["id"])
                credits += sum(Decimal(str(ln["credit"])) for ln in lines if ln["account_id"] == rec["id"])
                debit = sum(Decimal(str(ln["debit"])) for ln in lines)
                credit = sum(Decimal(str(ln["credit"])) for ln in lines)
                assert debit == credit
            assert credits == Decimal("10000000.00")

            rev = await client.post(f"/api/sales-receipts/{r2['id']}/reverse-accounting")
            assert rev.status_code == 200
            assert await _account_net(rec["id"]) == Decimal("6000000.00")
            assert await _account_net(bca["account_id"]) == Decimal("4000000.00")
            invoice2 = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert Decimal(str(invoice2["outstanding_amount"])) == Decimal("6000000")
            assert Decimal(str(invoice2["paid_total"])) == Decimal("4000000")
            assert invoice2["payment_status"] == "DP"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_l_historical_receipt_reads_not_posted():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            rid = str(uuid.uuid4())
            await server.db.sales_receipts.insert_one({
                "id": rid,
                "receipt_number": "PAY/HIST/001",
                "invoice_id": inv["id"],
                "invoice_number": inv["invoice_number"],
                "po_id": po["id"],
                "po_number": po["po_number"],
                "customer": "Customer ABC",
                "receipt_date": "2026-01-01",
                "amount": 1_000_000,
                "notes": "legacy",
            })
            res = await client.get(f"/api/sales-receipts/{rid}")
            assert res.status_code == 200
            body = res.json()["receipt"]
            assert body["accounting_status"] == "not_posted"
            assert body["accounting_posted"] is False
            assert body["accounting_journal_id"] is None
            assert await _source_journals(rid) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_n_o_proof_and_overpayment_unchanged():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"], notes="bukti tetap")
            before = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
            posted = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert posted.status_code == 200
            after = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
            assert after["amount"] == before["amount"]
            assert after["notes"] == "bukti tetap"
            assert after.get("payment_proof") == before.get("payment_proof")

            over = await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 7_000_000,
                "cash_bank_id": bca["id"],
                "receipt_date": "2026-09-06",
            })
            assert over.status_code == 400
            assert "melebihi sisa tagihan" in str(over.json()["detail"])
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_create_does_not_write_journal_and_cash_bank_options():
    await _configure_settings()
    bca = await _cash_bank("Kas Kecil")
    await _cash_bank("Mandiri Nonaktif", active=False)
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            opts = await client.get("/api/sales-receipts/cash-bank-options")
            assert opts.status_code == 200
            names = {row["name"] for row in opts.json()["items"]}
            assert "Kas Kecil" in names
            assert "Mandiri Nonaktif" not in names

            inv = await _create_invoice(client, po["id"])
            before = await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE})
            receipt = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bca["id"])
            assert receipt["accounting_status"] == "not_posted"
            assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_non_admin_without_accounting_perm_forbidden():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bca["id"])
        _user("crm", {
            "sales.receipt.create": True,
            "sales.receipt.view": True,
            "sales.receipt.delete": True,
            "po.edit": True,
            "sales_order.create": True,
        })
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            post = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert post.status_code == 403
            rev = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            assert rev.status_code == 403
            assert await _source_journals(receipt["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)
