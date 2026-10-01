"""Customer credit from sales receipt overpayments, on a dedicated test database."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "credit"
os.environ["DB_NAME"] = f"eternal_packaging_customer_credit_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounting_periods import close_period
from services.accounts_receivable import _load_current_invoices
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.general_ledger import get_general_ledger
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_customer_credit import COLLECTION, SOURCE_TYPE, apply_customer_credit, ensure_indexes as ensure_credit
from services.sales_receipt_payment_proof import load_proof_bytes
from services.sales_receipt_posting import SOURCE_TYPE as RECEIPT_SOURCE

pytestmark = pytest.mark.anyio
ADMIN = {"id": "admin-credit", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_cb(server.db)
    await ensure_credit(server.db)
    for name in (
        "orders", "sales_invoices", "sales_receipts", COLLECTION,
        "journal_entries", "journal_lines", "ledger_accounts",
        "cash_bank_accounts", "app_settings", "files", "accounting_periods",
    ):
        await server.db[name].delete_many({})
    app.dependency_overrides[current_user] = lambda: dict(ADMIN)
    yield
    app.dependency_overrides.pop(current_user, None)


async def _coa(prefix, name, account_type, **extra):
    doc = await create_account(
        server.db,
        {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user=ADMIN,
    )
    if extra:
        await server.db.ledger_accounts.update_one({"id": doc["id"]}, {"$set": extra})
        doc.update(extra)
    return doc


async def _settings(deposit=None):
    rec = await _coa("11", "Piutang", "AREC")
    rev = await _coa("41", "Pendapatan", "REVE")
    tax = await _coa("21", "PPN", "OCLY")
    patch = {
        "key": SETTINGS_KEY,
        "receivable_account_id": rec["id"],
        "revenue_account_id": rev["id"],
        "output_tax_account_id": tax["id"],
    }
    if deposit:
        patch["customer_deposit_account_id"] = deposit["id"]
    await server.db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": patch}, upsert=True)
    return rec, rev, tax


async def _bank():
    account = await _coa("12", "Kas", "BANK")
    return await create_cash_bank(server.db, {"name": "Kas Uji", "account_id": account["id"], "active": True}, user=ADMIN)


async def _po(customer_id="CUST-1", **extra):
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": f"PO/CREDIT/{uuid.uuid4().hex[:6]}",
        "customer": extra.pop("customer", "Pelanggan Uji"),
        "customer_id": customer_id,
        "outlet": "Outlet",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 10, "unit": "pcs"}],
        "current_stage": 4,
        "payment_status": "Waiting",
        "created_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.orders.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def _invoice(client, po_id, total, date="2026-09-10"):
    created = await client.post("/api/sales-invoices", json={
        "po_id": po_id,
        "invoice_date": date,
        "discount_amount": 0,
        "ppn_percent": 0,
        "lines": [{"line_idx": 0, "quantity": 1, "unit_price": total}],
    })
    assert created.status_code == 200, created.text
    invoice = created.json()["invoice"]
    assert (await client.post(f"/api/sales-invoices/{invoice['id']}/finalize")).status_code == 200
    assert (await client.post(f"/api/sales-invoices/{invoice['id']}/post-accounting")).status_code == 200
    return invoice


async def _receipt(client, invoice_id, amount, bank_id, **extra):
    payload = {
        "invoice_id": invoice_id,
        "amount": amount,
        "cash_bank_id": bank_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-12"),
        "idempotency_key": extra.pop("idempotency_key", str(uuid.uuid4())),
    }
    payload.update(extra)
    res = await client.post("/api/sales-receipts", json=payload)
    return res


def _lines(entries):
    return entries


async def _gl(account_id):
    return await get_general_ledger(
        server.db, account_id=account_id, start_date="2026-09-01", end_date="2026-09-30"
    )


async def _ar_row(invoice_id):
    rows = await _load_current_invoices(server.db, aging_reference_date="2026-09-30")
    return next(row for row in rows if row["invoice_id"] == invoice_id)


@pytest.mark.anyio
async def test_exact_and_underpayment_keep_previous_behavior():
    await _settings()
    bank = await _bank()
    po = await _po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice = await _invoice(client, po["id"], 700_000)
        under = await _receipt(client, invoice["id"], 200_000, bank["id"])
        assert under.status_code == 200, under.text
        body = under.json()["receipt"]
        assert body["allocated_amount"] == 200_000
        assert body["customer_credit_amount"] == 0
        assert body["customer_credit"]["state"] == "none"
        exact = await _receipt(client, invoice["id"], 500_000, bank["id"])
        assert exact.status_code == 200, exact.text
        fresh = await client.get(f"/api/sales-invoices/{invoice['id']}")
        assert fresh.json()["invoice"]["outstanding_amount"] == 0
        assert fresh.json()["invoice"]["payment_status"] == "LUNAS"


@pytest.mark.anyio
async def test_overpayment_credit_apply_partial_other_customer_and_ledger():
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    receivable, _, _ = await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    other = await _po("CUST-2", customer="Pelanggan Lain")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        assert created.status_code == 200, created.text
        receipt = created.json()["receipt"]
        assert receipt["amount"] == 750_000
        assert receipt["allocated_amount"] == 700_000
        assert receipt["customer_credit_amount"] == 50_000
        assert receipt["customer_credit"]["state"] == "draft"
        assert receipt["customer_credit"]["available"] == 0
        assert "Tersedia" not in str(receipt["customer_credit"].get("reason") or "")

        blocked = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_a["id"],
            "amount": 50_000,
        })
        assert blocked.status_code == 409

        journals_before = await server.db.journal_entries.count_documents({"source_type": RECEIPT_SOURCE})
        posted = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        assert await server.db.journal_entries.count_documents({"source_type": RECEIPT_SOURCE, "reversal_of": None}) == journals_before + 1
        journal = await server.db.journal_entries.find_one({"source_type": RECEIPT_SOURCE, "source_id": receipt["id"], "reversal_of": None})
        lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(10)
        by_account = {}
        for line in lines:
            by_account.setdefault(line["account_id"], {"debit": Decimal("0"), "credit": Decimal("0")})
            by_account[line["account_id"]]["debit"] += Decimal(str(line["debit"] or 0))
            by_account[line["account_id"]]["credit"] += Decimal(str(line["credit"] or 0))
        assert by_account[bank["account_id"]]["debit"] == Decimal("750000")
        assert by_account[receivable["id"]]["credit"] == Decimal("700000")
        assert by_account[deposit["id"]]["credit"] == Decimal("50000")
        assert len(lines) == 3

        detail = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert detail.json()["receipt"]["customer_credit"]["state"] == "available"
        assert detail.json()["receipt"]["customer_credit"]["available"] == 50_000
        paid_a = await client.get(f"/api/sales-invoices/{invoice_a['id']}")
        assert paid_a.json()["invoice"]["outstanding_amount"] == 0

        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        # Pin the journal date inside the September ledger window. Omitting it
        # dates the application today, so the reversal debits fall outside _gl.
        partial = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 20_000,
            "idempotency_key": "partial-20",
            "application_date": "2026-09-20",
        })
        assert partial.status_code == 200, partial.text
        again = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 20_000,
            "idempotency_key": "partial-20",
            "application_date": "2026-09-20",
        })
        assert again.status_code == 200
        assert again.json()["application"]["id"] == partial.json()["application"]["id"]
        assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "reversal_of": None}) == 1
        offer = await client.get("/api/sales-receipts/customer-credit", params={"invoice_id": invoice_b["id"]})
        assert offer.json()["available"] == 30_000
        mid = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert mid.json()["invoice"]["outstanding_amount"] == 100_000

        rest = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 30_000,
            "idempotency_key": "rest-30",
            "application_date": "2026-09-20",
        })
        assert rest.status_code == 200, rest.text
        done = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert done.json()["invoice"]["outstanding_amount"] == 70_000
        empty = await client.get("/api/sales-receipts/customer-credit", params={"invoice_id": invoice_b["id"]})
        assert empty.json()["loaded"] is True
        assert empty.json()["available"] == 0

        foreign = await _invoice(client, other["id"], 120_000, date="2026-09-16")
        # restore credit by reversing both applications first, then prove the other customer is rejected
        for app_id in (partial.json()["application"]["id"], rest.json()["application"]["id"]):
            rev = await client.post(f"/api/sales-receipts/customer-credit/applications/{app_id}/reverse")
            assert rev.status_code == 200, rev.text
        restored = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert restored.json()["invoice"]["outstanding_amount"] == 120_000
        available = await client.get("/api/sales-receipts/customer-credit", params={"customer_id": "CUST-1"})
        assert available.json()["available"] == 50_000

        denied = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": foreign["id"],
            "amount": 50_000,
        })
        assert denied.status_code == 409
        assert "tidak sama" in str(denied.json()["detail"])

        used = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "use-50",
            "application_date": "2026-09-20",
        })
        assert used.status_code == 200, used.text
        final_b = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert final_b.json()["invoice"]["outstanding_amount"] == 70_000

        cash = await _gl(bank["account_id"])
        ar = await _gl(receivable["id"])
        dep = await _gl(deposit["id"])
        assert cash["total_debit"] == 750_000
        assert cash["total_credit"] == 0
        # Invoice debits 700+120+120. Application reversals debit AR 20+30.
        assert ar["total_debit"] == 990_000
        # Receipt 700 plus applications 20+30+50. Reversals credit the deposit, not AR.
        assert ar["total_credit"] == 800_000
        assert dep["total_debit"] == 100_000
        assert dep["total_credit"] == 100_000

        row_a = await _ar_row(invoice_a["id"])
        row_b = await _ar_row(invoice_b["id"])
        assert row_a["accounting_outstanding"] == 0
        assert row_b["accounting_outstanding"] == 70_000
        assert row_b["operational_outstanding"] == 70_000

        blocked_source = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert blocked_source.status_code == 409
        assert "Balikkan pemakaian" in blocked_source.json()["detail"]
        rev_use = await client.post(
            f"/api/sales-receipts/customer-credit/applications/{used.json()['application']['id']}/reverse"
        )
        assert rev_use.status_code == 200, rev_use.text
        source_rev = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert source_rev.status_code == 200, source_rev.text
        after = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert after.json()["receipt"]["customer_credit"]["state"] == "cancelled"
        assert after.json()["receipt"]["customer_credit"]["available"] == 0
        kept = await server.db.journal_entries.count_documents({"source_id": receipt["id"]})
        assert kept >= 2


@pytest.mark.anyio
async def test_concurrent_apply_cannot_spend_the_same_balance_twice():
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200

    async def once(key):
        try:
            return await apply_customer_credit(server.db, {
                "source_receipt_id": receipt["id"],
                "invoice_id": invoice_b["id"],
                "amount": 50_000,
                "idempotency_key": key,
            }, ADMIN)
        except Exception as exc:
            return exc

    first, second = await asyncio.gather(once("con-1"), once("con-2"))
    successes = [item for item in (first, second) if isinstance(item, dict)]
    failures = [item for item in (first, second) if not isinstance(item, dict)]
    assert len(successes) == 1
    assert len(failures) == 1
    journals = await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "reversal_of": None, "status": "posted"})
    assert journals == 1
    stored = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0, "customer_credit_applied": 1})
    assert stored["customer_credit_applied"] == 50_000


@pytest.mark.anyio
async def test_unmapped_or_invalid_deposit_blocks_post_without_a_journal():
    await _settings()
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice = await _invoice(client, po["id"], 700_000)
        created = await _receipt(client, invoice["id"], 750_000, bank["id"])
        receipt_id = created.json()["receipt"]["id"]
        before = await server.db.journal_entries.count_documents({})
        denied = await client.post(f"/api/sales-receipts/{receipt_id}/post-accounting")
        assert denied.status_code == 409
        assert "belum dipetakan" in denied.json()["detail"]
        assert await server.db.journal_entries.count_documents({}) == before
        fresh = await client.get(f"/api/sales-receipts/{receipt_id}")
        assert fresh.json()["receipt"]["accounting_status"] == "not_posted"

        bad = await _coa("23", "Header Titipan", "OCLY", is_header=True)
        await server.db.app_settings.update_one(
            {"key": SETTINGS_KEY}, {"$set": {"customer_deposit_account_id": bad["id"]}}
        )
        denied_header = await client.post(f"/api/sales-receipts/{receipt_id}/post-accounting")
        assert denied_header.status_code == 400
        assert await server.db.journal_entries.count_documents({}) == before

        wrong = await _coa("24", "Bukan Kewajiban", "REVE")
        await server.db.app_settings.update_one(
            {"key": SETTINGS_KEY}, {"$set": {"customer_deposit_account_id": wrong["id"]}}
        )
        denied_type = await client.post(f"/api/sales-receipts/{receipt_id}/post-accounting")
        assert denied_type.status_code == 400
        assert await server.db.journal_entries.count_documents({}) == before
        still = await server.db.sales_receipts.find_one({"id": receipt_id}, {"_id": 0, "customer_credit_applied": 1})
        assert still.get("customer_credit_applied") in (0, 0.0, None)


@pytest.mark.anyio
async def test_closed_period_draft_legacy_and_cancelled_rules():
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice = await _invoice(client, po["id"], 700_000)
        created = await _receipt(client, invoice["id"], 750_000, bank["id"], receipt_date="2026-09-12")
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200
        target = await _invoice(client, po["id"], 120_000, date="2026-09-18")
        await close_period(server.db, "2026-09", user=ADMIN, reason="uji tutup")
        closed = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": target["id"],
            "amount": 50_000,
            "application_date": "2026-09-20",
        })
        assert closed.status_code == 409
        assert await server.db[COLLECTION].count_documents({}) == 0
        legacy_id = str(uuid.uuid4())
        await server.db.sales_receipts.insert_one({
            "id": legacy_id,
            "receipt_number": "PAY/LAMA/1",
            "invoice_id": invoice["id"],
            "invoice_number": invoice["invoice_number"],
            "po_id": po["id"],
            "customer": "Pelanggan Uji",
            "receipt_date": "2026-01-02",
            "amount": 10_000,
            "financial_status": "active",
        })
        legacy = await client.get(f"/api/sales-receipts/{legacy_id}")
        assert legacy.status_code == 200
        body = legacy.json()["receipt"]
        assert body["allocated_amount"] == 10_000
        assert body["customer_credit"]["state"] == "none"
        assert body["customer_credit"]["available"] == 0


@pytest.mark.anyio
async def test_payment_proof_requires_receipt_link_and_permission(monkeypatch):
    po = await _po("CUST-1")
    receipt_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    await server.db.sales_receipts.insert_one({
        "id": receipt_id,
        "receipt_number": "PAY/PROOF/1",
        "invoice_id": "inv",
        "po_id": po["id"],
        "customer": "Pelanggan Uji",
        "amount": 10,
        "payment_proof": {"id": file_id, "name": "bukti.pdf", "content_type": "application/pdf"},
    })
    await server.db.files.insert_one({
        "id": file_id,
        "kind": "sales_receipt_payment_proof",
        "receipt_id": other_id,
        "content_type": "text/html",
        "original_filename": "bukti.html",
        "storage_path": "secret/bukti.pdf",
        "is_deleted": False,
    })
    monkeypatch.setattr(
        "services.sales_receipt_payment_proof.obj_storage.get_object",
        lambda path: (b"<html></html>", "text/html"),
    )
    with pytest.raises(Exception) as denied_file:
        await load_proof_bytes(server.db, receipt_id)
    assert getattr(denied_file.value, "status_code", None) == 404

    await server.db.files.update_one({"id": file_id}, {"$set": {"receipt_id": receipt_id}})
    with pytest.raises(Exception) as bad_type:
        await load_proof_bytes(server.db, receipt_id)
    assert getattr(bad_type.value, "status_code", None) == 415

    app.dependency_overrides[current_user] = lambda: {
        "id": "outsider",
        "email": "outsider@example.com",
        "name": "Outsider",
        "role": "staff",
        "permissions_override": {},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(f"/api/sales-receipts/{receipt_id}/payment-proof")
        assert res.status_code == 403


def _net(report):
    return report["total_debit"] - report["total_credit"]


async def _posted_pair(client):
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    receivable, _, _ = await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    invoice_a = await _invoice(client, po["id"], 700_000)
    invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
    created = await _receipt(client, invoice_a["id"], 750_000, bank["id"], receipt_date="2026-09-12")
    assert created.status_code == 200, created.text
    receipt = created.json()["receipt"]
    posted = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
    assert posted.status_code == 200, posted.text
    return deposit, receivable, bank, po, invoice_a, invoice_b, receipt


@pytest.mark.anyio
async def test_credit_and_cash_cannot_drive_outstanding_negative():
    from services.sales_receipts import create_sales_receipt

    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200

    async def use_credit():
        return await apply_customer_credit(server.db, {
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "race-credit",
            "application_date": "2026-09-16",
        }, ADMIN)

    async def take_cash():
        return await create_sales_receipt(server.db, {
            "invoice_id": invoice_b["id"],
            "amount": 80_000,
            "cash_bank_id": bank["id"],
            "receipt_date": "2026-09-16",
            "idempotency_key": "race-cash",
        }, ADMIN)

    results = await asyncio.gather(use_credit(), take_cash(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    assert len(successes) == 1
    fresh = await server.db.sales_invoices.find_one({"id": invoice_b["id"]}, {"_id": 0})
    assert fresh["outstanding_amount"] >= 0
    assert fresh["paid_total"] <= fresh["grand_total"]
    assert fresh["outstanding_amount"] == fresh["grand_total"] - fresh["paid_total"]


@pytest.mark.anyio
async def test_retry_after_journal_does_not_duplicate_credit_use():
    from services.journal_core import post_balanced_journal

    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    receivable, _, _ = await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200
        await server.db.sales_receipts.update_one(
            {"id": receipt["id"]}, {"$set": {"customer_credit_applied": 0}},
        )
        app_id = str(uuid.uuid4())
        await server.db[COLLECTION].insert_one({
            "id": app_id,
            "idempotency_key": "crash-after-journal",
            "source_receipt_id": receipt["id"],
            "customer_id": "CUST-1",
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "status": "reserving",
            "financial_status": "active",
            "reserved": True,
            "claim_token": "crashed",
        })
        await post_balanced_journal(
            server.db,
            date="2026-09-16",
            source_type=SOURCE_TYPE,
            source_id=app_id,
            description="Pemakaian terputus",
            lines=[
                {"account_id": deposit["id"], "debit": 50_000, "credit": 0, "description": "Titipan"},
                {"account_id": receivable["id"], "debit": 0, "credit": 50_000, "description": "Piutang"},
            ],
            posted_by=ADMIN,
        )
        retried = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "crash-after-journal",
            "application_date": "2026-09-16",
        })
        assert retried.status_code == 200, retried.text
        assert retried.json()["application"]["id"] == app_id
        assert retried.json()["application"]["status"] == "posted"
        assert await server.db.journal_entries.count_documents({
            "source_type": SOURCE_TYPE, "source_id": app_id, "reversal_of": None,
        }) == 1
        stored = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
        assert stored["customer_credit_applied"] == 50_000
        target = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert target.json()["invoice"]["outstanding_amount"] == 70_000


@pytest.mark.anyio
async def test_expired_claim_cannot_overwrite_newer_credit_use(monkeypatch):
    import services.sales_customer_credit as credit_mod
    from services.sales_invoice_posting import RECEIPT_CREATE_CLAIM_AT_FIELD

    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200

    real_live = credit_mod._claim_is_live
    state = {"armed": True}

    async def expire_before_post(db, receipt_id, token):
        if state["armed"]:
            state["armed"] = False
            await db.sales_receipts.update_one(
                {"id": receipt_id},
                {"$set": {credit_mod.CLAIM_AT_FIELD: "2000-01-01T00:00:00+00:00"}},
            )
            await db.sales_invoices.update_one(
                {"id": invoice_b["id"]},
                {"$set": {RECEIPT_CREATE_CLAIM_AT_FIELD: "2000-01-01T00:00:00+00:00"}},
            )
            monkeypatch.setattr(credit_mod, "_claim_is_live", real_live)
            await apply_customer_credit(db, {
                "source_receipt_id": receipt["id"],
                "invoice_id": invoice_b["id"],
                "amount": 50_000,
                "idempotency_key": "newer-worker",
                "application_date": "2026-09-16",
            }, ADMIN)
            return False
        return await real_live(db, receipt_id, token)

    monkeypatch.setattr(credit_mod, "_claim_is_live", expire_before_post)
    with pytest.raises(HTTPException) as stale:
        await apply_customer_credit(server.db, {
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "old-worker",
            "application_date": "2026-09-16",
        }, ADMIN)
    assert stale.value.status_code == 409
    assert "lebih baru" in stale.value.detail
    posted = await server.db[COLLECTION].find(
        {"source_receipt_id": receipt["id"], "status": "posted"}, {"_id": 0, "idempotency_key": 1},
    ).to_list(10)
    assert [row["idempotency_key"] for row in posted] == ["newer-worker"]
    assert await server.db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "reversal_of": None, "status": "posted",
    }) == 1
    stored = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert stored["customer_credit_applied"] == 50_000


@pytest.mark.anyio
async def test_source_reverse_and_credit_use_cannot_leave_unbacked_credit():
    from services.sales_receipt_posting import reverse_sales_receipt_accounting

    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200

    async def use_credit():
        return await apply_customer_credit(server.db, {
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "race-use",
            "application_date": "2026-09-16",
        }, ADMIN)

    async def reverse_source():
        return await reverse_sales_receipt_accounting(server.db, receipt["id"], ADMIN)

    await asyncio.gather(use_credit(), reverse_source(), return_exceptions=True)
    active = await server.db[COLLECTION].count_documents({
        "source_receipt_id": receipt["id"], "status": "posted", "financial_status": "active",
    })
    fresh = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    journal = await server.db.journal_entries.find_one({
        "source_type": RECEIPT_SOURCE, "source_id": receipt["id"], "reversal_of": None,
    }, {"_id": 0, "status": 1})
    cancelled = bool(fresh.get("cancelled") or fresh.get("financial_status") == "cancelled")
    reversed_journal = journal and journal.get("status") == "reversed"
    assert not (active and (cancelled or reversed_journal))
    if active:
        assert fresh["customer_credit_applied"] == 50_000
        assert journal["status"] == "posted"
    else:
        assert fresh.get("customer_credit_applied") in (0, 0.0, None)
        assert reversed_journal


@pytest.mark.anyio
async def test_reversal_stages_reconcile_general_ledger():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        deposit, receivable, bank, _po_doc, _invoice_a, invoice_b, receipt = await _posted_pair(client)
        cash = await _gl(bank["account_id"])
        dep = await _gl(deposit["id"])
        assert cash["total_debit"] == 750_000
        assert cash["total_credit"] == 0
        assert dep["total_credit"] == 50_000
        assert dep["total_debit"] == 0
        detail = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert detail.json()["receipt"]["customer_credit"]["available"] == 50_000

        used = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "stage-use",
            "application_date": "2026-09-16",
        })
        assert used.status_code == 200, used.text
        app_id = used.json()["application"]["id"]
        target = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert target.json()["invoice"]["outstanding_amount"] == 70_000
        cash = await _gl(bank["account_id"])
        dep = await _gl(deposit["id"])
        assert cash["total_debit"] == 750_000 and cash["total_credit"] == 0
        assert dep["total_debit"] == 50_000 and dep["total_credit"] == 50_000
        after_use = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert after_use.json()["receipt"]["customer_credit"]["available"] == 0

        journals_before = await server.db.journal_entries.count_documents({})
        blocked = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert blocked.status_code == 409
        assert "Balikkan pemakaian" in blocked.json()["detail"]
        assert await server.db.journal_entries.count_documents({}) == journals_before
        still = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert still.json()["invoice"]["outstanding_amount"] == 70_000

        first = await client.post(f"/api/sales-receipts/customer-credit/applications/{app_id}/reverse")
        assert first.status_code == 200, first.text
        again = await client.post(f"/api/sales-receipts/customer-credit/applications/{app_id}/reverse")
        assert again.status_code == 200, again.text
        assert again.json()["application"]["id"] == app_id
        restored = await client.get(f"/api/sales-invoices/{invoice_b['id']}")
        assert restored.json()["invoice"]["outstanding_amount"] == 120_000
        available = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert available.json()["receipt"]["customer_credit"]["available"] == 50_000
        assert await server.db.journal_entries.count_documents({
            "source_type": "sales_customer_credit_application_reversal",
            "source_id": app_id,
        }) == 1
        cash = await _gl(bank["account_id"])
        dep = await _gl(deposit["id"])
        assert cash["total_debit"] == 750_000 and cash["total_credit"] == 0
        assert dep["total_credit"] - dep["total_debit"] == 50_000

        source = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert source.status_code == 200, source.text
        source_again = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert source_again.status_code == 200, source_again.text
        done = await client.get(f"/api/sales-receipts/{receipt['id']}")
        assert done.json()["receipt"]["customer_credit"]["state"] == "cancelled"
        assert done.json()["receipt"]["customer_credit"]["available"] == 0
        cash = await _gl(bank["account_id"])
        dep = await _gl(deposit["id"])
        ar = await _gl(receivable["id"])
        assert cash["total_debit"] == 750_000 and cash["total_credit"] == 750_000
        assert dep["total_debit"] == dep["total_credit"]
        # Both invoices stay posted, so AR returns to 700.000 + 120.000.
        assert ar["total_debit"] - ar["total_credit"] == 820_000
        primary = await server.db.journal_entries.find_one({
            "source_type": RECEIPT_SOURCE, "source_id": receipt["id"], "reversal_of": None,
        }, {"_id": 0, "id": 1})
        reversals = await server.db.journal_entries.find(
            {"reversal_of": primary["id"]}, {"_id": 0, "id": 1},
        ).to_list(5)
        assert len(reversals) == 1
        rev_lines = await server.db.journal_lines.find(
            {"journal_id": reversals[0]["id"]}, {"_id": 0, "account_id": 1, "debit": 1, "credit": 1},
        ).to_list(10)
        by_account = {}
        for line in rev_lines:
            bucket = by_account.setdefault(line["account_id"], {"debit": 0.0, "credit": 0.0})
            bucket["debit"] += float(line.get("debit") or 0)
            bucket["credit"] += float(line.get("credit") or 0)
        assert by_account[bank["account_id"]] == {"debit": 0.0, "credit": 750_000.0}
        assert by_account[receivable["id"]] == {"debit": 700_000.0, "credit": 0.0}
        assert by_account[deposit["id"]] == {"debit": 50_000.0, "credit": 0.0}


@pytest.mark.anyio
async def test_closed_period_rejects_credit_and_source_reversal():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        _deposit, _receivable, _bank, _po_doc, _invoice_a, invoice_b, receipt = await _posted_pair(client)
        used = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "application_date": "2026-09-16",
        })
        assert used.status_code == 200, used.text
        app_id = used.json()["application"]["id"]
        await close_period(server.db, "2026-09", user=ADMIN, reason="uji tutup")
        journals = await server.db.journal_entries.count_documents({})
        outstanding = (await client.get(f"/api/sales-invoices/{invoice_b['id']}")).json()["invoice"]["outstanding_amount"]
        applied = (await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}))["customer_credit_applied"]
        denied_use = await client.post(f"/api/sales-receipts/customer-credit/applications/{app_id}/reverse")
        denied_source = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
        assert denied_use.status_code == 409
        assert denied_source.status_code == 409
        assert await server.db.journal_entries.count_documents({}) == journals
        assert (await client.get(f"/api/sales-invoices/{invoice_b['id']}")).json()["invoice"]["outstanding_amount"] == outstanding
        assert (await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}))["customer_credit_applied"] == applied


@pytest.mark.anyio
async def test_currency_mismatch_is_rejected_and_blank_means_idr():
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert receipt["currency"] == "IDR"
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200
        await server.db.sales_receipts.update_one({"id": receipt["id"]}, {"$set": {"currency": "USD"}})
        denied = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "usd-vs-blank",
        })
        assert denied.status_code == 409
        assert "Mata uang" in denied.json()["detail"]
        assert await server.db[COLLECTION].count_documents({"status": "posted"}) == 0
        await server.db.orders.update_one({"id": po["id"]}, {"$set": {"currency": "USD"}})
        allowed = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "usd-vs-usd",
        })
        assert allowed.status_code == 200, allowed.text
        await server.db.orders.update_one({"id": po["id"]}, {"$set": {"currency": "EUR"}})
        different = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "usd-vs-eur",
        })
        assert different.status_code == 409
        assert "Mata uang" in different.json()["detail"]


@pytest.mark.anyio
async def test_branch_mismatch_and_one_sided_blank_are_rejected():
    deposit = await _coa("22", "Titipan Pelanggan", "OCLY")
    await _settings(deposit)
    bank = await _bank()
    po = await _po("CUST-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invoice_a = await _invoice(client, po["id"], 700_000)
        invoice_b = await _invoice(client, po["id"], 120_000, date="2026-09-15")
        created = await _receipt(client, invoice_a["id"], 750_000, bank["id"])
        receipt = created.json()["receipt"]
        assert receipt.get("branch_id") in (None, "")
        assert (await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")).status_code == 200
        await server.db.sales_receipts.update_one({"id": receipt["id"]}, {"$set": {"branch_id": "BR-1"}})
        one_sided = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "branch-one-sided",
        })
        assert one_sided.status_code == 409
        assert "Cabang" in one_sided.json()["detail"]
        await server.db.orders.update_one({"id": po["id"]}, {"$set": {"branch_id": "BR-2"}})
        different = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "branch-different",
        })
        assert different.status_code == 409
        assert "Cabang" in different.json()["detail"]
        await server.db.orders.update_one({"id": po["id"]}, {"$set": {"branch_id": "BR-1"}})
        matched = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "branch-same",
        })
        assert matched.status_code == 200, matched.text
        await server.db.sales_receipts.update_one({"id": receipt["id"]}, {"$unset": {"branch_id": ""}})
        blank_source = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 10_000,
            "idempotency_key": "branch-blank-source",
        })
        assert blank_source.status_code == 409
        assert "Cabang" in blank_source.json()["detail"]


@pytest.mark.anyio
async def test_credit_mutations_keep_accounting_permissions():
    from services.sales_receipt_posting import PERM_POST, PERM_REVERSE

    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    create = src[src.index("async def create_sales_receipt_endpoint"):src.index("async def create_sales_receipt_endpoint") + 500]
    for perm in ("sales.receipt.create", "sales.invoice.create", "sales_order.create", "po.edit"):
        assert f'"{perm}"' in create
    apply_chunk = src[src.index("async def apply_sales_customer_credit_endpoint"):src.index("async def reverse_sales_customer_credit_endpoint")]
    reverse_chunk = src[src.index("async def reverse_sales_customer_credit_endpoint"):src.index("async def reverse_sales_customer_credit_endpoint") + 700]
    assert PERM_POST == "sales.receipt.accounting.post"
    assert PERM_REVERSE == "sales.receipt.accounting.reverse"
    assert "_SALES_RECEIPT_ACCOUNTING_POST_PERM" in apply_chunk
    assert "_SALES_RECEIPT_ACCOUNTING_REVERSE_PERM" in reverse_chunk
    app.dependency_overrides[current_user] = lambda: {
        "id": "crm-credit",
        "email": "crm-credit@example.com",
        "name": "CRM",
        "role": "crm",
        "permissions_override": {},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied_apply = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": "missing",
            "invoice_id": "missing",
            "amount": 1,
        })
        denied_reverse = await client.post("/api/sales-receipts/customer-credit/applications/missing/reverse")
        assert denied_apply.status_code == 403
        assert denied_reverse.status_code == 403


async def _posted_credit_journals():
    journals = await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "reversal_of": None, "status": "posted"},
        {"_id": 0},
    ).to_list(20)
    total = 0.0
    for journal in journals:
        allocation = await server.db[COLLECTION].find_one({"id": journal["source_id"]}, {"_id": 0})
        assert allocation is not None
        assert allocation["status"] == "posted"
        assert allocation["financial_status"] == "active"
        lines = await server.db.journal_lines.find(
            {"journal_id": journal["id"]}, {"_id": 0, "debit": 1, "credit": 1},
        ).to_list(10)
        debit = sum(float(line.get("debit") or 0) for line in lines)
        credit = sum(float(line.get("credit") or 0) for line in lines)
        assert debit == float(allocation["amount"])
        assert credit == float(allocation["amount"])
        total += float(allocation["amount"])
    return journals, total


def _pause_before_credit_journal(monkeypatch):
    import services.sales_customer_credit as credit_mod

    entered = asyncio.Event()
    release = asyncio.Event()
    real = credit_mod.post_balanced_journal
    seen = {"paused": False}

    async def paused(*args, **kwargs):
        if kwargs.get("source_type") == SOURCE_TYPE and not seen["paused"]:
            seen["paused"] = True
            entered.set()
            await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(credit_mod, "post_balanced_journal", paused)
    return entered, release


@pytest.mark.anyio
async def test_expired_claim_cannot_journal_after_other_request_takes_balance(monkeypatch):
    import services.sales_customer_credit as credit_mod

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        deposit, _receivable, _bank, po, _invoice_a, invoice_b, receipt = await _posted_pair(client)
        invoice_c = await _invoice(client, po["id"], 120_000, date="2026-09-16")

    entered, release = _pause_before_credit_journal(monkeypatch)

    async def worker_a():
        return await apply_customer_credit(server.db, {
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "fence-a",
            "application_date": "2026-09-16",
        }, ADMIN)

    task_a = asyncio.create_task(worker_a())
    await asyncio.wait_for(entered.wait(), timeout=15)
    fenced = await server.db[COLLECTION].find_one({"idempotency_key": "fence-a"}, {"_id": 0})
    assert fenced["status"] == "posting"
    assert fenced["journal_fence"]
    assert await server.db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": fenced["id"],
    }) == 0
    await server.db.sales_receipts.update_one(
        {"id": receipt["id"]},
        {"$set": {credit_mod.CLAIM_AT_FIELD: "2000-01-01T00:00:00+00:00"}},
    )

    rival = await asyncio.gather(apply_customer_credit(server.db, {
        "source_receipt_id": receipt["id"],
        "invoice_id": invoice_c["id"],
        "amount": 50_000,
        "idempotency_key": "fence-b",
        "application_date": "2026-09-16",
    }, ADMIN), return_exceptions=True)
    assert isinstance(rival[0], HTTPException)
    assert "melebihi saldo" in str(rival[0].detail)
    release.set()
    winner = await task_a
    assert winner["id"] == fenced["id"]
    assert winner["status"] == "posted"

    journals, used = await _posted_credit_journals()
    assert len(journals) == 1
    assert used == 50_000
    stored = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert stored["customer_credit_applied"] == 50_000
    deposit_gl = await _gl(deposit["id"])
    assert deposit_gl["total_debit"] == 50_000
    assert deposit_gl["total_credit"] == 50_000
    target_b = await server.db.sales_invoices.find_one({"id": invoice_b["id"]}, {"_id": 0})
    target_c = await server.db.sales_invoices.find_one({"id": invoice_c["id"]}, {"_id": 0})
    assert target_b["outstanding_amount"] == 70_000
    assert target_c["outstanding_amount"] == 120_000
    assert target_b["outstanding_amount"] >= 0
    assert target_c["outstanding_amount"] >= 0


@pytest.mark.anyio
async def test_expired_claim_cannot_journal_after_source_reversal_starts(monkeypatch):
    import services.sales_customer_credit as credit_mod
    from services.sales_receipt_posting import receipt_is_cancelled, reverse_sales_receipt_accounting

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        deposit, _receivable, _bank, _po_doc, _invoice_a, invoice_b, receipt = await _posted_pair(client)

    entered, release = _pause_before_credit_journal(monkeypatch)

    async def worker_a():
        return await apply_customer_credit(server.db, {
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 50_000,
            "idempotency_key": "fence-reverse-a",
            "application_date": "2026-09-16",
        }, ADMIN)

    task_a = asyncio.create_task(worker_a())
    await asyncio.wait_for(entered.wait(), timeout=15)
    fenced = await server.db[COLLECTION].find_one({"idempotency_key": "fence-reverse-a"}, {"_id": 0})
    assert fenced["status"] == "posting"
    assert await server.db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": fenced["id"],
    }) == 0
    await server.db.sales_receipts.update_one(
        {"id": receipt["id"]},
        {"$set": {credit_mod.CLAIM_AT_FIELD: "2000-01-01T00:00:00+00:00"}},
    )
    source_before = await server.db.journal_entries.find_one({
        "source_type": RECEIPT_SOURCE, "source_id": receipt["id"], "reversal_of": None,
    }, {"_id": 0, "id": 1, "status": 1})
    denied = await asyncio.gather(
        reverse_sales_receipt_accounting(server.db, receipt["id"], ADMIN),
        return_exceptions=True,
    )
    assert isinstance(denied[0], HTTPException)
    assert denied[0].status_code == 409
    source_mid = await server.db.journal_entries.find_one({"id": source_before["id"]}, {"_id": 0, "status": 1})
    fresh_mid = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert source_mid["status"] == "posted"
    assert not receipt_is_cancelled(fresh_mid)
    assert await server.db.journal_entries.count_documents({"reversal_of": source_before["id"]}) == 0
    assert await server.db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": fenced["id"],
    }) == 0

    release.set()
    winner = await task_a
    assert winner["status"] == "posted"
    assert winner["amount"] == 50_000
    journals, used = await _posted_credit_journals()
    assert len(journals) == 1
    assert used == 50_000
    source_after = await server.db.journal_entries.find_one({"id": source_before["id"]}, {"_id": 0, "status": 1})
    fresh = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert source_after["status"] == "posted"
    assert not receipt_is_cancelled(fresh)
    assert await server.db.journal_entries.count_documents({"reversal_of": source_before["id"]}) == 0
    assert fresh["customer_credit_applied"] == 50_000
    deposit_gl = await _gl(deposit["id"])
    assert deposit_gl["total_debit"] == 50_000
    assert deposit_gl["total_credit"] == 50_000
    target = await server.db.sales_invoices.find_one({"id": invoice_b["id"]}, {"_id": 0})
    assert target["outstanding_amount"] == 70_000
