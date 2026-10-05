"""Purchase return relief uses the original goods-receipt unit cost."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.hpp_cutover import (
    BASELINES,
    CUTOVERS,
    EVENTS,
    SOURCE_PURCHASE_RETURN,
    SOURCE_PURCHASE_RETURN_REVERSAL,
    STATUS_PENDING_VALUATION,
    STATUS_RECOGNIZED,
    ensure_indexes,
    recognize_posted_goods_receipt,
)
from services.hpp_valuation import LAYERS, SETTINGS_KEY, moving_average_from_events
from services.inventory_valuation import build_inventory_valuation_rows
from services.journal_core import JOURNAL_ENTRIES
from services.physical_stock import ensure_indexes as ensure_stock_indexes, get_balance
from services.purchase_common import money_round
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_orders import create_purchase_order
from services.purchase_returns import cancel_purchase_return, confirm_purchase_return, create_purchase_return

TEST_DB = "eternal_packaging_test_purchase_return_hpp"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-hpp", "email": "admin@example.com", "name": "Admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_stock_indexes(database)
    await ensure_indexes(database)
    await database.purchase_returns.create_index("id", unique=True)
    await database.purchase_returns.create_index("return_number", unique=True)
    await database.purchase_returns.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.purchase_returns.create_index(
        "confirmation_key",
        unique=True,
        partialFilterExpression={"confirmation_key": {"$type": "string"}},
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _org(db):
    await db.branches.insert_one({"id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True})
    await db.warehouses.insert_one({
        "id": "wh-main", "branch_id": "branch-ygy", "code": "MAIN", "name": "Gudang Yogyakarta", "active": True,
    })


async def _item(db, code):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "base_unit": "PCS",
        "accurate_item_id": item_id,
        "item_type": "INVENTORY",
        "is_active": True,
    })
    return item_id


async def _cutover(db):
    await db[CUTOVERS].insert_one({
        "cutover_id": "cutover-return-hpp",
        "status": "ACTIVE",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "created_at": "2026-10-01T00:00:00+00:00",
        "active_marker": "ACTIVE",
        "client_request_id": "cutover-return-hpp",
    })


async def _baseline(db, item_id):
    await db[BASELINES].insert_one({
        "cutover_id": "cutover-return-hpp",
        "master_item_id": item_id,
        "accurate_item_id": item_id,
        "item_identity": f"m:{item_id}",
        "valuation_qty": 0,
        "opening_unit_cost": 1,
        "hpp_status": "OK",
        "item_code": item_id,
        "item_name": item_id,
    })


async def _stock(db, item_id, qty):
    await db.product_stock_state.delete_many({"master_item_id": item_id})
    await db.product_stock_state.insert_one({
        "_id": item_id,
        "master_item_id": item_id,
        "accurate_item_id": item_id,
        "base_uom": "PCS",
        "total_qty": qty,
        "unprinted_qty": qty,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })


async def _po(db, item_id, qty, price, name):
    return await create_purchase_order(db, {
        "supplier": "Supplier HPP",
        "idempotency_key": str(uuid.uuid4()),
        "lines": [{
            "master_item_id": item_id,
            "item_name": name,
            "item_code": name,
            "unit": "PCS",
            "ordered_qty": qty,
            "unit_price": price,
        }],
    }, USER)


async def _receive(db, po, qty):
    return await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": str(uuid.uuid4()),
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": qty}],
    }, USER)


async def _position(db, item_id):
    settings = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0, "active_version": 1})
    version_id = (settings or {}).get("active_version")
    layers = await db[LAYERS].find(
        {"calculation_version": version_id, "master_item_id": item_id},
        {"_id": 0},
    ).to_list(20)
    assert layers, item_id
    latest = max(layers, key=lambda row: (str(row.get("recognized_at") or ""), str(row.get("layer_id") or "")))
    return latest


def _decoys(item_id):
    return {
        "customer_hpp": 9999,
        "invoice_price": 7777,
        "master_item_id": item_id,
    }


async def _plant_decoys(db, po, item_id):
    await db.customer_pricing_groups.insert_one({
        "id": "group-decoy",
        "enabled": True,
        "current_published_version_id": "version-decoy",
    })
    await db.customer_pricing_versions.insert_one({
        "id": "version-decoy",
        "group_id": "group-decoy",
        "status": "PUBLISHED",
        "lines": [{
            "master_item_id": item_id,
            "accurate_item_id": item_id,
            "hpp": 9999,
            "manual_hpp": 9999,
            "pricing_mode": "MANUAL_HPP",
        }],
    })
    await db.purchase_invoices.insert_one({
        "id": "invoice-decoy",
        "purchase_order_id": po["id"],
        "cancelled": False,
        "lines": [{
            "line_id": "invoice-line-decoy",
            "master_item_id": item_id,
            "unit_price": 7777,
            "invoice_qty": 100,
        }],
    })


async def test_moving_average_relieves_original_receipt_cost():
    events = [
        {
            "event_id": "gr-a",
            "item_identity": "a:item",
            "source_type": "GOODS_RECEIPT",
            "source_id": "gr-a",
            "source_line_id": "gr-a",
            "recognized_at": "2026-10-01",
            "recognized_qty": 100,
            "unit_cost": 1000,
            "master_item_id": "item",
        },
        {
            "event_id": "gr-b",
            "item_identity": "a:item",
            "source_type": "GOODS_RECEIPT",
            "source_id": "gr-b",
            "source_line_id": "gr-b",
            "recognized_at": "2026-10-02",
            "recognized_qty": 100,
            "unit_cost": 2000,
            "master_item_id": "item",
        },
        {
            "event_id": "ret-a",
            "item_identity": "a:item",
            "source_type": SOURCE_PURCHASE_RETURN,
            "source_id": "ret-a",
            "source_line_id": "ret-a",
            "recognized_at": "2026-10-03",
            "recognized_qty": -20,
            "unit_cost": 1000,
            "master_item_id": "item",
        },
    ]
    layers, _cogs, state = moving_average_from_events(events)
    assert layers[-1]["remaining_qty"] == 180
    assert layers[-1]["total_cost"] == 280000
    assert layers[-1]["unit_cost"] == money_round(280000 / 180)
    assert state["a:item"]["qty"] == 180


async def test_return_reduces_same_receipt_average_and_display(db):
    await _org(db)
    item_id = await _item(db, "HPP-RET-1")
    other_id = await _item(db, "HPP-OTHER")
    await _cutover(db)
    await _baseline(db, item_id)
    await _baseline(db, other_id)
    await _stock(db, item_id, 100)
    await _stock(db, other_id, 0)
    po = await _po(db, item_id, 100, 1000, "HPP-RET-1")
    other_po = await _po(db, other_id, 10, 4000, "HPP-OTHER")
    receipt = await _receive(db, po, 100)
    await _stock(db, other_id, 10)
    other_receipt = await _receive(db, other_po, 10)
    before = await _position(db, item_id)
    other_before = await _position(db, other_id)
    assert before["remaining_qty"] == 100
    assert before["unit_cost"] == 1000
    assert before["total_cost"] == 100000
    await _plant_decoys(db, po, item_id)
    journals_before = await db[JOURNAL_ENTRIES].count_documents({})
    movements_before = await db.stock_movements.count_documents({})
    legacy_before = (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"]
    draft = await create_purchase_return(db, {
        "idempotency_key": "draft-20",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    confirmed = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "confirm-20"}, USER)
    again = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "confirm-20"}, USER)
    assert again["id"] == confirmed["id"]
    assert await db[EVENTS].count_documents({
        "source_type": SOURCE_PURCHASE_RETURN,
        "purchase_return_id": draft["id"],
    }) == 1
    event = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN}, {"_id": 0})
    assert event["status"] == STATUS_RECOGNIZED
    assert event["goods_receipt_id"] == receipt["id"]
    assert event["goods_receipt_line_id"] == receipt["lines"][0]["line_id"]
    assert event["purchase_return_line_id"] == draft["lines"][0]["line_id"]
    assert event["master_item_id"] == item_id
    assert event["returned_qty"] == 20
    assert event["unit_cost"] == 1000
    assert event["qty"] == -20
    assert event["unit_cost"] != 9999
    assert event["unit_cost"] != 7777
    assert event["original_source_key"] == f"gr:{receipt['id']}:{receipt['lines'][0]['line_id']}"
    assert event["warehouse_id"] == "wh-main"
    assert event["branch_id"] == "branch-ygy"
    position = await _position(db, item_id)
    assert position["remaining_qty"] == 80
    assert position["unit_cost"] == 1000
    assert position["total_cost"] == 80000
    other_after = await _position(db, other_id)
    assert other_after["remaining_qty"] == other_before["remaining_qty"]
    assert other_after["unit_cost"] == other_before["unit_cost"]
    assert other_after["total_cost"] == other_before["total_cost"]
    physical = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 80
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == legacy_before
    assert await db.stock_movements.count_documents({}) == movements_before
    assert await db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    rows = await build_inventory_valuation_rows(db)
    viewed = next(row for row in rows["rows"] if row["master_item_id"] == item_id)
    assert viewed["stock_fisik"] == 80
    assert viewed["current_hpp"] == 1000
    assert viewed["inventory_value"] == 80000
    assert other_receipt["id"]


async def test_two_receipt_costs_recalculate_average_from_the_cheaper_receipt(db):
    await _org(db)
    item_id = await _item(db, "HPP-AVG")
    await _cutover(db)
    await _baseline(db, item_id)
    await _stock(db, item_id, 100)
    cheap = await _po(db, item_id, 100, 1000, "HPP-AVG")
    cheap_receipt = await _receive(db, cheap, 100)
    await _stock(db, item_id, 200)
    expensive = await _po(db, item_id, 100, 2000, "HPP-AVG")
    await _receive(db, expensive, 100)
    draft = await create_purchase_return(db, {
        "idempotency_key": "draft-cheap",
        "lines": [{
            "purchase_goods_receipt_id": cheap_receipt["id"],
            "purchase_goods_receipt_line_id": cheap_receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    await confirm_purchase_return(db, draft["id"], {"idempotency_key": "confirm-cheap"}, USER)
    event = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN}, {"_id": 0})
    assert event["goods_receipt_id"] == cheap_receipt["id"]
    assert event["unit_cost"] == 1000
    position = await _position(db, item_id)
    assert position["remaining_qty"] == 180
    assert position["total_cost"] == 280000
    assert position["unit_cost"] == money_round(280000 / 180)
    physical = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 180


async def test_partial_and_multiple_returns_stay_within_the_receipt(db):
    await _org(db)
    item_id = await _item(db, "HPP-PART")
    await _cutover(db)
    await _baseline(db, item_id)
    await _stock(db, item_id, 100)
    po = await _po(db, item_id, 100, 1000, "HPP-PART")
    receipt = await _receive(db, po, 100)
    first = await create_purchase_return(db, {
        "idempotency_key": "partial-1",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    await confirm_purchase_return(db, first["id"], {"idempotency_key": "partial-confirm-1"}, USER)
    second = await create_purchase_return(db, {
        "idempotency_key": "partial-2",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 30,
        }],
    }, USER)
    await confirm_purchase_return(db, second["id"], {"idempotency_key": "partial-confirm-2"}, USER)
    assert await db[EVENTS].count_documents({"source_type": SOURCE_PURCHASE_RETURN, "status": STATUS_RECOGNIZED}) == 2
    position = await _position(db, item_id)
    assert position["remaining_qty"] == 50
    assert position["total_cost"] == 50000
    assert position["unit_cost"] == 1000
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 50


async def test_changed_idempotency_payload_conflicts_and_does_not_relieve_twice(db):
    await _org(db)
    item_id = await _item(db, "HPP-IDEM")
    await _cutover(db)
    await _baseline(db, item_id)
    await _stock(db, item_id, 100)
    po = await _po(db, item_id, 100, 1000, "HPP-IDEM")
    receipt = await _receive(db, po, 100)
    draft = await create_purchase_return(db, {
        "idempotency_key": "idem-draft",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    await db[EVENTS].insert_one({
        "event_id": "conflict-event",
        "source_type": SOURCE_PURCHASE_RETURN,
        "source_key": f"purchase-return:{draft['id']}:{draft['lines'][0]['line_id']}",
        "purchase_return_id": draft["id"],
        "purchase_return_line_id": draft["lines"][0]["line_id"],
        "goods_receipt_id": receipt["id"],
        "goods_receipt_line_id": receipt["lines"][0]["line_id"],
        "master_item_id": item_id,
        "returned_qty": 20,
        "unit_cost": 555,
        "qty": -20,
        "warehouse_id": "wh-main",
        "branch_id": "branch-ygy",
        "original_source_key": "other",
        "status": STATUS_RECOGNIZED,
        "item_identity": f"a:{item_id}",
    })
    with pytest.raises(HTTPException) as raised:
        await confirm_purchase_return(db, draft["id"], {"idempotency_key": "idem-confirm"}, USER)
    assert raised.value.status_code == 409
    balance = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert balance["physical_qty"] == 100
    await db[EVENTS].delete_one({"event_id": "conflict-event"})
    confirmed = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "idem-confirm"}, USER)
    replay = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "idem-confirm"}, USER)
    assert replay["lines"][0]["hpp_unit_cost"] == confirmed["lines"][0]["hpp_unit_cost"] == 1000
    assert await db[EVENTS].count_documents({"source_type": SOURCE_PURCHASE_RETURN}) == 1
    assert (await _position(db, item_id))["remaining_qty"] == 80
    with pytest.raises(HTTPException) as changed:
        await confirm_purchase_return(db, draft["id"], {
            "idempotency_key": "idem-confirm",
            "lines": [{
                "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
                "return_qty": 10,
            }],
        }, USER)
    assert changed.value.status_code == 409
    assert (await _position(db, item_id))["remaining_qty"] == 80


async def test_reversal_restores_qty_and_value_once(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV")
    await _cutover(db)
    await _baseline(db, item_id)
    await _stock(db, item_id, 100)
    po = await _po(db, item_id, 100, 1000, "HPP-REV")
    receipt = await _receive(db, po, 100)
    draft = await create_purchase_return(db, {
        "idempotency_key": "rev-draft",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    await confirm_purchase_return(db, draft["id"], {"idempotency_key": "rev-confirm"}, USER)
    assert (await _position(db, item_id))["remaining_qty"] == 80
    reversed_doc = await cancel_purchase_return(db, draft["id"], USER, reason="undo")
    assert reversed_doc["status"] == "REVERSED"
    original = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN}, {"_id": 0})
    reversal = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN_REVERSAL}, {"_id": 0})
    assert original["status"] == STATUS_RECOGNIZED
    assert reversal["status"] == STATUS_RECOGNIZED
    assert reversal["unit_cost"] == 1000
    assert reversal["qty"] == 20
    assert reversal["original_event_id"] == original["event_id"]
    restored = await _position(db, item_id)
    assert restored["remaining_qty"] == 100
    assert restored["unit_cost"] == 1000
    assert restored["total_cost"] == 100000
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 100
    await cancel_purchase_return(db, draft["id"], USER, reason="undo")
    assert await db[EVENTS].count_documents({"source_type": SOURCE_PURCHASE_RETURN_REVERSAL}) == 1
    assert (await _position(db, item_id))["remaining_qty"] == 100


async def test_missing_source_cost_does_not_invent_hpp(db):
    await _org(db)
    item_id = await _item(db, "HPP-MISS")
    po = await _po(db, item_id, 100, 1000, "HPP-MISS")
    receipt = await _receive(db, po, 100)
    await db.customer_pricing_versions.insert_one({
        "id": "manual-hpp",
        "lines": [{"master_item_id": item_id, "hpp": 9999}],
    })
    await db.purchase_invoices.insert_one({
        "id": "inv-miss",
        "lines": [{"master_item_id": item_id, "unit_price": 7777}],
    })
    journals = await db[JOURNAL_ENTRIES].count_documents({})
    movements = await db.stock_movements.count_documents({})
    draft = await create_purchase_return(db, {
        "idempotency_key": "miss-draft",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 20,
        }],
    }, USER)
    confirmed = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "miss-confirm"}, USER)
    assert confirmed["lines"][0]["hpp_valuation_status"] == STATUS_PENDING_VALUATION
    assert confirmed["lines"][0]["hpp_unit_cost"] is None
    event = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN}, {"_id": 0})
    assert event["status"] == STATUS_PENDING_VALUATION
    assert event["unit_cost"] is None
    assert await db[LAYERS].count_documents({"master_item_id": item_id}) == 0
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 80
    assert await db.stock_movements.count_documents({}) == movements
    assert await db[JOURNAL_ENTRIES].count_documents({}) == journals
    assert await db.product_stock_state.count_documents({"master_item_id": item_id}) == 0


async def test_re_recognize_uses_receipt_cost_not_customer_or_invoice_price(db):
    await _org(db)
    item_id = await _item(db, "HPP-SRC")
    await _cutover(db)
    await _baseline(db, item_id)
    await _stock(db, item_id, 40)
    po = await _po(db, item_id, 40, 1250, "HPP-SRC")
    receipt = await _receive(db, po, 40)
    await _plant_decoys(db, po, item_id)
    await db.purchase_invoices.update_one({}, {"$set": {"lines.0.unit_price": 7777}})
    await recognize_posted_goods_receipt(db, receipt, USER)
    draft = await create_purchase_return(db, {
        "idempotency_key": "src-draft",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 5,
        }],
    }, USER)
    confirmed = await confirm_purchase_return(db, draft["id"], {"idempotency_key": "src-confirm"}, USER)
    assert confirmed["lines"][0]["hpp_unit_cost"] == 1250
    event = await db[EVENTS].find_one({"source_type": SOURCE_PURCHASE_RETURN}, {"_id": 0})
    assert event["goods_receipt_line_id"] == receipt["lines"][0]["line_id"]
    assert event["unit_cost"] == 1250
    position = await _position(db, item_id)
    assert position["remaining_qty"] == 35
    assert position["total_cost"] == 43750
    assert position["unit_cost"] == 1250
