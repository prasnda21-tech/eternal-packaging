"""Package C — Sales Receipt edit revision. Synthetic local data only."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_receipt_edit_revision_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, OPT_IN_ONLY_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa, validate_payload
from services.financial_journal_foundation import ALLOWLIST_KEY, FLAG_KEY as JOURNAL_FLAG
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_posting import post_sales_invoice_accounting
from services.sales_customer_credit import SOURCE_IN_USE_MESSAGE
from services.sales_receipt_edit_revision import (
    COLLECTION,
    FLAG_KEY as EDIT_FLAG,
    PERM_EDIT,
    PHASE_COMPLETE,
    PHASE_NEW_POSTING_CREATED,
    PHASE_NEW_SETTLEMENT_VERIFIED,
    PHASE_OLD_POSTING_REVERSED,
    PHASE_OLD_SETTLEMENT_RELEASED,
    PHASE_PREPARED,
    PHASE_RECEIPT_UPDATED,
    REQUIRED_INTENT_INDEX_NAME,
    RevisionPaused,
    revise_posted_sales_receipt,
    update_sales_receipt,
)
from services.sales_receipt_posting import (
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    find_sales_receipt_source_journal,
    post_sales_receipt_accounting,
    select_sales_receipt_primary,
)
from services.sales_receipts import create_sales_receipt

# create_account writes ledger_accounts. cash_bank_master writes cash_bank_accounts.
# Wiping the old names left every seeded code in place for the rest of the process.
_ISOLATED_COLLECTIONS = (
    "sales_invoices",
    "sales_receipts",
    "journal_entries",
    "journal_lines",
    "orders",
    "sales_customer_credit_applications",
    COLLECTION,
    "accounting_periods",
    "cash_bank_accounts",
    "ledger_accounts",
)
_ACCOUNT_SEQ = 0


def _account_code(prefix: str) -> str:
    """Process-local account code. Non-empty trimmed string, accepted by validate_payload."""
    global _ACCOUNT_SEQ
    _ACCOUNT_SEQ += 1
    return f"{prefix}{_ACCOUNT_SEQ:06d}"


async def _wipe_isolated(db):
    for name in _ISOLATED_COLLECTIONS:
        await db[name].delete_many({})


ADMIN = {"id": "admin-rcpt-edit", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
EDITOR = {
    "id": "editor-rcpt",
    "email": "editor@example.com",
    "name": "Editor",
    "role": "sales",
    "permissions_override": {PERM_EDIT: True},
}


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
    await ensure_cb(db)
    # Tests may create the intent index; production code never runs DDL.
    await db[COLLECTION].create_index(
        [("receipt_id", 1), ("idempotency_key", 1)],
        unique=True,
        name=REQUIRED_INTENT_INDEX_NAME,
    )
    await db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, JOURNAL_FLAG, ALLOWLIST_KEY, EDIT_FLAG]}})
    await _wipe_isolated(db)
    yield
    await _wipe_isolated(db)


async def _enable_posted(db):
    await db.app_settings.update_one({"key": EDIT_FLAG}, {"$set": {"key": EDIT_FLAG, "enabled": True}}, upsert=True)
    await db.app_settings.update_one({"key": JOURNAL_FLAG}, {"$set": {"key": JOURNAL_FLAG, "enabled": True}}, upsert=True)
    await db.app_settings.update_one(
        {"key": ALLOWLIST_KEY},
        {"$set": {"key": ALLOWLIST_KEY, "source_types": ["sales_receipt"]}},
        upsert=True,
    )


async def _seed_invoice(db, *, grand=10_000, invoice_date="2026-09-01"):
    po_id = str(uuid.uuid4())
    invoice_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO/{invoice_id[:6]}",
        "customer": "PT Edit Receipt",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 10, "unit": "pcs"}],
        "current_stage": 5,
    })
    await db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"INV/{invoice_id[:6]}",
        "po_id": po_id,
        "po_number": f"PO/{invoice_id[:6]}",
        "customer": "PT Edit Receipt",
        "invoice_date": invoice_date,
        "financial_status": "finalized",
        "lines": [{
            "line_idx": 0, "line_id": "l0", "item_name": "Cup",
            "quantity": 1, "unit_price": grand, "line_subtotal": grand, "line_total": grand,
        }],
        "subtotal": grand,
        "discount_amount": 0,
        "after_discount": grand,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "other_charges": [],
        "other_charges_total": 0,
        "grand_total": grand,
        "paid_total": 0,
        "outstanding_amount": grand,
        "notes": "",
        "created_at": "2026-09-01T00:00:00+00:00",
    })
    rec = await create_account(db, {"code": _account_code("11"), "name": "Piutang", "account_type": "AREC"}, user=ADMIN)
    rev = await create_account(db, {"code": _account_code("41"), "name": "Pendapatan", "account_type": "REVE"}, user=ADMIN)
    tax = await create_account(db, {"code": _account_code("21"), "name": "PPN", "account_type": "OCLY"}, user=ADMIN)
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


async def _cash_bank(db, name="BCA Edit"):
    coa = await create_account(
        db, {"code": _account_code("12"), "name": name, "account_type": "BANK"}, user=ADMIN
    )
    return await create_cash_bank(db, {"name": name, "account_id": coa["id"], "active": True}, user=ADMIN)


async def _make_receipt(db, invoice_id, amount, *, cash_bank_id, receipt_date="2026-09-05", allocated=None):
    payload = {
        "invoice_id": invoice_id,
        "amount": amount,
        "receipt_date": receipt_date,
        "cash_bank_id": cash_bank_id,
        "notes": "DP",
        "idempotency_key": f"create-{uuid.uuid4().hex}",
    }
    if allocated is not None:
        payload["allocated_amount"] = allocated
    return await create_sales_receipt(db, payload, ADMIN)


@pytest.mark.anyio
async def test_opt_in_permission_not_default_including_admin(indexes):
    assert PERM_EDIT in ALL_PERMISSIONS
    assert PERM_EDIT in OPT_IN_ONLY_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        assert PERM_EDIT not in perms, role


@pytest.mark.anyio
async def test_unposted_edit_preserves_identity(indexes):
    db = server.db
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    number = receipt["receipt_number"]
    result = await update_sales_receipt(db, receipt["id"], {
        "amount": 3_000,
        "allocated_amount": 3_000,
        "receipt_date": "2026-09-06",
        "cash_bank_id": bank["id"],
        "notes": "koreksi",
    }, EDITOR)
    assert result["financial_complete"] is True
    assert result["mode"] == "unposted"
    updated = result["receipt"]
    assert updated["id"] == receipt["id"]
    assert updated["receipt_number"] == number
    assert updated["invoice_id"] == invoice_id
    assert updated["amount"] == 3_000
    assert updated["notes"] == "koreksi"
    inv = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv["paid_total"]) == 3_000


@pytest.mark.anyio
async def test_flag_off_posted_fails_closed(indexes):
    db = server.db
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    with pytest.raises(HTTPException) as exc:
        await update_sales_receipt(db, receipt["id"], {
            "amount": 5_000,
            "allocated_amount": 5_000,
            "receipt_date": "2026-09-07",
            "cash_bank_id": bank["id"],
            "idempotency_key": "k1",
        }, EDITOR)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert "edit_revision_flag_off" in detail.get("blockers", [])


@pytest.mark.anyio
async def test_admin_without_opt_in_denied(indexes):
    db = server.db
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 2_000, cash_bank_id=bank["id"])
    with pytest.raises(HTTPException) as exc:
        await update_sales_receipt(db, receipt["id"], {"notes": "x", "amount": 2_000, "allocated_amount": 2_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-05"}, ADMIN)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_posted_revision_happy_path_amount_bank_date(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank1 = await _cash_bank(db, "BCA Satu")
    bank2 = await _cash_bank(db, "Mandiri Dua")
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank1["id"], receipt_date="2026-09-05")
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank1["id"])
    number = receipt["receipt_number"]
    result = await revise_posted_sales_receipt(db, receipt["id"], {
        "idempotency_key": "edit-posted-1",
        "amount": 6_000,
        "allocated_amount": 6_000,
        "receipt_date": "2026-09-15",
        "cash_bank_id": bank2["id"],
        "notes": "naik",
    }, EDITOR)
    assert result["financial_complete"] is True
    updated = result["receipt"]
    assert updated["id"] == receipt["id"]
    assert updated["receipt_number"] == number
    assert updated["invoice_id"] == invoice_id
    assert updated.get("cancelled") is not True
    assert str(updated.get("financial_status") or "") != "cancelled"
    assert updated["amount"] == 6_000
    assert updated["cash_bank_id"] == bank2["id"]
    assert updated["accounting_status"] == "posted"
    active = await find_sales_receipt_source_journal(db, receipt["id"])
    assert active["status"] == "posted"
    assert active["date"] == "2026-09-15"
    raw = await db.journal_entries.find_one({"id": active["id"]}, {"_id": 0})
    assert raw.get("journal_revision") == 2
    assert raw.get("supersedes_journal_id")
    original = await db.journal_entries.find_one({"id": raw["supersedes_journal_id"]}, {"_id": 0})
    assert original["status"] == "reversed"
    posted = await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": receipt["id"], "reversal_of": None, "status": "posted",
    })
    assert posted == 1
    inv = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv["paid_total"]) == 6_000


@pytest.mark.anyio
async def test_idempotent_retry_and_fingerprint_conflict(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    payload = {
        "idempotency_key": "same-key",
        "amount": 5_000,
        "allocated_amount": 5_000,
        "receipt_date": "2026-09-10",
        "cash_bank_id": bank["id"],
    }
    first = await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR)
    second = await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR)
    assert first["receipt"]["amount"] == second["receipt"]["amount"] == 5_000
    assert await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": receipt["id"], "reversal_of": None, "status": "posted",
    }) == 1
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_receipt(db, receipt["id"], {**payload, "amount": 5_500, "allocated_amount": 5_500}, EDITOR)
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_sibling_receipt_and_over_allocation(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db, grand=10_000)
    bank = await _cash_bank(db)
    r1 = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    r2 = await _make_receipt(db, invoice_id, 3_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, r1["id"], ADMIN, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, r2["id"], ADMIN, cash_bank_id=bank["id"])
    # Increase r1 within remaining room (10k - 3k sibling = 7k)
    ok = await revise_posted_sales_receipt(db, r1["id"], {
        "idempotency_key": "sib-ok",
        "amount": 7_000,
        "allocated_amount": 7_000,
        "receipt_date": "2026-09-08",
        "cash_bank_id": bank["id"],
    }, EDITOR)
    assert ok["financial_complete"] is True
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_receipt(db, r1["id"], {
            "idempotency_key": "sib-over",
            "amount": 8_000,
            "allocated_amount": 8_000,
            "receipt_date": "2026-09-08",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert exc.value.status_code == 400
    sibling = await db.sales_receipts.find_one({"id": r2["id"]})
    assert float(sibling["amount"]) == 3_000


@pytest.mark.anyio
async def test_closed_period_and_cancelled_rejection(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"], receipt_date="2026-01-10")
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    await close_period(db, "2026-01", user=ADMIN, reason="tutup")
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "closed",
            "amount": 4_500,
            "allocated_amount": 4_500,
            "receipt_date": "2026-02-01",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert exc.value.status_code == 409

    # Cancelled receipt
    await db.sales_receipts.update_one({"id": receipt["id"]}, {"$set": {"cancelled": True, "financial_status": "cancelled"}})
    with pytest.raises(HTTPException) as exc2:
        await update_sales_receipt(db, receipt["id"], {
            "amount": 1, "allocated_amount": 1, "cash_bank_id": bank["id"], "receipt_date": "2026-02-01",
        }, EDITOR)
    assert exc2.value.status_code == 409


@pytest.mark.anyio
async def test_missing_intent_index_fail_closed(indexes):
    db = server.db
    await _enable_posted(db)
    await db[COLLECTION].drop_indexes()
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 2_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "no-idx",
            "amount": 2_500,
            "allocated_amount": 2_500,
            "receipt_date": "2026-09-09",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert "intent_index_not_ready" in exc.value.detail.get("blockers", [])


@pytest.mark.anyio
async def test_crash_resume_after_reverse_and_select_primary(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    payload = {
        "idempotency_key": "crash-1",
        "amount": 4_500,
        "allocated_amount": 4_500,
        "receipt_date": "2026-09-12",
        "cash_bank_id": bank["id"],
    }
    with pytest.raises(RevisionPaused):
        await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR, stop_after="after_reverse")
    row = await db[COLLECTION].find_one({"receipt_id": receipt["id"], "idempotency_key": "crash-1"})
    assert row["phase"] == PHASE_OLD_POSTING_REVERSED
    stored = await db.sales_receipts.find_one({"id": receipt["id"]})
    assert stored.get("cancelled") is not True
    done = await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR)
    assert done["financial_complete"] is True
    docs = await db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": receipt["id"], "reversal_of": None},
        {"_id": 0},
    ).to_list(10)
    chosen = select_sales_receipt_primary(docs)
    assert chosen and chosen.get("status") == "posted"
    assert not chosen.get("ambiguous")


@pytest.mark.anyio
async def test_invoice_reassignment_rejected(indexes):
    db = server.db
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 1_000, cash_bank_id=bank["id"])
    with pytest.raises(HTTPException) as exc:
        await update_sales_receipt(db, receipt["id"], {
            "invoice_id": "other-invoice",
            "amount": 1_000,
            "allocated_amount": 1_000,
            "cash_bank_id": bank["id"],
            "receipt_date": "2026-09-05",
        }, EDITOR)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# FINAL SAFETY GATES — focused regressions
# ---------------------------------------------------------------------------


async def _journal_snapshot(db, receipt_id: str) -> dict:
    docs = await db.journal_entries.find(
        {"source_id": receipt_id, "source_type": {"$in": [SOURCE_TYPE, SOURCE_REVERSAL]}},
        {"_id": 0, "id": 1, "status": 1, "reversal_of": 1, "journal_revision": 1, "source_type": 1},
    ).to_list(50)
    return {
        "ids": sorted(str(d["id"]) for d in docs),
        "posted_primary": await db.journal_entries.count_documents({
            "source_type": SOURCE_TYPE, "source_id": receipt_id,
            "reversal_of": None, "status": "posted",
        }),
        "reversed_primary": await db.journal_entries.count_documents({
            "source_type": SOURCE_TYPE, "source_id": receipt_id,
            "reversal_of": None, "status": "reversed",
        }),
        "reversals": await db.journal_entries.count_documents({
            "source_type": SOURCE_REVERSAL, "source_id": receipt_id,
            "reversal_of": {"$ne": None},
        }),
    }


@pytest.mark.anyio
async def test_gate1_active_credit_blocks_before_mutation(indexes):
    """Active credit application blocks revision; no journal/settlement mutation."""
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db, grand=10_000)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    # Synthetic active credit application (authoritative blocker for reverse/edit).
    await db.sales_customer_credit_applications.insert_one({
        "id": f"cred-{uuid.uuid4().hex[:8]}",
        "source_receipt_id": receipt["id"],
        "invoice_id": "other-invoice-credit",
        "amount": 500,
        "status": "posted",
        "financial_status": "active",
        "created_at": "2026-09-06T00:00:00+00:00",
    })
    before_j = await _journal_snapshot(db, receipt["id"])
    before_rec = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    before_inv = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    with pytest.raises(HTTPException) as exc:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "credit-block",
            "amount": 3_500,
            "allocated_amount": 3_500,
            "receipt_date": "2026-09-10",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert exc.value.status_code == 409
    assert SOURCE_IN_USE_MESSAGE in str(exc.value.detail)
    after_j = await _journal_snapshot(db, receipt["id"])
    after_rec = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    after_inv = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert after_j == before_j
    assert float(after_rec["amount"]) == float(before_rec["amount"])
    assert float(after_rec["allocated_amount"]) == float(before_rec["allocated_amount"])
    assert after_rec.get("cancelled") == before_rec.get("cancelled")
    assert float(after_inv["paid_total"]) == float(before_inv["paid_total"])
    assert await db[COLLECTION].count_documents({"receipt_id": receipt["id"]}) == 0

    # Historical reversed/cancelled credit must not block (authoritative helper rule).
    await db.sales_customer_credit_applications.update_one(
        {"source_receipt_id": receipt["id"]},
        {"$set": {"status": "reversed", "financial_status": "cancelled"}},
    )
    ok = await revise_posted_sales_receipt(db, receipt["id"], {
        "idempotency_key": "credit-cleared",
        "amount": 3_500,
        "allocated_amount": 3_500,
        "receipt_date": "2026-09-11",
        "cash_bank_id": bank["id"],
    }, EDITOR)
    assert ok["financial_complete"] is True
    assert ok["receipt"]["amount"] == 3_500


@pytest.mark.anyio
async def test_gate2_sibling_ar_increase_decrease_outstanding(indexes):
    """Sibling receipts + AR outstanding remain correct across increase/decrease."""
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db, grand=10_000)
    bank = await _cash_bank(db)
    r1 = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    r2 = await _make_receipt(db, invoice_id, 3_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, r1["id"], ADMIN, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, r2["id"], ADMIN, cash_bank_id=bank["id"])
    inv0 = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv0["paid_total"]) == 7_000
    assert float(inv0["outstanding_amount"]) == 3_000

    up = await revise_posted_sales_receipt(db, r1["id"], {
        "idempotency_key": "sib-up",
        "amount": 5_000,
        "allocated_amount": 5_000,
        "receipt_date": "2026-09-08",
        "cash_bank_id": bank["id"],
    }, EDITOR)
    assert up["financial_complete"] is True
    inv1 = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv1["paid_total"]) == 8_000
    assert float(inv1["outstanding_amount"]) == 2_000
    sibling = await db.sales_receipts.find_one({"id": r2["id"]})
    assert float(sibling["amount"]) == 3_000
    assert sibling.get("cancelled") is not True
    assert await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": r2["id"], "reversal_of": None, "status": "posted",
    }) == 1
    assert await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": r1["id"], "reversal_of": None, "status": "posted",
    }) == 1

    down = await revise_posted_sales_receipt(db, r1["id"], {
        "idempotency_key": "sib-down",
        "amount": 2_000,
        "allocated_amount": 2_000,
        "receipt_date": "2026-09-09",
        "cash_bank_id": bank["id"],
    }, EDITOR)
    assert down["financial_complete"] is True
    inv2 = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv2["paid_total"]) == 5_000
    assert float(inv2["outstanding_amount"]) == 5_000
    sibling2 = await db.sales_receipts.find_one({"id": r2["id"]})
    assert float(sibling2["amount"]) == 3_000
    assert float(sibling2["allocated_amount"]) == 3_000

    with pytest.raises(HTTPException) as over:
        await revise_posted_sales_receipt(db, r1["id"], {
            "idempotency_key": "sib-over2",
            "amount": 8_000,
            "allocated_amount": 8_000,
            "receipt_date": "2026-09-09",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert over.value.status_code == 400
    inv3 = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv3["paid_total"]) == 5_000


CRASH_MATRIX = [
    ("before_reverse", PHASE_PREPARED),
    ("after_reverse_journal", PHASE_PREPARED),  # journal mutated; phase CAS not yet
    ("after_reverse", PHASE_OLD_POSTING_REVERSED),
    ("after_settlement_release", PHASE_OLD_SETTLEMENT_RELEASED),
    ("after_receipt_update", PHASE_RECEIPT_UPDATED),
    ("after_repost", PHASE_NEW_POSTING_CREATED),
    ("after_settlement", PHASE_NEW_SETTLEMENT_VERIFIED),
]


@pytest.mark.anyio
@pytest.mark.parametrize("stop_after,expected_phase", CRASH_MATRIX)
async def test_gate3_crash_retry_each_boundary(indexes, stop_after, expected_phase):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 4_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    j0 = await find_sales_receipt_source_journal(db, receipt["id"])
    original_journal_id = str(j0["id"])
    key = f"crash-{stop_after}"
    payload = {
        "idempotency_key": key,
        "amount": 4_500,
        "allocated_amount": 4_500,
        "receipt_date": "2026-09-14",
        "cash_bank_id": bank["id"],
        "notes": f"resume-{stop_after}",
    }
    with pytest.raises(RevisionPaused) as paused:
        await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR, stop_after=stop_after)
    assert paused.value.phase == stop_after
    row = await db[COLLECTION].find_one({"receipt_id": receipt["id"], "idempotency_key": key})
    assert row is not None
    assert row["phase"] == expected_phase
    assert row.get("financial_complete") is not True
    # Premature success must not be claimable from incomplete intent.
    assert not (row.get("phase") == PHASE_COMPLETE and row.get("financial_complete") is True)
    mid = await db.sales_receipts.find_one({"id": receipt["id"]})
    assert mid.get("cancelled") is not True
    assert str(mid.get("financial_status") or "") != "cancelled"

    # Conflicting edit with different key while incomplete must be blocked.
    with pytest.raises(HTTPException) as conflict:
        await revise_posted_sales_receipt(db, receipt["id"], {
            **payload,
            "idempotency_key": f"{key}-other",
            "amount": 4_600,
            "allocated_amount": 4_600,
        }, EDITOR)
    assert conflict.value.status_code == 409

    done = await revise_posted_sales_receipt(db, receipt["id"], payload, EDITOR)
    assert done["financial_complete"] is True
    assert done["receipt"]["amount"] == 4_500
    assert done["receipt"].get("cancelled") is not True
    final_row = await db[COLLECTION].find_one({"receipt_id": receipt["id"], "idempotency_key": key})
    assert final_row["phase"] == PHASE_COMPLETE
    assert final_row.get("financial_complete") is True

    snap = await _journal_snapshot(db, receipt["id"])
    assert snap["posted_primary"] == 1
    assert snap["reversed_primary"] == 1
    assert snap["reversals"] == 1  # old reversal exactly once
    original = await db.journal_entries.find_one({"id": original_journal_id}, {"_id": 0})
    assert original["status"] == "reversed"
    active = await find_sales_receipt_source_journal(db, receipt["id"])
    assert active["status"] == "posted"
    assert active["id"] != original_journal_id
    inv = await db.sales_invoices.find_one({"id": invoice_id})
    assert float(inv["paid_total"]) == 4_500


@pytest.mark.anyio
async def test_gate4_journal_chain_and_cancel_state_unchanged(indexes):
    db = server.db
    await _enable_posted(db)
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 3_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])
    first = await find_sales_receipt_source_journal(db, receipt["id"])
    await revise_posted_sales_receipt(db, receipt["id"], {
        "idempotency_key": "chain-1",
        "amount": 3_500,
        "allocated_amount": 3_500,
        "receipt_date": "2026-09-16",
        "cash_bank_id": bank["id"],
    }, EDITOR)
    docs = await db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": receipt["id"], "reversal_of": None},
        {"_id": 0},
    ).to_list(20)
    chosen = select_sales_receipt_primary(docs)
    assert chosen and chosen["status"] == "posted"
    assert not chosen.get("ambiguous")
    assert chosen["id"] != first["id"]
    superseded = await db.journal_entries.find_one({"id": first["id"]}, {"_id": 0})
    assert superseded["status"] == "reversed"
    current = await db.journal_entries.find_one({"id": chosen["id"]}, {"_id": 0})
    assert current.get("supersedes_journal_id") == first["id"]
    live = await db.sales_receipts.find_one({"id": receipt["id"]})
    assert live.get("cancelled") is not True
    assert str(live.get("financial_status") or "") != "cancelled"
    assert await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE, "source_id": receipt["id"],
        "reversal_of": None, "status": "posted",
    }) == 1


@pytest.mark.anyio
async def test_gate5_allowlist_empty_invalid_bank_flag_index(indexes):
    db = server.db
    invoice_id, _ = await _seed_invoice(db)
    bank = await _cash_bank(db)
    receipt = await _make_receipt(db, invoice_id, 2_000, cash_bank_id=bank["id"])
    await post_sales_receipt_accounting(db, receipt["id"], ADMIN, cash_bank_id=bank["id"])

    # Flag ON but empty allowlist → fail closed before mutation.
    await db.app_settings.update_one({"key": EDIT_FLAG}, {"$set": {"key": EDIT_FLAG, "enabled": True}}, upsert=True)
    await db.app_settings.update_one({"key": JOURNAL_FLAG}, {"$set": {"key": JOURNAL_FLAG, "enabled": True}}, upsert=True)
    await db.app_settings.update_one(
        {"key": ALLOWLIST_KEY},
        {"$set": {"key": ALLOWLIST_KEY, "source_types": []}},
        upsert=True,
    )
    before = await _journal_snapshot(db, receipt["id"])
    with pytest.raises(HTTPException) as empty_al:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "empty-al",
            "amount": 2_100,
            "allocated_amount": 2_100,
            "receipt_date": "2026-09-10",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert "sales_receipt_not_allowlisted" in empty_al.value.detail.get("blockers", [])
    assert await _journal_snapshot(db, receipt["id"]) == before

    await _enable_posted(db)
    with pytest.raises(HTTPException) as bad_bank:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "bad-bank",
            "amount": 2_100,
            "allocated_amount": 2_100,
            "receipt_date": "2026-09-10",
            "cash_bank_id": "missing-bank-id",
        }, EDITOR)
    assert bad_bank.value.status_code == 400
    assert await _journal_snapshot(db, receipt["id"]) == before

    # Missing intent index still fails closed (reaffirm).
    await db[COLLECTION].drop_indexes()
    with pytest.raises(HTTPException) as no_idx:
        await revise_posted_sales_receipt(db, receipt["id"], {
            "idempotency_key": "no-idx-2",
            "amount": 2_100,
            "allocated_amount": 2_100,
            "receipt_date": "2026-09-10",
            "cash_bank_id": bank["id"],
        }, EDITOR)
    assert "intent_index_not_ready" in no_idx.value.detail.get("blockers", [])
    assert await _journal_snapshot(db, receipt["id"]) == before


def test_generated_account_codes_are_unique_and_valid():
    seen = set()
    samples = (
        ("11", "AREC", "Piutang"),
        ("41", "REVE", "Pendapatan"),
        ("21", "OCLY", "PPN"),
        ("12", "BANK", "Bank"),
    )
    for _ in range(40):
        for prefix, account_type, name in samples:
            code = _account_code(prefix)
            assert code not in seen
            seen.add(code)
            assert code == code.strip() and code
            got_code, _, got_type = validate_payload(
                {"code": code, "name": name, "account_type": account_type}
            )
            assert got_code == code
            assert got_type == account_type


@pytest.mark.anyio
async def test_repeated_seed_does_not_reuse_account_code(indexes):
    db = server.db
    await _seed_invoice(db)
    await _seed_invoice(db)
    codes = [
        doc["code"]
        for doc in await db.ledger_accounts.find({}, {"code": 1, "_id": 0}).to_list(20)
    ]
    assert len(codes) == 6
    assert len(set(codes)) == 6


@pytest.mark.anyio
async def test_prior_ledger_accounts_do_not_leak(indexes):
    assert "ledger_accounts" in _ISOLATED_COLLECTIONS
    assert "cash_bank_accounts" in _ISOLATED_COLLECTIONS
    assert await server.db.ledger_accounts.count_documents({}) == 0
    assert await server.db.cash_bank_accounts.count_documents({}) == 0
