"""First-GR bootstrap values pending R2 physical at canonical receipt cost."""
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
    SOURCE_OPENING,
    STATUS_NEEDS_COST,
    STATUS_RECOGNIZED,
    ensure_indexes,
    list_reconciliation,
)
from services.hpp_valuation import LAYERS, SETTINGS_KEY
from services.inventory_valuation import build_inventory_valuation_rows
from services.physical_stock import (
    LEDGER_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    ensure_indexes as ensure_stock_indexes,
    get_balance,
    post_opening_balance,
)
from services.purchase_common import money_round
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_orders import create_purchase_order
from services.r2_go_live import set_r2_authority_active

TEST_DB = "eternal_packaging_test_hpp_first_gr_bootstrap"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-bootstrap", "email": "admin@example.com", "name": "Admin"}


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
    await set_r2_authority_active(database, active=True)
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


async def _legacy_state(db, item_id, qty=0):
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
        "supplier": "Supplier Bootstrap",
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
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": qty}],
    }, USER)


def _valuation(view, item_id):
    return next(row for row in view["rows"] if row["master_item_id"] == item_id)


async def _opening(db, item_id, qty):
    return await post_opening_balance(db, item_id, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "physical_qty": qty,
        "reason": "Saldo awal UAT",
        "idempotency_key": f"open-{item_id}",
    }, actor_id="user-bootstrap")


async def _events(db, item_id):
    return await db[EVENTS].find({"master_item_id": item_id}, {"_id": 0}).to_list(20)


async def test_pending_r2_physical_first_gr_formula_a(db):
    await _org(db)
    item_id = await _item(db, "BOOT-A")
    await _legacy_state(db, item_id, 0)
    await _opening(db, item_id, 500)
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 500
    assert await db.hpp_cost_layers.count_documents({"master_item_id": item_id}) == 0

    po = await _po(db, item_id, 200, 1000, "BOOT-A")
    receipt = await _receive(db, po, 100, idempotency_key="gr-boot-a")

    physical = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 600
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 0
    assert await db.stock_movements.count_documents({"master_item_id": item_id}) == 0

    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["stock_fisik"] == 600
    assert view["valuation_qty"] == 600
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 600000
    assert view["hpp_source"] == "PURCHASING_MOVING_AVERAGE"

    baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert baseline["stock_fisik_snapshot"] == 500
    assert baseline["opening_unit_cost"] == 1000
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST
    assert baseline["stock_snapshot_source"] == "R2_OPERATIONAL_PHYSICAL"
    assert baseline.get("published_hpp_snapshot") in (None, "")

    events = await _events(db, item_id)
    opening = [row for row in events if row.get("source_type") == SOURCE_GOODS_RECEIPT]
    assert len(opening) == 1
    assert opening[0]["status"] == STATUS_RECOGNIZED
    assert opening[0]["qty"] == 100
    assert opening[0]["unit_cost"] == 1000
    assert opening[0]["source_key"] == f"gr:{receipt['id']}:{receipt['lines'][0]['line_id']}"

    settings = await db.app_settings.find_one({"key": SETTINGS_KEY})
    layers = await db[LAYERS].find(
        {"master_item_id": item_id, "calculation_version": settings["active_version"]},
        {"_id": 0},
    ).to_list(10)
    kinds = sorted(row["source_type"] for row in layers)
    assert kinds == [SOURCE_GOODS_RECEIPT, SOURCE_OPENING]
    by_kind = {row["source_type"]: row for row in layers}
    assert by_kind[SOURCE_OPENING]["recognized_qty"] == 500
    assert by_kind[SOURCE_OPENING]["unit_cost"] == 1000
    assert by_kind[SOURCE_GOODS_RECEIPT]["recognized_qty"] == 100
    assert by_kind[SOURCE_GOODS_RECEIPT]["unit_cost"] == 1000
    assert by_kind[SOURCE_OPENING]["layer_id"] == f"opening:{baseline['cutover_id']}:{item_id}"

    recon = await list_reconciliation(db, baseline["cutover_id"])
    row = next(item for item in recon if item["master_item_id"] == item_id)
    assert row["stock_fisik"] == 600
    assert row["valuation_qty"] == 600
    assert row["difference"] == 0


async def test_second_gr_uses_moving_average(db):
    await _org(db)
    item_id = await _item(db, "BOOT-B")
    await _legacy_state(db, item_id, 0)
    await _opening(db, item_id, 500)
    first_po = await _po(db, item_id, 100, 1000, "BOOT-B")
    await _receive(db, first_po, 100, idempotency_key="gr-boot-b1")
    second_po = await _po(db, item_id, 100, 1400, "BOOT-B")
    await _receive(db, second_po, 100, idempotency_key="gr-boot-b2")

    physical = await get_balance(db, item_id, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 700
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 700
    assert view["inventory_value"] == 740000
    assert view["current_hpp"] == money_round(740000 / 700)
    assert view["current_hpp"] == 1057.14

    baselines = await db[BASELINES].count_documents({"master_item_id": item_id})
    assert baselines == 1
    gr_events = await db[EVENTS].count_documents({
        "master_item_id": item_id,
        "source_type": SOURCE_GOODS_RECEIPT,
        "status": STATUS_RECOGNIZED,
    })
    assert gr_events == 2


async def test_first_gr_bootstrap_does_not_require_published_manual_hpp(db):
    await _org(db)
    item_id = await _item(db, "BOOT-NO-PUB")
    await _opening(db, item_id, 500)
    await db.customer_pricing_groups.insert_one({
        "id": "group-ignored",
        "enabled": True,
        "current_published_version_id": "ver-ignored",
    })
    await db.customer_pricing_versions.insert_one({
        "id": "ver-ignored",
        "pricing_group_id": "group-ignored",
        "status": "PUBLISHED",
        "hpp_source": "MANUAL_HPP",
        "products": [{
            "master_item_id": item_id,
            "accurate_item_id": item_id,
            "manual_hpp": 9999,
            "hpp_snapshot": 9999,
        }],
    })
    po = await _po(db, item_id, 100, 1000, "BOOT-NO-PUB")
    await _receive(db, po, 100)
    baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert baseline["opening_unit_cost"] == 1000
    assert baseline["opening_unit_cost"] != 9999
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 600000


async def test_bootstrap_uses_r2_opening_and_opname_qty(db):
    from services.stock_opname_sessions import (
        confirm_stock_opname_session,
        create_stock_opname_session,
        ensure_indexes as ensure_opname_indexes,
        record_stock_opname_counts,
        start_stock_opname_cutoff,
        submit_stock_opname_review,
    )

    await _org(db)
    await ensure_opname_indexes(db)
    open_id = await _item(db, "BOOT-OPEN")
    opname_id = await _item(db, "BOOT-OPNAME")
    await _opening(db, open_id, 500)
    await _opening(db, opname_id, 400)
    session = await create_stock_opname_session(db, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "idempotency_key": "opname-boot",
    }, USER)
    counting = await start_stock_opname_cutoff(db, session["id"], USER)
    opname_line = next(line for line in counting["lines"] if line["master_item_id"] == opname_id)
    lines = []
    for line in counting["lines"]:
        qty = 500 if line["master_item_id"] == opname_id else line["book_qty"]
        lines.append({
            "line_id": line["line_id"],
            "physical_count_qty": qty,
            "difference_qty": qty - int(line["book_qty"] or 0),
        })
    await record_stock_opname_counts(db, counting["id"], {"lines": lines}, USER)
    await submit_stock_opname_review(db, counting["id"], USER)
    await confirm_stock_opname_session(db, counting["id"], {"idempotency_key": "opname-boot-confirm"}, USER)
    assert opname_line["master_item_id"] == opname_id

    for item_id, code in ((open_id, "BOOT-OPEN"), (opname_id, "BOOT-OPNAME")):
        assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 500
        po = await _po(db, item_id, 100, 1000, code)
        await _receive(db, po, 100)
        baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
        assert baseline["stock_fisik_snapshot"] == 500
        assert baseline["pre_gr_operational_qty"] == 500
        view = _valuation(await build_inventory_valuation_rows(db), item_id)
        assert view["valuation_qty"] == 600
        assert view["current_hpp"] == 1000


async def test_legacy_product_stock_state_zero_does_not_block_rebuild(db):
    await _org(db)
    item_id = await _item(db, "BOOT-LEGACY0")
    await _legacy_state(db, item_id, 0)
    await _opening(db, item_id, 500)
    po = await _po(db, item_id, 100, 1000, "BOOT-LEGACY0")
    await _receive(db, po, 100)
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 0
    recon = await list_reconciliation(db)
    row = next(item for item in recon if item["master_item_id"] == item_id)
    assert row["stock_fisik"] == 600
    assert row["valuation_qty"] == 600
    assert row["hpp_status"] == "OK"
    settings = await db.app_settings.find_one({"key": SETTINGS_KEY})
    assert settings["last_recalculation_status"] == "SUCCESS"


async def test_exact_retry_keeps_one_opening_and_one_gr(db):
    await _org(db)
    item_id = await _item(db, "BOOT-RETRY")
    await _opening(db, item_id, 500)
    po = await _po(db, item_id, 100, 1000, "BOOT-RETRY")
    first = await _receive(db, po, 100, idempotency_key="gr-exact")
    second = await _receive(db, po, 100, idempotency_key="gr-exact")
    assert second["id"] == first["id"]
    assert await db[BASELINES].count_documents({"master_item_id": item_id}) == 1
    assert await db[EVENTS].count_documents({
        "master_item_id": item_id,
        "source_type": SOURCE_GOODS_RECEIPT,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
    }) == 1
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 600
    assert view["inventory_value"] == 600000


async def test_changed_payload_same_key_does_not_duplicate(db):
    await _org(db)
    item_id = await _item(db, "BOOT-CONFLICT")
    await _opening(db, item_id, 500)
    po = await _po(db, item_id, 200, 1000, "BOOT-CONFLICT")
    first = await _receive(db, po, 100, idempotency_key="gr-conflict")
    with pytest.raises(HTTPException) as conflict:
        await _receive(db, po, 50, idempotency_key="gr-conflict")
    assert conflict.value.status_code == 409
    assert await db.purchase_goods_receipts.count_documents({"idempotency_key": "gr-conflict"}) == 1
    assert await db[EVENTS].count_documents({
        "master_item_id": item_id,
        "source_type": SOURCE_GOODS_RECEIPT,
    }) == 1
    assert await db[BASELINES].count_documents({"master_item_id": item_id}) == 1
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 600
    assert first["id"]


async def test_missing_po_cost_stays_needs_cost_without_inventing_hpp(db):
    await _org(db)
    item_id = await _item(db, "BOOT-NEED")
    await _opening(db, item_id, 500)
    po = await _po(db, item_id, 100, 1000, "BOOT-NEED")
    await db.purchase_orders.update_one(
        {"id": po["id"]},
        {"$set": {"lines.0.unit_price": 0}},
    )
    receipt = await _receive(db, po, 100, idempotency_key="gr-need")
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 600
    event = await db[EVENTS].find_one({"source_key": {"$regex": f"^gr:{receipt['id']}:" }}, {"_id": 0})
    assert event["status"] == STATUS_NEEDS_COST
    assert event["unit_cost"] is None
    assert event["qty"] is None
    assert await db[BASELINES].count_documents({"master_item_id": item_id}) == 0
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 0
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["current_hpp"] is None
    assert view["inventory_value"] is None


async def test_later_valid_retry_completes_valuation(db):
    await _org(db)
    item_id = await _item(db, "BOOT-RECOVER")
    await _legacy_state(db, item_id, 0)
    await _opening(db, item_id, 500)
    po = await _po(db, item_id, 100, 1000, "BOOT-RECOVER")
    await db.purchase_orders.update_one(
        {"id": po["id"]},
        {"$set": {"lines.0.unit_price": 0}},
    )
    first = await _receive(db, po, 100, idempotency_key="gr-recover")
    event = await db[EVENTS].find_one({"source_key": {"$regex": f"^gr:{first['id']}:" }}, {"_id": 0})
    assert event["status"] == STATUS_NEEDS_COST

    await db.purchase_orders.update_one(
        {"id": po["id"]},
        {"$set": {"lines.0.unit_price": 1000}},
    )
    again = await _receive(db, po, 100, idempotency_key="gr-recover")
    assert again["id"] == first["id"]
    repaired = await db[EVENTS].find_one({"source_key": event["source_key"]}, {"_id": 0})
    assert repaired["status"] == STATUS_RECOGNIZED
    assert repaired["unit_cost"] == 1000
    assert repaired["qty"] == 100
    assert await db[EVENTS].count_documents({
        "master_item_id": item_id,
        "source_type": SOURCE_GOODS_RECEIPT,
    }) == 1
    baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert baseline["stock_fisik_snapshot"] == 500
    assert baseline["opening_unit_cost"] == 1000
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["valuation_qty"] == 600
    assert view["current_hpp"] == 1000
    assert view["inventory_value"] == 600000
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 0


async def test_existing_valid_baseline_is_not_rebootstrapped(db):
    await _org(db)
    item_id = await _item(db, "BOOT-KEEP")
    await _opening(db, item_id, 500)
    first_po = await _po(db, item_id, 100, 1000, "BOOT-KEEP")
    await _receive(db, first_po, 100)
    before = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    second_po = await _po(db, item_id, 100, 1400, "BOOT-KEEP")
    await _receive(db, second_po, 100)
    after = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert after["opening_unit_cost"] == before["opening_unit_cost"] == 1000
    assert after["stock_fisik_snapshot"] == before["stock_fisik_snapshot"] == 500
    assert after["cutover_id"] == before["cutover_id"]
    view = _valuation(await build_inventory_valuation_rows(db), item_id)
    assert view["current_hpp"] == 1057.14
