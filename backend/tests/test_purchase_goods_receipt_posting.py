"""Purchase goods receipt explicit inventory posting — Phase 4B.2."""
from __future__ import annotations

import asyncio
import json
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
os.environ["DB_NAME"] = f"eternal_packaging_gr_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.general_ledger import SOURCE_LABELS
from services.inventory_accounting import (
    REASON_INVALID_UNIT_CONVERSION,
    REASON_NON_INVENTORY_ITEM,
    REASON_UNKNOWN_ITEM_TYPE,
    WARNING_PARTIAL_INVENTORY_COVERAGE,
)
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    ensure_indexes as ensure_journal,
    post_balanced_journal,
    reverse_journal,
)
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_goods_receipt_posting import (
    ACCOUNTING_POST_CLAIM_TTL_SECONDS,
    ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS,
    CANCEL_CLAIM_FIELD,
    CANCEL_CLAIM_MESSAGE,
    CANCEL_IN_PROGRESS_MESSAGE,
    CANCEL_POSTED_MESSAGE,
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    NO_ELIGIBLE_LINES_MESSAGE,
    PERM_POST,
    PERM_REVERSE,
    POSTING_BASIS_FIELD,
    POSTING_CLAIM_REVERSE_MESSAGE,
    RECEIPT_CANCEL_CLAIM_TTL_SECONDS,
    RECOVERY_REQUIRED_CODE,
    REVERSE_CLAIM_AT_FIELD,
    REVERSE_CLAIM_FIELD,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    STOCK_OPNAME_CODE,
    acquire_posting_claim,
    acquire_receipt_cancel_claim,
    acquire_reverse_claim,
    cancel_claim_is_active,
    post_purchase_goods_receipt_accounting,
    posting_claim_is_active,
    reverse_claim_is_active,
    reverse_purchase_goods_receipt_accounting,
)
from services.purchase_goods_receipts import cancel_purchase_goods_receipt
from services.stock_ledger import MOVEMENT_KOREKSI, MOVEMENT_PENERIMAAN, MOVEMENT_REVERSAL_PENERIMAAN

POST_USER = {
    "id": "admin-gr-posting",
    "email": "admin@eternalpackaging.com",
    "name": "Admin GR Posting",
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
    await ensure_periods(server.db)
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, "physical_stock"]}})
    await server.db.accounting_periods.delete_many({})
    await server.db.purchase_orders.delete_many({})
    await server.db.purchase_goods_receipts.delete_many({})
    await server.db.purchase_invoices.delete_many({})
    await server.db.master_items.delete_many({})
    await server.db.product_unit_settings.delete_many({})
    await server.db.product_stock_state.delete_many({})
    await server.db.stock_movements.delete_many({})
    await server.db.app_settings.update_one(
        {"key": "physical_stock"},
        {"$set": {"key": "physical_stock", "enabled": False, "stock_in_trigger": "goods_receipt"}},
        upsert=True,
    )
    yield
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, "physical_stock"]}})
    await server.db.accounting_periods.delete_many({})


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(POST_USER)


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_inventory(effective="2026-10-01", inventory=None, clearing=None):
    payable = await _coa("21", "Hutang Usaha", "APAY")
    clearing = clearing or await _coa("21", "Purchase Clearing", "OCLY")
    inventory = inventory or await _coa("13", "Persediaan", "INTR")
    cogs = await _coa("51", "HPP", "COGS")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "payable_account_id": payable["id"],
                "purchase_clearing_account_id": clearing["id"],
                "inventory_account_id": inventory["id"],
                "cogs_account_id": cogs["id"],
                "inventory_accounting_effective_date": effective,
            }
        },
        upsert=True,
    )
    return inventory, clearing, payable, cogs


async def _seed_master(mid: str, item_type="INVENTORY", unit="PCS"):
    await server.db.master_items.delete_many({"id": mid})
    doc = {"id": mid, "item_type": item_type, "unit": unit, "item_name": mid, "item_code": mid}
    await server.db.master_items.insert_one(doc)
    return doc


async def _seed_gr(
    *,
    qty=400,
    price=500,
    unit="PCS",
    item_type="INVENTORY",
    receipt_date="2026-10-02",
    mid=None,
    extra_lines=None,
    receipt_id=None,
):
    mid = mid or f"m-{uuid.uuid4().hex[:8]}"
    await _seed_master(mid, item_type=item_type, unit="PCS" if unit == "CARTON" else unit)
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    po_lines = [{
        "line_id": line_id,
        "ordered_qty": max(qty, 1000),
        "unit_price": price,
        "unit": unit,
        "master_item_id": mid,
    }]
    gr_lines = [{
        "line_id": f"gl-{uuid.uuid4().hex[:8]}",
        "purchase_order_line_id": line_id,
        "received_qty": qty,
        "unit": unit,
        "master_item_id": mid,
        "unit_price": price,
    }]
    if extra_lines:
        for extra in extra_lines:
            po_lines.append(extra["po"])
            gr_lines.append(extra["gr"])
    po = {
        "id": po_id,
        "po_number": f"PB-{uuid.uuid4().hex[:6].upper()}",
        "order_date": "2026-09-01",
        "cancelled": False,
        "supplier": "PT Supplier",
        "lines": po_lines,
    }
    rid = receipt_id or str(uuid.uuid4())
    gr = {
        "id": rid,
        "receipt_number": f"GR-{uuid.uuid4().hex[:6].upper()}",
        "purchase_order_id": po_id,
        "po_number": po["po_number"],
        "receipt_date": receipt_date,
        "supplier": "PT Supplier",
        "cancelled": False,
        "source": "po_linked",
        "lines": gr_lines,
        "created_at": f"{receipt_date}T00:00:00Z",
    }
    await server.db.purchase_orders.insert_one(po)
    await server.db.purchase_goods_receipts.insert_one(gr)
    gr.pop("_id", None)
    return po, gr


async def _source_journals(receipt_id: str):
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_TYPE, "source_id": receipt_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(20)


async def _reversal_journals(receipt_id: str):
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_REVERSAL, "source_id": receipt_id},
        {"_id": 0},
    ).to_list(20)


async def _unlocked_post(receipt_id: str):
    try:
        return await post_purchase_goods_receipt_accounting(
            server.db, receipt_id, POST_USER, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_reverse(receipt_id: str, reason: str = "koreksi"):
    try:
        return await reverse_purchase_goods_receipt_accounting(
            server.db, receipt_id, POST_USER, reason, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_cancel(receipt_id: str, reason: str = "batal"):
    try:
        return await cancel_purchase_goods_receipt(server.db, receipt_id, POST_USER, reason=reason)
    except HTTPException as exc:
        return exc


def _stale_claim_at() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()


async def _journal_lines(journal_id: str):
    return await server.db[JOURNAL_LINES].find({"journal_id": journal_id}, {"_id": 0}).to_list(20)


@pytest.mark.anyio
async def test_permissions_and_gl_labels_are_explicit():
    assert PERM_POST in ALL_PERMISSIONS
    assert PERM_REVERSE in ALL_PERMISSIONS
    assert PERM_POST in ROLE_DEFAULT_PERMISSIONS.get("admin", [])
    assert PERM_REVERSE in ROLE_DEFAULT_PERMISSIONS.get("admin", [])
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert PERM_POST not in perms
        assert PERM_REVERSE not in perms
    assert SOURCE_LABELS[SOURCE_TYPE] == "Penerimaan Barang"
    assert SOURCE_LABELS[SOURCE_REVERSAL] == "Reversal Penerimaan Barang"


@pytest.mark.anyio
async def test_simple_partial_receipt_journal():
    inv, clearing, _, _ = await _configure_inventory()
    _, gr = await _seed_gr(qty=400, price=500)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
            res = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert res.status_code == 200, res.text
            body = res.json()["receipt"]
            assert body["accounting_status"] == "posted"
            assert body["accounting_journal_id"]
            assert body["accounting_amount"] == 200000
            journals = await _source_journals(gr["id"])
            assert len(journals) == 1
            assert journals[0]["date"] == "2026-10-02"
            assert journals[0]["source_type"] == SOURCE_TYPE
            lines = await _journal_lines(journals[0]["id"])
            by_acc = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("200000.00")
            assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("200000.00")
            assert await server.db[JOURNAL_ENTRIES].count_documents({}) == created_journals + 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_create_receipt_does_not_auto_post():
    await _configure_inventory()
    _admin()
    mid = f"m-{uuid.uuid4().hex[:8]}"
    await _seed_master(mid)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await client.post("/api/purchase-orders", json={
                "supplier": "PT Auto",
                "lines": [{
                    "master_item_id": mid,
                    "item_code": "A1",
                    "item_name": "Item",
                    "unit": "pcs",
                    "ordered_qty": 10,
                    "unit_price": 500,
                }],
                "idempotency_key": f"po-{uuid.uuid4()}",
            })
            assert po.status_code == 200, po.text
            order = po.json()["order"]
            before = await server.db[JOURNAL_ENTRIES].count_documents({})
            gr = await client.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": order["id"],
                "receipt_date": "2026-10-02",
                "lines": [{"purchase_order_line_id": order["lines"][0]["line_id"], "received_qty": 4}],
                "idempotency_key": f"gr-{uuid.uuid4()}",
            })
            assert gr.status_code == 200, gr.text
            receipt = gr.json()["receipt"]
            assert receipt["accounting_status"] == "not_posted"
            assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_carton_uses_transaction_value_not_base_qty():
    inv, clearing, _, _ = await _configure_inventory()
    mid = f"m-carton-{uuid.uuid4().hex[:6]}"
    _, gr = await _seed_gr(qty=1, price=500000, unit="CARTON", mid=mid)
    await server.db.product_unit_settings.insert_one({
        "master_item_id": mid,
        "transaction_uom": "CARTON",
        "base_uom": "PCS",
        "conversion_factor": 1000,
    })
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    journals = await _source_journals(gr["id"])
    assert len(journals) == 1
    lines = await _journal_lines(journals[0]["id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("500000.00")
    assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("500000.00")


@pytest.mark.anyio
async def test_mixed_service_posts_inventory_only_with_warning():
    inv, clearing, _, _ = await _configure_inventory()
    svc_mid = f"m-svc-{uuid.uuid4().hex[:6]}"
    await _seed_master(svc_mid, item_type="SERVICE")
    svc_line = f"ln-svc-{uuid.uuid4().hex[:6]}"
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[{
            "po": {
                "line_id": svc_line,
                "ordered_qty": 1,
                "unit_price": 100000,
                "unit": "PCS",
                "master_item_id": svc_mid,
            },
            "gr": {
                "line_id": f"gl-svc-{uuid.uuid4().hex[:6]}",
                "purchase_order_line_id": svc_line,
                "received_qty": 1,
                "unit": "PCS",
                "master_item_id": svc_mid,
                "unit_price": 100000,
            },
        }],
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get(f"/api/purchase-goods-receipts/{gr['id']}/accounting-readiness")
            assert ready.status_code == 200, ready.text
            warnings = ready.json()["readiness"]["accounting_warnings"]
            assert any(w.get("code") == WARNING_PARTIAL_INVENTORY_COVERAGE for w in warnings)
            posted = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            body = posted.json()["receipt"]
            assert body["accounting_amount"] == 50000
            assert any(w.get("code") == WARNING_PARTIAL_INVENTORY_COVERAGE for w in body["accounting_warnings"])
            lines = await _journal_lines(body["accounting_journal_id"])
            by_acc = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("50000.00")
            assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("50000.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_service_only_blocks_new_post():
    await _configure_inventory()
    _, gr = await _seed_gr(qty=1, price=100000, item_type="SERVICE")
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    blocked = await _unlocked_post(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert NO_ELIGIBLE_LINES_MESSAGE in str(blocked.detail)
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before


@pytest.mark.anyio
async def test_effective_date_gates_new_post_only():
    await _configure_inventory("2026-10-01")
    _, legacy = await _seed_gr(receipt_date="2026-09-30")
    _, current = await _seed_gr(receipt_date="2026-10-01")
    blocked = await _unlocked_post(legacy["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code in (400, 409)
    ok = await _unlocked_post(current["id"])
    assert not isinstance(ok, HTTPException)
    assert ok["accounting_status"] == "posted"


@pytest.mark.anyio
async def test_physical_config_independence_and_no_stock_from_post():
    await _configure_inventory()
    _, gr = await _seed_gr()
    await server.db.app_settings.update_one(
        {"key": "physical_stock"},
        {"$set": {"enabled": False, "stock_in_trigger": "purchase_order"}},
        upsert=True,
    )
    before_mov = await server.db.stock_movements.count_documents({})
    first = await _unlocked_post(gr["id"])
    assert not isinstance(first, HTTPException)
    assert await server.db.stock_movements.count_documents({}) == before_mov
    jid = first["accounting_journal_id"]
    amount = first["accounting_amount"]
    _, gr2 = await _seed_gr()
    await server.db.app_settings.update_one(
        {"key": "physical_stock"},
        {"$set": {"enabled": False, "stock_in_trigger": "goods_receipt"}},
        upsert=True,
    )
    second = await _unlocked_post(gr2["id"])
    assert not isinstance(second, HTTPException)
    assert second["accounting_amount"] == amount
    assert await server.db.stock_movements.count_documents({}) == before_mov
    assert len(await _source_journals(gr["id"])) == 1
    assert (await _source_journals(gr["id"]))[0]["id"] == jid


@pytest.mark.anyio
async def test_post_idempotency_and_period_close_retry():
    await _configure_inventory()
    _, gr = await _seed_gr(receipt_date="2026-10-05")
    first = await _unlocked_post(gr["id"])
    assert not isinstance(first, HTTPException)
    jid = first["accounting_journal_id"]
    second = await _unlocked_post(gr["id"])
    assert second["accounting_journal_id"] == jid
    assert len(await _source_journals(gr["id"])) == 1
    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    retry = await _unlocked_post(gr["id"])
    assert retry["accounting_journal_id"] == jid
    assert len(await _source_journals(gr["id"])) == 1
    _, other = await _seed_gr(receipt_date="2026-10-06")
    blocked = await _unlocked_post(other["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409


@pytest.mark.anyio
async def test_post_crash_heal_keeps_one_journal():
    inv, clearing, _, _ = await _configure_inventory()
    _, gr = await _seed_gr()
    await post_balanced_journal(
        server.db,
        date="2026-10-02",
        source_type=SOURCE_TYPE,
        source_id=gr["id"],
        description="crash before metadata",
        lines=[
            {"account_id": inv["id"], "debit": Decimal("200000.00"), "credit": Decimal("0.00")},
            {"account_id": clearing["id"], "debit": Decimal("0.00"), "credit": Decimal("200000.00")},
        ],
        posted_by=POST_USER,
    )
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("accounting_posted_at")
    healed = await _unlocked_post(gr["id"])
    assert not isinstance(healed, HTTPException)
    assert healed["accounting_status"] == "posted"
    assert len(await _source_journals(gr["id"])) == 1
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert after.get("accounting_journal_id")


@pytest.mark.anyio
async def test_post_wins_blocks_direct_cancel():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    blocked = await _unlocked_cancel(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert CANCEL_POSTED_MESSAGE in str(blocked.detail)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    assert len(await _source_journals(gr["id"])) == 1


@pytest.mark.anyio
async def test_cancel_wins_blocks_post():
    await _configure_inventory()
    _, gr = await _seed_gr()
    cancelled = await _unlocked_cancel(gr["id"])
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    posted = await _unlocked_post(gr["id"])
    assert isinstance(posted, HTTPException)
    assert posted.status_code == 409
    assert await _source_journals(gr["id"]) == []


@pytest.mark.anyio
async def test_post_vs_cancel_concurrent_one_winner():
    await _configure_inventory()
    _, gr = await _seed_gr()
    cancel_res, post_res = await asyncio.gather(_unlocked_cancel(gr["id"]), _unlocked_post(gr["id"]))
    journals = await _source_journals(gr["id"])
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
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
async def test_fresh_cancel_claim_blocks_post_and_stale_recovers():
    await _configure_inventory()
    _, gr = await _seed_gr()
    claimed = await acquire_receipt_cancel_claim(server.db, gr["id"], token="fresh-cancel")
    assert claimed is not None
    blocked = await _unlocked_post(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert CANCEL_IN_PROGRESS_MESSAGE in str(blocked.detail)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {CANCEL_CLAIM_FIELD: "stale", "receipt_cancel_claim_at": _stale_claim_at()}},
    )
    hanging = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert RECEIPT_CANCEL_CLAIM_TTL_SECONDS == 600
    assert cancel_claim_is_active(hanging) is False
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    assert ACCOUNTING_POST_CLAIM_TTL_SECONDS == 600


@pytest.mark.anyio
async def test_fresh_post_claim_blocks_cancel():
    await _configure_inventory()
    _, gr = await _seed_gr()
    claimed = await acquire_posting_claim(server.db, gr["id"], token="fresh-post")
    assert claimed[CLAIM_FIELD] == "fresh-post"
    blocked = await _unlocked_cancel(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert CANCEL_CLAIM_MESSAGE in str(blocked.detail)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")


@pytest.mark.anyio
async def test_stale_posting_claim_allows_cancel_when_no_journal():
    await _configure_inventory()
    _, gr = await _seed_gr()
    await acquire_posting_claim(server.db, gr["id"], token="stale-post")
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {CLAIM_FIELD: "stale-post", CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    hanging = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert posting_claim_is_active(hanging) is False
    cancelled = await _unlocked_cancel(gr["id"])
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    assert await _source_journals(gr["id"]) == []


@pytest.mark.anyio
async def test_reverse_no_physical_movement():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    reversed_doc = await _unlocked_reverse(gr["id"], "koreksi stok")
    assert not isinstance(reversed_doc, HTTPException), getattr(reversed_doc, "detail", reversed_doc)
    assert reversed_doc["accounting_status"] == "reversed"
    assert reversed_doc["cancelled"] is True
    assert reversed_doc.get("accounting_recovery_required") in (None, False)
    assert len(await _source_journals(gr["id"])) == 1
    assert (await _source_journals(gr["id"]))[0]["status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("accounting_recovery_required")


@pytest.mark.anyio
async def test_reverse_safe_physical_movement():
    inv_acct, clearing, _, _ = await _configure_inventory()
    _admin()
    mid = f"m-{uuid.uuid4().hex[:8]}"
    await _seed_master(mid)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await server.db.app_settings.update_one(
                {"key": "physical_stock"},
                {"$set": {
                    "key": "physical_stock",
                    "enabled": True,
                    "stock_in_trigger": "goods_receipt",
                    "stock_out_trigger": "sales_delivery",
                }},
                upsert=True,
            )
            po = (await client.post("/api/purchase-orders", json={
                "supplier": "PT Stock",
                "lines": [{
                    "master_item_id": mid,
                    "item_code": "ST-1",
                    "item_name": "Stock Item",
                    "unit": "pcs",
                    "ordered_qty": 10,
                    "unit_price": 500,
                }],
                "idempotency_key": f"po-{uuid.uuid4()}",
            })).json()["order"]
            await server.db.product_stock_state.insert_one({
                "_id": mid,
                "master_item_id": mid,
                "base_uom": "PCS",
                "initialized": True,
                "status": "INITIALIZED",
                "total_qty": 10,
                "unprinted_qty": 7,
                "printed_qty": 3,
                "version": 1,
                "pending_event": None,
            })
            gr = (await client.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "receipt_date": "2026-10-02",
                "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 4}],
                "idempotency_key": f"gr-{uuid.uuid4()}",
            })).json()["receipt"]
            after_receipt = await server.db.product_stock_state.find_one({"master_item_id": mid})
            assert after_receipt["total_qty"] == 14
            posted = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            rev = await client.post(
                f"/api/purchase-goods-receipts/{gr['id']}/reverse-accounting",
                json={"reason": "qty salah"},
            )
            assert rev.status_code == 200, rev.text
            body = rev.json()["receipt"]
            assert body["cancelled"] is True
            assert body["accounting_status"] == "reversed"
            assert not body.get("accounting_recovery_required")
            after = await server.db.product_stock_state.find_one({"master_item_id": mid})
            assert after["total_qty"] == 10
            assert after["unprinted_qty"] == 7
            originals = await server.db.stock_movements.count_documents({
                "reference_type": "goods_receipt",
                "reference_id": gr["id"],
                "movement_type": MOVEMENT_PENERIMAAN,
            })
            reversals = await server.db.stock_movements.count_documents({
                "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
                "goods_receipt_id": gr["id"],
            })
            assert originals == 1
            assert reversals == 1
            assert len(await _reversal_journals(gr["id"])) == 1
            lines = await _journal_lines(posted.json()["receipt"]["accounting_journal_id"])
            assert {ln["account_id"] for ln in lines} == {inv_acct["id"], clearing["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_reverse_blocked_when_stock_insufficient():
    await _configure_inventory()
    _, gr = await _seed_gr(qty=4, price=500)
    mid = gr["lines"][0]["master_item_id"]
    await server.db.stock_movements.insert_one({
        "id": f"mv-{gr['id']}",
        "event_key": f"mv-{gr['id']}",
        "reference_type": "goods_receipt",
        "reference_id": gr["id"],
        "movement_type": MOVEMENT_PENERIMAAN,
        "master_item_id": mid,
        "delta": 4,
        "base_qty": 4,
        "created_at": "2026-10-02T00:00:00Z",
        "status": "POSTED",
    })
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 1,
        "unprinted_qty": 1,
        "printed_qty": 0,
        "pending_event": None,
    })
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    blocked = await _unlocked_reverse(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"
    assert await _reversal_journals(gr["id"]) == []
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")


@pytest.mark.anyio
async def test_reverse_stock_opname_barrier():
    await _configure_inventory()
    _, gr = await _seed_gr(qty=4, price=500)
    mid = gr["lines"][0]["master_item_id"]
    await server.db.stock_movements.insert_one({
        "id": f"mv-{gr['id']}",
        "event_key": f"mv-{gr['id']}",
        "reference_type": "goods_receipt",
        "reference_id": gr["id"],
        "movement_type": MOVEMENT_PENERIMAAN,
        "master_item_id": mid,
        "delta": 4,
        "base_qty": 4,
        "created_at": "2026-10-02T00:00:00Z",
        "status": "POSTED",
    })
    await server.db.stock_movements.insert_one({
        "id": f"opname-{mid}",
        "event_key": f"opname-{mid}",
        "master_item_id": mid,
        "movement_type": MOVEMENT_KOREKSI,
        "reference_type": "stock_opname",
        "created_at": "2026-10-03T00:00:00Z",
        "status": "POSTED",
        "delta": 6,
    })
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 20,
        "unprinted_qty": 20,
        "printed_qty": 0,
        "pending_event": None,
    })
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    blocked = await _unlocked_reverse(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert STOCK_OPNAME_CODE in str(blocked.detail)
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"
    assert await _reversal_journals(gr["id"]) == []
    assert await server.db.stock_movements.count_documents({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": gr["id"],
    }) == 0


@pytest.mark.anyio
async def test_reverse_blocked_by_active_invoice():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    await server.db.purchase_invoices.insert_one({
        "id": str(uuid.uuid4()),
        "invoice_number": "FPB-GR",
        "receipt_id": gr["id"],
        "goods_receipt_id": gr["id"],
        "cancelled": False,
        "grand_total": 200000,
    })
    blocked = await _unlocked_reverse(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")


@pytest.mark.anyio
async def test_reversal_crash_heal_and_period_close_retry(monkeypatch):
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    import services.purchase_goods_receipts as pgr

    async def boom(*_a, **_k):
        raise RuntimeError("crash after reverse journal")

    monkeypatch.setattr(pgr, "cancel_purchase_goods_receipt", boom)
    crashed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert isinstance(crashed, HTTPException)
    assert crashed.status_code == 409
    assert RECOVERY_REQUIRED_CODE in str(crashed.detail)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert stored.get("accounting_recovery_required") is True
    assert stored.get("accounting_recovery_reason")
    assert not stored.get("cancelled")
    assert (await _source_journals(gr["id"]))[0]["status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1

    monkeypatch.setattr(pgr, "cancel_purchase_goods_receipt", cancel_purchase_goods_receipt)
    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    healed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert not isinstance(healed, HTTPException), getattr(healed, "detail", healed)
    assert healed["cancelled"] is True
    assert healed["accounting_status"] == "reversed"
    assert not healed.get("accounting_recovery_required")
    assert len(await _reversal_journals(gr["id"])) == 1
    again = await _unlocked_reverse(gr["id"], "ulangi")
    assert again["accounting_status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1


@pytest.mark.anyio
async def test_settings_and_item_uom_drift_keep_original_journal():
    inv_a, clearing_x, _, _ = await _configure_inventory()
    mid = f"m-drift-{uuid.uuid4().hex[:6]}"
    _, gr = await _seed_gr(qty=1, price=500000, unit="CARTON", mid=mid)
    await server.db.product_unit_settings.insert_one({
        "master_item_id": mid,
        "transaction_uom": "CARTON",
        "base_uom": "PCS",
        "conversion_factor": 1000,
    })
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    jid = posted["accounting_journal_id"]
    inv_b, clearing_y, _, _ = await _configure_inventory()
    await server.db.master_items.update_one({"id": mid}, {"$set": {"item_type": "SERVICE"}})
    await server.db.product_unit_settings.update_one(
        {"master_item_id": mid},
        {"$set": {"conversion_factor": 1200}},
    )
    retry = await _unlocked_post(gr["id"])
    assert retry["accounting_journal_id"] == jid
    assert len(await _source_journals(gr["id"])) == 1
    reversed_doc = await _unlocked_reverse(gr["id"], "koreksi akun")
    assert not isinstance(reversed_doc, HTTPException)
    rev = (await _reversal_journals(gr["id"]))[0]
    lines = await _journal_lines(rev["id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[inv_a["id"]]["credit"])) == Decimal("500000.00")
    assert Decimal(str(by_acc[clearing_x["id"]]["debit"])) == Decimal("500000.00")
    assert inv_b["id"] not in by_acc
    assert clearing_y["id"] not in by_acc


@pytest.mark.anyio
async def test_new_post_uses_current_item_type_after_drift():
    await _configure_inventory()
    _, gr = await _seed_gr()
    mid = gr["lines"][0]["master_item_id"]
    await server.db.master_items.update_one({"id": mid}, {"$set": {"item_type": "SERVICE"}})
    blocked = await _unlocked_post(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert await _source_journals(gr["id"]) == []


@pytest.mark.anyio
async def test_get_list_enrichment_is_zero_write_and_batched():
    await _configure_inventory()
    _, gr = await _seed_gr()
    _admin()
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            one = await client.get(f"/api/purchase-goods-receipts/{gr['id']}")
            assert one.status_code == 200
            body = one.json()["receipt"]
            assert body["accounting_status"] == "not_posted"
            assert body["can_post_accounting"] is True
            listed = await client.get("/api/purchase-goods-receipts", params={"include_cancelled": True})
            assert listed.status_code == 200
            row = next(r for r in listed.json()["receipts"] if r["id"] == gr["id"])
            assert row["accounting_status"] == "not_posted"
            assert "accounting_warnings" in row
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unposted_direct_cancel_still_works():
    await _configure_inventory()
    _, gr = await _seed_gr()
    cancelled = await _unlocked_cancel(gr["id"], "salah input")
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    assert cancelled["status"] == "CANCELLED"
    assert await _source_journals(gr["id"]) == []


@pytest.mark.anyio
async def test_reverse_reason_minimum_and_period_lock_new_reverse():
    await _configure_inventory()
    _, gr = await _seed_gr(receipt_date="2026-10-08")
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    short = await _unlocked_reverse(gr["id"], "ab")
    assert isinstance(short, HTTPException)
    assert short.status_code == 400
    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    blocked = await _unlocked_reverse(gr["id"], "koreksi")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"


def _extra_line(*, mid, qty, price, unit="PCS", line_prefix="x"):
    po_line = f"ln-{line_prefix}-{uuid.uuid4().hex[:6]}"
    return {
        "po": {
            "line_id": po_line,
            "ordered_qty": max(qty, 1),
            "unit_price": price,
            "unit": unit,
            "master_item_id": mid,
        },
        "gr": {
            "line_id": f"gl-{line_prefix}-{uuid.uuid4().hex[:6]}",
            "purchase_order_line_id": po_line,
            "received_qty": qty,
            "unit": unit,
            "master_item_id": mid,
            "unit_price": price,
        },
    }


@pytest.mark.anyio
async def test_invalid_uom_mix_blocks_new_post_then_retries_after_fix():
    await _configure_inventory()
    bad_mid = f"m-bad-uom-{uuid.uuid4().hex[:6]}"
    await _seed_master(bad_mid, item_type="INVENTORY", unit="PCS")
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[_extra_line(mid=bad_mid, qty=1, price=100000, unit="CARTON", line_prefix="uom")],
    )
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get(f"/api/purchase-goods-receipts/{gr['id']}/accounting-readiness")
            assert ready.status_code == 200, ready.text
            body = ready.json()["readiness"]
            assert body["can_post_accounting"] is False
            assert body["has_fatal_accounting_blocker"] is True
            assert REASON_INVALID_UNIT_CONVERSION in (body.get("fatal_accounting_reasons") or [])
            assert len(body.get("eligible_inventory_lines") or []) == 1
            assert len(body.get("fatal_ineligible_lines") or []) == 1
            assert not body.get("partial_inventory_coverage")
            blocked = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert blocked.status_code == 409, blocked.text
            assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
            await server.db.product_unit_settings.insert_one({
                "master_item_id": bad_mid,
                "transaction_uom": "CARTON",
                "base_uom": "PCS",
                "conversion_factor": 1,
            })
            posted = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            receipt = posted.json()["receipt"]
            assert receipt["accounting_amount"] == 150000
            journals = await _source_journals(gr["id"])
            assert len(journals) == 1
            lines = await _journal_lines(journals[0]["id"])
            assert sum(Decimal(str(ln.get("debit") or 0)) for ln in lines) == Decimal("150000.00")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unknown_item_type_mix_blocks_new_post():
    await _configure_inventory()
    unknown_mid = f"m-unk-{uuid.uuid4().hex[:6]}"
    await _seed_master(unknown_mid, item_type="", unit="PCS")
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[_extra_line(mid=unknown_mid, qty=1, price=100000, line_prefix="unk")],
    )
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get(f"/api/purchase-goods-receipts/{gr['id']}/accounting-readiness")
            body = ready.json()["readiness"]
            assert body["can_post_accounting"] is False
            assert body["has_fatal_accounting_blocker"] is True
            assert REASON_UNKNOWN_ITEM_TYPE in (body.get("fatal_accounting_reasons") or [])
            assert not any(
                row.get("financial_ineligibility_reason") == REASON_NON_INVENTORY_ITEM
                for row in (body.get("fatal_ineligible_lines") or [])
            )
            blocked = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert blocked.status_code == 409, blocked.text
            assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
            assert await _source_journals(gr["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_service_mix_records_immutable_posting_basis():
    inv, clearing, _, _ = await _configure_inventory()
    svc_mid = f"m-svc-{uuid.uuid4().hex[:6]}"
    await _seed_master(svc_mid, item_type="SERVICE")
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[_extra_line(mid=svc_mid, qty=1, price=100000, line_prefix="svc")],
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get(f"/api/purchase-goods-receipts/{gr['id']}/accounting-readiness")
            body = ready.json()["readiness"]
            assert body["can_post_accounting"] is True
            assert body["has_fatal_accounting_blocker"] is False
            assert body["partial_inventory_coverage"] is True
            assert len(body.get("excluded_service_lines") or []) == 1
            posted = await client.post(f"/api/purchase-goods-receipts/{gr['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            receipt = posted.json()["receipt"]
            assert receipt["accounting_amount"] == 50000
            assert any(w.get("code") == WARNING_PARTIAL_INVENTORY_COVERAGE for w in receipt["accounting_warnings"])
            stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
            basis = stored.get(POSTING_BASIS_FIELD) or {}
            assert basis["inventory_account_id"] == inv["id"]
            assert basis["purchase_clearing_account_id"] == clearing["id"]
            assert basis["eligible_inventory_amount"] == 50000
            assert basis["partial_coverage"] is True
            assert len(basis.get("eligible_inventory_lines") or []) == 1
            excluded = basis.get("excluded_service_lines") or []
            assert len(excluded) == 1
            assert excluded[0]["reason"] == REASON_NON_INVENTORY_ITEM
            assert excluded[0]["line_amount"] == 100000
            assert excluded[0]["master_item_id"] == svc_mid
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_historical_snapshot_survives_service_to_inventory_mutation():
    await _configure_inventory()
    svc_mid = f"m-svc-hist-{uuid.uuid4().hex[:6]}"
    await _seed_master(svc_mid, item_type="SERVICE")
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[_extra_line(mid=svc_mid, qty=1, price=100000, line_prefix="hist")],
    )
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    original_basis = (await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})).get(POSTING_BASIS_FIELD)
    await server.db.master_items.update_one({"id": svc_mid}, {"$set": {"item_type": "INVENTORY"}})
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            got = await client.get(f"/api/purchase-goods-receipts/{gr['id']}")
            assert got.status_code == 200, got.text
            receipt = got.json()["receipt"]
            assert receipt["accounting_status"] == "posted"
            assert receipt["accounting_amount"] == 50000
            assert any(w.get("code") == WARNING_PARTIAL_INVENTORY_COVERAGE for w in receipt["accounting_warnings"])
            assert receipt.get("partial_inventory_coverage") is True
            basis = receipt.get(POSTING_BASIS_FIELD) or {}
            assert [row["master_item_id"] for row in (basis.get("excluded_service_lines") or [])] == [svc_mid]
            assert basis.get("eligible_inventory_amount") == original_basis.get("eligible_inventory_amount")
            ready = await client.get(f"/api/purchase-goods-receipts/{gr['id']}/accounting-readiness")
            view = ready.json()["readiness"]
            assert view["accounting_amount"] == 50000
            assert [row["master_item_id"] for row in (view.get("excluded_service_lines") or [])] == [svc_mid]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_markerless_reversal_crash_heals_without_duplicate(monkeypatch):
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    import services.purchase_goods_receipt_posting as pgp

    async def boom(*_a, **_k):
        raise RuntimeError("crash before recovery marker")

    monkeypatch.setattr(pgp, "_persist_recovery_marker", boom)
    crashed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert isinstance(crashed, HTTPException)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("accounting_recovery_required")
    assert not stored.get("cancelled")
    assert (await _source_journals(gr["id"]))[0]["status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1

    monkeypatch.undo()
    healed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert not isinstance(healed, HTTPException), getattr(healed, "detail", healed)
    assert healed["cancelled"] is True
    assert healed["accounting_status"] == "reversed"
    assert not healed.get("accounting_recovery_required")
    assert len(await _reversal_journals(gr["id"])) == 1


@pytest.mark.anyio
async def test_markerless_reversal_retry_after_period_close(monkeypatch):
    await _configure_inventory()
    _, gr = await _seed_gr(receipt_date="2026-10-08")
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    import services.purchase_goods_receipt_posting as pgp

    async def boom(*_a, **_k):
        raise RuntimeError("crash before recovery marker")

    monkeypatch.setattr(pgp, "_persist_recovery_marker", boom)
    crashed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert isinstance(crashed, HTTPException)
    assert len(await _reversal_journals(gr["id"])) == 1
    monkeypatch.undo()
    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    healed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert not isinstance(healed, HTTPException), getattr(healed, "detail", healed)
    assert healed["cancelled"] is True
    assert healed["accounting_status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_REVERSAL, "source_id": gr["id"]}) == 1


@pytest.mark.anyio
async def test_direct_cancel_blocked_during_recovery(monkeypatch):
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    import services.purchase_goods_receipts as pgr

    async def boom(*_a, **_k):
        raise RuntimeError("crash after reverse journal")

    monkeypatch.setattr(pgr, "cancel_purchase_goods_receipt", boom)
    crashed = await _unlocked_reverse(gr["id"], "pulihkan")
    assert isinstance(crashed, HTTPException)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert stored.get("accounting_recovery_required") is True
    before_rev = await server.db.stock_movements.count_documents({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": gr["id"],
    })
    monkeypatch.setattr(pgr, "cancel_purchase_goods_receipt", cancel_purchase_goods_receipt)
    blocked = await _unlocked_cancel(gr["id"], "langsung")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert RECOVERY_REQUIRED_CODE in str(blocked.detail)
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not after.get("cancelled")
    assert after.get("accounting_recovery_required") is True
    assert await server.db.stock_movements.count_documents({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": gr["id"],
    }) == before_rev


@pytest.mark.anyio
async def test_reverse_claim_blocks_direct_cancel():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    claimed = await acquire_reverse_claim(server.db, gr["id"], token="fresh-reverse")
    assert claimed is not None
    blocked = await _unlocked_cancel(gr["id"], "langsung")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""}},
    )
    reversed_doc = await _unlocked_reverse(gr["id"], "selesai")
    assert not isinstance(reversed_doc, HTTPException)
    assert reversed_doc["cancelled"] is True


@pytest.mark.anyio
async def test_post_reverse_claim_race_then_finalize_allows_reverse():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {CLAIM_FIELD: "fresh-post-after-journal", CLAIM_AT_FIELD: now_iso_for_claim()}},
    )
    hanging = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert posting_claim_is_active(hanging) is True
    blocked = await _unlocked_reverse(gr["id"], "tunggu")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert POSTING_CLAIM_REVERSE_MESSAGE in str(blocked.detail)
    assert (await _source_journals(gr["id"]))[0]["status"] == "posted"
    assert await _reversal_journals(gr["id"]) == []
    healed = await _unlocked_post(gr["id"])
    assert not isinstance(healed, HTTPException)
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert posting_claim_is_active(after) is False
    reversed_doc = await _unlocked_reverse(gr["id"], "lanjut")
    assert not isinstance(reversed_doc, HTTPException)
    assert reversed_doc["cancelled"] is True
    assert len(await _reversal_journals(gr["id"])) == 1


@pytest.mark.anyio
async def test_stale_reverse_claim_recovers():
    await _configure_inventory()
    _, gr = await _seed_gr()
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    claimed = await acquire_reverse_claim(server.db, gr["id"], token="fresh-reverse")
    assert claimed is not None
    assert ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS == 600
    blocked = await _unlocked_reverse(gr["id"], "tunggu")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {REVERSE_CLAIM_FIELD: "stale-reverse", REVERSE_CLAIM_AT_FIELD: _stale_claim_at()}},
    )
    hanging = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert reverse_claim_is_active(hanging) is False
    reversed_doc = await _unlocked_reverse(gr["id"], "pulihkan")
    assert not isinstance(reversed_doc, HTTPException), getattr(reversed_doc, "detail", reversed_doc)
    assert reversed_doc["cancelled"] is True
    assert len(await _reversal_journals(gr["id"])) == 1


@pytest.mark.anyio
async def test_post_crash_window_keeps_basis_after_settings_drift(monkeypatch):
    inv, clearing, _, _ = await _configure_inventory()
    _, gr = await _seed_gr()
    import services.purchase_goods_receipt_posting as pgp

    real_heal = pgp._heal_posted_metadata

    async def boom_heal(*_a, **_k):
        raise RuntimeError("crash after journal before metadata")

    monkeypatch.setattr(pgp, "_heal_posted_metadata", boom_heal)
    crashed = await _unlocked_post(gr["id"])
    assert isinstance(crashed, HTTPException)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    basis = stored.get(POSTING_BASIS_FIELD) or {}
    assert basis.get("eligible_inventory_amount") == 200000
    assert basis.get("inventory_account_id") == inv["id"]
    assert basis.get("purchase_clearing_account_id") == clearing["id"]
    assert len(await _source_journals(gr["id"])) == 1
    monkeypatch.setattr(pgp, "_heal_posted_metadata", real_heal)
    inv_b, clearing_y, _, _ = await _configure_inventory()
    mid = gr["lines"][0]["master_item_id"]
    await server.db.master_items.update_one({"id": mid}, {"$set": {"item_type": "SERVICE"}})
    retry = await _unlocked_post(gr["id"])
    assert not isinstance(retry, HTTPException)
    assert retry["accounting_amount"] == 200000
    assert retry["accounting_status"] == "posted"
    assert len(await _source_journals(gr["id"])) == 1
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    after_basis = after.get(POSTING_BASIS_FIELD) or {}
    assert after_basis.get("eligible_inventory_amount") == 200000
    assert after_basis.get("inventory_account_id") == inv["id"]
    assert after_basis.get("purchase_clearing_account_id") == clearing["id"]
    assert inv_b["id"] != inv["id"]
    lines = await _journal_lines(retry["accounting_journal_id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("200000.00")
    assert clearing_y["id"] not in by_acc


def now_iso_for_claim() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


async def _primary_journal_lines(receipt_id: str):
    journals = await _source_journals(receipt_id)
    assert len(journals) == 1
    lines = await _journal_lines(journals[0]["id"])
    return journals[0], lines


async def _physical_snapshot(mid: str, receipt_id: str):
    state = await server.db.product_stock_state.find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]},
        {"_id": 0},
    )
    movements = await server.db.stock_movements.find(
        {"$or": [
            {"master_item_id": mid},
            {"reference_id": receipt_id},
            {"goods_receipt_id": receipt_id},
        ]},
        {"_id": 0},
    ).sort("id", 1).to_list(100)
    receipt = await server.db.purchase_goods_receipts.find_one({"id": receipt_id}, {"_id": 0})
    hpp_versions = await server.db.hpp_cost_versions.find({}, {"_id": 0}).to_list(100)
    hpp_layers = await server.db.hpp_cost_layers.find({}, {"_id": 0}).to_list(100)
    hpp_settings = await server.db.app_settings.find_one({"key": "hpp_valuation"}, {"_id": 0})
    return {
        "state": _jsonable(state or {}),
        "movements": _jsonable(movements),
        "received_qty": [row.get("received_qty") for row in (receipt or {}).get("lines") or []],
        "cancelled": bool((receipt or {}).get("cancelled")),
        "hpp_versions": _jsonable(hpp_versions),
        "hpp_layers": _jsonable(hpp_layers),
        "hpp_settings": _jsonable(hpp_settings or {}),
    }


async def _seed_stocked_gr(*, qty=4, price=500):
    _, gr = await _seed_gr(qty=qty, price=price)
    mid = gr["lines"][0]["master_item_id"]
    await server.db.stock_movements.insert_one({
        "id": f"mv-{gr['id']}",
        "event_key": f"mv-{gr['id']}",
        "reference_type": "goods_receipt",
        "reference_id": gr["id"],
        "movement_type": MOVEMENT_PENERIMAAN,
        "master_item_id": mid,
        "delta": qty,
        "base_qty": qty,
        "created_at": "2026-10-02T00:00:00Z",
        "status": "POSTED",
    })
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 20,
        "unprinted_qty": 16,
        "printed_qty": 4,
        "pending_event": None,
        "version": 1,
    })
    return gr, mid


@pytest.mark.anyio
async def test_double_post_after_success_keeps_one_journal_and_basis():
    inv, clearing, _, _ = await _configure_inventory()
    _, gr = await _seed_gr(qty=100, price=500)
    first = await _unlocked_post(gr["id"])
    assert not isinstance(first, HTTPException)
    first_journal, first_lines = await _primary_journal_lines(gr["id"])
    first_basis = (await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})).get(POSTING_BASIS_FIELD)
    assert first["accounting_status"] == "posted"
    assert first["accounting_journal_id"] == first_journal["id"]
    assert len(first_lines) == 2
    by_acc = {ln["account_id"]: ln for ln in first_lines}
    assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("50000.00")
    assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("50000.00")

    second = await _unlocked_post(gr["id"])
    assert not isinstance(second, HTTPException)
    assert second["accounting_status"] == "posted"
    assert second["accounting_journal_id"] == first_journal["id"]
    journals = await _source_journals(gr["id"])
    assert len(journals) == 1
    retry_lines = await _journal_lines(first_journal["id"])
    assert len(retry_lines) == 2
    assert await server.db[JOURNAL_LINES].count_documents({"journal_id": first_journal["id"]}) == 2
    retry_by_acc = {ln["account_id"]: ln for ln in retry_lines}
    assert Decimal(str(retry_by_acc[inv["id"]]["debit"])) == Decimal("50000.00")
    assert Decimal(str(retry_by_acc[clearing["id"]]["credit"])) == Decimal("50000.00")
    retry_basis = (await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})).get(POSTING_BASIS_FIELD)
    assert _jsonable(retry_basis) == _jsonable(first_basis)
    assert retry_basis["eligible_inventory_amount"] == 50000


@pytest.mark.anyio
async def test_fresh_post_claim_blocks_direct_cancel_without_stock_reversal():
    await _configure_inventory()
    gr, mid = await _seed_stocked_gr()
    claimed = await acquire_posting_claim(server.db, gr["id"], token="fresh-post-race")
    assert claimed is not None
    before = await _physical_snapshot(mid, gr["id"])
    blocked = await _unlocked_cancel(gr["id"], "langsung")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    after_block = await _physical_snapshot(mid, gr["id"])
    assert after_block["state"] == before["state"]
    assert after_block["movements"] == before["movements"]
    assert after_block["received_qty"] == before["received_qty"]
    assert await server.db.stock_movements.count_documents({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": gr["id"],
    }) == 0
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    final = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not final.get("cancelled")
    assert posted["accounting_status"] == "posted"
    assert len(await _source_journals(gr["id"])) == 1
    assert not (final.get("cancelled") and await _source_journals(gr["id"]))


@pytest.mark.anyio
async def test_fresh_cancel_claim_blocks_post_then_cancel_wins_without_journal():
    await _configure_inventory()
    gr, mid = await _seed_stocked_gr()
    claimed = await acquire_receipt_cancel_claim(server.db, gr["id"], token="fresh-cancel-race")
    assert claimed is not None
    before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    blocked = await _unlocked_post(gr["id"])
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert CANCEL_IN_PROGRESS_MESSAGE in str(blocked.detail)
    assert await _source_journals(gr["id"]) == []
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals
    try:
        cancelled = await cancel_purchase_goods_receipt(
            server.db, gr["id"], POST_USER, reason="batal stok", claim_token="fresh-cancel-race"
        )
    except HTTPException as exc:
        cancelled = exc
    assert not isinstance(cancelled, HTTPException)
    assert cancelled["cancelled"] is True
    assert await _source_journals(gr["id"]) == []
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert stored.get("cancelled") is True
    assert not (stored.get("cancelled") and await _source_journals(gr["id"]))
    after = await _physical_snapshot(mid, gr["id"])
    assert after["cancelled"] is True


@pytest.mark.anyio
async def test_post_crash_after_journal_recovers_even_after_period_close(monkeypatch):
    inv, clearing, _, _ = await _configure_inventory()
    _, gr = await _seed_gr(receipt_date="2026-10-08")
    import services.purchase_goods_receipt_posting as pgp

    real_heal = pgp._heal_posted_metadata

    async def boom_heal(*_a, **_k):
        raise RuntimeError("crash after journal before metadata")

    monkeypatch.setattr(pgp, "_heal_posted_metadata", boom_heal)
    crashed = await _unlocked_post(gr["id"])
    assert isinstance(crashed, HTTPException)
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    basis = stored.get(POSTING_BASIS_FIELD) or {}
    assert basis.get("eligible_inventory_amount") == 200000
    journals = await _source_journals(gr["id"])
    assert len(journals) == 1
    jid = journals[0]["id"]
    assert not stored.get("accounting_posted_at") or stored.get("accounting_journal_id") in (None, jid)
    monkeypatch.setattr(pgp, "_heal_posted_metadata", real_heal)
    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    retry = await _unlocked_post(gr["id"])
    assert not isinstance(retry, HTTPException), getattr(retry, "detail", retry)
    assert retry["accounting_status"] == "posted"
    assert retry["accounting_journal_id"] == jid
    assert len(await _source_journals(gr["id"])) == 1
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert after.get("accounting_journal_id") == jid
    assert after.get(POSTING_BASIS_FIELD) == basis
    lines = await _journal_lines(jid)
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("200000.00")
    assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("200000.00")
    hanging = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert posting_claim_is_active(hanging) is False


@pytest.mark.anyio
async def test_accounting_reversal_does_not_mutate_physical_stock(monkeypatch):
    await _configure_inventory()
    gr, mid = await _seed_stocked_gr(qty=4, price=500)
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    before = await _physical_snapshot(mid, gr["id"])
    import services.purchase_goods_receipts as pgr

    async def boom(*_a, **_k):
        raise RuntimeError("stop before operational cancel")

    monkeypatch.setattr(pgr, "cancel_purchase_goods_receipt", boom)
    crashed = await _unlocked_reverse(gr["id"], "hanya accounting")
    assert isinstance(crashed, HTTPException)
    after = await _physical_snapshot(mid, gr["id"])
    assert after["state"] == before["state"]
    assert after["movements"] == before["movements"]
    assert after["received_qty"] == before["received_qty"] == [4]
    assert after["hpp_versions"] == before["hpp_versions"]
    assert after["hpp_layers"] == before["hpp_layers"]
    assert after["hpp_settings"] == before["hpp_settings"]
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert not stored.get("cancelled")
    journal, lines = await _primary_journal_lines(gr["id"])
    assert journal["status"] == "reversed"
    assert len(await _reversal_journals(gr["id"])) == 1
    rev = (await _reversal_journals(gr["id"]))[0]
    rev_lines = await _journal_lines(rev["id"])
    src_by = {ln["account_id"]: ln for ln in lines}
    rev_by = {ln["account_id"]: ln for ln in rev_lines}
    for account_id, src in src_by.items():
        assert Decimal(str(rev_by[account_id]["debit"])) == Decimal(str(src.get("credit") or 0))
        assert Decimal(str(rev_by[account_id]["credit"])) == Decimal(str(src.get("debit") or 0))
    assert await server.db.stock_movements.count_documents({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": gr["id"],
    }) == 0


@pytest.mark.anyio
async def test_mixed_service_posts_inventory_only_and_reverses_exact_amount():
    inv, clearing, _, _ = await _configure_inventory()
    svc_mid = f"m-svc-rev-{uuid.uuid4().hex[:6]}"
    await _seed_master(svc_mid, item_type="SERVICE")
    _, gr = await _seed_gr(
        qty=100,
        price=500,
        extra_lines=[_extra_line(mid=svc_mid, qty=1, price=100000, line_prefix="mixrev")],
    )
    posted = await _unlocked_post(gr["id"])
    assert not isinstance(posted, HTTPException)
    assert posted["accounting_amount"] == 50000
    stored = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    basis = stored.get(POSTING_BASIS_FIELD) or {}
    assert basis["eligible_inventory_amount"] == 50000
    assert len(basis.get("eligible_inventory_lines") or []) == 1
    excluded = basis.get("excluded_service_lines") or []
    assert len(excluded) == 1
    assert excluded[0]["reason"] == REASON_NON_INVENTORY_ITEM
    assert excluded[0]["line_amount"] == 100000
    journal, lines = await _primary_journal_lines(gr["id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[inv["id"]]["debit"])) == Decimal("50000.00")
    assert Decimal(str(by_acc[clearing["id"]]["credit"])) == Decimal("50000.00")
    reversed_doc = await _unlocked_reverse(gr["id"], "koreksi mix")
    assert not isinstance(reversed_doc, HTTPException)
    assert reversed_doc["accounting_status"] == "reversed"
    revs = await _reversal_journals(gr["id"])
    assert len(revs) == 1
    rev_lines = await _journal_lines(revs[0]["id"])
    rev_by = {ln["account_id"]: ln for ln in rev_lines}
    assert Decimal(str(rev_by[inv["id"]]["credit"])) == Decimal("50000.00")
    assert Decimal(str(rev_by[clearing["id"]]["debit"])) == Decimal("50000.00")
    assert Decimal(str(rev_by[inv["id"]]["debit"])) == Decimal("0.00")
    assert len(await _source_journals(gr["id"])) == 1
