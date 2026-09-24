"""Purchase invoice explicit accounting posting — Phase 4A."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
TEST_DB_NAME = f"eternal_packaging_purchase_invoice_posting_test_{_worker}"
os.environ["DB_NAME"] = TEST_DB_NAME
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods, reopen_period
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    ensure_indexes as ensure_journal,
    reverse_journal,
)
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_invoice_posting import (
    ACCOUNTING_POST_CLAIM_TTL_SECONDS,
    CANCEL_CLAIM_MESSAGE,
    CANCEL_POSTED_MESSAGE,
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    INVOICE_CANCEL_CLAIM_AT_FIELD,
    INVOICE_CANCEL_CLAIM_FIELD,
    INVOICE_CANCEL_CLAIM_TTL_SECONDS,
    PAYMENT_CREATE_CLAIM_AT_FIELD,
    PAYMENT_CREATE_CLAIM_FIELD,
    PAYMENT_CREATE_CLAIM_TTL_SECONDS,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    acquire_invoice_cancel_claim,
    acquire_payment_create_claim,
    acquire_posting_claim,
    acquire_reverse_claim,
    invoice_cancel_claim_is_active,
    payment_create_claim_is_active,
    post_purchase_invoice_accounting,
    posting_claim_is_active,
    reverse_purchase_invoice_accounting,
)
from services.purchase_invoices import cancel_purchase_invoice
from services.purchase_payments import create_purchase_payment

POST_USER = {
    "id": "admin-posting",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Posting",
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
    server.db = server.client[TEST_DB_NAME]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    await server.client.drop_database(TEST_DB_NAME)
    server.db = server.client[TEST_DB_NAME]
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    await server.db.accounting_periods.delete_many({})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    await server.db.accounting_periods.delete_many({})


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(POST_USER)


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings(payable=None, clearing=None):
    payable = payable or await _coa("21", "Hutang Usaha", "APAY")
    clearing = clearing or await _coa("21", "Purchase Clearing", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "payable_account_id": payable["id"],
                "purchase_clearing_account_id": clearing["id"],
            }
        },
        upsert=True,
    )
    return payable, clearing


async def _seed_invoice(**extra) -> dict:
    inv_id = extra.pop("id", str(uuid.uuid4()))
    doc = {
        "id": inv_id,
        "invoice_number": extra.pop("invoice_number", f"FPB-{uuid.uuid4().hex[:8].upper()}"),
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "supplier": extra.pop("supplier", "PT ABC"),
        "grand_total": extra.pop("grand_total", 10_000_000),
        "cancelled": extra.pop("cancelled", False),
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.purchase_invoices.delete_many({"id": inv_id})
    await server.db.purchase_invoices.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _source_journals(invoice_id: str):
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_TYPE, "source_id": invoice_id},
        {"_id": 0},
    ).to_list(20)


async def _unlocked_post(invoice_id: str):
    try:
        return await post_purchase_invoice_accounting(
            server.db, invoice_id, POST_USER, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_reverse(invoice_id: str, reason: str = "koreksi"):
    try:
        return await reverse_purchase_invoice_accounting(
            server.db, invoice_id, POST_USER, reason, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_cancel(invoice_id: str, reason: str = "batal"):
    try:
        return await cancel_purchase_invoice(server.db, invoice_id, POST_USER, reason=reason)
    except HTTPException as exc:
        return exc


def _stale_claim_at() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()


@pytest.mark.anyio
async def test_invoice_journal_dr_clearing_cr_ap():
    payable, clearing = await _configure_settings()
    inv = await _seed_invoice(grand_total=10_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert res.status_code == 200, res.text
            body = res.json()["invoice"]
            assert body["accounting_status"] == "posted"
            assert body["accounting_posted"] is True
            assert body["accounting_journal_id"]
            journals = await _source_journals(inv["id"])
            assert len(journals) == 1
            assert journals[0]["date"] == "2026-09-01"
            lines = await server.db[JOURNAL_LINES].find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
            by_acc = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_acc[clearing["id"]]["debit"])) == Decimal("10000000.00")
            assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("0.00")
            assert Decimal(str(by_acc[payable["id"]]["credit"])) == Decimal("10000000.00")
            assert Decimal(str(by_acc[payable["id"]]["debit"])) == Decimal("0.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_settings_not_ready_and_cancelled_blocked():
    inv = await _seed_invoice()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert missing.status_code == 400
            await _configure_settings()
            cancelled = await _seed_invoice(cancelled=True, invoice_number="FPB-CAN")
            bad = await client.post(f"/api/purchase-invoices/{cancelled['id']}/post-accounting")
            assert bad.status_code == 409
            zero = await _seed_invoice(grand_total=0, invoice_number="FPB-ZERO")
            z = await client.post(f"/api/purchase-invoices/{zero['id']}/post-accounting")
            assert z.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_idempotent_retry_and_concurrent_post():
    await _configure_settings()
    inv = await _seed_invoice()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert first.status_code == 200, first.text
            jid = first.json()["invoice"]["accounting_journal_id"]
            retry = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert retry.status_code == 200
            assert retry.json()["invoice"]["accounting_journal_id"] == jid
            assert len(await _source_journals(inv["id"])) == 1

            inv2 = await _seed_invoice(invoice_number="FPB-CONC")
            results = await asyncio.gather(
                client.post(f"/api/purchase-invoices/{inv2['id']}/post-accounting"),
                client.post(f"/api/purchase-invoices/{inv2['id']}/post-accounting"),
            )
            assert all(r.status_code == 200 for r in results)
            journals = await _source_journals(inv2["id"])
            assert len(journals) == 1
            ids = {r.json()["invoice"]["accounting_journal_id"] for r in results}
            assert ids == {journals[0]["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_settings_drift_keeps_original_journal_accounts():
    payable, clearing = await _configure_settings()
    inv = await _seed_invoice()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            posted = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200
            jid = posted.json()["invoice"]["accounting_journal_id"]
            payable2, clearing2 = await _configure_settings()
            assert payable2["id"] != payable["id"]
            get2 = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert get2.status_code == 200
            assert get2.json()["invoice"]["accounting_status"] == "posted"
            lines = await server.db[JOURNAL_LINES].find({"journal_id": jid}, {"_id": 0}).to_list(10)
            account_ids = {ln["account_id"] for ln in lines}
            assert payable["id"] in account_ids
            assert clearing["id"] in account_ids
            assert payable2["id"] not in account_ids
            assert clearing2["id"] not in account_ids
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_posted_cancel_blocked_unposted_cancel_ok():
    await _configure_settings()
    unposted = await _seed_invoice(invoice_number="FPB-UNP")
    posted = await _seed_invoice(invoice_number="FPB-PST")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            c1 = await client.post(f"/api/purchase-invoices/{unposted['id']}/cancel")
            assert c1.status_code == 200
            assert c1.json()["invoice"]["cancelled"] is True
            p = await client.post(f"/api/purchase-invoices/{posted['id']}/post-accounting")
            assert p.status_code == 200
            blocked = await client.post(f"/api/purchase-invoices/{posted['id']}/cancel")
            assert blocked.status_code == 409
            assert CANCEL_POSTED_MESSAGE in str(blocked.json()["detail"])
            still = await server.db.purchase_invoices.find_one({"id": posted["id"]}, {"_id": 0})
            assert not still.get("cancelled")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_reversal_soft_cancels_and_blocks_active_payments():
    await _configure_settings()
    inv = await _seed_invoice()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            posted = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200
            short = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "ab"},
            )
            assert short.status_code == 400
            await server.db.purchase_payments.insert_one({
                "id": str(uuid.uuid4()),
                "purchase_invoice_id": inv["id"],
                "payment_number": "PPB-DRAFT",
                "amount": 1_000_000,
                "settlement_status": "UNPAID",
                "submission_status": "NOT_SUBMITTED",
                "cancelled": False,
            })
            blocked = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert blocked.status_code == 409
            await server.db.purchase_payments.update_many(
                {"purchase_invoice_id": inv["id"]},
                {"$set": {"cancelled": True}},
            )
            ok = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()["invoice"]
            assert body["accounting_status"] == "reversed"
            assert body["cancelled"] is True
            stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["invoice_number"] == inv["invoice_number"]
            assert stored.get("accounting_reversal_reason") == "koreksi"
            assert stored.get("accounting_reversal_journal_id")
            retry = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi ulang"},
            )
            assert retry.status_code == 200
            assert retry.json()["invoice"]["accounting_status"] == "reversed"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_period_lock_blocks_post_and_reverse_then_reopen_works():
    await _configure_settings()
    inv = await _seed_invoice(invoice_date="2026-03-15")
    _admin()
    try:
        await close_period(server.db, "2026-03", user=POST_USER, reason="aksi periode")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            blocked = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert blocked.status_code == 409
            await reopen_period(server.db, "2026-03", user=POST_USER, reason="aksi periode")
            posted = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            await close_period(server.db, "2026-03", user=POST_USER, reason="aksi periode")
            rev = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert rev.status_code == 409
            await reopen_period(server.db, "2026-03", user=POST_USER, reason="aksi periode")
            ok = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_stale_claim_and_unlocked_concurrent_post():
    await _configure_settings()
    inv = await _seed_invoice()
    assert ACCOUNTING_POST_CLAIM_TTL_SECONDS == 600
    claimed = await acquire_posting_claim(server.db, inv["id"], token="stale-before-journal")
    assert claimed[CLAIM_FIELD] == "stale-before-journal"
    assert await _source_journals(inv["id"]) == []
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    journals = await _source_journals(inv["id"])
    assert len(journals) == 1
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not posting_claim_is_active(stored)

    stale_at = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {CLAIM_FIELD: "old", CLAIM_AT_FIELD: stale_at}},
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert posting_claim_is_active(hanging) is False
    retry = await _unlocked_post(inv["id"])
    assert retry["accounting_journal_id"] == journals[0]["id"]

    inv2 = await _seed_invoice(invoice_number="FPB-UL")
    results = await asyncio.gather(_unlocked_post(inv2["id"]), _unlocked_post(inv2["id"]))
    assert all(not isinstance(r, HTTPException) for r in results)
    assert len(await _source_journals(inv2["id"])) == 1


@pytest.mark.anyio
async def test_get_is_zero_write():
    await _configure_settings()
    inv = await _seed_invoice()
    _admin()
    before = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            get1 = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert get1.status_code == 200
            assert get1.json()["invoice"]["accounting_status"] == "not_posted"
            assert get1.json()["invoice"]["can_post_accounting"] is True
        assert await server.db.journal_entries.count_documents({}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_post_claim_wins_blocks_cancel_and_posts_once():
    await _configure_settings()
    inv = await _seed_invoice()
    claimed = await acquire_posting_claim(server.db, inv["id"], token="fresh-post")
    assert claimed[CLAIM_FIELD] == "fresh-post"
    blocked = await _unlocked_cancel(inv["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert CANCEL_CLAIM_MESSAGE in str(blocked.detail)
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    assert posted["accounting_status"] == "posted"
    assert not posted.get("cancelled")
    assert len(await _source_journals(inv["id"])) == 1


@pytest.mark.anyio
async def test_cancel_wins_blocks_post_and_writes_no_journal():
    await _configure_settings()
    inv = await _seed_invoice()
    cancelled = await _unlocked_cancel(inv["id"])
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    posted = await _unlocked_post(inv["id"])
    assert isinstance(posted, HTTPException)
    assert posted.status_code == 409
    assert await _source_journals(inv["id"]) == []
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert stored.get("cancelled") is True


@pytest.mark.anyio
async def test_cancel_vs_post_concurrent_one_clean_winner():
    await _configure_settings()
    inv = await _seed_invoice()
    cancel_res, post_res = await asyncio.gather(_unlocked_cancel(inv["id"]), _unlocked_post(inv["id"]))
    journals = await _source_journals(inv["id"])
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    post_ok = not isinstance(post_res, HTTPException)
    cancel_ok = not isinstance(cancel_res, HTTPException)
    if post_ok:
        assert len(journals) == 1
        assert not stored.get("cancelled")
        if isinstance(cancel_res, HTTPException):
            assert cancel_res.status_code == 409
    else:
        assert cancel_ok
        assert stored.get("cancelled") is True
        assert journals == []
        assert post_res.status_code == 409
    assert not (stored.get("cancelled") and journals)


@pytest.mark.anyio
async def test_stale_posting_claim_allows_cancel_when_no_journal():
    await _configure_settings()
    inv = await _seed_invoice()
    await acquire_posting_claim(server.db, inv["id"], token="stale-post")
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {CLAIM_FIELD: "stale-post", CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert posting_claim_is_active(hanging) is False
    cancelled = await _unlocked_cancel(inv["id"])
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    assert await _source_journals(inv["id"]) == []


@pytest.mark.anyio
async def test_recheck_before_journal_write_aborts_if_invoice_cancelled(monkeypatch):
    await _configure_settings()
    inv = await _seed_invoice()
    import services.purchase_invoice_posting as pip

    orig = pip.acquire_posting_claim

    async def cancel_after_claim(db, invoice_id, *, token=None):
        claimed = await orig(db, invoice_id, token=token)
        await db.purchase_invoices.update_one(
            {"id": invoice_id},
            {"$set": {"cancelled": True, "cancelled_at": "2026-09-01T00:00:00Z", "cancel_reason": "race"}},
        )
        return claimed

    monkeypatch.setattr(pip, "acquire_posting_claim", cancel_after_claim)
    result = await _unlocked_post(inv["id"])
    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    assert await _source_journals(inv["id"]) == []
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not posting_claim_is_active(stored)


@pytest.mark.anyio
async def test_payment_create_wins_blocks_invoice_reverse():
    await _configure_settings()
    inv = await _seed_invoice(purchase_order_id="po-pay-win", po_number="PO-PAY-WIN")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            posted = await client.post(f"/api/purchase-invoices/{inv['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            pay = await create_purchase_payment(
                server.db,
                {"purchase_invoice_id": inv["id"], "amount": 1_000_000, "payment_date": "2026-09-10"},
                POST_USER,
            )
            assert pay["id"]
            blocked = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert blocked.status_code == 409
            journals = await _source_journals(inv["id"])
            assert len(journals) == 1
            assert journals[0]["status"] == "posted"
            stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert not stored.get("cancelled")
            active = await server.db.purchase_payments.count_documents(
                {"purchase_invoice_id": inv["id"], "cancelled": {"$ne": True}}
            )
            assert active == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_reverse_claim_wins_blocks_payment_create():
    await _configure_settings()
    inv = await _seed_invoice(purchase_order_id="po-rev-win", po_number="PO-REV-WIN")
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    claimed = await acquire_reverse_claim(server.db, inv["id"], token="fresh-reverse")
    assert claimed is not None
    with pytest.raises(HTTPException) as exc:
        await create_purchase_payment(
            server.db,
            {"purchase_invoice_id": inv["id"], "amount": 1_000_000, "payment_date": "2026-09-10"},
            POST_USER,
        )
    assert exc.value.status_code == 409
    reversed_inv = await _unlocked_reverse(inv["id"])
    assert not isinstance(reversed_inv, HTTPException)
    assert reversed_inv["cancelled"] is True
    assert reversed_inv["accounting_status"] == "reversed"
    active = await server.db.purchase_payments.count_documents(
        {"purchase_invoice_id": inv["id"], "cancelled": {"$ne": True}}
    )
    assert active == 0
    source = await _source_journals(inv["id"])
    assert len(source) == 1
    assert source[0]["status"] == "reversed"


@pytest.mark.anyio
async def test_payment_creation_stale_claim_recoverable():
    await _configure_settings()
    inv = await _seed_invoice(purchase_order_id="po-stale-pay", po_number="PO-STALE-PAY")
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    await acquire_payment_create_claim(server.db, inv["id"], token="stale-create")
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {PAYMENT_CREATE_CLAIM_FIELD: "stale-create", PAYMENT_CREATE_CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert PAYMENT_CREATE_CLAIM_TTL_SECONDS == 600
    assert payment_create_claim_is_active(hanging) is False
    pay = await create_purchase_payment(
        server.db,
        {"purchase_invoice_id": inv["id"], "amount": 500_000, "payment_date": "2026-09-10"},
        POST_USER,
    )
    assert pay["id"]
    await server.db.purchase_payments.update_one({"id": pay["id"]}, {"$set": {"cancelled": True}})
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {PAYMENT_CREATE_CLAIM_FIELD: "stale-again", PAYMENT_CREATE_CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    reversed_inv = await _unlocked_reverse(inv["id"])
    assert not isinstance(reversed_inv, HTTPException)
    assert reversed_inv["cancelled"] is True
    assert reversed_inv["accounting_status"] == "reversed"


@pytest.mark.anyio
async def test_invoice_retry_after_period_close_returns_existing_journal():
    await _configure_settings()
    inv = await _seed_invoice(invoice_date="2026-05-10")
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    jid = posted["accounting_journal_id"]
    await close_period(server.db, "2026-05", user=POST_USER, reason="aksi periode")
    retry = await _unlocked_post(inv["id"])
    assert not isinstance(retry, HTTPException)
    assert retry["accounting_journal_id"] == jid
    assert len(await _source_journals(inv["id"])) == 1
    other = await _seed_invoice(invoice_date="2026-05-11", invoice_number="FPB-NEW-CLOSED")
    blocked = await _unlocked_post(other["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert await _source_journals(other["id"]) == []


@pytest.mark.anyio
async def test_invoice_reversal_crash_heals_on_retry():
    await _configure_settings()
    inv = await _seed_invoice()
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    source = (await _source_journals(inv["id"]))[0]
    await reverse_journal(
        server.db,
        source["id"],
        source_type=SOURCE_REVERSAL,
        source_id=inv["id"],
        posted_by=POST_USER,
        description="crash before invoice finalize",
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not hanging.get("cancelled")
    assert not hanging.get("accounting_reversed_at")
    healed = await _unlocked_reverse(inv["id"], "pulihkan")
    assert not isinstance(healed, HTTPException)
    assert healed["cancelled"] is True
    assert healed["accounting_status"] == "reversed"
    reversals = await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_REVERSAL, "source_id": inv["id"]},
        {"_id": 0},
    ).to_list(10)
    assert len(reversals) == 1
    source_after = await _source_journals(inv["id"])
    assert source_after[0]["status"] == "reversed"
    from services.accounts_payable import get_accounts_payable_aging

    aging = await get_accounts_payable_aging(server.db)
    row = next((r for r in aging["items"] if r["invoice_id"] == inv["id"]), None)
    assert row is None or row["accounting_outstanding"] == 0


@pytest.mark.anyio
async def test_invoice_reversal_retry_after_period_close_is_idempotent():
    await _configure_settings()
    inv = await _seed_invoice(invoice_date="2026-07-08")
    posted = await _unlocked_post(inv["id"])
    assert not isinstance(posted, HTTPException)
    reversed_inv = await _unlocked_reverse(inv["id"])
    assert not isinstance(reversed_inv, HTTPException)
    await close_period(server.db, "2026-07", user=POST_USER, reason="aksi periode")
    retry = await _unlocked_reverse(inv["id"], "ulangi")
    assert not isinstance(retry, HTTPException)
    assert retry["accounting_status"] == "reversed"
    reversals = await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_REVERSAL, "source_id": inv["id"]},
        {"_id": 0},
    ).to_list(10)
    assert len(reversals) == 1


def _active_payment_count(invoice_id: str):
    return server.db.purchase_payments.count_documents(
        {"purchase_invoice_id": invoice_id, "cancelled": {"$ne": True}}
    )


@pytest.mark.anyio
async def test_invoice_cancel_claim_wins_blocks_payment_create():
    inv = await _seed_invoice(purchase_order_id="po-cwin", po_number="PO-CWIN")
    claimed = await acquire_invoice_cancel_claim(server.db, inv["id"], token="cancel-win")
    assert claimed is not None
    assert claimed[INVOICE_CANCEL_CLAIM_FIELD] == "cancel-win"
    with pytest.raises(HTTPException) as exc:
        await create_purchase_payment(
            server.db,
            {"purchase_invoice_id": inv["id"], "amount": 1_000_000, "payment_date": "2026-09-10"},
            POST_USER,
        )
    assert exc.value.status_code == 409
    cancelled = await cancel_purchase_invoice(
        server.db, inv["id"], POST_USER, reason="batal", claim_token="cancel-win"
    )
    assert cancelled["cancelled"] is True
    assert await _active_payment_count(inv["id"]) == 0
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert stored.get("cancelled") is True
    assert not invoice_cancel_claim_is_active(stored)
    assert INVOICE_CANCEL_CLAIM_TTL_SECONDS == 600


@pytest.mark.anyio
async def test_payment_create_claim_wins_blocks_invoice_cancel():
    inv = await _seed_invoice(purchase_order_id="po-pwin", po_number="PO-PWIN")
    claimed = await acquire_payment_create_claim(server.db, inv["id"], token="pay-win")
    assert claimed is not None
    blocked = await _unlocked_cancel(inv["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    pay = await create_purchase_payment(
        server.db,
        {
            "purchase_invoice_id": inv["id"],
            "amount": 1_000_000,
            "payment_date": "2026-09-10",
            "idempotency_key": "pay-win",
        },
        POST_USER,
    )
    assert pay["id"]
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    assert await _active_payment_count(inv["id"]) == 1


@pytest.mark.anyio
async def test_cancel_release_window_does_not_use_stale_payment_count(monkeypatch):
    inv = await _seed_invoice(purchase_order_id="po-rw", po_number="PO-RW")
    import services.purchase_invoice_posting as pip

    orig = pip.acquire_invoice_cancel_claim

    async def payment_then_acquire(db, invoice_id, *, token=None):
        await db.purchase_payments.insert_one({
            "id": str(uuid.uuid4()),
            "payment_number": f"PPB-{uuid.uuid4().hex[:8].upper()}",
            "purchase_invoice_id": invoice_id,
            "amount": 1_000_000,
            "settlement_status": "UNPAID",
            "submission_status": "NOT_SUBMITTED",
            "cancelled": False,
        })
        return await orig(db, invoice_id, token=token)

    monkeypatch.setattr(pip, "acquire_invoice_cancel_claim", payment_then_acquire)
    result = await _unlocked_cancel(inv["id"])
    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    assert await _active_payment_count(inv["id"]) == 1


@pytest.mark.anyio
async def test_stale_invoice_cancel_claim_allows_payment_create():
    inv = await _seed_invoice(purchase_order_id="po-stale-c", po_number="PO-STALE-C")
    await acquire_invoice_cancel_claim(server.db, inv["id"], token="stale-cancel")
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {INVOICE_CANCEL_CLAIM_FIELD: "stale-cancel", INVOICE_CANCEL_CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert invoice_cancel_claim_is_active(hanging) is False
    pay = await create_purchase_payment(
        server.db,
        {"purchase_invoice_id": inv["id"], "amount": 1_000_000, "payment_date": "2026-09-10"},
        POST_USER,
    )
    assert pay["id"]
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    assert await _active_payment_count(inv["id"]) == 1


@pytest.mark.anyio
async def test_stale_payment_creation_claim_allows_invoice_cancel():
    inv = await _seed_invoice(purchase_order_id="po-stale-p", po_number="PO-STALE-P")
    await acquire_payment_create_claim(server.db, inv["id"], token="stale-pay-create")
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {
            PAYMENT_CREATE_CLAIM_FIELD: "stale-pay-create",
            PAYMENT_CREATE_CLAIM_AT_FIELD: _stale_claim_at(),
        }},
    )
    hanging = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert payment_create_claim_is_active(hanging) is False
    assert await _active_payment_count(inv["id"]) == 0
    cancelled = await _unlocked_cancel(inv["id"])
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    assert await _active_payment_count(inv["id"]) == 0


@pytest.mark.anyio
async def test_idempotent_cancel_does_not_require_new_claim():
    inv = await _seed_invoice()
    first = await _unlocked_cancel(inv["id"])
    assert not isinstance(first, HTTPException)
    assert first["cancelled"] is True
    await server.db.purchase_invoices.update_one(
        {"id": inv["id"]},
        {"$unset": {INVOICE_CANCEL_CLAIM_FIELD: "", INVOICE_CANCEL_CLAIM_AT_FIELD: ""}},
    )
    second = await _unlocked_cancel(inv["id"])
    assert not isinstance(second, HTTPException)
    assert second["cancelled"] is True
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert stored.get("cancelled") is True
    assert not stored.get(INVOICE_CANCEL_CLAIM_FIELD)
    assert await _active_payment_count(inv["id"]) == 0
