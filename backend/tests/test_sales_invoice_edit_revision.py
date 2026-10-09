"""Phase 2.2C posted Sales Invoice revision. Local synthetic data only."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_edit_revision_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.accounts_receivable import _load_current_invoices
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.financial_journal_foundation import ALLOWLIST_KEY, FLAG_KEY
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_edit_revision import (
    EDIT_CLAIM_AT_FIELD,
    EDIT_CLAIM_FIELD,
    FENCE_FIELD,
    FENCE_KEY_FIELD,
    FLAG_KEY as EDIT_FLAG,
    RevisionPaused,
    revise_posted_sales_invoice,
)
from services.sales_invoice_posting import (
    SOURCE_TYPE,
    find_sales_invoice_source_journal,
    post_sales_invoice_accounting,
    reverse_sales_invoice_accounting,
)
from services.sales_invoices import update_sales_invoice
from services.sales_receipt_posting import SOURCE_TYPE as RECEIPT_SOURCE

ADMIN = {"id": "admin-rev", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def indexes(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_journal(db)
    await ensure_periods(db)
    await db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, FLAG_KEY, ALLOWLIST_KEY, EDIT_FLAG]}})
    yield
    for name in (
        "sales_invoices", "sales_receipts", "journal_entries", "journal_lines",
        "orders", "sales_customer_credit_applications", "sales_invoice_edit_revisions",
        "accounting_periods",
    ):
        await db[name].delete_many({})


async def _enable(db):
    await db.app_settings.update_one({"key": EDIT_FLAG}, {"$set": {"key": EDIT_FLAG, "enabled": True}}, upsert=True)
    await db.app_settings.update_one({"key": FLAG_KEY}, {"$set": {"key": FLAG_KEY, "enabled": True}}, upsert=True)
    await db.app_settings.update_one(
        {"key": ALLOWLIST_KEY},
        {"$set": {"key": ALLOWLIST_KEY, "source_types": ["sales_invoice"]}},
        upsert=True,
    )


async def _seed(db, *, invoice_date="2026-10-09", qty=2, po_qty=10, sales_user_id="sales-1"):
    po_id = str(uuid.uuid4())
    invoice_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO/{invoice_id[:6]}",
        "customer": "PT Revisi",
        "sales_user_id": sales_user_id,
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": po_qty, "unit": "pcs"}],
        "current_stage": 5,
    })
    await db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"INV/{invoice_id[:6]}",
        "po_id": po_id,
        "invoice_date": invoice_date,
        "financial_status": "finalized",
        "payment_term_days": 0,
        "lines": [{
            "line_idx": 0, "line_id": "l0", "item_name": "Cup",
            "quantity": qty, "unit_price": 1000, "line_subtotal": qty * 1000, "line_total": qty * 1000,
        }],
        "subtotal": qty * 1000,
        "discount_amount": 0,
        "after_discount": qty * 1000,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "other_charges": [],
        "other_charges_total": 0,
        "grand_total": qty * 1000,
        "paid_total": 0,
        "outstanding_amount": qty * 1000,
        "notes": "",
        "created_at": "2026-10-01T00:00:00+00:00",
    })
    rec = await create_account(db, {"code": f"11{uuid.uuid4().hex[:4]}", "name": "Piutang", "account_type": "AREC"}, user=ADMIN)
    rev = await create_account(db, {"code": f"41{uuid.uuid4().hex[:4]}", "name": "Pendapatan", "account_type": "REVE"}, user=ADMIN)
    tax = await create_account(db, {"code": f"21{uuid.uuid4().hex[:4]}", "name": "PPN", "account_type": "OCLY"}, user=ADMIN)
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "receivable_account_id": rec["id"],
            "revenue_account_id": rev["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )
    await post_sales_invoice_accounting(db, invoice_id, ADMIN)
    return invoice_id, po_id


def _payload(qty=3, invoice_date="2026-10-15", key="edit-1"):
    return {
        "idempotency_key": key,
        "invoice_date": invoice_date,
        "lines": [{"line_idx": 0, "quantity": qty, "unit_price": 1000}],
    }


async def _posted_count(db, invoice_id):
    return await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": invoice_id, "reversal_of": None, "status": "posted",
    })


@pytest.mark.anyio
async def test_successful_edit_same_and_cross_period(indexes):
    db = server.db
    await _enable(db)
    invoice_id, po_id = await _seed(db)
    number = (await db.sales_invoices.find_one({"id": invoice_id}))["invoice_number"]
    detail = await revise_posted_sales_invoice(db, invoice_id, _payload(), ADMIN)
    assert detail["id"] == invoice_id
    assert detail["invoice_number"] == number
    assert detail["po_id"] == po_id
    assert detail["financial_status"] == "finalized"
    assert detail["grand_total"] == 3000
    assert await _posted_count(db, invoice_id) == 1
    active = await find_sales_invoice_source_journal(db, invoice_id)
    assert active["status"] == "posted"
    assert active["date"] == "2026-10-15"
    raw = await db.journal_entries.find_one({"id": active["id"]}, {"_id": 0})
    assert raw["supersedes_journal_id"]
    assert raw["journal_revision"] == 2
    original = await db.journal_entries.find_one({"id": raw["supersedes_journal_id"]}, {"_id": 0})
    assert original["status"] == "reversed"
    assert original["date"] == "2026-10-09"
    reversal = await db.journal_entries.find_one({"reversal_of": original["id"]}, {"_id": 0})
    assert reversal["status"] == "posted"
    stored = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "financial_status": 1, "paid_total": 1})
    assert stored["financial_status"] == "finalized"
    assert stored["paid_total"] == 0

    other_id, _po = await _seed(db, invoice_date="2026-08-20", qty=1)
    moved = await revise_posted_sales_invoice(
        db, other_id, _payload(qty=2, invoice_date="2026-12-01", key="cross-1"), ADMIN,
    )
    assert moved["invoice_date"] == "2026-12-01"
    active2 = await find_sales_invoice_source_journal(db, other_id)
    assert active2["date"] == "2026-12-01"
    assert await _posted_count(db, other_id) == 1


@pytest.mark.anyio
async def test_closed_periods_block_before_mutation(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db)
    await close_period(db, "2026-10", user={"email": "admin@eternalpackaging.com"}, reason="tutup asli")
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(db, invoice_id, _payload(invoice_date="2026-10-20", key="closed-old"), ADMIN)
    assert exc.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 1
    assert (await db.sales_invoices.find_one({"id": invoice_id}))["grand_total"] == 2000

    fresh, _po = await _seed(db, invoice_date="2026-07-04")
    await close_period(db, "2026-11", user={"email": "admin@eternalpackaging.com"}, reason="tutup baru")
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(db, fresh, _payload(invoice_date="2026-11-02", key="closed-new"), ADMIN)
    assert exc.value.status_code == 409
    assert await _posted_count(db, fresh) == 1
    assert await db.journal_entries.count_documents({"source_id": fresh, "status": "reversed"}) == 0


@pytest.mark.anyio
async def test_receipt_and_credit_blockers(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db)
    await db.sales_receipts.insert_one({
        "id": "r-active", "invoice_id": invoice_id, "financial_status": "finalized",
        "settlement_effective": False, "amount": 100,
    })
    with pytest.raises(HTTPException):
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="rec-1"), ADMIN)
    await db.sales_receipts.delete_many({})

    await db.sales_receipts.insert_one({
        "id": "r-cancel", "invoice_id": invoice_id, "financial_status": "cancelled", "amount": 100,
    })
    await db.journal_entries.insert_one({
        "id": "j-rec", "number": f"JRN-{uuid.uuid4().hex[:8]}", "date": "2026-10-09",
        "source_type": RECEIPT_SOURCE, "source_id": "r-cancel", "status": "posted",
        "reversal_of": None, "posted_at": "2026-10-09T00:00:00Z",
    })
    with pytest.raises(HTTPException):
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="rec-2"), ADMIN)
    await db.sales_receipts.delete_many({})
    await db.journal_entries.delete_one({"id": "j-rec"})

    await db.sales_receipts.insert_one({
        "id": "r-plain", "invoice_id": invoice_id, "financial_status": "finalized", "amount": 50,
    })
    with pytest.raises(HTTPException):
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="rec-3"), ADMIN)
    await db.sales_receipts.delete_many({})

    await db.sales_customer_credit_applications.insert_one({
        "id": "cred-1", "invoice_id": invoice_id, "financial_status": "active", "status": "posted", "amount": 10,
    })
    with pytest.raises(HTTPException):
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="cred"), ADMIN)
    assert await _posted_count(db, invoice_id) == 1


@pytest.mark.anyio
async def test_claim_race_retry_and_crash_windows(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db)
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="crash"), ADMIN, stop_after="before_reverse")
    from services.sales_invoice_posting import acquire_receipt_create_claim

    assert await acquire_receipt_create_claim(db, invoice_id) is None
    with pytest.raises(HTTPException) as raced:
        await revise_posted_sales_invoice(db, invoice_id, _payload(qty=4, key="other"), ADMIN)
    assert raced.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 1

    done = await revise_posted_sales_invoice(db, invoice_id, _payload(key="crash"), ADMIN)
    assert done["grand_total"] == 3000
    assert await _posted_count(db, invoice_id) == 1
    again = await revise_posted_sales_invoice(db, invoice_id, _payload(key="crash"), ADMIN)
    assert again["id"] == invoice_id
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1

    with pytest.raises(HTTPException) as changed:
        await revise_posted_sales_invoice(db, invoice_id, _payload(qty=4, key="crash"), ADMIN)
    assert changed.value.status_code == 409

    second, _po = await _seed(db, invoice_date="2026-06-02", qty=1)
    for phase in ("after_reverse", "after_invoice", "after_repost"):
        key = f"phase-{phase}"
        with pytest.raises(RevisionPaused):
            await revise_posted_sales_invoice(
                db, second, _payload(qty=2, invoice_date="2026-06-03", key=key), ADMIN, stop_after=phase,
            )
        if phase != "after_repost":
            assert (await db.sales_invoices.find_one({"id": second}))["financial_status"] == "finalized"
        resumed = await revise_posted_sales_invoice(
            db, second, _payload(qty=2, invoice_date="2026-06-03", key=key), ADMIN,
        )
        assert resumed["financial_status"] == "finalized"
        assert await _posted_count(db, second) == 1

    third, _po = await _seed(db, invoice_date="2026-05-04", qty=1)
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, third, _payload(qty=2, invoice_date="2026-05-06", key="stale"), ADMIN, stop_after="before_reverse",
        )
    await db.sales_invoices.update_one(
        {"id": third},
        {"$set": {EDIT_CLAIM_AT_FIELD: "2020-01-01T00:00:00+00:00"}},
    )
    resumed = await revise_posted_sales_invoice(
        db, third, _payload(qty=2, invoice_date="2026-05-06", key="stale"), ADMIN,
    )
    assert resumed["grand_total"] == 2000
    assert await _posted_count(db, third) == 1
    assert (await db.sales_invoices.find_one({"id": third})).get(EDIT_CLAIM_FIELD) in (None, "")


@pytest.mark.anyio
async def test_period_closes_after_reversal_stays_pending(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-04-08", qty=1)
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-04-20", key="mid"), ADMIN, stop_after="after_reverse",
        )
    await close_period(db, "2026-04", user={"email": "admin@eternalpackaging.com"}, reason="tutup tengah")
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-04-20", key="mid"), ADMIN,
        )
    assert exc.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 0
    rev = await db.sales_invoice_edit_revisions.find_one({"invoice_id": invoice_id})
    assert rev["financial_complete"] is not True
    assert (await db.sales_invoices.find_one({"id": invoice_id}))["financial_status"] == "finalized"


@pytest.mark.anyio
async def test_missing_index_and_ambiguous_primary(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db)
    await db.journal_entries.drop_index("uniq_primary_source_journal")
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="idx"), ADMIN)
    assert "primary_index_not_ready" in exc.value.detail["blockers"]
    assert await _posted_count(db, invoice_id) == 1
    await ensure_journal(db)

    invoice_id, _po = await _seed(db, invoice_date="2026-03-03")
    await db.journal_entries.drop_index("uniq_primary_source_journal")
    current = await db.journal_entries.find_one({"source_id": invoice_id, "reversal_of": None})
    clone = dict(current)
    clone.pop("_id", None)
    clone["id"] = str(uuid.uuid4())
    clone["number"] = f"JRN-{uuid.uuid4().hex[:8]}"
    await db.journal_entries.insert_one(clone)
    ambiguous = await find_sales_invoice_source_journal(db, invoice_id)
    assert ambiguous.get("ambiguous") is True
    with pytest.raises(HTTPException) as exc:
        await post_sales_invoice_accounting(db, invoice_id, ADMIN)
    assert exc.value.status_code == 409
    assert "tidak tunggal" in str(exc.value.detail)


@pytest.mark.anyio
async def test_readers_ignore_reversed_history(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db)
    await revise_posted_sales_invoice(db, invoice_id, _payload(key="read"), ADMIN)
    current = await find_sales_invoice_source_journal(db, invoice_id)
    assert current["status"] == "posted"
    rows = await _load_current_invoices(db, aging_reference_date="2026-10-31")
    assert invoice_id in {row["invoice_id"] for row in rows}
    reversed_only = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "number": f"JRN-{uuid.uuid4().hex[:6]}", "date": "2026-01-02",
        "source_type": SOURCE_TYPE, "source_id": reversed_only, "status": "reversed",
        "reversal_of": None, "posted_at": "2026-01-02T00:00:00Z",
    })
    rows = await _load_current_invoices(db, aging_reference_date="2026-10-31")
    assert reversed_only not in {row["invoice_id"] for row in rows}


@pytest.mark.anyio
async def test_po_qty_and_ownership(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, po_qty=2, qty=1)
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(db, invoice_id, _payload(qty=3, key="qty"), ADMIN)
    assert exc.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 1

    owned, _po = await _seed(db, invoice_date="2026-02-02", qty=1)
    stranger = {
        "id": "other-sales",
        "role": "sales_eternal",
        "permissions_override": {"sales.invoice.edit": True},
    }
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_invoice(db, owned, _payload(qty=1, invoice_date="2026-02-03", key="own"), stranger)
    assert exc.value.status_code == 404
    assert await _posted_count(db, owned) == 1


@pytest.mark.anyio
async def test_flag_off_and_reverse_to_cancel_unchanged(indexes):
    db = server.db
    invoice_id, _po = await _seed(db)
    with pytest.raises(HTTPException) as exc:
        await update_sales_invoice(db, invoice_id, _payload(key="off"), ADMIN)
    assert exc.value.status_code == 409
    assert "difinalisasi" in str(exc.value.detail)
    assert await _posted_count(db, invoice_id) == 1

    await reverse_sales_invoice_accounting(db, invoice_id, ADMIN, "salah catat")
    stored = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "financial_status": 1})
    assert stored["financial_status"] == "cancelled"
    assert await _posted_count(db, invoice_id) == 0
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1


@pytest.mark.anyio
async def test_legacy_date_spelling_preserved_on_same_day(indexes):
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026/10/09")
    detail = await revise_posted_sales_invoice(
        db, invoice_id, _payload(qty=2, invoice_date="09/10/2026", key="spell"), ADMIN,
    )
    assert detail["invoice_date"] == "2026/10/09"
    changed = await revise_posted_sales_invoice(
        db, invoice_id, _payload(qty=2, invoice_date="10/10/2026", key="spell-2"), ADMIN,
    )
    assert changed["invoice_date"] == "2026-10-10"


def _fence(doc) -> str:
    return str((doc or {}).get(FENCE_FIELD) or "")


@pytest.mark.anyio
async def test_fence_survives_ttl_and_blocks_financial_ops(indexes):
    """A–E. Receipt, credit, and cancel stay blocked after the old 120s claim age."""
    db = server.db
    await _enable(db)
    invoice_id, po_id = await _seed(db, invoice_date="2026-09-09", qty=2)
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=3, invoice_date="2026-09-12", key="fence-a"), ADMIN,
            stop_after="before_reverse",
        )
    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {EDIT_CLAIM_AT_FIELD: "2020-01-01T00:00:00+00:00", "paid_total": 100, "outstanding_amount": 1900}},
    )
    stored = await db.sales_invoices.find_one({"id": invoice_id})
    assert _fence(stored)
    assert stored["financial_status"] == "finalized"

    from services.sales_invoice_posting import acquire_receipt_create_claim
    from services.sales_invoices import cancel_sales_invoice
    from services.sales_receipts import create_sales_receipt

    assert await acquire_receipt_create_claim(db, invoice_id) is None
    with pytest.raises(HTTPException) as created:
        await create_sales_receipt(
            db, {"invoice_id": invoice_id, "amount": 100, "receipt_date": "2026-09-12"}, ADMIN,
        )
    assert created.value.status_code == 409
    assert await db.sales_receipts.count_documents({"invoice_id": invoice_id}) == 0

    await db.sales_receipts.insert_one({
        "id": "r-post", "invoice_id": invoice_id, "receipt_number": "RCP-POST",
        "financial_status": "finalized", "amount": 100, "allocated_amount": 100,
        "customer_credit_amount": 0, "receipt_date": "2026-09-12",
    })
    from services.sales_receipt_posting import post_sales_receipt_accounting

    with pytest.raises(HTTPException) as posted:
        await post_sales_receipt_accounting(db, "r-post", ADMIN)
    assert posted.value.status_code == 409
    assert await db.journal_entries.count_documents({"source_id": "r-post"}) == 0
    await db.sales_receipts.delete_many({"invoice_id": invoice_id})

    deposit = await create_account(
        db, {"code": f"21{uuid.uuid4().hex[:4]}", "name": "Titipan", "account_type": "OCLY"}, user=ADMIN,
    )
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": {"customer_deposit_account_id": deposit["id"]}})
    await db.orders.update_one({"id": po_id}, {"$set": {"customer_id": "cust-fence"}})
    await db.sales_receipts.insert_one({
        "id": "src-credit", "invoice_id": "other-invoice", "customer_id": "cust-fence",
        "financial_status": "finalized", "amount": 100, "allocated_amount": 0,
        "customer_credit_amount": 100, "receipt_number": "RCP-CR",
    })
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "number": f"JRN-{uuid.uuid4().hex[:8]}", "date": "2026-09-01",
        "source_type": RECEIPT_SOURCE, "source_id": "src-credit", "status": "posted",
        "reversal_of": None, "posted_at": "2026-09-01T00:00:00Z",
    })
    from services.sales_customer_credit import apply_customer_credit

    with pytest.raises(HTTPException) as credit:
        await apply_customer_credit(db, {
            "source_receipt_id": "src-credit",
            "invoice_id": invoice_id,
            "amount": 10,
            "idempotency_key": "credit-fence",
            "application_date": "2026-09-12",
        }, ADMIN)
    assert credit.value.status_code == 409
    assert "Revisi faktur belum selesai" in str(credit.value.detail)
    assert await db.sales_customer_credit_applications.count_documents({"invoice_id": invoice_id}) == 0

    with pytest.raises(HTTPException) as cancelled:
        await cancel_sales_invoice(db, invoice_id, ADMIN)
    assert cancelled.value.status_code == 409
    after = await db.sales_invoices.find_one({"id": invoice_id})
    assert after["financial_status"] == "finalized"
    assert after["paid_total"] == 100
    assert _fence(after)
    with pytest.raises(HTTPException):
        await post_sales_invoice_accounting(db, invoice_id, ADMIN)


@pytest.mark.anyio
async def test_reverse_to_cancel_blocked_during_revision(indexes, monkeypatch):
    """F–G. Reverse-to-cancel must not cancel or zero balances while a fence is active."""
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-08-08", qty=2)
    await db.sales_invoices.update_one(
        {"id": invoice_id}, {"$set": {"paid_total": 100, "outstanding_amount": 1900}},
    )
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-08-18", key="rev-cancel"), ADMIN,
            stop_after="after_reverse_journal",
        )
    row = await db.sales_invoice_edit_revisions.find_one({"invoice_id": invoice_id})
    assert row["phase"] == "pending"
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "status": "reversed"}) == 1
    with pytest.raises(HTTPException) as denied:
        await reverse_sales_invoice_accounting(db, invoice_id, ADMIN, "batal")
    assert denied.value.status_code == 409
    stored = await db.sales_invoices.find_one({"id": invoice_id})
    assert stored["financial_status"] == "finalized"
    assert stored["paid_total"] == 100
    assert _fence(stored)
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1

    other, _po = await _seed(db, invoice_date="2026-08-02", qty=1)
    await db.sales_invoices.update_one(
        {"id": other}, {"$set": {"paid_total": 50, "outstanding_amount": 950}},
    )
    from services import sales_invoice_posting as posting

    async def _claim_miss(db, invoice_id, token=None):
        await db.journal_entries.update_one(
            {"source_id": invoice_id, "source_type": SOURCE_TYPE, "reversal_of": None, "status": "posted"},
            {"$set": {"status": "reversed"}},
        )
        await db.sales_invoices.update_one(
            {"id": invoice_id},
            {"$set": {FENCE_FIELD: "fence-race", FENCE_KEY_FIELD: "race-key", EDIT_CLAIM_FIELD: "owner-token"}},
        )
        return None

    monkeypatch.setattr(posting, "acquire_reverse_claim", _claim_miss)
    with pytest.raises(HTTPException) as missed:
        await reverse_sales_invoice_accounting(db, other, ADMIN, "batal")
    assert missed.value.status_code == 409
    raced = await db.sales_invoices.find_one({"id": other})
    assert raced["financial_status"] == "finalized"
    assert raced["paid_total"] == 50
    assert raced["outstanding_amount"] == 950


@pytest.mark.anyio
async def test_crash_after_reverse_and_period_recovery(indexes):
    """H–K, O–P. Journal evidence resumes the same key. A closed period does not drop the fence."""
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-01-15", qty=1)
    payload = _payload(qty=2, invoice_date="2026-02-10", key="crash-key")
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(db, invoice_id, payload, ADMIN, stop_after="after_reverse_journal")
    assert (await db.sales_invoice_edit_revisions.find_one({"invoice_id": invoice_id}))["phase"] == "pending"
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "status": "reversed"}) == 1
    assert _fence(await db.sales_invoices.find_one({"id": invoice_id}))
    rows = await _load_current_invoices(db, aging_reference_date="2026-02-28")
    assert invoice_id not in {row["invoice_id"] for row in rows}

    done = await revise_posted_sales_invoice(db, invoice_id, payload, ADMIN)
    assert done["financial_status"] == "finalized"
    assert done["grand_total"] == 2000
    assert await _posted_count(db, invoice_id) == 1
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1
    assert _fence(await db.sales_invoices.find_one({"id": invoice_id})) == ""
    again = await revise_posted_sales_invoice(db, invoice_id, payload, ADMIN)
    assert again["id"] == invoice_id
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1

    closed_original, _po = await _seed(db, invoice_date="2026-01-20", qty=1)
    original_payload = _payload(qty=2, invoice_date="2026-02-11", key="orig-period")
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, closed_original, original_payload, ADMIN, stop_after="after_reverse_journal",
        )
    await close_period(db, "2026-01", user={"email": "admin@eternalpackaging.com"}, reason="asli tertutup")
    recovered = await revise_posted_sales_invoice(db, closed_original, original_payload, ADMIN)
    assert recovered["financial_status"] == "finalized"
    assert await _posted_count(db, closed_original) == 1
    assert await db.journal_entries.count_documents({"source_id": closed_original, "reversal_of": {"$ne": None}}) == 1
    rev = await db.sales_invoice_edit_revisions.find_one({"invoice_id": closed_original})
    assert rev["phase"] == "complete"
    assert rev["financial_complete"] is True
    assert _fence(await db.sales_invoices.find_one({"id": closed_original})) == ""

    new_closed, _po = await _seed(db, invoice_date="2026-03-03", qty=1)
    new_payload = _payload(qty=2, invoice_date="2026-03-18", key="new-period")
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(db, new_closed, new_payload, ADMIN, stop_after="after_invoice")
    await close_period(db, "2026-03", user={"email": "admin@eternalpackaging.com"}, reason="baru tertutup")
    with pytest.raises(HTTPException) as pending:
        await revise_posted_sales_invoice(db, new_closed, new_payload, ADMIN)
    assert pending.value.status_code == 409
    waiting = await db.sales_invoice_edit_revisions.find_one({"invoice_id": new_closed})
    assert waiting["phase"] != "failed"
    assert waiting["financial_complete"] is not True
    assert waiting["idempotency_key"] == "new-period"
    held = await db.sales_invoices.find_one({"id": new_closed})
    assert held["financial_status"] == "finalized"
    assert _fence(held) == waiting["id"]
    assert await _posted_count(db, new_closed) == 0
    assert await db.journal_entries.count_documents({"source_id": new_closed, "reversal_of": {"$ne": None}}) == 1
    with pytest.raises(HTTPException):
        await revise_posted_sales_invoice(
            db, new_closed, _payload(qty=4, invoice_date="2026-03-18", key="other-key"), ADMIN,
        )
    assert _fence(await db.sales_invoices.find_one({"id": new_closed}))


@pytest.mark.anyio
async def test_posted_revision_idempotency_and_legacy_paths(indexes):
    """L–N, Q–R. Posted revision requires a caller key. Draft and flag-off stay on the old path."""
    db = server.db
    await _enable(db)
    invoice_id, po_id = await _seed(db, invoice_date="2026-07-07", qty=1)
    missing = dict(_payload(qty=2, invoice_date="2026-07-08", key="unused"))
    missing.pop("idempotency_key")
    with pytest.raises(HTTPException) as no_key:
        await revise_posted_sales_invoice(db, invoice_id, missing, ADMIN)
    assert no_key.value.status_code == 400
    assert "idempotency_key" in str(no_key.value.detail)
    assert await db.sales_invoice_edit_revisions.count_documents({"invoice_id": invoice_id}) == 0
    assert _fence(await db.sales_invoices.find_one({"id": invoice_id})) == ""

    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-07-08", key="same"), ADMIN,
            stop_after="before_reverse",
        )
    with pytest.raises(HTTPException) as changed:
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=4, invoice_date="2026-07-08", key="same"), ADMIN,
        )
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as other:
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-07-08", key="different"), ADMIN,
        )
    assert other.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 1
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "status": "reversed"}) == 0

    await db.app_settings.delete_many({"key": EDIT_FLAG})
    with pytest.raises(HTTPException) as legacy:
        await update_sales_invoice(db, invoice_id, _payload(qty=2, invoice_date="2026-07-09", key="off-2"), ADMIN)
    assert legacy.value.status_code == 409
    assert "difinalisasi" in str(legacy.value.detail)

    draft_id = str(uuid.uuid4())
    await db.sales_invoices.insert_one({
        "id": draft_id,
        "invoice_number": f"INV/{draft_id[:6]}",
        "po_id": po_id,
        "invoice_date": "2026-07-07",
        "financial_status": "draft",
        "payment_term_days": 0,
        "lines": [{
            "line_idx": 0, "line_id": "l0", "item_name": "Cup",
            "quantity": 1, "unit_price": 1000, "line_subtotal": 1000, "line_total": 1000,
        }],
        "subtotal": 1000, "discount_amount": 0, "after_discount": 1000,
        "ppn_percent": 0, "ppn_amount": 0, "other_charges": [], "other_charges_total": 0,
        "grand_total": 1000, "paid_total": 0, "outstanding_amount": 1000, "notes": "",
        "created_at": "2026-07-01T00:00:00+00:00",
    })
    updated = await update_sales_invoice(
        db, draft_id, {"lines": [{"line_idx": 0, "quantity": 4, "unit_price": 1000}]}, ADMIN,
    )
    assert updated["financial_status"] == "draft"
    assert updated["grand_total"] == 4000
    assert await db.sales_invoice_edit_revisions.count_documents({"invoice_id": draft_id}) == 0


@pytest.mark.anyio
async def test_same_token_phase_is_monotonic(indexes):
    """A–D. Shared token cannot regress a completed revision or rewrite the invoice."""
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-02-02", qty=1)
    payload = _payload(qty=2, invoice_date="2026-02-12", key="same-token")
    first, second = await asyncio.gather(
        revise_posted_sales_invoice(db, invoice_id, payload, ADMIN),
        revise_posted_sales_invoice(db, invoice_id, payload, ADMIN),
    )
    assert first["id"] == invoice_id and second["id"] == invoice_id
    assert first["grand_total"] == 2000 and second["grand_total"] == 2000
    assert await _posted_count(db, invoice_id) == 1
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "reversal_of": {"$ne": None}}) == 1
    row = await db.sales_invoice_edit_revisions.find_one({"invoice_id": invoice_id})
    assert row["phase"] == "complete"
    assert row["financial_complete"] is True
    assert _fence(await db.sales_invoices.find_one({"id": invoice_id})) == ""

    from services.sales_invoice_edit_revision import PHASE_REVERSED, RevisionCompleted, _advance_phase

    with pytest.raises(RevisionCompleted):
        await _advance_phase(db, row["id"], PHASE_REVERSED, {"original_journal_id": "stale"})
    again = await db.sales_invoice_edit_revisions.find_one({"id": row["id"]})
    assert again["phase"] == "complete"
    assert again["financial_complete"] is True

    replay = await revise_posted_sales_invoice(db, invoice_id, payload, ADMIN)
    assert replay["grand_total"] == 2000
    assert await _posted_count(db, invoice_id) == 1
    stored = await db.sales_invoices.find_one({"id": invoice_id})
    stale = await db.sales_invoices.update_one(
        {"id": invoice_id, "sales_invoice_edit_revision_claim": row["claim_token"]},
        {"$set": {"grand_total": 1}},
    )
    assert stale.matched_count == 0
    assert (await db.sales_invoices.find_one({"id": invoice_id}))["grand_total"] == stored["grand_total"]


@pytest.mark.anyio
async def test_credit_posting_recovery_respects_revision_fence(indexes):
    """E–G. A posting credit application cannot post or recalc across the fence."""
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-05-05", qty=2)
    await db.sales_customer_credit_applications.insert_one({
        "id": "cred-posting",
        "invoice_id": invoice_id,
        "financial_status": "active",
        "status": "posting",
        "amount": 10,
        "idempotency_key": "credit-posting-1",
    })
    with pytest.raises(HTTPException) as blocked:
        await revise_posted_sales_invoice(db, invoice_id, _payload(key="blocked-by-posting"), ADMIN)
    assert blocked.value.status_code == 409
    assert await _posted_count(db, invoice_id) == 1
    assert await db.journal_entries.count_documents({"source_id": invoice_id, "status": "reversed"}) == 0
    await db.sales_customer_credit_applications.delete_many({})

    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=3, invoice_date="2026-05-06", key="fence-credit"), ADMIN,
            stop_after="before_reverse",
        )
    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {"paid_total": 80, "outstanding_amount": 1920}},
    )
    await db.sales_customer_credit_applications.insert_one({
        "id": "cred-recover",
        "invoice_id": invoice_id,
        "financial_status": "active",
        "status": "posting",
        "amount": 10,
        "idempotency_key": "credit-recover-1",
        "source_receipt_id": "src-recover",
    })
    from services.sales_customer_credit import _complete_posting_application, apply_customer_credit

    with pytest.raises(HTTPException) as recovered:
        await apply_customer_credit(db, {
            "source_receipt_id": "src-recover",
            "invoice_id": invoice_id,
            "amount": 10,
            "idempotency_key": "credit-recover-1",
            "application_date": "2026-05-06",
        }, ADMIN)
    assert recovered.value.status_code == 409
    with pytest.raises(HTTPException) as direct:
        await _complete_posting_application(
            db,
            await db.sales_customer_credit_applications.find_one({"id": "cred-recover"}),
            ADMIN,
            "2026-05-06",
        )
    assert direct.value.status_code == 409
    assert "Revisi faktur belum selesai" in str(direct.value.detail)
    assert await db.journal_entries.count_documents({"source_id": "cred-recover"}) == 0
    held = await db.sales_invoices.find_one({"id": invoice_id})
    assert held["paid_total"] == 80
    assert held["outstanding_amount"] == 1920
    assert held["financial_status"] == "finalized"


@pytest.mark.anyio
async def test_stale_reverse_finalizer_loses_to_revision_fence(indexes):
    """H–K. A stale reverse claim cannot cancel or zero balances while the fence is active."""
    db = server.db
    await _enable(db)
    invoice_id, _po = await _seed(db, invoice_date="2026-06-06", qty=2)
    from services.sales_invoice_posting import (
        REVERSE_CLAIM_AT_FIELD,
        REVERSE_CLAIM_FIELD,
        _finalize_invoice_reversal_state,
    )

    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "paid_total": 100,
            "outstanding_amount": 1900,
            REVERSE_CLAIM_FIELD: "old-reverse",
            REVERSE_CLAIM_AT_FIELD: "2020-01-01T00:00:00+00:00",
            FENCE_FIELD: "fence-already",
            FENCE_KEY_FIELD: "other-key",
        }},
    )
    invoice = await db.sales_invoices.find_one({"id": invoice_id})
    with pytest.raises(HTTPException) as lost:
        await _finalize_invoice_reversal_state(db, invoice, ADMIN, "batal", None)
    assert lost.value.status_code == 409
    kept = await db.sales_invoices.find_one({"id": invoice_id})
    assert kept["financial_status"] == "finalized"
    assert kept["paid_total"] == 100
    assert kept["outstanding_amount"] == 1900

    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$unset": {FENCE_FIELD: "", FENCE_KEY_FIELD: "", REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""}},
    )
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_invoice(
            db, invoice_id, _payload(qty=2, invoice_date="2026-06-16", key="before-stale-final"), ADMIN,
            stop_after="before_reverse",
        )
    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "paid_total": 100,
            "outstanding_amount": 1900,
            REVERSE_CLAIM_FIELD: "old-reverse",
            REVERSE_CLAIM_AT_FIELD: "2020-01-01T00:00:00+00:00",
        }},
    )
    invoice = await db.sales_invoices.find_one({"id": invoice_id})
    with pytest.raises(HTTPException) as raced:
        await _finalize_invoice_reversal_state(db, invoice, ADMIN, "batal", None)
    assert raced.value.status_code == 409
    after = await db.sales_invoices.find_one({"id": invoice_id})
    assert after["financial_status"] == "finalized"
    assert after["paid_total"] == 100
    assert after["outstanding_amount"] == 1900
    assert _fence(after)

    await db.app_settings.delete_many({"key": EDIT_FLAG})
    plain, _po = await _seed(db, invoice_date="2026-06-01", qty=1)
    with pytest.raises(HTTPException) as legacy:
        await update_sales_invoice(db, plain, _payload(key="still-off"), ADMIN)
    assert legacy.value.status_code == 409
    assert "difinalisasi" in str(legacy.value.detail)
