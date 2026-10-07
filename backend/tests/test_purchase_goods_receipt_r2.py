"""Canonical goods receipt posts R2 PURCHASE_RECEIPT atomically. Isolated DB only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    PhysicalStockAbortError,
    ensure_indexes,
    get_balance,
    list_physical_mutations,
    list_physical_stock_catalog,
    post_movement,
    reconcile_balance,
    set_fail_after_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.purchase_goods_receipts import (
    create_purchase_goods_receipt,
    cancel_purchase_goods_receipt,
    set_fail_after_r2_movements,
    set_fail_before_cancel_update,
    set_fail_before_receipt_insert,
)
from services.purchase_orders import create_purchase_order

TEST_DB = "eternal_packaging_test_purchase_receipt_r2"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-1", "email": "admin@example.com", "name": "Admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    yield database
    set_fail_after_balance(False)
    set_fail_before_receipt_insert(False)
    set_fail_after_r2_movements(False)
    set_fail_before_cancel_update(False)
    await client.drop_database(TEST_DB)
    client.close()


async def _org(db, warehouses):
    branch_id = "branch-ygy"
    await db.branches.insert_one({"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True})
    await db.warehouses.insert_many([
        {"id": row[0], "branch_id": branch_id, "code": row[1], "name": row[2], "active": True}
        for row in warehouses
    ])
    return branch_id


async def _item(db, code="ITEM"):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": code,
        "base_unit": "PCS",
    })
    return item_id


async def _po(db, lines):
    return await create_purchase_order(db, {
        "supplier": "Supplier UAT",
        "lines": lines,
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


async def _receive(db, po, lines, **extra):
    payload = {
        "purchase_order_id": po["id"],
        "lines": lines,
        "idempotency_key": extra.pop("idempotency_key", str(uuid.uuid4())),
        **extra,
    }
    return await create_purchase_goods_receipt(db, payload, USER)


def _line(item_id, qty, name="Item"):
    return {
        "master_item_id": item_id,
        "item_name": name,
        "item_code": name,
        "unit": "PCS",
        "ordered_qty": qty,
        "unit_price": 1000,
    }


async def test_single_line_receipt_adds_r2_stock_at_the_selected_warehouse(db):
    branch_id = await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    po_line = po["lines"][0]["line_id"]
    receipt = await _receive(db, po, [{"purchase_order_line_id": po_line, "received_qty": 40}])
    assert receipt["warehouse_id"] == "wh-main"
    assert receipt["branch_id"] == branch_id
    assert receipt["lines"][0]["warehouse_id"] == "wh-main"
    balance = await get_balance(db, item_id, branch_id, "wh-main")
    assert balance["physical_qty"] == 40
    movements = await db[LEDGER_COLLECTION].find({"metadata.receipt_id": receipt["id"]}, {"_id": 0}).to_list(10)
    assert [row["movement_type"] for row in movements] == [MOVEMENT_PURCHASE_RECEIPT]
    assert movements[0]["qty_delta"] == 40
    assert movements[0]["reference_id"] == f"{receipt['id']}:{receipt['lines'][0]['line_id']}"
    assert await db.stock_movements.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0
    assert await db.item_stock_manual.count_documents({}) == 0
    recon = await reconcile_balance(db, item_id, branch_id, "wh-main")
    assert recon["match"] is True
    mutations = await list_physical_mutations(db, item_id)
    assert mutations["items"][0]["movement_type"] == MOVEMENT_PURCHASE_RECEIPT
    catalog = await list_physical_stock_catalog(db)
    row = next(item for item in catalog["items"] if item["master_item_id"] == item_id)
    assert row["physical_qty"] == 40


async def test_partial_receipts_sum_without_over_receipt(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 1000)])
    po_line = po["lines"][0]["line_id"]
    first = await _receive(db, po, [{"purchase_order_line_id": po_line, "received_qty": 400}])
    second = await _receive(db, po, [{"purchase_order_line_id": po_line, "received_qty": 300}])
    third = await _receive(db, po, [{"purchase_order_line_id": po_line, "received_qty": 300}])
    assert len({first["id"], second["id"], third["id"]}) == 3
    balance = await get_balance(db, item_id, first["branch_id"], "wh-main")
    assert balance["physical_qty"] == 1000
    with pytest.raises(HTTPException) as exc:
        await _receive(db, po, [{"purchase_order_line_id": po_line, "received_qty": 1}])
    assert exc.value.status_code == 400


async def test_multi_line_receipt_is_atomic(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_a = await _item(db, "A")
    item_b = await _item(db, "B")
    po = await _po(db, [_line(item_a, 100, "A"), _line(item_b, 100, "B")])
    lines = {row["master_item_id"]: row["line_id"] for row in po["lines"]}
    receipt = await _receive(db, po, [
        {"purchase_order_line_id": lines[item_a], "received_qty": 100},
        {"purchase_order_line_id": lines[item_b], "received_qty": 50},
    ])
    assert len(receipt["lines"]) == 2
    assert await db[LEDGER_COLLECTION].count_documents({"metadata.receipt_id": receipt["id"]}) == 2
    set_fail_after_balance(True)
    po2 = await _po(db, [_line(item_a, 10, "A2")])
    with pytest.raises(PhysicalStockAbortError):
        await _receive(db, po2, [{"purchase_order_line_id": po2["lines"][0]["line_id"], "received_qty": 10}])
    set_fail_after_balance(False)
    assert await db.purchase_goods_receipts.count_documents({"purchase_order_id": po2["id"]}) == 0
    assert await get_balance(db, item_a, receipt["branch_id"], "wh-main")
    assert (await get_balance(db, item_a, receipt["branch_id"], "wh-main"))["physical_qty"] == 100


async def test_warehouse_rules(db):
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 10)])
    line_id = po["lines"][0]["line_id"]
    with pytest.raises(HTTPException) as missing:
        await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 1}])
    assert missing.value.status_code == 400
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta"), ("wh-b", "WHB", "Gudang B")])
    with pytest.raises(HTTPException) as many:
        await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 1}])
    assert "Pilih gudang" in str(many.value.detail)
    receipt = await _receive(
        db, po, [{"purchase_order_line_id": line_id, "received_qty": 1}], warehouse_id="wh-b",
    )
    assert receipt["warehouse_id"] == "wh-b"
    assert receipt["branch_id"] == "branch-ygy"


async def test_idempotent_retry_and_payload_conflict(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 20)])
    line_id = po["lines"][0]["line_id"]
    key = "receipt-once"
    first = await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 5}], idempotency_key=key)
    second = await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 5}], idempotency_key=key)
    assert second["id"] == first["id"]
    assert await db[LEDGER_COLLECTION].count_documents({"metadata.receipt_id": first["id"]}) == 1
    with pytest.raises(HTTPException) as conflict:
        await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 6}], idempotency_key=key)
    assert conflict.value.status_code == 409
    assert (await get_balance(db, item_id, first["branch_id"], "wh-main"))["physical_qty"] == 5


async def test_concurrent_duplicate_posts_once(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 20)])
    line_id = po["lines"][0]["line_id"]
    key = "race-key"
    results = await asyncio.gather(
        _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 4}], idempotency_key=key),
        _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 4}], idempotency_key=key),
        return_exceptions=True,
    )
    saved = [row for row in results if not isinstance(row, Exception)]
    assert saved
    assert len({row["id"] for row in saved}) == 1
    assert await db.purchase_goods_receipts.count_documents({"idempotency_key": key}) == 1
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RECEIPT}) == 1
    assert (await get_balance(db, item_id, saved[0]["branch_id"], "wh-main"))["physical_qty"] == 4


async def test_persist_failure_creates_no_movement(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 10)])
    set_fail_before_receipt_insert(True)
    with pytest.raises(PhysicalStockAbortError):
        await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 3}])
    set_fail_before_receipt_insert(False)
    assert await db.purchase_goods_receipts.count_documents({}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0


async def test_r2_failure_rolls_back_receipt(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 10)])
    set_fail_after_r2_movements(True)
    with pytest.raises(PhysicalStockAbortError):
        await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 3}])
    set_fail_after_r2_movements(False)
    assert await db.purchase_goods_receipts.count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


async def test_cancellation_reverses_once_and_blocks_invoice_or_shortage(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    line_id = po["lines"][0]["line_id"]
    receipt = await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 100}])
    await db.purchase_invoices.insert_one({"id": "inv-1", "receipt_id": receipt["id"], "cancelled": False})
    with pytest.raises(HTTPException) as blocked:
        await cancel_purchase_goods_receipt(db, receipt["id"], USER, reason="batal")
    assert blocked.value.status_code == 409
    await db.purchase_invoices.delete_many({})
    await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": receipt["branch_id"],
        "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": -80,
        "idempotency_key": "consume-80",
        "reason": "test consume",
    }, actor_id="user-1")
    with pytest.raises(HTTPException) as short:
        await cancel_purchase_goods_receipt(db, receipt["id"], USER, reason="batal")
    assert short.value.status_code == 409
    assert (await db.purchase_goods_receipts.find_one({"id": receipt["id"]})).get("cancelled") is not True
    await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": receipt["branch_id"],
        "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 80,
        "idempotency_key": "restore-80",
        "reason": "test restore",
    }, actor_id="user-1")
    cancelled = await cancel_purchase_goods_receipt(db, receipt["id"], USER, reason="batal")
    assert cancelled["status"] == "CANCELLED"
    again = await cancel_purchase_goods_receipt(db, receipt["id"], USER, reason="batal")
    assert again["status"] == "CANCELLED"
    rows = await db[LEDGER_COLLECTION].find(
        {"metadata.receipt_id": receipt["id"]},
        {"_id": 0, "movement_type": 1},
    ).to_list(10)
    assert [row["movement_type"] for row in rows] == [MOVEMENT_PURCHASE_RECEIPT]
    reversals = await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL})
    assert reversals == 1
    original = await db[LEDGER_COLLECTION].find_one({"metadata.receipt_id": receipt["id"]})
    assert original["qty_delta"] == 100
    assert (await get_balance(db, item_id, receipt["branch_id"], "wh-main"))["physical_qty"] == 0
    assert await db.stock_movements.count_documents({}) == 0


async def test_cancellation_rollback_keeps_receipt_active(db):
    await _org(db, [("wh-main", "MAIN", "Gudang Yogyakarta")])
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 10)])
    receipt = await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 10}])
    set_fail_before_cancel_update(True)
    with pytest.raises(PhysicalStockAbortError):
        await cancel_purchase_goods_receipt(db, receipt["id"], USER, reason="batal")
    set_fail_before_cancel_update(False)
    stored = await db.purchase_goods_receipts.find_one({"id": receipt["id"]})
    assert stored.get("cancelled") is not True
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 0
    assert (await get_balance(db, item_id, receipt["branch_id"], "wh-main"))["physical_qty"] == 10
