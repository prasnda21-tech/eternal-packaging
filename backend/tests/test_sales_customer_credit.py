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
        partial = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 20_000,
            "idempotency_key": "partial-20",
        })
        assert partial.status_code == 200, partial.text
        again = await client.post("/api/sales-receipts/customer-credit/applications", json={
            "source_receipt_id": receipt["id"],
            "invoice_id": invoice_b["id"],
            "amount": 20_000,
            "idempotency_key": "partial-20",
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
