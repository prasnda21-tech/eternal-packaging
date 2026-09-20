"""Purchase payment explicit accounting posting — Phase 4A."""
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
os.environ["DB_NAME"] = f"eternal_packaging_purchase_payment_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods, reopen_period
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.cash_bank_mutasi import get_mutasi
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    ensure_indexes as ensure_journal,
    post_balanced_journal,
    reverse_journal,
)
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_invoice_posting import (
    SOURCE_TYPE as INV_SOURCE,
    acquire_invoice_cancel_claim,
    acquire_reverse_claim,
)
from services.purchase_payment_posting import (
    ACCOUNTING_POST_CLAIM_TTL_SECONDS,
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    INVOICE_AP_AMBIGUOUS_MESSAGE,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    acquire_posting_claim,
    post_purchase_payment_accounting,
    posting_claim_is_active,
    reverse_purchase_payment_accounting,
)
from services.purchase_payments import (
    SETTLEMENT_PAID,
    SUBMISSION_SUBMITTED,
    create_and_submit_purchase_payment,
)

POST_USER = {
    "id": "admin-pay-posting",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Pay",
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
    await ensure_cb(server.db)
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


async def _cash_bank(name: str = "BCA Operasional", *, active: bool = True) -> dict:
    bank_coa = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank_coa["id"], "active": active},
        user=POST_USER,
    )


async def _seed_invoice(**extra) -> dict:
    inv_id = extra.pop("id", str(uuid.uuid4()))
    doc = {
        "id": inv_id,
        "invoice_number": extra.pop("invoice_number", f"FPB-{uuid.uuid4().hex[:8].upper()}"),
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "supplier": extra.pop("supplier", "PT ABC"),
        "grand_total": extra.pop("grand_total", 10_000_000),
        "cancelled": False,
        "created_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.purchase_invoices.delete_many({"id": inv_id})
    await server.db.purchase_invoices.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _seed_payment(invoice: dict, **extra) -> dict:
    pay_id = extra.pop("id", str(uuid.uuid4()))
    doc = {
        "id": pay_id,
        "payment_number": extra.pop("payment_number", f"PPB-{uuid.uuid4().hex[:8].upper()}"),
        "purchase_invoice_id": invoice["id"],
        "invoice_number": invoice.get("invoice_number"),
        "supplier": invoice.get("supplier"),
        "amount": extra.pop("amount", 4_000_000),
        "payment_date": extra.pop("payment_date", "2026-09-10"),
        "settlement_status": extra.pop("settlement_status", SETTLEMENT_PAID),
        "submission_status": extra.pop("submission_status", SUBMISSION_SUBMITTED),
        "cancelled": extra.pop("cancelled", False),
        "created_at": "2026-09-10T00:00:00Z",
    }
    doc.update(extra)
    await server.db.purchase_payments.delete_many({"id": pay_id})
    await server.db.purchase_payments.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _post_invoice(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/purchase-invoices/{invoice_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _source_journals(payment_id: str):
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_TYPE, "source_id": payment_id},
        {"_id": 0},
    ).to_list(20)


async def _unlocked_post(payment_id: str, cash_bank_id: str):
    try:
        return await post_purchase_payment_accounting(
            server.db, payment_id, POST_USER, cash_bank_id=cash_bank_id, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_reverse_payment(payment_id: str, reason: str = "koreksi"):
    try:
        return await reverse_purchase_payment_accounting(
            server.db, payment_id, POST_USER, reason, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


@pytest.mark.anyio
async def test_payment_requires_paid_invoice_posted_and_cash_bank():
    payable, _clearing = await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    unpaid = await _seed_payment(inv, settlement_status="UNPAID", payment_number="PPB-UNPAID")
    paid = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            not_paid = await client.post(
                f"/api/purchase-payments/{unpaid['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert not_paid.status_code == 409
            no_inv = await client.post(
                f"/api/purchase-payments/{paid['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert no_inv.status_code == 409
            await _post_invoice(client, inv["id"])
            missing_bank = await client.post(f"/api/purchase-payments/{paid['id']}/post-accounting", json={})
            assert missing_bank.status_code == 400
            ok = await client.post(
                f"/api/purchase-payments/{paid['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()["payment"]
            assert body["accounting_status"] == "posted"
            assert body["cash_bank_id"] == bank["id"]
            assert body["cash_bank_name"]
            assert body["cash_bank_account_id"]
            journals = await _source_journals(paid["id"])
            assert len(journals) == 1
            assert journals[0]["date"] == "2026-09-10"
            lines = await server.db[JOURNAL_LINES].find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
            by_acc = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_acc[payable["id"]]["debit"])) == Decimal("4000000.00")
            assert Decimal(str(by_acc[bank["account_id"]]["credit"])) == Decimal("4000000.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_payment_debits_invoice_journal_ap_not_current_settings():
    payable_a, clearing = await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            payable_b, _ = await _configure_settings(clearing=clearing)
            assert payable_b["id"] != payable_a["id"]
            ok = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert ok.status_code == 200, ok.text
            jid = ok.json()["payment"]["accounting_journal_id"]
            lines = await server.db[JOURNAL_LINES].find({"journal_id": jid}, {"_id": 0}).to_list(10)
            account_ids = {ln["account_id"] for ln in lines}
            assert payable_a["id"] in account_ids
            assert payable_b["id"] not in account_ids
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_payment_reversal_soft_cancels_and_restores_outstanding():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice(grand_total=10_000_000)
    pay = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            posted = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert posted.status_code == 200
            blocked = await client.post(f"/api/purchase-payments/{pay['id']}/cancel")
            assert blocked.status_code == 409
            after_pay = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert after_pay.json()["invoice"]["paid_total"] == 4_000_000
            assert after_pay.json()["invoice"]["outstanding_amount"] == 6_000_000
            short = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "ab"},
            )
            assert short.status_code == 400
            rev = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi bayar"},
            )
            assert rev.status_code == 200, rev.text
            body = rev.json()["payment"]
            assert body["accounting_status"] == "reversed"
            assert body["cancelled"] is True
            stored = await server.db.purchase_payments.find_one({"id": pay["id"]}, {"_id": 0})
            assert stored["payment_number"] == pay["payment_number"]
            assert stored.get("accounting_reversal_reason") == "koreksi bayar"
            invoice = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert invoice.json()["invoice"]["paid_total"] == 0
            assert invoice.json()["invoice"]["outstanding_amount"] == 10_000_000
            retry = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi bayar"},
            )
            assert retry.status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_period_lock_blocks_payment_post_and_reverse():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, payment_date="2026-04-12")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            await close_period(server.db, "2026-04", user=POST_USER, reason="aksi periode")
            blocked = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert blocked.status_code == 409
            await reopen_period(server.db, "2026-04", user=POST_USER, reason="aksi periode")
            posted = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert posted.status_code == 200, posted.text
            await close_period(server.db, "2026-04", user=POST_USER, reason="aksi periode")
            rev = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert rev.status_code == 409
            await reopen_period(server.db, "2026-04", user=POST_USER, reason="aksi periode")
            ok = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrent_payment_post_and_stale_claim():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            results = await asyncio.gather(
                client.post(f"/api/purchase-payments/{pay['id']}/post-accounting", json={"cash_bank_id": bank["id"]}),
                client.post(f"/api/purchase-payments/{pay['id']}/post-accounting", json={"cash_bank_id": bank["id"]}),
            )
            assert all(r.status_code == 200 for r in results)
            assert len(await _source_journals(pay["id"])) == 1

            pay2 = await _seed_payment(inv, payment_number="PPB-UL", amount=1_000_000)
            await acquire_posting_claim(server.db, pay2["id"], token="stale-pay")
            hanging = await server.db.purchase_payments.find_one({"id": pay2["id"]}, {"_id": 0})
            assert hanging[CLAIM_FIELD] == "stale-pay"
            posted = await _unlocked_post(pay2["id"], bank["id"])
            assert not isinstance(posted, HTTPException)
            assert len(await _source_journals(pay2["id"])) == 1
            stored = await server.db.purchase_payments.find_one({"id": pay2["id"]}, {"_id": 0})
            assert not posting_claim_is_active(stored)

            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
            await server.db.purchase_payments.update_one(
                {"id": pay2["id"]},
                {"$set": {CLAIM_FIELD: "old", CLAIM_AT_FIELD: stale_at}},
            )
            hanging2 = await server.db.purchase_payments.find_one({"id": pay2["id"]}, {"_id": 0})
            assert ACCOUNTING_POST_CLAIM_TTL_SECONDS == 600
            assert posting_claim_is_active(hanging2) is False
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_reverse_blocked_while_payment_active_no_cascade():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            blocked = await client.post(
                f"/api/purchase-invoices/{inv['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert blocked.status_code == 409
            inv_j = await server.db[JOURNAL_ENTRIES].find_one(
                {"source_type": INV_SOURCE, "source_id": inv["id"]},
                {"_id": 0},
            )
            assert inv_j["status"] == "posted"
            pay_j = await _source_journals(pay["id"])
            assert pay_j[0]["status"] == "posted"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_confirm_paid_does_not_auto_post():
    await _configure_settings()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, settlement_status="UNPAID", submission_status=SUBMISSION_SUBMITTED)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            before = await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE})
            res = await client.post(f"/api/purchase-payments/{pay['id']}/confirm-paid")
            assert res.status_code == 200, res.text
            assert res.json()["payment"]["settlement_status"] == SETTLEMENT_PAID
            assert res.json()["payment"]["accounting_status"] == "not_posted"
            assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_multi_ap_invoice_blocks_payment_writer():
    payable_a = await _coa("21", "AP A", "APAY")
    payable_b = await _coa("21", "AP B", "APAY")
    clearing = await _coa("21", "Clear", "OCLY")
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, amount=3_000_000)
    await post_balanced_journal(
        server.db,
        date="2026-09-01",
        source_type=INV_SOURCE,
        source_id=inv["id"],
        description="split AP",
        lines=[
            {"account_id": clearing["id"], "debit": Decimal("10000000.00"), "credit": Decimal("0.00"), "description": "clear"},
            {"account_id": payable_a["id"], "debit": Decimal("0.00"), "credit": Decimal("6000000.00"), "description": "A"},
            {"account_id": payable_b["id"], "debit": Decimal("0.00"), "credit": Decimal("4000000.00"), "description": "B"},
        ],
        posted_by=POST_USER,
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            blocked = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert blocked.status_code in {400, 409}
            assert INVOICE_AP_AMBIGUOUS_MESSAGE in str(blocked.json()["detail"])
            assert await _source_journals(pay["id"]) == []
            lines = await server.db[JOURNAL_LINES].find(
                {"journal_id": {"$in": [j["id"] for j in await _source_journals(pay["id"])]}},
                {"_id": 0},
            ).to_list(10)
            assert lines == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_same_ap_account_split_lines_allowed_for_payment_writer():
    payable, clearing = await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, amount=3_000_000)
    await post_balanced_journal(
        server.db,
        date="2026-09-01",
        source_type=INV_SOURCE,
        source_id=inv["id"],
        description="same AP split",
        lines=[
            {"account_id": clearing["id"], "debit": Decimal("10000000.00"), "credit": Decimal("0.00"), "description": "clear"},
            {"account_id": payable["id"], "debit": Decimal("0.00"), "credit": Decimal("6000000.00"), "description": "A1"},
            {"account_id": payable["id"], "debit": Decimal("0.00"), "credit": Decimal("4000000.00"), "description": "A2"},
        ],
        posted_by=POST_USER,
    )
    posted = await _unlocked_post(pay["id"], bank["id"])
    assert not isinstance(posted, HTTPException)
    journals = await _source_journals(pay["id"])
    assert len(journals) == 1
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[payable["id"]]["debit"])) == Decimal("3000000.00")
    assert payable["id"] in by_acc


@pytest.mark.anyio
async def test_payment_retry_after_period_close_returns_existing_journal():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, payment_date="2026-06-08")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            first = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert first.status_code == 200, first.text
            jid = first.json()["payment"]["accounting_journal_id"]
            await close_period(server.db, "2026-06", user=POST_USER, reason="aksi periode")
            retry = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert retry.status_code == 200, retry.text
            assert retry.json()["payment"]["accounting_journal_id"] == jid
            assert len(await _source_journals(pay["id"])) == 1
            other = await _seed_payment(inv, payment_number="PPB-NEW-CLOSED", payment_date="2026-06-09", amount=1_000_000)
            blocked = await client.post(
                f"/api/purchase-payments/{other['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert blocked.status_code == 409
            assert await _source_journals(other["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_payment_reversal_crash_heals_on_retry():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice(grand_total=10_000_000)
    pay = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            posted = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert posted.status_code == 200, posted.text
            source = (await _source_journals(pay["id"]))[0]
            await reverse_journal(
                server.db,
                source["id"],
                source_type=SOURCE_REVERSAL,
                source_id=pay["id"],
                posted_by=POST_USER,
                description="crash before payment finalize",
            )
            hanging = await server.db.purchase_payments.find_one({"id": pay["id"]}, {"_id": 0})
            assert not hanging.get("cancelled")
            invoice_before = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert invoice_before.json()["invoice"]["paid_total"] == 4_000_000
            healed = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "pulihkan"},
            )
            assert healed.status_code == 200, healed.text
            body = healed.json()["payment"]
            assert body["cancelled"] is True
            assert body["accounting_status"] == "reversed"
            reversals = await server.db[JOURNAL_ENTRIES].find(
                {"source_type": SOURCE_REVERSAL, "source_id": pay["id"]},
                {"_id": 0},
            ).to_list(10)
            assert len(reversals) == 1
            invoice_after = await client.get(f"/api/purchase-invoices/{inv['id']}")
            assert invoice_after.json()["invoice"]["paid_total"] == 0
            assert invoice_after.json()["invoice"]["outstanding_amount"] == 10_000_000
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_bank_balance_decreases_on_post_and_restores_on_reverse():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            before = await get_mutasi(
                server.db, cash_bank_id=bank["id"], start_date="2026-09-01", end_date="2026-09-30"
            )
            assert before["ending_balance"] == 0
            posted = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert posted.status_code == 200, posted.text
            after = await get_mutasi(
                server.db, cash_bank_id=bank["id"], start_date="2026-09-01", end_date="2026-09-30"
            )
            assert after["ending_balance"] == before["ending_balance"] - 4_000_000
            labels = {row["source_label"] for row in after["items"]}
            assert "Pembayaran Pembelian" in labels
            assert await server.db.cash_bank_payments.count_documents({}) == 0
            rev = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi bank"},
            )
            assert rev.status_code == 200, rev.text
            restored = await get_mutasi(
                server.db, cash_bank_id=bank["id"], start_date="2026-09-01", end_date="2026-09-30"
            )
            assert restored["ending_balance"] == before["ending_balance"]
            rev_labels = {row["source_label"] for row in restored["items"]}
            assert "Reversal Pembayaran Pembelian" in rev_labels
            assert await server.db.cash_bank_payments.count_documents({}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_create_and_submit_blocked_by_fresh_invoice_reverse_claim():
    from services.purchase_invoice_posting import post_purchase_invoice_accounting

    await _configure_settings()
    inv = await _seed_invoice(purchase_order_id="po-cas", po_number="PO-CAS")
    result = await post_purchase_invoice_accounting(
        server.db, inv["id"], POST_USER, serialize_in_process=False
    )
    assert result["accounting_status"] == "posted"
    claimed = await acquire_reverse_claim(server.db, inv["id"], token="fresh-rev-cas")
    assert claimed is not None
    await server.db.users.insert_one({
        "id": "target-cas",
        "status": "active",
        "name": "Target",
        "email": "target-cas@example.com",
        "role": "admin",
    })
    with pytest.raises(HTTPException) as exc:
        await create_and_submit_purchase_payment(
            server.db,
            {
                "purchase_invoice_id": inv["id"],
                "amount": 1_000_000,
                "payment_date": "2026-09-10",
                "target_user_id": "target-cas",
            },
            POST_USER,
        )
    assert exc.value.status_code == 409
    active = await server.db.purchase_payments.count_documents(
        {"purchase_invoice_id": inv["id"], "cancelled": {"$ne": True}}
    )
    assert active == 0


@pytest.mark.anyio
async def test_create_and_submit_blocked_by_fresh_invoice_cancel_claim():
    inv = await _seed_invoice(purchase_order_id="po-cas-c", po_number="PO-CAS-C")
    claimed = await acquire_invoice_cancel_claim(server.db, inv["id"], token="fresh-cancel-cas")
    assert claimed is not None
    await server.db.users.insert_one({
        "id": "target-cas-c",
        "status": "active",
        "name": "Target C",
        "email": "target-cas-c@example.com",
        "role": "admin",
    })
    with pytest.raises(HTTPException) as exc:
        await create_and_submit_purchase_payment(
            server.db,
            {
                "purchase_invoice_id": inv["id"],
                "amount": 1_000_000,
                "payment_date": "2026-09-10",
                "target_user_id": "target-cas-c",
            },
            POST_USER,
        )
    assert exc.value.status_code == 409
    stored = await server.db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    active = await server.db.purchase_payments.count_documents(
        {"purchase_invoice_id": inv["id"], "cancelled": {"$ne": True}}
    )
    assert active == 0


@pytest.mark.anyio
async def test_payment_reversal_retry_after_period_close_is_idempotent():
    await _configure_settings()
    bank = await _cash_bank()
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, payment_date="2026-08-08")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            posted = await client.post(
                f"/api/purchase-payments/{pay['id']}/post-accounting",
                json={"cash_bank_id": bank["id"]},
            )
            assert posted.status_code == 200, posted.text
            rev = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "koreksi"},
            )
            assert rev.status_code == 200, rev.text
            await close_period(server.db, "2026-08", user=POST_USER, reason="aksi periode")
            retry = await client.post(
                f"/api/purchase-payments/{pay['id']}/reverse-accounting",
                json={"reason": "ulangi"},
            )
            assert retry.status_code == 200, retry.text
            reversals = await server.db[JOURNAL_ENTRIES].find(
                {"source_type": SOURCE_REVERSAL, "source_id": pay["id"]},
                {"_id": 0},
            ).to_list(10)
            assert len(reversals) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
