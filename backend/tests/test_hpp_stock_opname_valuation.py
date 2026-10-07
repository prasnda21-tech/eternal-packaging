"""Stock Opname adjustments use pre-adjustment canonical HPP. Isolated DB only."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.hpp_cutover import (
    BASELINES,
    EVENTS,
    SOURCE_FIRST_GR_UNIT_COST,
    SOURCE_GOODS_RECEIPT,
    SOURCE_STOCK_OPNAME_ADJUSTMENT,
    STATUS_PENDING_VALUATION,
    STATUS_RECOGNIZED,
    ensure_indexes,
    stock_opname_source_key,
)
from services.hpp_valuation import LAYERS, SETTINGS_KEY
from services.inventory_valuation import build_inventory_valuation_rows
from services.physical_stock import (
    LEDGER_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    ensure_indexes as ensure_stock_indexes,
    get_balance,
    post_opening_balance,
)
from services.purchase_common import money_round
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_orders import create_purchase_order
from services.stock_opname_sessions import (
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes as ensure_opname_indexes,
    record_stock_opname_counts,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_hpp_stock_opname_valuation"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-opname-hpp", "email": "admin@example.com", "name": "Admin"}


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
    await ensure_opname_indexes(database)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _org(db):
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await db.branches.insert_one({
            "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": "wh-main", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
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


async def _opening(db, item_id, qty):
    return await post_opening_balance(db, item_id, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "physical_qty": qty,
        "reason": "Saldo awal",
        "idempotency_key": f"open-{item_id}-{qty}",
    }, actor_id="user-opname-hpp")


async def _po(db, item_id, qty, price, name):
    return await create_purchase_order(db, {
        "supplier": "Supplier Opname HPP",
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


async def _receive(db, po, qty, *, idempotency_key=None):
    return await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "warehouse_id": "wh-main",
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": qty}],
    }, USER)


def _valuation(view, item_id):
    return next(row for row in view["rows"] if row["master_item_id"] == item_id)


async def _seed_valued(db, code, qty=100, unit_cost=1000):
    """Physical qty with matching canonical valuation at unit_cost via first GR."""
    await _org(db)
    item_id = await _item(db, code)
    po = await _po(db, item_id, qty, unit_cost, code)
    await _receive(db, po, qty, idempotency_key=f"seed-{code}")
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == qty
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == qty
    assert view["current_hpp"] == unit_cost
    return item_id


async def _confirm_opname(db, item_id, actual_qty, *, key):
    session = await create_stock_opname_session(db, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "idempotency_key": f"sess-{key}",
    }, USER)
    counting = await start_stock_opname_cutoff(db, session["id"], USER)
    lines = []
    for line in counting["lines"]:
        qty = actual_qty if line["master_item_id"] == item_id else line["book_qty"]
        lines.append({
            "line_id": line["line_id"],
            "physical_count_qty": qty,
            "difference_qty": int(qty) - int(line["book_qty"] or 0),
        })
    await record_stock_opname_counts(db, counting["id"], {"lines": lines}, USER)
    await submit_stock_opname_review(db, counting["id"], USER)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": f"confirm-{key}"}, USER,
    )
    target = next(line for line in confirmed["lines"] if line["master_item_id"] == item_id)
    return confirmed, target


async def test_negative_adjustment_keeps_hpp(db):
    item_id = await _seed_valued(db, "OP-NEG", 100, 1000)
    confirmed, line = await _confirm_opname(db, item_id, 80, key="neg")
    assert line["difference_qty"] == -20
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 80
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 80
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 80000
    event = await db[EVENTS].find_one({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }, {"_id": 0})
    assert event["status"] == STATUS_RECOGNIZED
    assert event["qty"] == -20
    assert event["unit_cost"] == 1000
    assert event["source_key"] == stock_opname_source_key(confirmed["id"], line["line_id"])
    assert await db.stock_movements.count_documents({"master_item_id": item_id}) == 0


async def test_positive_adjustment_keeps_hpp(db):
    item_id = await _seed_valued(db, "OP-POS", 100, 1000)
    confirmed, line = await _confirm_opname(db, item_id, 120, key="pos")
    assert line["difference_qty"] == 20
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 120
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 120
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 120000
    event = await db[EVENTS].find_one({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }, {"_id": 0})
    assert event["status"] == STATUS_RECOGNIZED
    assert event["qty"] == 20
    assert event["unit_cost"] == 1000
    assert confirmed["id"]


async def test_zero_difference_writes_nothing(db):
    item_id = await _seed_valued(db, "OP-ZERO", 100, 1000)
    confirmed, line = await _confirm_opname(db, item_id, 100, key="zero")
    assert line["difference_qty"] == 0
    assert line.get("movement_id") in (None, "")
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item_id,
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0
    assert await db[EVENTS].count_documents({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }) == 0
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 100
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 100000
    assert confirmed["status"] == "CONFIRMED"


async def test_exact_retry_one_physical_one_valuation(db):
    item_id = await _seed_valued(db, "OP-RETRY", 100, 1000)
    first, line = await _confirm_opname(db, item_id, 80, key="retry")
    second = await confirm_stock_opname_session(
        db, first["id"], {"idempotency_key": "confirm-retry"}, USER,
    )
    assert second["id"] == first["id"]
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item_id,
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 1
    assert await db[EVENTS].count_documents({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }) == 1
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 80
    assert view["inventory_value"] == 80000
    assert line["line_id"]


async def test_changed_payload_conflict_no_duplicate(db):
    item_id = await _seed_valued(db, "OP-CONFLICT", 100, 1000)
    first, line = await _confirm_opname(db, item_id, 80, key="conflict")
    with pytest.raises(HTTPException) as conflict:
        await confirm_stock_opname_session(
            db,
            first["id"],
            {
                "idempotency_key": "other-key",
                "lines": [{
                    "line_id": line["line_id"],
                    "physical_count_qty": 70,
                    "difference_qty": -30,
                }],
            },
            USER,
        )
    assert conflict.value.status_code == 409
    assert await db[EVENTS].count_documents({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }) == 1
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 80


async def test_missing_hpp_physical_succeeds_without_invented_cost(db):
    await _org(db)
    item_id = await _item(db, "OP-MISS")
    await _opening(db, item_id, 100)
    confirmed, line = await _confirm_opname(db, item_id, 80, key="miss")
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 80
    event = await db[EVENTS].find_one({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }, {"_id": 0})
    assert event["status"] == STATUS_PENDING_VALUATION
    assert event["unit_cost"] is None
    assert event["qty"] is None
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["current_hpp"] is None
    assert view["inventory_value"] is None
    assert line["hpp_valuation_status"] == STATUS_PENDING_VALUATION
    assert confirmed["status"] == "CONFIRMED"
    assert await db.customer_pricing_versions.count_documents({}) == 0


async def test_pending_opname_then_first_gr_bootstrap(db):
    await _org(db)
    item_id = await _item(db, "OP-BOOT")
    await _opening(db, item_id, 500)
    await _confirm_opname(db, item_id, 520, key="boot")
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 520
    pending = await db[EVENTS].find_one({
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }, {"_id": 0})
    assert pending["status"] == STATUS_PENDING_VALUATION
    assert pending["unit_cost"] is None

    po = await _po(db, item_id, 100, 1000, "OP-BOOT")
    await _receive(db, po, 100, idempotency_key="gr-after-opname")
    physical = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 620
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 620
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 620000
    baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert baseline["stock_fisik_snapshot"] == 520
    assert baseline["opening_unit_cost"] == 1000
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST
    assert await db[EVENTS].count_documents({
        "master_item_id": item_id,
        "source_type": SOURCE_GOODS_RECEIPT,
        "status": STATUS_RECOGNIZED,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
    }) == 1


async def test_moving_average_after_positive_opname(db):
    item_id = await _seed_valued(db, "OP-AVG", 100, 1000)
    await _confirm_opname(db, item_id, 120, key="avg")
    before_gr = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert before_gr["valuation_qty"] == 120
    assert before_gr["current_hpp"] == 1000
    assert before_gr["inventory_value"] == 120000

    po = await _po(db, item_id, 80, 1500, "OP-AVG")
    await _receive(db, po, 80, idempotency_key="gr-after-avg")
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 200
    assert view["inventory_value"] == 240000
    assert view["current_hpp"] == money_round(240000 / 200)
    assert view["current_hpp"] == 1200
    settings = await db.app_settings.find_one({"key": SETTINGS_KEY})
    layers = await db[LAYERS].find(
        {"calculation_version": settings["active_version"], "master_item_id": item_id},
        {"_id": 0, "source_type": 1},
    ).to_list(20)
    kinds = {row["source_type"] for row in layers}
    assert SOURCE_STOCK_OPNAME_ADJUSTMENT in kinds
    assert SOURCE_GOODS_RECEIPT in kinds


async def test_no_legacy_stock_writes_on_valued_opname(db):
    item_id = await _seed_valued(db, "OP-LEGACY", 100, 1000)
    await db.product_stock_state.insert_one({
        "_id": item_id,
        "master_item_id": item_id,
        "accurate_item_id": item_id,
        "base_uom": "PCS",
        "total_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    await _confirm_opname(db, item_id, 120, key="legacy")
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 0
    assert await db.stock_movements.count_documents({"master_item_id": item_id}) == 0
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 120
    assert view["current_hpp"] == 1000
