"""Phase 2 accounting controls: period lock, writer leases, COA type lock, audit."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_accounting_periods_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.accounting_audit import (
    COLLECTION as AUDIT_COLLECTION,
    EVENT_COA_RENAMED,
    EVENT_PAYROLL_SETTINGS_CHANGED,
    EVENT_PERIOD_CLOSED,
    EVENT_PERIOD_REOPENED,
    EVENT_SALES_SETTINGS_CHANGED,
)
from fastapi import HTTPException

from services.accounting_periods import (
    COLLECTION as PERIODS,
    WRITER_CLAIM_TTL_SECONDS,
    PeriodConflictError,
    PeriodLockedError,
    acquire_period_writer_claim,
    close_period,
    ensure_indexes as ensure_periods,
    get_period,
    public_period,
    release_period_writer_claim,
    reopen_period,
)
from services.accounting_audit import ensure_indexes as ensure_audit
from services.chart_of_accounts import (
    COLLECTION as COA_COLLECTION,
    TYPE_LOCKED_MESSAGE,
    AccountInUseError,
    create_account,
    delete_account,
    ensure_indexes as ensure_coa,
    update_account,
)
from services.general_journal import COLLECTION as GJ_COLLECTION, ensure_indexes as ensure_gj
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    ensure_indexes as ensure_journal,
    post_balanced_journal,
    reverse_journal,
)
from services.opening_balance import (
    COLLECTION as OB_COLLECTION,
    OpeningBalanceConflictError,
    ensure_indexes as ensure_ob,
    post_voucher,
)
from services.payroll_accounting_settings import update_payroll_accounting_settings
from services.sales_accounting_settings import update_sales_accounting_settings
from services.sales_invoice_posting import reverse_sales_invoice_accounting
from services.sales_receipt_posting import reverse_sales_receipt_accounting
from services.payroll_accounting_settings import SETTINGS_KEY as PAYROLL_SETTINGS_KEY
from services.purchase_accounting_settings import SETTINGS_KEY as PURCHASE_SETTINGS_KEY
from services.sales_accounting_settings import SETTINGS_KEY as SALES_SETTINGS_KEY

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
    db = server.db
    await ensure_coa(db)
    await ensure_journal(db)
    await ensure_gj(db)
    await ensure_ob(db)
    await ensure_periods(db)
    await ensure_audit(db)
    for coll in (
        PERIODS,
        AUDIT_COLLECTION,
        GJ_COLLECTION,
        OB_COLLECTION,
        COA_COLLECTION,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        "app_settings",
        "cash_bank_accounts",
        "cash_bank_receipts",
        "cash_bank_payments",
        "cash_bank_transfers",
        "sales_invoices",
        "sales_receipts",
    ):
        await db[coll].delete_many({})
    yield
    for coll in (
        PERIODS,
        AUDIT_COLLECTION,
        GJ_COLLECTION,
        OB_COLLECTION,
        COA_COLLECTION,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        "app_settings",
        "cash_bank_accounts",
        "cash_bank_receipts",
        "cash_bank_payments",
        "cash_bank_transfers",
        "sales_invoices",
        "sales_receipts",
    ):
        await db[coll].delete_many({})


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        if role == "admin":
            override = {}
        elif perms is not None:
            override = {p: True for p in perms}
        else:
            override = {}
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role if role != "viewer" else "operator",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name, account_type="EXPS"):
    return await create_account(
        server.db, {"code": code, "name": name, "account_type": account_type}, user=_user()
    )


def _journal_lines(a, b, amount=10000):
    return [
        {"account_id": a, "debit": amount, "credit": 0, "description": "Dr"},
        {"account_id": b, "debit": 0, "credit": amount, "description": "Cr"},
    ]


@pytest.mark.anyio
async def test_missing_period_row_is_open():
    period = await get_period(server.db, "2026-01")
    assert period["status"] == "open"
    assert await server.db[PERIODS].count_documents({}) == 0


@pytest.mark.anyio
async def test_close_reopen_and_audit_append_only():
    admin = _user()
    async with await client_for(admin) as c:
        closed = await c.post("/api/ledger/accounting-periods/2026-04/close", json={"reason": "month end"})
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        listed = await c.get("/api/ledger/accounting-periods", params={"start": "2026-04", "end": "2026-04"})
        assert listed.json()["items"][0]["status"] == "closed"

        again = await c.post("/api/ledger/accounting-periods/2026-04/close", json={"reason": "retry close"})
        assert again.status_code == 200
        assert again.json()["status"] == "closed"

        reopened = await c.post("/api/ledger/accounting-periods/2026-04/reopen", json={"reason": "fix"})
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "open"

        audit = await c.get("/api/ledger/accounting-audit")
        assert audit.status_code == 200
        types = [row["event_type"] for row in audit.json()["items"]]
        assert "period_closed" in types
        assert "period_reopened" in types
        assert (await c.delete("/api/ledger/accounting-audit")).status_code == 405
        assert (await c.patch("/api/ledger/accounting-audit", json={})).status_code == 405


@pytest.mark.anyio
async def test_fresh_writer_claim_blocks_close_expired_does_not():
    claim = await acquire_period_writer_claim(server.db, "2026-05-10")
    with pytest.raises(PeriodConflictError, match="diposting"):
        await close_period(server.db, "2026-05", user=_user(), reason="blocked")
    await release_period_writer_claim(server.db, claim["period_key"], claim["token"])
    closed = await close_period(server.db, "2026-05", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"

    await reopen_period(server.db, "2026-05", user=_user(), reason="aksi periode")
    stale = await acquire_period_writer_claim(server.db, "2026-05-11")
    await server.db[PERIODS].update_one(
        {"period_key": "2026-05", "writer_claims.token": stale["token"]},
        {"$set": {"writer_claims.$.expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()}},
    )
    closed2 = await close_period(server.db, "2026-05", user=_user(), reason="aksi periode")
    assert closed2["status"] == "closed"


@pytest.mark.anyio
async def test_release_is_token_scoped():
    first = await acquire_period_writer_claim(server.db, "2026-06-01")
    second = await acquire_period_writer_claim(server.db, "2026-06-02")
    await release_period_writer_claim(server.db, first["period_key"], first["token"])
    with pytest.raises(PeriodConflictError):
        await close_period(server.db, "2026-06", user=_user(), reason="aksi periode")
    await release_period_writer_claim(server.db, second["period_key"], second["token"])
    closed = await close_period(server.db, "2026-06", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_new_post_and_reverse_blocked_idempotent_existing_allowed():
    a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    source_id = str(uuid.uuid4())
    posted = await post_balanced_journal(
        server.db,
        date="2026-07-08",
        source_type="general_journal",
        source_id=source_id,
        description="open month",
        lines=_journal_lines(a["id"], b["id"]),
        posted_by=_user(),
    )
    await close_period(server.db, "2026-07", user=_user(), reason="aksi periode")

    retry = await post_balanced_journal(
        server.db,
        date="2026-07-08",
        source_type="general_journal",
        source_id=source_id,
        description="retry",
        lines=_journal_lines(a["id"], b["id"]),
        posted_by=_user(),
    )
    assert retry["id"] == posted["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_id": source_id, "reversal_of": None}) == 1

    with pytest.raises(PeriodLockedError):
        await post_balanced_journal(
            server.db,
            date="2026-07-09",
            source_type="general_journal",
            source_id=str(uuid.uuid4()),
            description="closed",
            lines=_journal_lines(a["id"], b["id"]),
            posted_by=_user(),
        )
    with pytest.raises(PeriodLockedError):
        await reverse_journal(
            server.db,
            posted["id"],
            source_type="general_journal_reversal",
            source_id=source_id,
            posted_by=_user(),
        )
    assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": posted["id"]}) == 0

    await reopen_period(server.db, "2026-07", user=_user(), reason="aksi periode")
    rev = await reverse_journal(
        server.db,
        posted["id"],
        source_type="general_journal_reversal",
        source_id=source_id,
        posted_by=_user(),
    )
    assert rev["date"] == "2026-07-08"


@pytest.mark.anyio
async def test_general_journal_http_obeys_lock_drafts_allowed():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        await c.post("/api/ledger/accounting-periods/2026-08/close", json={"reason": "tutup periode"})
        draft = await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-08-12", "description": "draft closed", "lines": _journal_lines(a["id"], b["id"])},
        )
        assert draft.status_code == 201, draft.text
        patched = await c.patch(
            f"/api/ledger/general-journals/{draft.json()['id']}",
            json={"date": "2026-08-15", "description": "still draft"},
        )
        assert patched.status_code == 200
        posted = await c.post(f"/api/ledger/general-journals/{draft.json()['id']}/post")
        assert posted.status_code == 409
        assert "ditutup" in posted.json()["detail"].lower()
        deleted = await c.delete(f"/api/ledger/general-journals/{draft.json()['id']}")
        assert deleted.status_code == 200
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


@pytest.mark.anyio
async def test_opening_balance_claim_blocks_close_until_sequence_ends():
    a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    admin = _user()
    async with await client_for(admin) as c:
        draft = (
            await c.post(
                "/api/ledger/opening-balances",
                json={
                    "cutoff_date": "2026-03-15",
                    "description": "OB claim",
                    "lines": _journal_lines(a["id"], b["id"], 1500),
                },
            )
        ).json()

    close_attempts = []
    original = post_voucher.__globals__["post_balanced_journal"]

    async def wrapped(*args, **kwargs):
        with pytest.raises(PeriodConflictError, match="diposting"):
            await close_period(server.db, "2026-03", user=_user(), reason="aksi periode")
        close_attempts.append("failed_close")
        return await original(*args, **kwargs)

    post_voucher.__globals__["post_balanced_journal"] = wrapped
    try:
        posted = await post_voucher(server.db, draft["id"], user=_user())
        assert posted["status"] == "posted"
        assert close_attempts == ["failed_close"]
    finally:
        post_voucher.__globals__["post_balanced_journal"] = original

    closed = await close_period(server.db, "2026-03", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_unauthorized_period_actions_blocked_and_roles_ungranted():
    viewer = _user(role="crm", perms=["sales.invoice.view"])
    async with await client_for(viewer) as c:
        assert (await c.get("/api/ledger/accounting-periods")).status_code == 403
        assert (await c.post("/api/ledger/accounting-periods/2026-01/close", json={})).status_code == 403
        assert (await c.post("/api/ledger/accounting-periods/2026-01/reopen", json={})).status_code == 403
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        keys = set(perms)
        assert "accounting.period.view" not in keys
        assert "accounting.period.close" not in keys
        assert "accounting.period.reopen" not in keys
    assert "accounting.period.view" in ALL_PERMISSIONS
    assert "accounting.period.close" in ALL_PERMISSIONS
    assert "accounting.period.reopen" in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_period_http_rbac_splits_view_close_reopen():
    viewer = _user(role="crm", perms=["accounting.period.view"])
    closer = _user(role="crm", perms=["accounting.period.close"])
    reopener = _user(role="crm", perms=["accounting.period.reopen"])

    async with await client_for(viewer) as c:
        listed = await c.get("/api/ledger/accounting-periods", params={"start": "2026-10", "end": "2026-10"})
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["status"] == "open"
        assert (await c.post("/api/ledger/accounting-periods/2026-10/close", json={"reason": "tutup periode"})).status_code == 403
        assert (await c.post("/api/ledger/accounting-periods/2026-10/reopen", json={})).status_code == 403

    async with await client_for(closer) as c:
        assert (await c.get("/api/ledger/accounting-periods")).status_code == 403
        closed = await c.post("/api/ledger/accounting-periods/2026-10/close", json={"reason": "tutup periode"})
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        assert (await c.post("/api/ledger/accounting-periods/2026-10/reopen", json={})).status_code == 403

    async with await client_for(reopener) as c:
        assert (await c.get("/api/ledger/accounting-periods")).status_code == 403
        assert (await c.post("/api/ledger/accounting-periods/2026-10/close", json={"reason": "tutup periode"})).status_code == 403
        reopened = await c.post("/api/ledger/accounting-periods/2026-10/reopen", json={"reason": "buka periode"})
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "open"


@pytest.mark.anyio
async def test_coa_type_lock_and_settings_block_delete():
    unused = await _coa(f"U{uuid.uuid4().hex[:6]}", "Free", "EXPS")
    renamed = await update_account(server.db, unused["id"], {"name": "Still Free", "account_type": "OEXP"}, user=_user())
    assert renamed["account_type"] == "OEXP"
    assert renamed["account_type_locked"] is False

    used = await _coa(f"J{uuid.uuid4().hex[:6]}", "Used", "AREC")
    await post_balanced_journal(
        server.db,
        date="2026-09-01",
        source_type="sales_invoice",
        source_id=str(uuid.uuid4()),
        description="invoice",
        lines=_journal_lines(used["id"], unused["id"]),
        posted_by=_user(),
    )
    with pytest.raises(AccountInUseError, match="terkunci"):
        await update_account(server.db, used["id"], {"account_type": "REVE"}, user=_user())
    still = await update_account(server.db, used["id"], {"name": "Piutang Lama"}, user=_user())
    assert still["name"] == "Piutang Lama"
    assert still["account_type"] == "AREC"
    assert still["account_type_locked"] is True

    mapped = await _coa(f"S{uuid.uuid4().hex[:6]}", "Settings AR", "AREC")
    await server.db.app_settings.insert_one({
        "key": SALES_SETTINGS_KEY,
        "receivable_account_id": mapped["id"],
        "revenue_account_id": unused["id"],
        "output_tax_account_id": unused["id"],
    })
    with pytest.raises(AccountInUseError):
        await delete_account(server.db, mapped["id"])
    with pytest.raises(AccountInUseError, match="terkunci"):
        await update_account(server.db, mapped["id"], {"account_type": "REVE"}, user=_user())

    admin = _user()
    async with await client_for(admin) as c:
        blocked = await c.patch(f"/api/ledger/accounts/{used['id']}", json={"account_type": "REVE"})
        assert blocked.status_code == 409
        assert "terkunci" in blocked.json()["detail"]
        renamed_ok = await c.patch(f"/api/ledger/accounts/{used['id']}", json={"name": "Piutang HTTP"})
        assert renamed_ok.status_code == 200
        assert renamed_ok.json()["account_type"] == "AREC"
        assert renamed_ok.json()["account_type_locked"] is True


async def _assert_locked(account_id: str):
    with pytest.raises(AccountInUseError, match="terkunci"):
        await update_account(server.db, account_id, {"account_type": "OEXP"}, user=_user())
    still = await update_account(server.db, account_id, {"name": "Nama Tetap Boleh"}, user=_user())
    assert still["account_type_locked"] is True
    with pytest.raises(AccountInUseError):
        await delete_account(server.db, account_id)


@pytest.mark.anyio
async def test_coa_type_lock_from_each_reference_and_mapping():
    counterpart = await _coa(f"C{uuid.uuid4().hex[:6]}", "Counterpart", "EXPS")

    journal_acc = await _coa(f"JL{uuid.uuid4().hex[:6]}", "Journal Line", "AREC")
    await server.db[JOURNAL_LINES].insert_one(
        {"id": str(uuid.uuid4()), "journal_id": str(uuid.uuid4()), "account_id": journal_acc["id"]}
    )
    await _assert_locked(journal_acc["id"])

    cash_master = await _coa(f"CM{uuid.uuid4().hex[:6]}", "Cash Master", "BANK")
    await server.db.cash_bank_accounts.insert_one(
        {"id": str(uuid.uuid4()), "account_id": cash_master["id"], "name": "Kas"}
    )
    await _assert_locked(cash_master["id"])

    cash_receipt = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Cash Receipt", "REVE")
    await server.db.cash_bank_receipts.insert_one(
        {
            "id": str(uuid.uuid4()),
            "cash_bank_account_id": counterpart["id"],
            "counter_account_id": cash_receipt["id"],
        }
    )
    await _assert_locked(cash_receipt["id"])

    gj_draft = await _coa(f"GJ{uuid.uuid4().hex[:6]}", "GJ Draft", "EXPS")
    await server.db[GJ_COLLECTION].insert_one(
        {
            "id": str(uuid.uuid4()),
            "status": "draft",
            "lines": [{"account_id": gj_draft["id"]}, {"account_id": counterpart["id"]}],
        }
    )
    await _assert_locked(gj_draft["id"])

    ob_draft = await _coa(f"OB{uuid.uuid4().hex[:6]}", "OB Draft", "EQTY")
    await server.db[OB_COLLECTION].insert_one(
        {
            "id": str(uuid.uuid4()),
            "status": "draft",
            "lines": [{"account_id": ob_draft["id"]}, {"account_id": counterpart["id"]}],
        }
    )
    await _assert_locked(ob_draft["id"])

    payroll_mapped = await _coa(f"PR{uuid.uuid4().hex[:6]}", "Payroll Payable", "APAY")
    await server.db.app_settings.insert_one(
        {
            "key": PAYROLL_SETTINGS_KEY,
            "payroll_payable_account_id": payroll_mapped["id"],
            "component_mappings": {
                "gaji_pokok": {"treatment": "expense", "account_id": counterpart["id"]}
            },
        }
    )
    await _assert_locked(payroll_mapped["id"])

    purchase_ap = await _coa(f"PA{uuid.uuid4().hex[:6]}", "Purchase AP", "APAY")
    purchase_clearing = await _coa(f"PC{uuid.uuid4().hex[:6]}", "Purchase Clearing", "OCLY")
    purchase_inventory = await _coa(f"PI{uuid.uuid4().hex[:6]}", "Purchase Inventory", "INTR")
    purchase_cogs = await _coa(f"PG{uuid.uuid4().hex[:6]}", "Purchase COGS", "COGS")
    await server.db.app_settings.insert_one(
        {
            "key": PURCHASE_SETTINGS_KEY,
            "payable_account_id": purchase_ap["id"],
            "purchase_clearing_account_id": purchase_clearing["id"],
            "inventory_account_id": purchase_inventory["id"],
            "cogs_account_id": purchase_cogs["id"],
            "inventory_accounting_effective_date": "2026-10-01",
        }
    )
    await _assert_locked(purchase_ap["id"])
    await _assert_locked(purchase_clearing["id"])
    await _assert_locked(purchase_inventory["id"])
    await _assert_locked(purchase_cogs["id"])


@pytest.mark.anyio
async def test_writer_claim_ttl_and_crash_recovery_allows_close():
    assert WRITER_CLAIM_TTL_SECONDS == 600
    claim = await acquire_period_writer_claim(server.db, "2026-01-20")
    raw = await server.db[PERIODS].find_one({"period_key": "2026-01"}, {"_id": 0})
    assert raw["writer_claims"][0]["token"] == claim["token"]
    assert raw["writer_claims"][0]["expires_at"]
    with pytest.raises(PeriodConflictError):
        await close_period(server.db, "2026-01", user=_user(), reason="aksi periode")
    # Worker disappears without release. After TTL the stale lease must not block close.
    await server.db[PERIODS].update_one(
        {"period_key": "2026-01", "writer_claims.token": claim["token"]},
        {"$set": {"writer_claims.$.expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}},
    )
    closed = await close_period(server.db, "2026-01", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"
    assert "writer_claims" not in closed
    assert "token" not in closed


@pytest.mark.anyio
async def test_multiple_writers_token_scoped_release():
    a = await acquire_period_writer_claim(server.db, "2026-02-01")
    b = await acquire_period_writer_claim(server.db, "2026-02-02")
    with pytest.raises(PeriodConflictError, match="diposting"):
        await close_period(server.db, "2026-02", user=_user(), reason="aksi periode")
    await release_period_writer_claim(server.db, a["period_key"], a["token"])
    with pytest.raises(PeriodConflictError, match="diposting"):
        await close_period(server.db, "2026-02", user=_user(), reason="aksi periode")
    await release_period_writer_claim(server.db, b["period_key"], b["token"])
    closed = await close_period(server.db, "2026-02", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_close_wins_new_journal_not_inserted():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    closed = await close_period(server.db, "2026-11", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"
    before_e = await server.db[JOURNAL_ENTRIES].count_documents({})
    before_l = await server.db[JOURNAL_LINES].count_documents({})
    source_id = str(uuid.uuid4())
    with pytest.raises(PeriodLockedError, match="ditutup"):
        await post_balanced_journal(
            server.db,
            date="2026-11-05",
            source_type="general_journal",
            source_id=source_id,
            description="closed wins",
            lines=_journal_lines(acc_a["id"], acc_b["id"]),
            posted_by=_user(),
        )
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_e
    assert await server.db[JOURNAL_LINES].count_documents({}) == before_l
    assert await server.db[JOURNAL_ENTRIES].find_one({"source_id": source_id}) is None


@pytest.mark.anyio
async def test_writer_wins_then_close_retries():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    claim = await acquire_period_writer_claim(server.db, "2026-12-01")
    with pytest.raises(PeriodConflictError, match="diposting"):
        await close_period(server.db, "2026-12", user=_user(), reason="aksi periode")
    posted = await post_balanced_journal(
        server.db,
        date="2026-12-01",
        source_type="general_journal",
        source_id=str(uuid.uuid4()),
        description="writer wins",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
        writer_claim_token=claim["token"],
    )
    assert posted["id"]
    await release_period_writer_claim(server.db, claim["period_key"], claim["token"])
    closed = await close_period(server.db, "2026-12", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_close_vs_reversal_both_directions():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    source_id = str(uuid.uuid4())
    posted = await post_balanced_journal(
        server.db,
        date="2026-01-08",
        source_type="general_journal",
        source_id=source_id,
        description="rev race",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
    )
    claim = await acquire_period_writer_claim(server.db, "2026-01-08")
    with pytest.raises(PeriodConflictError):
        await close_period(server.db, "2026-01", user=_user(), reason="aksi periode")
    rev = await reverse_journal(
        server.db,
        posted["id"],
        source_type="general_journal_reversal",
        source_id=source_id,
        posted_by=_user(),
        writer_claim_token=claim["token"],
    )
    assert rev["date"] == "2026-01-08"
    await release_period_writer_claim(server.db, claim["period_key"], claim["token"])
    closed = await close_period(server.db, "2026-01", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"

    other = await post_balanced_journal(
        server.db,
        date="2026-03-04",
        source_type="general_journal",
        source_id=str(uuid.uuid4()),
        description="close wins reverse",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
    )
    await close_period(server.db, "2026-03", user=_user(), reason="aksi periode")
    with pytest.raises(PeriodLockedError):
        await reverse_journal(
            server.db,
            other["id"],
            source_type="general_journal_reversal",
            source_id=other["source_id"],
            posted_by=_user(),
        )
    assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": other["id"]}) == 0


@pytest.mark.anyio
async def test_idempotent_retry_after_close_returns_existing_reversal():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    source_id = str(uuid.uuid4())
    posted = await post_balanced_journal(
        server.db,
        date="2026-04-02",
        source_type="general_journal",
        source_id=source_id,
        description="idempotent",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
    )
    rev = await reverse_journal(
        server.db,
        posted["id"],
        source_type="general_journal_reversal",
        source_id=source_id,
        posted_by=_user(),
    )
    await close_period(server.db, "2026-04", user=_user(), reason="aksi periode")
    retry_post = await post_balanced_journal(
        server.db,
        date="2026-04-02",
        source_type="general_journal",
        source_id=source_id,
        description="retry closed",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
    )
    assert retry_post["id"] == posted["id"]
    retry_rev = await reverse_journal(
        server.db,
        posted["id"],
        source_type="general_journal_reversal",
        source_id=source_id,
        posted_by=_user(),
    )
    assert retry_rev["id"] == rev["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": posted["id"]}) == 1


@pytest.mark.anyio
async def test_opening_balance_same_lease_compensation_and_close():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    admin = _user()
    async with await client_for(admin) as c:
        draft = (
            await c.post(
                "/api/ledger/opening-balances",
                json={
                    "cutoff_date": "2026-02-15",
                    "description": "OB compensate",
                    "lines": _journal_lines(acc_a["id"], acc_b["id"], 2500),
                },
            )
        ).json()

    close_attempts = []
    original = post_voucher.__globals__["post_balanced_journal"]

    async def wrapped(*args, **kwargs):
        journal = await original(*args, **kwargs)
        with pytest.raises(PeriodConflictError, match="diposting"):
            await close_period(server.db, "2026-02", user=_user(), reason="aksi periode")
        close_attempts.append("blocked")
        await server.db[OB_COLLECTION].insert_one(
            {
                "id": str(uuid.uuid4()),
                "number": f"SA-WIN-{uuid.uuid4().hex[:6]}",
                "status": "posted",
                "cutoff_date": "2026-02-15",
            }
        )
        return journal

    post_voucher.__globals__["post_balanced_journal"] = wrapped
    try:
        with pytest.raises(OpeningBalanceConflictError, match="cut-off"):
            await post_voucher(server.db, draft["id"], user=_user())
    finally:
        post_voucher.__globals__["post_balanced_journal"] = original

    assert close_attempts == ["blocked"]
    voucher = await server.db[OB_COLLECTION].find_one({"id": draft["id"]}, {"_id": 0})
    assert voucher["status"] == "draft"
    orig = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": "opening_balance", "source_id": draft["id"]}, {"_id": 0}
    )
    assert orig is not None
    assert orig["status"] == "reversed"
    rev = await server.db[JOURNAL_ENTRIES].find_one({"reversal_of": orig["id"]}, {"_id": 0})
    assert rev is not None
    assert rev["date"] == orig["date"] == "2026-02-15"
    orig_lines = await server.db[JOURNAL_LINES].find({"journal_id": orig["id"]}, {"_id": 0}).to_list(20)
    rev_lines = await server.db[JOURNAL_LINES].find({"journal_id": rev["id"]}, {"_id": 0}).to_list(20)
    net = {}
    for ln in orig_lines + rev_lines:
        net[ln["account_id"]] = net.get(ln["account_id"], 0) + float(ln.get("debit") or 0) - float(ln.get("credit") or 0)
    assert all(abs(v) < 0.001 for v in net.values())
    closed = await close_period(server.db, "2026-02", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"
    leftover = await server.db[OB_COLLECTION].find_one({"id": draft["id"]}, {"_id": 0})
    assert leftover.get("accounting_recovery_required") in (None, False)


@pytest.mark.anyio
async def test_opening_balance_compensation_failure_is_not_swallowed():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    admin = _user()
    async with await client_for(admin) as c:
        draft = (
            await c.post(
                "/api/ledger/opening-balances",
                json={
                    "cutoff_date": "2026-05-10",
                    "description": "OB fail compensate",
                    "lines": _journal_lines(acc_a["id"], acc_b["id"], 800),
                },
            )
        ).json()

    original_post = post_voucher.__globals__["post_balanced_journal"]
    original_rev = post_voucher.__globals__["reverse_journal"]

    async def wrap_post(*args, **kwargs):
        journal = await original_post(*args, **kwargs)
        await server.db[OB_COLLECTION].insert_one(
            {
                "id": str(uuid.uuid4()),
                "number": f"SA-WIN-{uuid.uuid4().hex[:6]}",
                "status": "posted",
                "cutoff_date": "2026-05-10",
            }
        )
        return journal

    async def fail_rev(*args, **kwargs):
        raise RuntimeError("compensation boom")

    post_voucher.__globals__["post_balanced_journal"] = wrap_post
    post_voucher.__globals__["reverse_journal"] = fail_rev
    try:
        with pytest.raises(OpeningBalanceConflictError, match="recovery"):
            await post_voucher(server.db, draft["id"], user=_user())
    finally:
        post_voucher.__globals__["post_balanced_journal"] = original_post
        post_voucher.__globals__["reverse_journal"] = original_rev

    orig = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": "opening_balance", "source_id": draft["id"]}, {"_id": 0}
    )
    assert orig is not None
    assert orig["status"] == "posted"
    assert await server.db[JOURNAL_ENTRIES].find_one({"reversal_of": orig["id"]}) is None
    voucher = await server.db[OB_COLLECTION].find_one({"id": draft["id"]}, {"_id": 0})
    assert voucher["accounting_recovery_required"] is True
    assert voucher["accounting_recovery_journal_id"] == orig["id"]
    assert voucher["accounting_recovery_reason"] == "opening_balance_cutoff_conflict"
    await server.db[PERIODS].update_one({"period_key": "2026-05"}, {"$set": {"writer_claims": []}})
    with pytest.raises(PeriodConflictError, match="recovery akuntansi"):
        await close_period(server.db, "2026-05", user=_user(), reason="aksi periode")
    async with await client_for(admin) as c:
        blocked = await c.post("/api/ledger/accounting-periods/2026-05/close", json={"reason": "tutup periode"})
        assert blocked.status_code == 409
        assert "recovery akuntansi" in blocked.json()["detail"]
        assert (voucher.get("number") or draft["id"]) in blocked.json()["detail"]
        assert orig["id"] in blocked.json()["detail"]

    recovered = await post_voucher(server.db, draft["id"], user=_user())
    assert recovered["status"] == "draft"
    assert recovered["accounting_recovery_required"] is False
    await server.db[OB_COLLECTION].update_one(
        {"id": draft["id"]},
        {
            "$set": {
                "accounting_recovery_required": True,
                "accounting_recovery_journal_id": orig["id"],
                "accounting_recovery_reason": "opening_balance_cutoff_conflict",
            }
        },
    )
    again = await post_voucher(server.db, draft["id"], user=_user())
    assert again["status"] == "draft"
    assert again["accounting_recovery_required"] is False
    assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": orig["id"]}) == 1
    refreshed = await server.db[JOURNAL_ENTRIES].find_one({"id": orig["id"]}, {"_id": 0})
    assert refreshed["status"] == "reversed"
    orig_lines = await server.db[JOURNAL_LINES].find({"journal_id": orig["id"]}, {"_id": 0}).to_list(20)
    rev = await server.db[JOURNAL_ENTRIES].find_one({"reversal_of": orig["id"]}, {"_id": 0})
    rev_lines = await server.db[JOURNAL_LINES].find({"journal_id": rev["id"]}, {"_id": 0}).to_list(20)
    net = {}
    for ln in orig_lines + rev_lines:
        net[ln["account_id"]] = net.get(ln["account_id"], 0) + float(ln.get("debit") or 0) - float(ln.get("credit") or 0)
    assert all(abs(v) < 0.001 for v in net.values())
    closed = await close_period(server.db, "2026-05", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"


@pytest.mark.anyio
async def test_one_jump_missing_periods_collection_allows_post():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    await server.db[PERIODS].drop()
    posted = await post_balanced_journal(
        server.db,
        date="2026-06-01",
        source_type="general_journal",
        source_id=str(uuid.uuid4()),
        description="fresh db",
        lines=_journal_lines(acc_a["id"], acc_b["id"]),
        posted_by=_user(),
    )
    assert posted["status"] == "posted"
    await ensure_periods(server.db)
    period = await get_period(server.db, "2026-06")
    assert period["status"] == "open"


@pytest.mark.anyio
async def test_public_period_hides_writer_tokens():
    claim = await acquire_period_writer_claim(server.db, "2026-07-01")
    raw = await server.db[PERIODS].find_one({"period_key": "2026-07"}, {"_id": 0})
    assert raw["writer_claims"][0]["token"] == claim["token"]
    published = public_period(raw, period_key="2026-07")
    assert "writer_claims" not in published
    assert "token" not in published
    assert "expires_at" not in published
    admin = _user()
    async with await client_for(admin) as c:
        listed = await c.get("/api/ledger/accounting-periods", params={"start": "2026-07", "end": "2026-07"})
        item = listed.json()["items"][0]
        blob = listed.text
        assert claim["token"] not in blob
        assert "writer_claims" not in item
        assert item["period_key"] == "2026-07"
        assert item["status"] == "open"
    await release_period_writer_claim(server.db, claim["period_key"], claim["token"])


@pytest.mark.anyio
async def test_coa_same_type_resubmit_and_unused_settings_mapping():
    used = await _coa(f"L{uuid.uuid4().hex[:6]}", "Locked AR", "AREC")
    other = await _coa(f"X{uuid.uuid4().hex[:6]}", "Other", "EXPS")
    await post_balanced_journal(
        server.db,
        date="2026-08-01",
        source_type="sales_invoice",
        source_id=str(uuid.uuid4()),
        description="lock",
        lines=_journal_lines(used["id"], other["id"]),
        posted_by=_user(),
    )
    same = await update_account(
        server.db,
        used["id"],
        {"name": "Piutang Resubmit", "account_type": "AREC"},
        user=_user(),
    )
    assert same["name"] == "Piutang Resubmit"
    assert same["account_type"] == "AREC"
    assert same["account_type_locked"] is True
    with pytest.raises(AccountInUseError, match="terkunci"):
        await update_account(server.db, used["id"], {"account_type": "REVE"}, user=_user())

    sales_only = await _coa(f"SM{uuid.uuid4().hex[:6]}", "Sales Mapped", "AREC")
    await server.db.app_settings.insert_one(
        {
            "key": SALES_SETTINGS_KEY,
            "receivable_account_id": sales_only["id"],
            "revenue_account_id": other["id"],
            "output_tax_account_id": other["id"],
        }
    )
    await _assert_locked(sales_only["id"])

    payroll_only = await _coa(f"PM{uuid.uuid4().hex[:6]}", "Payroll Mapped", "APAY")
    await server.db.app_settings.insert_one(
        {
            "key": PAYROLL_SETTINGS_KEY,
            "payroll_payable_account_id": other["id"],
            "component_mappings": {"tunjangan": {"treatment": "expense", "account_id": payroll_only["id"]}},
        }
    )
    await _assert_locked(payroll_only["id"])


@pytest.mark.anyio
async def test_drafts_and_real_source_reversals_obey_closed_month():
    acc_exps = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_bank = await _coa(f"K{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    acc_reve = await _coa(f"R{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
    admin = _user()
    async with await client_for(admin) as c:
        cb = (
            await c.post(
                "/api/cash-bank/accounts",
                json={"name": "Kas Period", "account_id": acc_bank["id"], "active": True},
            )
        ).json()
        gj = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-03", "description": "gj posted", "lines": _journal_lines(acc_exps["id"], acc_bank["id"])},
            )
        ).json()
        assert (await c.post(f"/api/ledger/general-journals/{gj['id']}/post")).status_code == 200
        receipt = (
            await c.post(
                "/api/cash-bank/receipts",
                json={
                    "date": "2026-09-04",
                    "cash_bank_id": cb["id"],
                    "counter_account_id": acc_reve["id"],
                    "amount": 5000,
                    "description": "cb posted",
                },
            )
        ).json()
        assert (await c.post(f"/api/cash-bank/receipts/{receipt['id']}/post")).status_code == 200
        ob = (
            await c.post(
                "/api/ledger/opening-balances",
                json={"cutoff_date": "2026-09-01", "description": "ob posted", "lines": _journal_lines(acc_exps["id"], acc_bank["id"], 900)},
            )
        ).json()
        assert (await c.post(f"/api/ledger/opening-balances/{ob['id']}/post")).status_code == 200

        inv_id = str(uuid.uuid4())
        rec_id = str(uuid.uuid4())
        await server.db.sales_invoices.insert_one(
            {"id": inv_id, "invoice_number": "INV-P2", "financial_status": "finalized"}
        )
        await server.db.sales_receipts.insert_one({"id": rec_id, "receipt_number": "RCV-P2"})
        await post_balanced_journal(
            server.db,
            date="2026-09-06",
            source_type="sales_invoice",
            source_id=inv_id,
            description="inv",
            lines=_journal_lines(acc_exps["id"], acc_bank["id"]),
            posted_by=_user(),
        )
        await post_balanced_journal(
            server.db,
            date="2026-09-07",
            source_type="sales_receipt",
            source_id=rec_id,
            description="rcv",
            lines=_journal_lines(acc_bank["id"], acc_reve["id"]),
            posted_by=_user(),
        )

        await c.post("/api/ledger/accounting-periods/2026-09/close", json={"reason": "tutup periode"})

        gj_rev = await c.post(f"/api/ledger/general-journals/{gj['id']}/reverse")
        assert gj_rev.status_code == 409
        cb_rev = await c.post(f"/api/cash-bank/receipts/{receipt['id']}/reverse")
        assert cb_rev.status_code == 409
        ob_rev = await c.post(f"/api/ledger/opening-balances/{ob['id']}/reverse")
        assert ob_rev.status_code == 409
        with pytest.raises(HTTPException) as inv_exc:
            await reverse_sales_invoice_accounting(server.db, inv_id, admin, "koreksi")
        assert inv_exc.value.status_code == 409
        with pytest.raises(HTTPException) as rec_exc:
            await reverse_sales_receipt_accounting(server.db, rec_id, admin)
        assert rec_exc.value.status_code == 409

        draft_gj = await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-09-10", "description": "draft closed", "lines": _journal_lines(acc_exps["id"], acc_bank["id"])},
        )
        assert draft_gj.status_code == 201
        patched_gj = await c.patch(
            f"/api/ledger/general-journals/{draft_gj.json()['id']}",
            json={"description": "edit closed"},
        )
        assert patched_gj.status_code == 200
        assert (await c.delete(f"/api/ledger/general-journals/{draft_gj.json()['id']}")).status_code == 200

        draft_cb = await c.post(
            "/api/cash-bank/receipts",
            json={
                "date": "2026-09-11",
                "cash_bank_id": cb["id"],
                "counter_account_id": acc_reve["id"],
                "amount": 1000,
            },
        )
        assert draft_cb.status_code == 201, draft_cb.text
        patched_cb = await c.patch(
            f"/api/cash-bank/receipts/{draft_cb.json()['id']}",
            json={
                "date": "2026-09-12",
                "cash_bank_id": cb["id"],
                "counter_account_id": acc_reve["id"],
                "amount": 1100,
            },
        )
        assert patched_cb.status_code == 200
        assert (await c.delete(f"/api/cash-bank/receipts/{draft_cb.json()['id']}")).status_code == 200

        draft_ob = await c.post(
            "/api/ledger/opening-balances",
            json={"cutoff_date": "2026-09-20", "description": "ob draft", "lines": _journal_lines(acc_exps["id"], acc_bank["id"], 100)},
        )
        assert draft_ob.status_code == 201
        patched_ob = await c.patch(
            f"/api/ledger/opening-balances/{draft_ob.json()['id']}",
            json={"description": "ob draft edit"},
        )
        assert patched_ob.status_code == 200
        assert (await c.delete(f"/api/ledger/opening-balances/{draft_ob.json()['id']}")).status_code == 200

        post_closed = await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-09-18", "description": "post blocked", "lines": _journal_lines(acc_exps["id"], acc_bank["id"])},
        )
        assert post_closed.status_code == 201
        posted_closed = await c.post(f"/api/ledger/general-journals/{post_closed.json()['id']}/post")
        assert posted_closed.status_code == 409

        await c.post("/api/ledger/accounting-periods/2026-09/reopen", json={"reason": "reverse"})
        gj_ok = await c.post(f"/api/ledger/general-journals/{gj['id']}/reverse")
        assert gj_ok.status_code == 200, gj_ok.text
        assert gj_ok.json()["reversal_journal"]["date"] == "2026-09-03"
        cb_ok = await c.post(f"/api/cash-bank/receipts/{receipt['id']}/reverse")
        assert cb_ok.status_code == 200, cb_ok.text
        ob_ok = await c.post(f"/api/ledger/opening-balances/{ob['id']}/reverse")
        assert ob_ok.status_code == 200, ob_ok.text
        inv_rev = await reverse_journal(
            server.db,
            (await server.db[JOURNAL_ENTRIES].find_one({"source_type": "sales_invoice", "source_id": inv_id}))["id"],
            source_type="sales_invoice_reversal",
            source_id=inv_id,
            posted_by=_user(),
        )
        assert inv_rev["date"] == "2026-09-06"
        rec_rev = await reverse_journal(
            server.db,
            (await server.db[JOURNAL_ENTRIES].find_one({"source_type": "sales_receipt", "source_id": rec_id}))["id"],
            source_type="sales_receipt_reversal",
            source_id=rec_id,
            posted_by=_user(),
        )
        assert rec_rev["date"] == "2026-09-07"


@pytest.mark.anyio
async def test_source_http_409_on_closed_period_post():
    acc_exps = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_bank = await _coa(f"K{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    acc_reve = await _coa(f"R{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
    admin = _user()
    async with await client_for(admin) as c:
        cb = (
            await c.post(
                "/api/cash-bank/accounts",
                json={"name": "Kas 409", "account_id": acc_bank["id"], "active": True},
            )
        ).json()
        await c.post("/api/ledger/accounting-periods/2026-10/close", json={"reason": "tutup periode"})
        gj = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-10-02", "description": "gj", "lines": _journal_lines(acc_exps["id"], acc_bank["id"])},
            )
        ).json()
        assert (await c.post(f"/api/ledger/general-journals/{gj['id']}/post")).status_code == 409
        rec = (
            await c.post(
                "/api/cash-bank/receipts",
                json={"date": "2026-10-03", "cash_bank_id": cb["id"], "counter_account_id": acc_reve["id"], "amount": 2000},
            )
        ).json()
        assert (await c.post(f"/api/cash-bank/receipts/{rec['id']}/post")).status_code == 409
        ob = (
            await c.post(
                "/api/ledger/opening-balances",
                json={"cutoff_date": "2026-10-01", "description": "ob", "lines": _journal_lines(acc_exps["id"], acc_bank["id"], 300)},
            )
        ).json()
        assert (await c.post(f"/api/ledger/opening-balances/{ob['id']}/post")).status_code == 409


@pytest.mark.anyio
async def test_audit_event_fields_and_no_mutation_endpoints():
    admin = _user()
    async with await client_for(admin) as c:
        acc = await _coa(f"AU{uuid.uuid4().hex[:6]}", "Audit COA", "EXPS")
        await c.patch(f"/api/ledger/accounts/{acc['id']}", json={"name": "Renamed Audit"})
        await c.post("/api/ledger/accounting-periods/2026-12/close", json={"reason": "year end"})
        audit = await c.get("/api/ledger/accounting-audit")
        assert audit.status_code == 200
        items = audit.json()["items"]
        types = {row["event_type"] for row in items}
        assert "period_closed" in types
        assert "coa_created" in types
        assert "coa_renamed" in types
        closed = next(row for row in items if row["event_type"] == "period_closed")
        assert closed["actor"]
        assert closed["at"]
        assert closed["before"]["status"] == "open"
        assert closed["after"]["status"] == "closed"
        assert closed["reason"] == "year end"
        assert (await c.delete("/api/ledger/accounting-audit")).status_code == 405
        assert (await c.put("/api/ledger/accounting-audit", json={})).status_code == 405


@pytest.mark.anyio
async def test_expired_writer_is_fenced_after_close():
    acc_a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
    acc_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
    claim = await acquire_period_writer_claim(server.db, "2026-01-18")
    await server.db[PERIODS].update_one(
        {"period_key": "2026-01", "writer_claims.token": claim["token"]},
        {"$set": {"writer_claims.$.expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}},
    )
    closed = await close_period(server.db, "2026-01", user=_user(), reason="aksi periode")
    assert closed["status"] == "closed"
    raw = await server.db[PERIODS].find_one({"period_key": "2026-01"}, {"_id": 0})
    assert raw.get("writer_claims") == []
    before_e = await server.db[JOURNAL_ENTRIES].count_documents({})
    before_l = await server.db[JOURNAL_LINES].count_documents({})
    with pytest.raises(PeriodLockedError):
        await post_balanced_journal(
            server.db,
            date="2026-01-18",
            source_type="general_journal",
            source_id=str(uuid.uuid4()),
            description="stale resume",
            lines=_journal_lines(acc_a["id"], acc_b["id"]),
            posted_by=_user(),
            writer_claim_token=claim["token"],
        )
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_e
    assert await server.db[JOURNAL_LINES].count_documents({}) == before_l


@pytest.mark.anyio
async def test_period_close_retry_writes_missing_audit_without_extra_cycle():
    first = await close_period(server.db, "2026-08", user=_user(), reason="first")
    assert first["status"] == "closed"
    await server.db[AUDIT_COLLECTION].delete_many(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-08"}
    )
    closed_at = (await server.db[PERIODS].find_one({"period_key": "2026-08"}, {"_id": 0}))["closed_at"]
    again = await close_period(server.db, "2026-08", user=_user(), reason="retry-audit")
    assert again["status"] == "closed"
    refreshed = await server.db[PERIODS].find_one({"period_key": "2026-08"}, {"_id": 0})
    assert refreshed["closed_at"] == closed_at
    assert refreshed["close_reason"] == "first"
    events = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-08"}, {"_id": 0}
    ).to_list(10)
    assert len(events) == 1
    assert events[0]["operation_key"] == f"period:2026-08:close:{closed_at}"
    assert events[0]["reason"] == "first"

    reopened = await reopen_period(server.db, "2026-08", user=_user(), reason="reopen-first")
    assert reopened["status"] == "open"
    await server.db[AUDIT_COLLECTION].delete_many(
        {"event_type": EVENT_PERIOD_REOPENED, "entity_id": "2026-08"}
    )
    reopened_at = (await server.db[PERIODS].find_one({"period_key": "2026-08"}, {"_id": 0}))["reopened_at"]
    again_reopen = await reopen_period(server.db, "2026-08", user=_user(), reason="retry-audit")
    assert again_reopen["status"] == "open"
    refreshed_reopen = await server.db[PERIODS].find_one({"period_key": "2026-08"}, {"_id": 0})
    assert refreshed_reopen["reopened_at"] == reopened_at
    assert refreshed_reopen["reopen_reason"] == "reopen-first"
    reopen_events = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_REOPENED, "entity_id": "2026-08"}, {"_id": 0}
    ).to_list(10)
    assert len(reopen_events) == 1
    assert reopen_events[0]["operation_key"] == f"period:2026-08:reopen:{reopened_at}"


@pytest.mark.anyio
async def test_close_reopen_reason_required_http():
    admin = _user()
    async with await client_for(admin) as c:
        missing = await c.post("/api/ledger/accounting-periods/2026-03/close", json={})
        assert missing.status_code == 400
        blank = await c.post("/api/ledger/accounting-periods/2026-03/close", json={"reason": "  "})
        assert blank.status_code == 400
        short = await c.post("/api/ledger/accounting-periods/2026-03/close", json={"reason": "ab"})
        assert short.status_code == 400
        ok = await c.post("/api/ledger/accounting-periods/2026-03/close", json={"reason": "tutup"})
        assert ok.status_code == 200, ok.text
        missing_reopen = await c.post("/api/ledger/accounting-periods/2026-03/reopen", json={})
        assert missing_reopen.status_code == 400
        short_reopen = await c.post("/api/ledger/accounting-periods/2026-03/reopen", json={"reason": " x "})
        assert short_reopen.status_code == 400
        ok_reopen = await c.post("/api/ledger/accounting-periods/2026-03/reopen", json={"reason": "buka"})
        assert ok_reopen.status_code == 200, ok_reopen.text


@pytest.mark.anyio
async def test_period_audit_operation_keys_are_distinct_across_cycles():
    close1 = await close_period(server.db, "2026-11", user=_user(), reason="close one")
    reopen1 = await reopen_period(server.db, "2026-11", user=_user(), reason="reopen one")
    close2 = await close_period(server.db, "2026-11", user=_user(), reason="close two")
    reopen2 = await reopen_period(server.db, "2026-11", user=_user(), reason="reopen two")
    assert close1["status"] == "closed"
    assert reopen1["status"] == "open"
    assert close2["status"] == "closed"
    assert reopen2["status"] == "open"
    closed = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-11"}, {"_id": 0}
    ).to_list(10)
    reopened = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_REOPENED, "entity_id": "2026-11"}, {"_id": 0}
    ).to_list(10)
    assert len(closed) == 2
    assert len(reopened) == 2
    keys = [row["operation_key"] for row in closed + reopened]
    assert len(set(keys)) == 4
    assert all(key.startswith("period:2026-11:") for key in keys)


@pytest.mark.anyio
async def test_second_close_and_reopen_audit_failure_retries():
    await close_period(server.db, "2026-02", user=_user(), reason="close one")
    await reopen_period(server.db, "2026-02", user=_user(), reason="reopen one")
    original = close_period.__globals__["record_accounting_event"]

    async def boom(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    close_period.__globals__["record_accounting_event"] = boom
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            await close_period(server.db, "2026-02", user=_user(), reason="close two")
    finally:
        close_period.__globals__["record_accounting_event"] = original

    row = await server.db[PERIODS].find_one({"period_key": "2026-02"}, {"_id": 0})
    assert row["status"] == "closed"
    closed_at = row["closed_at"]
    assert row["close_reason"] == "close two"
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-02"}
    ) == 1

    retried = await close_period(server.db, "2026-02", user=_user(), reason="other reason")
    assert retried["status"] == "closed"
    refreshed = await server.db[PERIODS].find_one({"period_key": "2026-02"}, {"_id": 0})
    assert refreshed["closed_at"] == closed_at
    assert refreshed["close_reason"] == "close two"
    closed_events = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-02"}, {"_id": 0}
    ).to_list(10)
    assert len(closed_events) == 2
    assert len({row["operation_key"] for row in closed_events}) == 2

    close_period.__globals__["record_accounting_event"] = boom
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            await reopen_period(server.db, "2026-02", user=_user(), reason="reopen two")
    finally:
        close_period.__globals__["record_accounting_event"] = original

    row = await server.db[PERIODS].find_one({"period_key": "2026-02"}, {"_id": 0})
    assert row["status"] == "open"
    reopened_at = row["reopened_at"]
    assert row["reopen_reason"] == "reopen two"
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_PERIOD_REOPENED, "entity_id": "2026-02"}
    ) == 1

    retried_reopen = await reopen_period(server.db, "2026-02", user=_user(), reason="other reopen")
    assert retried_reopen["status"] == "open"
    refreshed_reopen = await server.db[PERIODS].find_one({"period_key": "2026-02"}, {"_id": 0})
    assert refreshed_reopen["reopened_at"] == reopened_at
    assert refreshed_reopen["reopen_reason"] == "reopen two"
    reopen_events = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_REOPENED, "entity_id": "2026-02"}, {"_id": 0}
    ).to_list(10)
    assert len(reopen_events) == 2
    assert len({row["operation_key"] for row in reopen_events}) == 2


@pytest.mark.anyio
async def test_concurrent_period_audit_retry_writes_one_event():
    await close_period(server.db, "2026-07", user=_user(), reason="first close")
    await server.db[AUDIT_COLLECTION].delete_many(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-07"}
    )
    closed_at = (await server.db[PERIODS].find_one({"period_key": "2026-07"}, {"_id": 0}))["closed_at"]
    results = await asyncio.gather(
        close_period(server.db, "2026-07", user=_user(), reason="retry aaa"),
        close_period(server.db, "2026-07", user=_user(), reason="retry bbb"),
    )
    assert all(row["status"] == "closed" for row in results)
    events = await server.db[AUDIT_COLLECTION].find(
        {"event_type": EVENT_PERIOD_CLOSED, "entity_id": "2026-07"}, {"_id": 0}
    ).to_list(10)
    assert len(events) == 1
    assert events[0]["operation_key"] == f"period:2026-07:close:{closed_at}"
    refreshed = await server.db[PERIODS].find_one({"period_key": "2026-07"}, {"_id": 0})
    assert refreshed["closed_at"] == closed_at
    assert refreshed["close_reason"] == "first close"


@pytest.mark.anyio
async def test_coa_and_settings_audit_delivery_is_best_effort():
    unused = await _coa(f"U{uuid.uuid4().hex[:6]}", "Audit Name", "EXPS")
    original = update_account.__globals__["record_accounting_event"]

    async def boom(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    update_account.__globals__["record_accounting_event"] = boom
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            await update_account(server.db, unused["id"], {"name": "Renamed After Audit Fail"}, user=_user())
    finally:
        update_account.__globals__["record_accounting_event"] = original
    stored = await server.db[COA_COLLECTION].find_one({"id": unused["id"]}, {"_id": 0})
    assert stored["name"] == "Renamed After Audit Fail"
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_COA_RENAMED, "entity_id": unused["id"]}
    ) == 0
    retry = await update_account(server.db, unused["id"], {"name": "Renamed After Audit Fail"}, user=_user())
    assert retry["name"] == "Renamed After Audit Fail"
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_COA_RENAMED, "entity_id": unused["id"]}
    ) == 0

    arec = await _coa(f"AR{uuid.uuid4().hex[:6]}", "AR", "AREC")
    reve = await _coa(f"RV{uuid.uuid4().hex[:6]}", "REV", "REVE")
    tax = await _coa(f"TX{uuid.uuid4().hex[:6]}", "TAX", "APAY")
    sales_mod = update_sales_accounting_settings.__globals__
    sales_original = sales_mod["record_accounting_event"]
    sales_mod["record_accounting_event"] = boom
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            await update_sales_accounting_settings(
                server.db,
                {
                    "receivable_account_id": arec["id"],
                    "revenue_account_id": reve["id"],
                    "output_tax_account_id": tax["id"],
                },
                _user(),
            )
    finally:
        sales_mod["record_accounting_event"] = sales_original
    sales = await server.db.app_settings.find_one({"key": "sales_accounting"}, {"_id": 0})
    assert sales["receivable_account_id"] == arec["id"]
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_SALES_SETTINGS_CHANGED}
    ) == 0

    payable = await _coa(f"PY{uuid.uuid4().hex[:6]}", "Payable", "APAY")
    pay_mod = update_payroll_accounting_settings.__globals__
    pay_original = pay_mod["record_accounting_event"]
    pay_mod["record_accounting_event"] = boom
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            await update_payroll_accounting_settings(
                server.db,
                {"payroll_payable_account_id": payable["id"], "component_mappings": {}},
                _user(),
            )
    finally:
        pay_mod["record_accounting_event"] = pay_original
    payroll = await server.db.app_settings.find_one({"key": "payroll_accounting"}, {"_id": 0})
    assert payroll["payroll_payable_account_id"] == payable["id"]
    assert await server.db[AUDIT_COLLECTION].count_documents(
        {"event_type": EVENT_PAYROLL_SETTINGS_CHANGED}
    ) == 0
