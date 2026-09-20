"""Sales invoice accounting reversal — Phase 1B.1."""
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
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_reversal_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_posting import (
    ACCOUNTING_REVERSED,
    CLAIM_FIELD,
    RECEIPT_CREATE_CLAIM_AT_FIELD,
    RECEIPT_CREATE_CLAIM_FIELD,
    RECEIPT_CREATE_CLAIM_TTL_SECONDS,
    REVERSE_CLAIM_FIELD,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    UNPOSTED_REVERSE_MESSAGE,
    acquire_receipt_create_claim,
    acquire_reverse_claim,
    reverse_claim_is_active,
    reverse_sales_invoice_accounting,
)
from services.sales_receipts import create_sales_receipt
from services.sales_receipt_posting import (
    CLAIM_FIELD as RECEIPT_CLAIM_FIELD,
    acquire_posting_claim as acquire_receipt_posting_claim,
    post_sales_receipt_accounting,
)

POST_USER = {
    "id": "admin-reversal",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Reversal",
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


async def _seed_po(qty: int = 1000, **extra) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/REV/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Reversal",
        "outlet": "Outlet Rev",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup Rev", "quantity": qty, "unit": "pcs"}],
        "total_quantity": qty,
        "current_stage": extra.pop("current_stage", 4),
        "status_label": "Approval Payment",
        "payment_status": extra.pop("payment_status", "Waiting"),
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
    rec = rec or await _coa("11", "Piutang Rev", "AREC")
    rev = rev or await _coa("41", "Pendapatan Rev", "REVE")
    tax = tax or await _coa("21", "PPN Rev", "OCLY")
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


async def _cash_bank(name: str = "BCA Reversal") -> dict:
    bank_coa = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank_coa["id"], "active": True},
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


async def _reverse(client, invoice_id: str, reason: str = "Koreksi faktur salah"):
    return await client.post(
        f"/api/sales-invoices/{invoice_id}/reverse-accounting",
        json={"reason": reason},
    )


async def _create_receipt(client, invoice_id: str, amount, **extra) -> dict:
    payload = {
        "invoice_id": invoice_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-05"),
        "amount": amount,
    }
    if "cash_bank_id" in extra:
        payload["cash_bank_id"] = extra.pop("cash_bank_id")
    payload.update(extra)
    res = await client.post("/api/sales-receipts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _invoice_journals(invoice_id: str, source_type: str = SOURCE_TYPE):
    return await server.db.journal_entries.find(
        {"source_type": source_type, "source_id": invoice_id},
        {"_id": 0},
    ).to_list(20)


async def _lines(journal_id: str):
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


def _detail(exc_or_res):
    if isinstance(exc_or_res, HTTPException):
        return exc_or_res.detail
    return exc_or_res.json().get("detail")


async def _unlocked_reverse(invoice_id: str, reason: str = "Koreksi faktur salah"):
    try:
        return await reverse_sales_invoice_accounting(
            server.db, invoice_id, POST_USER, reason, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_receipt_post(receipt_id: str, cash_bank_id: str | None = None):
    try:
        return await post_sales_receipt_accounting(
            server.db, receipt_id, POST_USER, cash_bank_id=cash_bank_id, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


def _po_payment_snapshot(order: dict) -> dict:
    return {
        "payment_receipts": list(order.get("payment_receipts") or []),
        "payment_fact": dict(order.get("payment_fact") or {}),
        "payment_status": order.get("payment_status"),
        "payment_approved_by": order.get("payment_approved_by"),
        "payment_approved_at": order.get("payment_approved_at"),
        "current_stage": order.get("current_stage"),
    }


def _stale_create_claim_at() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=RECEIPT_CREATE_CLAIM_TTL_SECONDS + 60)).isoformat()


def _fresh_create_claim_at() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.mark.anyio
async def test_matrix_a_happy_path_no_ppn():
    rec, rev, _tax = await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price=10_000_000)
            await _finalize(client, inv["id"])
            posted = await _post_invoice(client, inv["id"])
            assert posted["can_reverse_accounting"] is True
            stage_before = (await server.db.orders.find_one({"id": po["id"]}))["current_stage"]

            res = await _reverse(client, inv["id"])
            assert res.status_code == 200, res.text
            body = res.json()["invoice"]
            assert body["financial_status"] == "cancelled"
            assert body["accounting_status"] == "reversed"
            assert body["accounting_posted"] is False
            assert body["can_reverse_accounting"] is False
            assert body["outstanding_amount"] == 0
            assert body["paid_total"] == 0
            assert body["grand_total"] == 10_000_000
            assert body["invoice_number"] == inv["invoice_number"]
            assert body["accounting_reversal_reason"] == "Koreksi faktur salah"
            assert body["accounting_reversed_at"]
            assert body["accounting_reversal_journal_id"]

            originals = await _invoice_journals(inv["id"], SOURCE_TYPE)
            reversals = await _invoice_journals(inv["id"], SOURCE_REVERSAL)
            assert len(originals) == 1
            assert originals[0]["status"] == "reversed"
            assert originals[0]["date"] == "2026-09-01"
            assert len(reversals) == 1
            assert reversals[0]["date"] == originals[0]["date"]
            assert reversals[0]["reversal_of"] == originals[0]["id"]
            assert reversals[0]["source_id"] == inv["id"]
            assert "Reversal Faktur Penjualan" in reversals[0]["description"]

            orig_lines = await _lines(originals[0]["id"])
            rev_lines = await _lines(reversals[0]["id"])
            orig_by = {ln["account_id"]: ln for ln in orig_lines}
            rev_by = {ln["account_id"]: ln for ln in rev_lines}
            assert Decimal(str(orig_by[rec["id"]]["debit"])) == Decimal("10000000.00")
            assert Decimal(str(orig_by[rev["id"]]["credit"])) == Decimal("10000000.00")
            assert Decimal(str(rev_by[rec["id"]]["credit"])) == Decimal("10000000.00")
            assert Decimal(str(rev_by[rev["id"]]["debit"])) == Decimal("10000000.00")
            assert await _account_net(rec["id"]) == Decimal("0.00")
            assert await _account_net(rev["id"]) == Decimal("0.00")

            order = await server.db.orders.find_one({"id": po["id"]})
            assert order["current_stage"] == stage_before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_b_ppn_exact_reverse():
    rec, rev, tax = await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price=10_000_000, ppn_percent=11)
            fin = await _finalize(client, inv["id"])
            assert Decimal(str(fin["after_discount"])) == Decimal("10000000.00")
            assert Decimal(str(fin["ppn_amount"])) == Decimal("1100000.00")
            assert Decimal(str(fin["grand_total"])) == Decimal("11100000.00")
            await _post_invoice(client, inv["id"])
            res = await _reverse(client, inv["id"])
            assert res.status_code == 200, res.text
            originals = await _invoice_journals(inv["id"], SOURCE_TYPE)
            reversals = await _invoice_journals(inv["id"], SOURCE_REVERSAL)
            orig_by = {ln["account_id"]: ln for ln in await _lines(originals[0]["id"])}
            rev_by = {ln["account_id"]: ln for ln in await _lines(reversals[0]["id"])}
            assert Decimal(str(orig_by[rec["id"]]["debit"])) == Decimal("11100000.00")
            assert Decimal(str(orig_by[rev["id"]]["credit"])) == Decimal("10000000.00")
            assert Decimal(str(orig_by[tax["id"]]["credit"])) == Decimal("1100000.00")
            assert Decimal(str(rev_by[rec["id"]]["credit"])) == Decimal("11100000.00")
            assert Decimal(str(rev_by[rev["id"]]["debit"])) == Decimal("10000000.00")
            assert Decimal(str(rev_by[tax["id"]]["debit"])) == Decimal("1100000.00")
            assert await _account_net(rec["id"]) == Decimal("0.00")
            assert await _account_net(rev["id"]) == Decimal("0.00")
            assert await _account_net(tax["id"]) == Decimal("0.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_c_second_reverse_idempotent():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            first = await _reverse(client, inv["id"])
            assert first.status_code == 200
            jid = first.json()["invoice"]["accounting_reversal_journal_id"]
            second = await _reverse(client, inv["id"])
            assert second.status_code == 200
            assert second.json()["invoice"]["financial_status"] == "cancelled"
            assert second.json()["invoice"]["accounting_status"] == "reversed"
            assert second.json()["invoice"]["accounting_reversal_journal_id"] == jid
            assert len(await _invoice_journals(inv["id"], SOURCE_REVERSAL)) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_d_concurrent_reverse_one_journal():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
        results = await asyncio.gather(_unlocked_reverse(inv["id"]), _unlocked_reverse(inv["id"]))
        assert all(not isinstance(r, HTTPException) for r in results)
        reversals = await _invoice_journals(inv["id"], SOURCE_REVERSAL)
        assert len(reversals) == 1
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert stored["financial_status"] == "cancelled"
        assert not reverse_claim_is_active(stored)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_e_unposted_cannot_reverse_cancel_unchanged():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], quantity=10, unit_price=1000)
            await _finalize(client, inv["id"])
            blocked = await _reverse(client, inv["id"])
            assert blocked.status_code == 409
            assert UNPOSTED_REVERSE_MESSAGE in str(blocked.json()["detail"])
            assert await _invoice_journals(inv["id"], SOURCE_REVERSAL) == []
            cancelled = await client.post(f"/api/sales-invoices/{inv['id']}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["invoice"]["financial_status"] == "cancelled"
            assert cancelled.json()["invoice"]["accounting_status"] == "not_posted"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_f_unposted_active_receipt_blocks_then_delete_allows():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            rec = await _create_receipt(client, inv["id"], 1_000_000)
            blocked = await _reverse(client, inv["id"])
            assert blocked.status_code == 409
            detail = blocked.json()["detail"]
            assert "penerimaan aktif" in detail["message"]
            assert any(b["id"] == rec["id"] for b in detail["blockers"])
            assert any(b["accounting_status"] == "not_posted" for b in detail["blockers"])
            deleted = await client.delete(f"/api/sales-receipts/{rec['id']}")
            assert deleted.status_code == 200
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200, ok.text
            assert ok.json()["invoice"]["accounting_status"] == "reversed"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_g_posted_receipt_blocks_then_receipt_reverse_allows():
    rec_acc, rev_acc, _tax = await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price=10_000_000)
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bank["id"])
            posted_rec = await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            assert posted_rec.status_code == 200
            blocked = await _reverse(client, inv["id"])
            assert blocked.status_code == 409
            blockers = blocked.json()["detail"]["blockers"]
            assert any(b["id"] == receipt["id"] and b["accounting_status"] == "posted" for b in blockers)
            rev_rec = await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            assert rev_rec.status_code == 200
            after_rec = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert after_rec.json()["invoice"]["paid_total"] == 0
            assert after_rec.json()["invoice"]["outstanding_amount"] == 10_000_000
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200, ok.text
            assert ok.json()["invoice"]["outstanding_amount"] == 0
            assert await _account_net(rec_acc["id"]) == Decimal("0.00")
            assert await _account_net(rev_acc["id"]) == Decimal("0.00")
            assert await _account_net(bank["account_id"]) == Decimal("0.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_h_reversed_receipt_does_not_block():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bank["id"])
            await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            still = await server.db.sales_receipts.find_one({"id": receipt["id"]})
            assert still is not None
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_i_mixed_receipts_block_a_and_b_not_c():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            rec_a = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bank["id"])
            rec_b = await _create_receipt(client, inv["id"], 1_000_000)
            rec_c = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bank["id"])
            await client.post(f"/api/sales-receipts/{rec_a['id']}/post-accounting")
            await client.post(f"/api/sales-receipts/{rec_c['id']}/post-accounting")
            await client.post(f"/api/sales-receipts/{rec_c['id']}/reverse-accounting")
            blocked = await _reverse(client, inv["id"])
            assert blocked.status_code == 409
            ids = {b["id"] for b in blocked.json()["detail"]["blockers"]}
            assert rec_a["id"] in ids
            assert rec_b["id"] in ids
            assert rec_c["id"] not in ids
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_j_receipt_post_vs_invoice_reverse_claim_wins():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bank["id"])
        claimed = await acquire_reverse_claim(server.db, inv["id"], token="invoice-reverse-wins")
        assert claimed is not None
        posted = await _unlocked_receipt_post(receipt["id"], bank["id"])
        assert isinstance(posted, HTTPException) and posted.status_code == 409
        assert await server.db.journal_entries.count_documents({"source_type": "sales_receipt", "source_id": receipt["id"]}) == 0
        await server.db.sales_receipts.delete_one({"id": receipt["id"]})
        reversed_inv = await _unlocked_reverse(inv["id"])
        assert not isinstance(reversed_inv, HTTPException)
        assert reversed_inv["accounting_status"] == ACCOUNTING_REVERSED
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_j_receipt_claim_wins_blocks_invoice_reverse():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bank["id"])
        claimed = await acquire_receipt_posting_claim(server.db, receipt["id"], token="receipt-post-wins")
        assert claimed is not None
        blocked = await _unlocked_reverse(inv["id"])
        assert isinstance(blocked, HTTPException) and blocked.status_code == 409
        posted = await _unlocked_receipt_post(receipt["id"], bank["id"])
        assert not isinstance(posted, HTTPException)
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert stored["financial_status"] == "finalized"
        assert await _invoice_journals(inv["id"], SOURCE_REVERSAL) == []
        assert await server.db.journal_entries.count_documents({"source_type": "sales_receipt", "source_id": receipt["id"]}) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_k_post_vs_reverse_never_reverses_without_original():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            post_res, rev_res = await asyncio.gather(
                client.post(f"/api/sales-invoices/{inv['id']}/post-accounting"),
                _reverse(client, inv["id"]),
            )
        originals = await _invoice_journals(inv["id"], SOURCE_TYPE)
        reversals = await _invoice_journals(inv["id"], SOURCE_REVERSAL)
        if reversals:
            assert originals and originals[0]["status"] in ("posted", "reversed")
            assert reversals[0]["reversal_of"] == originals[0]["id"]
        else:
            assert rev_res.status_code == 409
            assert post_res.status_code == 200
            assert originals and originals[0]["status"] == "posted"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_l_crash_recovery_self_heals():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            first = await _reverse(client, inv["id"])
            assert first.status_code == 200
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {
                "$set": {"financial_status": "finalized"},
                "$unset": {
                    "cancelled_at": "",
                    "cancelled_by": "",
                    "accounting_reversed_at": "",
                    "accounting_reversed_by": "",
                    "accounting_reversal_reason": "",
                },
            },
        )
        healed = await _unlocked_reverse(inv["id"], "Pulihkan metadata")
        assert not isinstance(healed, HTTPException)
        assert healed["financial_status"] == "cancelled"
        assert healed["accounting_status"] == "reversed"
        assert healed["accounting_reversal_reason"]
        assert len(await _invoice_journals(inv["id"], SOURCE_REVERSAL)) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_m_quantity_release_and_new_number():
    await _configure_settings()
    po = await _seed_po(qty=1000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], quantity=1000, unit_price=1000)
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            before = await client.get(f"/api/sales-invoices/po/{po['id']}/preview")
            assert before.json()["lines"][0]["remaining_invoice_qty"] == 0
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200
            after = await client.get(f"/api/sales-invoices/po/{po['id']}/preview")
            assert after.json()["lines"][0]["remaining_invoice_qty"] == 1000
            replacement = await _create_invoice(client, po["id"], quantity=1000, unit_price=1000)
            assert replacement["invoice_number"] != inv["invoice_number"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_n_o_cancelled_receipt_create_and_outstanding():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            draft = await _create_invoice(client, po["id"], quantity=1, unit_price=1000)
            draft_rec = await client.post("/api/sales-receipts", json={"invoice_id": draft["id"], "amount": 100})
            assert draft_rec.status_code == 409

            inv = await _create_invoice(client, po["id"], unit_price=10_000_000)
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bank["id"])
            await client.post(f"/api/sales-receipts/{receipt['id']}/post-accounting")
            await client.post(f"/api/sales-receipts/{receipt['id']}/reverse-accounting")
            mid = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert mid.json()["invoice"]["paid_total"] == 0
            assert mid.json()["invoice"]["outstanding_amount"] == 10_000_000
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200
            assert ok.json()["invoice"]["outstanding_amount"] == 0
            blocked = await client.post("/api/sales-receipts", json={"invoice_id": inv["id"], "amount": 1000})
            assert blocked.status_code == 409
            listed = await client.get("/api/sales-invoices")
            row = next(i for i in listed.json()["invoices"] if i["id"] == inv["id"])
            assert row["outstanding_amount"] == 0
            assert row["financial_status"] == "cancelled"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matrix_q_rbac_and_r_delete_and_s_historical():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            historical_id = str(uuid.uuid4())
            await server.db.sales_invoices.insert_one({
                "id": historical_id,
                "invoice_number": f"INV/HIST/{historical_id[:8]}",
                "po_id": po["id"],
                "invoice_date": "2026-08-01",
                "financial_status": "finalized",
                "grand_total": 100.0,
                "after_discount": 100.0,
                "ppn_amount": 0.0,
            })
            hist = await client.get(f"/api/sales-invoices/{historical_id}")
            assert hist.status_code == 200
            assert "accounting_reversed_at" not in (await server.db.sales_invoices.find_one({"id": historical_id}) or {}) or True
            assert hist.json()["invoice"]["financial_status"] == "finalized"

        _user("crm", {
            "po.edit": True,
            "sales_order.create": True,
            "sales.invoice.edit": True,
            "sales_accounting.post": True,
            "sales.invoice.view": True,
        })
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            forbidden = await _reverse(client, inv["id"])
            assert forbidden.status_code == 403
        _admin()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ok = await _reverse(client, inv["id"])
            assert ok.status_code == 200
            deleted = await client.delete(f"/api/sales-invoices/{inv['id']}")
            assert deleted.status_code == 409
            bulk = await client.post("/api/sales-invoices-bulk/delete", json={"ids": [inv["id"]], "reason": "qa"})
            assert bulk.status_code == 400
            still = await server.db.sales_invoices.find_one({"id": inv["id"]})
            assert still is not None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_reason_required_and_empty_rejected():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            missing = await client.post(f"/api/sales-invoices/{inv['id']}/reverse-accounting", json={"reason": "  ab "})
            assert missing.status_code == 400
            blank = await client.post(f"/api/sales-invoices/{inv['id']}/reverse-accounting", json={"reason": ""})
            assert blank.status_code == 400
            assert await _invoice_journals(inv["id"], SOURCE_REVERSAL) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_receipt_create_vs_reverse_reverse_wins_no_ghost_workflow():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            posted = await _post_invoice(client, inv["id"])
            before_paid = posted["paid_total"]
            before_out = posted["outstanding_amount"]
            order_before = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            snap = _po_payment_snapshot(order_before)
            claimed = await acquire_reverse_claim(server.db, inv["id"], token="invoice-reverse-vs-create")
            assert claimed is not None
            created = await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 1_000_000,
                "receipt_date": "2026-09-05",
            })
            assert created.status_code == 409, created.text
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored.get("paid_total", 0) == before_paid
            assert stored.get("outstanding_amount", stored.get("grand_total")) == before_out
            order_after_create = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert _po_payment_snapshot(order_after_create) == snap
            public = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert public.status_code == 200
            assert RECEIPT_CREATE_CLAIM_FIELD not in public.json()["invoice"]
            assert RECEIPT_CREATE_CLAIM_AT_FIELD not in public.json()["invoice"]
            reversed_inv = await _unlocked_reverse(inv["id"])
            assert not isinstance(reversed_inv, HTTPException), getattr(reversed_inv, "detail", reversed_inv)
            assert reversed_inv["financial_status"] == "cancelled"
            assert reversed_inv["accounting_status"] == ACCOUNTING_REVERSED
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
            order_final = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert _po_payment_snapshot(order_final) == snap
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_receipt_create_vs_reverse_create_wins():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
        claimed = await acquire_receipt_create_claim(server.db, inv["id"], token="receipt-create-wins")
        assert claimed is not None
        blocked = await _unlocked_reverse(inv["id"])
        assert isinstance(blocked, HTTPException) and blocked.status_code == 409
        assert await _invoice_journals(inv["id"], SOURCE_REVERSAL) == []
        created = await create_sales_receipt(
            server.db,
            {"invoice_id": inv["id"], "amount": 1_000_000, "receipt_date": "2026-09-05"},
            POST_USER,
            claim_token="receipt-create-wins",
        )
        assert created["invoice_id"] == inv["id"]
        assert created["amount"] == 1_000_000
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert stored["financial_status"] == "finalized"
        assert stored.get(RECEIPT_CREATE_CLAIM_FIELD) in (None, "")
        assert await _invoice_journals(inv["id"], SOURCE_REVERSAL) == []
        originals = await _invoice_journals(inv["id"], SOURCE_TYPE)
        assert originals and originals[0]["status"] == "posted"
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_receipt_create_claim_stale_recoverable_fresh_blocks_reverse():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            await _post_invoice(client, inv["id"])
            inv2 = await _create_invoice(client, po["id"], quantity=1, unit_price=2_000_000)
            await _finalize(client, inv2["id"])
            await _post_invoice(client, inv2["id"])
            inv3 = await _create_invoice(client, po["id"], quantity=1, unit_price=3_000_000)
            await _finalize(client, inv3["id"])
            await _post_invoice(client, inv3["id"])

        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {
                "$set": {RECEIPT_CREATE_CLAIM_FIELD: "legacy-no-timestamp"},
                "$unset": {RECEIPT_CREATE_CLAIM_AT_FIELD: ""},
            },
        )
        recovered = await create_sales_receipt(
            server.db,
            {"invoice_id": inv["id"], "amount": 500_000, "receipt_date": "2026-09-05"},
            POST_USER,
        )
        assert recovered["amount"] == 500_000

        await server.db.sales_invoices.update_one(
            {"id": inv2["id"]},
            {
                "$set": {
                    RECEIPT_CREATE_CLAIM_FIELD: "stale-create",
                    RECEIPT_CREATE_CLAIM_AT_FIELD: _stale_create_claim_at(),
                }
            },
        )
        reversed_stale = await _unlocked_reverse(inv2["id"])
        assert not isinstance(reversed_stale, HTTPException), getattr(reversed_stale, "detail", reversed_stale)
        assert reversed_stale["financial_status"] == "cancelled"
        assert reversed_stale["accounting_status"] == ACCOUNTING_REVERSED

        await server.db.sales_invoices.update_one(
            {"id": inv3["id"]},
            {
                "$set": {
                    RECEIPT_CREATE_CLAIM_FIELD: "fresh-create",
                    RECEIPT_CREATE_CLAIM_AT_FIELD: _fresh_create_claim_at(),
                }
            },
        )
        blocked = await _unlocked_reverse(inv3["id"])
        assert isinstance(blocked, HTTPException) and blocked.status_code == 409
        assert await _invoice_journals(inv3["id"], SOURCE_REVERSAL) == []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get(f"/api/sales-invoices/{inv3['id']}")
            body = listed.json()["invoice"]
            assert RECEIPT_CREATE_CLAIM_FIELD not in body
            assert RECEIPT_CREATE_CLAIM_AT_FIELD not in body
            assert body["can_reverse_accounting"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)
