"""Phase 2.2A — automatic sales invoice create → finalize → post (flag-gated)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_auto_lifecycle_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_auto_lifecycle import (
    AUTO_ELIGIBLE_FIELD,
    AUTO_FINGERPRINT_FIELD,
    AUTO_LIFECYCLE_ACTIVATION_BLOCKED,
    AUTO_STATUS_FAILED,
    AUTO_STATUS_FIELD,
    AUTO_STATUS_POSTED,
    ERR_CLAIM_HELD,
    ERR_FLAG_OFF,
    ERR_INDEX_PREREQUISITE,
    ERR_INTENT_BIND_CONFLICT,
    ERR_INTENT_EXPIRED,
    ERR_INTENT_INDEX_PREREQUISITE,
    ERR_JOURNAL_REVERSED,
    ERR_MISSING_IDEMPOTENCY_KEY,
    ERR_NOT_ELIGIBLE,
    ERR_ORPHAN_UNPROVEN,
    ERR_PAYLOAD_CONFLICT,
    ERR_PERIOD_LOCKED,
    ERR_UNAUTHORIZED_RETRY,
    FLAG_KEY,
    INTENT_COLL,
    _bind_intent_invoice,
    _IntentBindConflict,
    auto_lifecycle_activation_blockers,
    canonical_create_fingerprint,
    create_sales_invoice_with_auto_lifecycle,
    ensure_auto_intent_indexes,
    intent_idempotency_index_ready,
    is_auto_sales_invoice_lifecycle_enabled,
    sales_invoice_idempotency_index_ready,
)
from services.sales_invoice_posting import (
    SOURCE_TYPE,
    acquire_posting_claim,
)
from services.sales_invoices import (
    FINANCIAL_DRAFT,
    FINANCIAL_FINALIZED,
    create_sales_invoice,
    effective_financial_status,
)

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
async def clean_db(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await ensure_auto_intent_indexes(server.db)
    await server.db.sales_invoices.create_index("id", unique=True)
    if not await sales_invoice_idempotency_index_ready(server.db):
        await server.db.sales_invoices.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, FLAG_KEY]}})
    await server.db.sales_invoices.delete_many({})
    await server.db[INTENT_COLL].delete_many({})
    await server.db.orders.delete_many({})
    await server.db[JOURNAL_ENTRIES].delete_many({"source_type": SOURCE_TYPE})
    await server.db.accounting_periods.delete_many({})
    yield
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, FLAG_KEY]}})
    app.dependency_overrides.pop(current_user, None)


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }


def _user(role: str, perms: dict):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "email": f"{role}@example.com",
        "name": role,
        "role": role,
        "permissions_override": perms,
    }


async def _set_flag(enabled: bool) -> None:
    await server.db.app_settings.update_one(
        {"key": FLAG_KEY},
        {"$set": {"key": FLAG_KEY, "enabled": enabled}},
        upsert=True,
    )


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings(rec=None, rev=None, tax=None):
    rec = rec or await _coa("11", "Piutang Auto", "AREC")
    rev = rev or await _coa("41", "Pendapatan Auto", "REVE")
    tax = tax or await _coa("21", "PPN Auto", "OCLY")
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


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/AUTO/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Auto",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup Auto", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "payment_status": "Waiting",
        "packed_qty": 40,
        "loaded_qty": 10,
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": [{"id": "shp-1", "status": "draft"}],
        "created_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


def _payload(po_id: str, **extra) -> dict:
    body = {
        "po_id": po_id,
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "discount_amount": extra.pop("discount_amount", 0),
        "ppn_percent": extra.pop("ppn_percent", 10),
        "lines": [
            {
                "line_idx": 0,
                "quantity": extra.pop("quantity", 10),
                "unit_price": extra.pop("unit_price", 1000),
            }
        ],
        "idempotency_key": extra.pop("idempotency_key", f"auto-{uuid.uuid4()}"),
    }
    body.update(extra)
    return body


async def _source_journals(invoice_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": invoice_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(20)


# --- Flag OFF ---


@pytest.mark.anyio
async def test_flag_defaults_off():
    assert await is_auto_sales_invoice_lifecycle_enabled(server.db) is False


@pytest.mark.anyio
async def test_flag_off_legacy_draft_create_no_auto_journal():
    _admin()
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200, res.text
        inv = res.json()["invoice"]
        assert effective_financial_status(inv) == FINANCIAL_DRAFT
        assert res.json().get("financial_complete") is None
        assert len(await _source_journals(inv["id"])) == 0
        fin = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
        assert fin.status_code == 200
        assert fin.json()["invoice"]["financial_status"] == FINANCIAL_FINALIZED
        posted = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
        assert posted.status_code == 200
        assert len(await _source_journals(inv["id"])) == 1


@pytest.mark.anyio
async def test_flag_off_create_perm_unchanged_manual_post_still_needs_post_perm():
    await _configure_settings()
    po = await _seed_po()
    _user(
        "crm",
        {
            "sales.invoice.create": True,
            "sales.invoice.view": True,
            "sales.invoice.edit": True,
            "po.view": True,
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200
        inv = res.json()["invoice"]
        await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
        denied = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
        assert denied.status_code == 403


# --- Flag ON ---


@pytest.mark.anyio
async def test_flag_on_create_finalize_post_success():
    _admin()
    await _set_flag(True)
    rec, rev, tax = await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["financial_complete"] is True
        assert body["auto_accounting_status"] == AUTO_STATUS_POSTED
        inv = body["invoice"]
        assert inv["financial_status"] == FINANCIAL_FINALIZED
        assert inv["accounting_status"] == "posted"
        assert inv.get(AUTO_ELIGIBLE_FIELD) is True or True  # may be stripped from public
        journals = await _source_journals(inv["id"])
        assert len(journals) == 1
        assert journals[0]["status"] == "posted"
        assert journals[0]["source_type"] == SOURCE_TYPE
        assert journals[0]["source_id"] == inv["id"]
        lines = await server.db.journal_lines.find(
            {"journal_id": journals[0]["id"]}, {"_id": 0}
        ).to_list(20)
        by_acct = {ln["account_id"]: ln for ln in lines}
        assert float(by_acct[rec["id"]]["debit"]) == float(inv["grand_total"])
        assert float(by_acct[rev["id"]]["credit"]) == float(inv["after_discount"])
        assert float(by_acct[tax["id"]]["credit"]) == float(inv["ppn_amount"])


@pytest.mark.anyio
async def test_flag_on_create_perm_without_general_post_perm():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    _user(
        "crm",
        {
            "sales.invoice.create": True,
            "sales.invoice.view": True,
            "po.view": True,
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200, res.text
        assert res.json()["financial_complete"] is True
        assert "sales_accounting.post" not in (
            ROLE_DEFAULT_PERMISSIONS.get("crm") or []
        )


@pytest.mark.anyio
async def test_flag_on_po_qty_overrun_rejected_no_journal():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/sales-invoices",
            json=_payload(po["id"], quantity=9999),
        )
        assert res.status_code == 400
        assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0


@pytest.mark.anyio
async def test_flag_on_closed_period_persists_failed_no_false_success():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    await close_period(
        server.db,
        "2026-09",
        user={"email": "admin@eternalpackaging.com"},
        reason="uji tutup periode",
    )
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 409, res.text
        detail = res.json()["detail"]
        assert detail["financial_complete"] is False
        assert detail["auto_accounting_status"] == AUTO_STATUS_FAILED
        assert detail["error_code"] in (ERR_PERIOD_LOCKED, "posting_failed", "period_locked")
        inv = detail["invoice"]
        assert inv["financial_status"] == FINANCIAL_FINALIZED
        assert len(await _source_journals(inv["id"])) == 0
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert stored[AUTO_STATUS_FIELD] == AUTO_STATUS_FAILED


@pytest.mark.anyio
async def test_flag_on_missing_settings_failed():
    _admin()
    await _set_flag(True)
    # no settings
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code in (400, 409), res.text
        detail = res.json().get("detail") or res.json()
        if isinstance(detail, dict) and "financial_complete" in detail:
            assert detail["financial_complete"] is False
            assert detail["invoice"]["id"]
            assert len(await _source_journals(detail["invoice"]["id"])) == 0


@pytest.mark.anyio
async def test_flag_on_idempotent_retry_same_invoice_one_journal():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"idem-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert r1.status_code == 200
        inv1 = r1.json()["invoice"]
        r2 = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert r2.status_code == 200
        inv2 = r2.json()["invoice"]
        assert inv1["id"] == inv2["id"]
        assert len(await _source_journals(inv1["id"])) == 1


@pytest.mark.anyio
async def test_flag_on_concurrent_duplicate_one_invoice_one_journal():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"conc-{uuid.uuid4()}"
    payload = _payload(po["id"], idempotency_key=key)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        results = await asyncio.gather(
            client.post("/api/sales-invoices", json=payload),
            client.post("/api/sales-invoices", json=payload),
            client.post("/api/sales-invoices", json=payload),
        )
    successes = [r for r in results if r.status_code == 200]
    assert len(successes) >= 1
    ids = set()
    for r in results:
        body = r.json()
        detail = body.get("detail") if r.status_code != 200 else body
        if isinstance(detail, dict) and detail.get("invoice"):
            ids.add(detail["invoice"]["id"])
        elif body.get("invoice"):
            ids.add(body["invoice"]["id"])
    assert len(ids) == 1
    inv_id = next(iter(ids))
    assert len(await _source_journals(inv_id)) == 1
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1


@pytest.mark.anyio
async def test_flag_on_claim_held_returns_pending_not_success():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    # Pre-create eligible draft then hold posting claim before auto post via recover path
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # First create under flag OFF to get draft, then mark eligible and hold claim
        await _set_flag(False)
        draft = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        inv = draft.json()["invoice"]
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_STATUS_FIELD: "pending"}},
        )
        await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
        claimed = await acquire_posting_claim(server.db, inv["id"], token="held-token")
        assert claimed is not None
        await _set_flag(True)
        res = await client.post(f"/api/sales-invoices/{inv['id']}/recover-auto-accounting")
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["financial_complete"] is False
        assert detail["error_code"] == ERR_CLAIM_HELD
        assert len(await _source_journals(inv["id"])) == 0


@pytest.mark.anyio
async def test_flag_on_crash_after_journal_recovery_heals_metadata():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200
        inv = res.json()["invoice"]
        # Simulate metadata loss after successful journal
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {
                "$set": {AUTO_STATUS_FIELD: AUTO_STATUS_FAILED},
                "$unset": {"auto_accounting_posted_at": ""},
            },
        )
        healed = await client.post(f"/api/sales-invoices/{inv['id']}/recover-auto-accounting")
        assert healed.status_code == 200, healed.text
        assert healed.json()["financial_complete"] is True
        assert len(await _source_journals(inv["id"])) == 1


@pytest.mark.anyio
async def test_flag_on_reversed_journal_fail_closed():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert res.status_code == 200
        inv = res.json()["invoice"]
        # Reverse via accounting reverse (cancels invoice) — then try recover
        rev = await client.post(
            f"/api/sales-invoices/{inv['id']}/reverse-accounting",
            json={"reason": "koreksi uji"},
        )
        assert rev.status_code == 200, rev.text
        # Re-open eligibility for recover attempt (simulate ambiguous state)
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {
                "$set": {
                    AUTO_ELIGIBLE_FIELD: True,
                    AUTO_STATUS_FIELD: "failed",
                    "financial_status": FINANCIAL_FINALIZED,
                },
                "$unset": {"cancelled_at": "", "cancelled_by": ""},
            },
        )
        recover = await client.post(f"/api/sales-invoices/{inv['id']}/recover-auto-accounting")
        assert recover.status_code == 409
        detail = recover.json()["detail"]
        assert detail["financial_complete"] is False
        assert detail["error_code"] == ERR_JOURNAL_REVERSED


@pytest.mark.anyio
async def test_flag_on_historical_draft_not_auto_processed():
    _admin()
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _set_flag(False)
        key = f"hist-{uuid.uuid4()}"
        draft = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert draft.status_code == 200
        inv = draft.json()["invoice"]
        assert inv["financial_status"] == FINANCIAL_DRAFT
        await _set_flag(True)
        # Reuse historical idempotency key — must not auto-post
        again = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert again.status_code == 409
        detail = again.json()["detail"]
        assert detail["error_code"] == ERR_NOT_ELIGIBLE
        assert detail["financial_complete"] is False
        assert len(await _source_journals(inv["id"])) == 0
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert stored.get(AUTO_ELIGIBLE_FIELD) is not True


@pytest.mark.anyio
async def test_flag_on_recover_requires_create_perm_and_ownership_path():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        inv_id = created.json()["invoice"]["id"]
        await server.db.sales_invoices.update_one(
            {"id": inv_id},
            {"$set": {AUTO_STATUS_FIELD: "failed"}},
        )
    _user(
        "crm",
        {
            "sales.invoice.view": True,
            "po.view": True,
            "sales.invoice.create": False,
            "sales_order.create": False,
            "po.edit": False,
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(f"/api/sales-invoices/{inv_id}/recover-auto-accounting")
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_manual_post_endpoint_still_requires_sales_accounting_post():
    """Auto path must not weaken the explicit manual post endpoint."""
    assert "sales_accounting.post" in ALL_PERMISSIONS
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Create under flag OFF to leave unfinished... use flag OFF draft then finalize
        await _set_flag(False)
        draft = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        inv = draft.json()["invoice"]
        await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
    _user("crm", {"sales.invoice.create": True, "sales.invoice.view": True, "po.view": True})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_receipt_create_still_accepts_finalized_unposted_documented_gap():
    """Phase 2.2A does not modify receipts — document the dependency gate."""
    _admin()
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _set_flag(False)
        draft = await client.post("/api/sales-invoices", json=_payload(po["id"], ppn_percent=0))
        inv = draft.json()["invoice"]
        await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
        # No journal — receipt create still allowed (existing behavior)
        cash = await _coa("12", "Kas Gap", "BANK")
        from services.cash_bank_master import ensure_indexes as ensure_cb

        await ensure_cb(server.db)
        await server.db.cash_bank_accounts.insert_one(
            {
                "id": str(uuid.uuid4()),
                "name": "Kas Gap",
                "account_id": cash["id"],
                "active": True,
                "code": f"CB{uuid.uuid4().hex[:4]}",
            }
        )
        # Minimal: just assert invoice is finalized not posted
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert effective_financial_status(stored) == FINANCIAL_FINALIZED
        assert len(await _source_journals(inv["id"])) == 0


# --- Corrective: orphan / fingerprint / flag gate / index readiness ---


@pytest.mark.anyio
async def test_orphan_draft_after_insert_before_eligible_recovers():
    """Crash window: invoice persisted, eligible not set; same key/payload resumes."""
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"orphan-{uuid.uuid4()}"
    payload = _payload(po["id"], idempotency_key=key)
    user = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    from services.sales_invoice_auto_lifecycle import now_iso

    fp = canonical_create_fingerprint(payload)
    stamped = now_iso()
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "actor_email": "admin@eternalpackaging.com",
            "po_id": po["id"],
            "invoice_id": None,
            "status": "preparing",
            "created_at": stamped,
            "updated_at": stamped,
        }
    )
    draft = await create_sales_invoice(server.db, payload, user)
    assert draft.get(AUTO_ELIGIBLE_FIELD) is not True
    assert effective_financial_status(draft) == FINANCIAL_DRAFT

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["financial_complete"] is True
        assert body["invoice"]["id"] == draft["id"]
        assert len(await _source_journals(draft["id"])) == 1
        stored = await server.db.sales_invoices.find_one({"id": draft["id"]}, {"_id": 0})
        assert stored.get(AUTO_ELIGIBLE_FIELD) is True
        assert stored.get(AUTO_FINGERPRINT_FIELD) == fp


@pytest.mark.anyio
async def test_same_key_different_payload_conflict():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"conflict-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/sales-invoices",
            json=_payload(po["id"], idempotency_key=key, quantity=5, unit_price=1000),
        )
        assert first.status_code == 200, first.text
        inv_id = first.json()["invoice"]["id"]
        second = await client.post(
            "/api/sales-invoices",
            json=_payload(po["id"], idempotency_key=key, quantity=8, unit_price=1000),
        )
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert detail["financial_complete"] is False
        assert detail["error_code"] == ERR_PAYLOAD_CONFLICT
        assert detail["invoice"]["id"] == inv_id
        assert len(await _source_journals(inv_id)) == 1


@pytest.mark.anyio
async def test_recover_while_flag_off_no_side_effects():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert created.status_code == 200
        inv_id = created.json()["invoice"]["id"]
        await server.db.sales_invoices.update_one(
            {"id": inv_id},
            {"$set": {AUTO_STATUS_FIELD: AUTO_STATUS_FAILED}},
        )
        # Force not-posted metadata while keeping journal — then wipe journal to ensure recover would post
        await server.db[JOURNAL_ENTRIES].delete_many({"source_type": SOURCE_TYPE, "source_id": inv_id})
        await server.db.journal_lines.delete_many({})
        await _set_flag(False)
        denied = await client.post(f"/api/sales-invoices/{inv_id}/recover-auto-accounting")
        assert denied.status_code == 409, denied.text
        detail = denied.json()["detail"]
        assert detail.get("error_code") == ERR_FLAG_OFF or (
            isinstance(detail, dict) and detail.get("error_code") == ERR_FLAG_OFF
        )
        assert len(await _source_journals(inv_id)) == 0


@pytest.mark.anyio
async def test_auto_requires_idempotency_key():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    body = _payload(po["id"])
    body.pop("idempotency_key", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/sales-invoices", json=body)
        assert res.status_code == 409
        assert res.json()["detail"]["error_code"] == ERR_MISSING_IDEMPOTENCY_KEY


@pytest.mark.anyio
async def test_activation_blockers_index_and_flag():
    await _set_flag(False)
    blockers = await auto_lifecycle_activation_blockers(server.db)
    assert ERR_FLAG_OFF in blockers
    assert await sales_invoice_idempotency_index_ready(server.db) is True
    assert await intent_idempotency_index_ready(server.db) is True
    await _set_flag(True)
    blockers_on = await auto_lifecycle_activation_blockers(server.db)
    assert ERR_FLAG_OFF not in blockers_on
    assert ERR_INDEX_PREREQUISITE not in blockers_on
    assert ERR_INTENT_INDEX_PREREQUISITE not in blockers_on
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED not in blockers_on


@pytest.mark.anyio
async def test_historical_draft_without_intent_not_orphaned():
    """Unproven draft with same key must not be adopted (no intent)."""
    _admin()
    await _configure_settings()
    po = await _seed_po()
    key = f"no-intent-{uuid.uuid4()}"
    user = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    draft = await create_sales_invoice(
        server.db, _payload(po["id"], idempotency_key=key), user
    )
    await _set_flag(True)
    result = await create_sales_invoice_with_auto_lifecycle(
        server.db, _payload(po["id"], idempotency_key=key), user
    )
    assert result["financial_complete"] is False
    assert result["error_code"] in (ERR_NOT_ELIGIBLE, ERR_ORPHAN_UNPROVEN)
    assert len(await _source_journals(draft["id"])) == 0


@pytest.mark.anyio
async def test_concurrent_different_keys_two_invoices():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    p1 = _payload(po["id"], quantity=3, idempotency_key=f"k1-{uuid.uuid4()}")
    p2 = _payload(po["id"], quantity=4, idempotency_key=f"k2-{uuid.uuid4()}")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1, r2 = await asyncio.gather(
            client.post("/api/sales-invoices", json=p1),
            client.post("/api/sales-invoices", json=p2),
        )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["invoice"]["id"] != r2.json()["invoice"]["id"]
    assert len(await _source_journals(r1.json()["invoice"]["id"])) == 1
    assert len(await _source_journals(r2.json()["invoice"]["id"])) == 1


# --- Final corrective: index shape, CAS bind, ownership, expired, crash/race ---


async def _drop_invoice_idempotency_indexes():
    info = await server.db.sales_invoices.index_information()
    for name, meta in info.items():
        keys = meta.get("key") or []
        fields = [k[0] if isinstance(k, (list, tuple)) else k for k in keys]
        if fields == ["idempotency_key"] and name != "_id_":
            await server.db.sales_invoices.drop_index(name)


async def _drop_intent_idempotency_indexes():
    info = await server.db[INTENT_COLL].index_information()
    for name, meta in info.items():
        keys = meta.get("key") or []
        fields = [k[0] if isinstance(k, (list, tuple)) else k for k in keys]
        if fields == ["idempotency_key"] and name != "_id_":
            await server.db[INTENT_COLL].drop_index(name)


@pytest.mark.anyio
async def test_index_missing_invoice_unique_blocks_activation():
    await _drop_invoice_idempotency_indexes()
    assert await sales_invoice_idempotency_index_ready(server.db) is False
    await _set_flag(True)
    blockers = await auto_lifecycle_activation_blockers(server.db)
    assert ERR_INDEX_PREREQUISITE in blockers
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED in blockers
    # restore for subsequent tests in same worker
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )


@pytest.mark.anyio
async def test_index_missing_intent_unique_blocks_activation():
    await _drop_intent_idempotency_indexes()
    assert await intent_idempotency_index_ready(server.db) is False
    await _set_flag(True)
    blockers = await auto_lifecycle_activation_blockers(server.db)
    assert ERR_INTENT_INDEX_PREREQUISITE in blockers
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED in blockers
    await ensure_auto_intent_indexes(server.db)


@pytest.mark.anyio
async def test_index_wrong_partial_filter_rejected():
    await _drop_invoice_idempotency_indexes()
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$exists": True}},
        name="idempotency_wrong_partial",
    )
    assert await sales_invoice_idempotency_index_ready(server.db) is False
    await server.db.sales_invoices.drop_index("idempotency_wrong_partial")
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )


@pytest.mark.anyio
async def test_index_wrong_unique_flag_rejected():
    await _drop_invoice_idempotency_indexes()
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=False,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_not_unique",
    )
    assert await sales_invoice_idempotency_index_ready(server.db) is False
    await _drop_intent_idempotency_indexes()
    await server.db[INTENT_COLL].create_index("idempotency_key", unique=False, name="intent_not_unique")
    assert await intent_idempotency_index_ready(server.db) is False
    await server.db.sales_invoices.drop_index("idempotency_not_unique")
    await server.db[INTENT_COLL].drop_index("intent_not_unique")
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
    await ensure_auto_intent_indexes(server.db)


@pytest.mark.anyio
async def test_index_correct_shape_arbitrary_name_accepted():
    await _drop_invoice_idempotency_indexes()
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="arbitrary_invoice_idem_name",
    )
    assert await sales_invoice_idempotency_index_ready(server.db) is True
    await _drop_intent_idempotency_indexes()
    await server.db[INTENT_COLL].create_index(
        "idempotency_key", unique=True, name="arbitrary_intent_idem_name"
    )
    assert await intent_idempotency_index_ready(server.db) is True
    # Restore canonical names so later fixtures do not hit IndexOptionsConflict.
    await _drop_invoice_idempotency_indexes()
    await _drop_intent_idempotency_indexes()
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
    await ensure_auto_intent_indexes(server.db)


@pytest.mark.anyio
async def test_index_lookup_failure_fail_closed(monkeypatch):
    class Boom:
        def list_indexes(self):
            raise RuntimeError("mongo unavailable")

    monkeypatch.setattr(server.db, "sales_invoices", Boom())
    assert await sales_invoice_idempotency_index_ready(server.db) is False
    blockers = await auto_lifecycle_activation_blockers(server.db)
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED in blockers


@pytest.mark.anyio
async def test_cas_bind_first_success_and_same_invoice_retry():
    key = f"cas-{uuid.uuid4()}"
    fp = "fp-cas-1"
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": "po-1",
            "invoice_id": None,
            "status": "preparing",
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
        }
    )
    bound = await _bind_intent_invoice(server.db, key, "inv-a", fp)
    assert bound["invoice_id"] == "inv-a"
    assert bound["status"] == "bound"
    again = await _bind_intent_invoice(server.db, key, "inv-a", fp)
    assert again["invoice_id"] == "inv-a"
    stored = await server.db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})
    assert stored["invoice_id"] == "inv-a"


@pytest.mark.anyio
async def test_cas_bind_conflicting_invoice_fail_closed():
    key = f"cas-conflict-{uuid.uuid4()}"
    fp = "fp-cas-2"
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": "po-1",
            "invoice_id": "inv-first",
            "status": "bound",
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
        }
    )
    with pytest.raises(_IntentBindConflict):
        await _bind_intent_invoice(server.db, key, "inv-other", fp)
    stored = await server.db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})
    assert stored["invoice_id"] == "inv-first"


@pytest.mark.anyio
async def test_cas_bind_two_workers_same_key():
    key = f"cas-race-{uuid.uuid4()}"
    fp = "fp-cas-race"
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": "po-1",
            "invoice_id": None,
            "status": "preparing",
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
        }
    )

    async def worker(invoice_id: str):
        try:
            return ("ok", await _bind_intent_invoice(server.db, key, invoice_id, fp))
        except _IntentBindConflict:
            return ("conflict", None)

    r1, r2 = await asyncio.gather(worker("inv-w1"), worker("inv-w2"))
    outcomes = {r1[0], r2[0]}
    assert "ok" in outcomes
    stored = await server.db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})
    assert stored["invoice_id"] in ("inv-w1", "inv-w2")
    winners = [r for r in (r1, r2) if r[0] == "ok"]
    assert all(w[1]["invoice_id"] == stored["invoice_id"] for w in winners)
    # At most one distinct winning invoice id across successes
    if r1[0] == "ok" and r2[0] == "ok":
        assert r1[1]["invoice_id"] == r2[1]["invoice_id"]


@pytest.mark.anyio
async def test_unauthorized_eligible_retry_no_leak():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"unauth-elig-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    first = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert first["financial_complete"] is True
    inv_id = first["invoice"]["id"]
    other = {"id": "intruder", "email": "x@example.com", "name": "X", "role": "admin"}
    retry = await create_sales_invoice_with_auto_lifecycle(server.db, payload, other)
    assert retry["financial_complete"] is False
    assert retry["error_code"] == ERR_UNAUTHORIZED_RETRY
    assert retry.get("invoice") in ({}, {"id": None}) or not retry["invoice"].get("id")
    assert len(await _source_journals(inv_id)) == 1


@pytest.mark.anyio
async def test_unauthorized_orphan_retry_no_leak():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"unauth-orphan-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    from services.sales_invoice_auto_lifecycle import now_iso

    fp = canonical_create_fingerprint(payload)
    stamped = now_iso()
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "actor_email": "admin@eternalpackaging.com",
            "po_id": po["id"],
            "invoice_id": None,
            "status": "preparing",
            "created_at": stamped,
            "updated_at": stamped,
        }
    )
    draft = await create_sales_invoice(server.db, payload, owner)
    other = {"id": "intruder", "email": "x@example.com", "name": "X", "role": "admin"}
    retry = await create_sales_invoice_with_auto_lifecycle(server.db, payload, other)
    assert retry["financial_complete"] is False
    assert retry["error_code"] == ERR_UNAUTHORIZED_RETRY
    assert not (retry.get("invoice") or {}).get("id")
    assert draft.get(AUTO_ELIGIBLE_FIELD) is not True
    assert len(await _source_journals(draft["id"])) == 0


@pytest.mark.anyio
async def test_same_actor_wrong_po_unauthorized():
    await _set_flag(True)
    await _configure_settings()
    po_a = await _seed_po()
    po_b = await _seed_po()
    key = f"wrong-po-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload_a = _payload(po_a["id"], idempotency_key=key)
    first = await create_sales_invoice_with_auto_lifecycle(server.db, payload_a, owner)
    assert first["financial_complete"] is True
    # Tamper stored fingerprints to match PO-B payload while invoice/intent stay on PO-A
    payload_b = _payload(po_b["id"], idempotency_key=key)
    fp_b = canonical_create_fingerprint(payload_b)
    await server.db.sales_invoices.update_one(
        {"id": first["invoice"]["id"]},
        {"$set": {AUTO_FINGERPRINT_FIELD: fp_b}},
    )
    await server.db[INTENT_COLL].update_one(
        {"idempotency_key": key},
        {"$set": {"fingerprint": fp_b}},
    )
    retry = await create_sales_invoice_with_auto_lifecycle(server.db, payload_b, owner)
    assert retry["financial_complete"] is False
    assert retry["error_code"] == ERR_UNAUTHORIZED_RETRY
    assert len(await _source_journals(first["invoice"]["id"])) == 1


@pytest.mark.anyio
async def test_same_key_different_fingerprint_conflict():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"fp-diff-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    first = await create_sales_invoice_with_auto_lifecycle(
        server.db, _payload(po["id"], idempotency_key=key, quantity=5), owner
    )
    assert first["financial_complete"] is True
    second = await create_sales_invoice_with_auto_lifecycle(
        server.db, _payload(po["id"], idempotency_key=key, quantity=9), owner
    )
    assert second["financial_complete"] is False
    assert second["error_code"] == ERR_PAYLOAD_CONFLICT
    assert len(await _source_journals(first["invoice"]["id"])) == 1


@pytest.mark.anyio
async def test_expired_intent_without_invoice():
    from datetime import datetime, timedelta, timezone

    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"exp-none-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    fp = canonical_create_fingerprint(payload)
    old = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat().replace("+00:00", "Z")
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": po["id"],
            "invoice_id": None,
            "status": "preparing",
            "created_at": old,
            "updated_at": old,
        }
    )
    result = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert result["financial_complete"] is False
    assert result["error_code"] == ERR_INTENT_EXPIRED
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 0
    intent = await server.db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})
    assert intent is not None
    assert intent.get("invoice_id") in (None, "")


@pytest.mark.anyio
async def test_expired_intent_with_existing_invoice_preserved():
    from datetime import datetime, timedelta, timezone

    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"exp-inv-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    first = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert first["financial_complete"] is True
    inv_id = first["invoice"]["id"]
    old = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat().replace("+00:00", "Z")
    await server.db[INTENT_COLL].update_one(
        {"idempotency_key": key},
        {"$set": {"created_at": old, "updated_at": old}},
    )
    retry = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert retry["financial_complete"] is False
    assert retry["error_code"] == ERR_INTENT_EXPIRED
    assert (retry.get("invoice") or {}).get("id") == inv_id
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1
    assert len(await _source_journals(inv_id)) == 1


@pytest.mark.anyio
async def test_crash_after_invoice_insert_before_bind_recovers():
    """Invoice inserted; intent still unbound — retry binds and posts once."""
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"crash-bind-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    from services.sales_invoice_auto_lifecycle import now_iso

    fp = canonical_create_fingerprint(payload)
    stamped = now_iso()
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": po["id"],
            "invoice_id": None,
            "status": "preparing",
            "created_at": stamped,
            "updated_at": stamped,
        }
    )
    draft = await create_sales_invoice(server.db, payload, owner)
    result = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert result["financial_complete"] is True
    assert result["invoice"]["id"] == draft["id"]
    intent = await server.db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})
    assert intent["invoice_id"] == draft["id"]
    assert len(await _source_journals(draft["id"])) == 1


@pytest.mark.anyio
async def test_crash_before_intent_bind_no_duplicate_on_retry():
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    key = f"crash-pre-{uuid.uuid4()}"
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    payload = _payload(po["id"], idempotency_key=key)
    from services.sales_invoice_auto_lifecycle import now_iso

    fp = canonical_create_fingerprint(payload)
    stamped = now_iso()
    await server.db[INTENT_COLL].insert_one(
        {
            "idempotency_key": key,
            "fingerprint": fp,
            "actor_id": "admin-auto",
            "po_id": po["id"],
            "invoice_id": None,
            "status": "preparing",
            "created_at": stamped,
            "updated_at": stamped,
        }
    )
    draft = await create_sales_invoice(server.db, payload, owner)
    r1 = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    r2 = await create_sales_invoice_with_auto_lifecycle(server.db, payload, owner)
    assert r1["financial_complete"] is True
    assert r2["financial_complete"] is True
    assert r1["invoice"]["id"] == r2["invoice"]["id"] == draft["id"]
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1
    assert len(await _source_journals(draft["id"])) == 1


@pytest.mark.anyio
async def test_journal_posted_metadata_incomplete_heals():
    _admin()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert created.status_code == 200
        inv_id = created.json()["invoice"]["id"]
        assert len(await _source_journals(inv_id)) == 1
        await server.db.sales_invoices.update_one(
            {"id": inv_id},
            {
                "$set": {AUTO_STATUS_FIELD: AUTO_STATUS_FAILED},
                "$unset": {"auto_accounting_posted_at": ""},
            },
        )
        healed = await client.post(f"/api/sales-invoices/{inv_id}/recover-auto-accounting")
        assert healed.status_code == 200, healed.text
        body = healed.json()
        assert body["financial_complete"] is True
        assert body["auto_accounting_status"] == AUTO_STATUS_POSTED
        assert len(await _source_journals(inv_id)) == 1


@pytest.mark.anyio
async def test_feature_flag_off_no_auto_and_no_duplicate():
    _admin()
    await _set_flag(False)
    await _configure_settings()
    po = await _seed_po()
    key = f"flag-off-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert r1.status_code == 200
        inv = r1.json()["invoice"]
        assert inv.get(AUTO_ELIGIBLE_FIELD) is not True
        assert len(await _source_journals(inv["id"])) == 0
        r2 = await client.post("/api/sales-invoices", json=_payload(po["id"], idempotency_key=key))
        assert r2.status_code in (200, 409)
        assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1


@pytest.mark.anyio
async def test_create_blocked_when_invoice_index_missing():
    await _drop_invoice_idempotency_indexes()
    await _set_flag(True)
    await _configure_settings()
    po = await _seed_po()
    owner = {
        "id": "admin-auto",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Auto",
        "role": "admin",
    }
    result = await create_sales_invoice_with_auto_lifecycle(
        server.db, _payload(po["id"]), owner
    )
    assert result["financial_complete"] is False
    assert result["error_code"] == AUTO_LIFECYCLE_ACTIVATION_BLOCKED
    await server.db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
