"""Published HPP fallback and lazy per-product purchasing bootstrap."""
import copy
import os
import sys
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
if "replicaSet=" not in os.environ["MONGO_URL"]:
    os.environ["MONGO_URL"] = os.environ["MONGO_URL"].rstrip("/") + (
        "&" if "?" in os.environ["MONGO_URL"] else "?"
    ) + "replicaSet=eternal_local_rs"

from services.hpp_cutover import SOURCE_FIRST_GR_UNIT_COST, ensure_indexes, list_reconciliation
from services.inventory_valuation import build_inventory_valuation_rows
from services.physical_stock import ensure_indexes as ensure_stock_indexes
from services.purchase_goods_receipts import create_purchase_goods_receipt

DB_NAME = "eternal_hpp_published_fallback_test"
USER = {"id": "holding", "name": "Holding", "email": "holding@test", "role": "holding_team"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    await ensure_stock_indexes(database)
    await ensure_indexes(database)
    await database.hpp_cost_versions.create_index("id", unique=True)
    await database.hpp_cost_layers.create_index(
        [("calculation_version", 1), ("layer_id", 1)],
        unique=True,
    )
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.app_settings.create_index("key", unique=True)
    yield database
    await client.drop_database(DB_NAME)
    client.close()


async def _master(db, suffix, stock, unit="PCS"):
    mid = f"mid-{suffix}"
    aid = f"aid-{suffix}"
    await db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": aid,
        "item_code": f"CODE-{suffix}",
        "item_name": f"Product {suffix}",
        "item_type": "INVENTORY",
        "unit": unit,
        "is_active": True,
    })
    await db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "base_uom": unit,
        "total_qty": stock,
        "unprinted_qty": stock,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    return mid, aid


def _product(mid, aid, hpp):
    return {
        "master_item_id": mid,
        "accurate_item_id": aid,
        "display_name": aid,
        "manual_hpp": hpp,
        "hpp_snapshot": hpp,
        "hpp_status": "OK" if hpp else "MISSING",
        "tier_prices": [999],
    }


async def _pricing(
    db,
    suffix,
    mid,
    aid,
    *,
    published=None,
    draft=None,
    historical=None,
):
    pub_id = f"pub-{suffix}" if published is not None else None
    draft_id = f"draft-{suffix}" if draft is not None else None
    await db.customer_pricing_groups.insert_one({
        "id": f"group-{suffix}",
        "group_name": suffix,
        "enabled": True,
        "current_published_version_id": pub_id,
        "current_draft_version_id": draft_id,
    })
    if historical is not None:
        await db.customer_pricing_versions.insert_one({
            "id": f"old-{suffix}",
            "pricing_group_id": f"group-{suffix}",
            "version_number": 1,
            "revision": 1,
            "status": "PUBLISHED",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": "MANUAL_HPP",
            "products": [_product(mid, aid, historical)],
        })
    if pub_id:
        await db.customer_pricing_versions.insert_one({
            "id": pub_id,
            "pricing_group_id": f"group-{suffix}",
            "version_number": 2,
            "revision": 7,
            "status": "PUBLISHED",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": "MANUAL_HPP",
            "published_at": "2026-09-22T00:00:00+00:00",
            "products": [_product(mid, aid, published)],
        })
    if draft_id:
        await db.customer_pricing_versions.insert_one({
            "id": draft_id,
            "pricing_group_id": f"group-{suffix}",
            "version_number": 3,
            "revision": 2,
            "status": "DRAFT",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": "MANUAL_HPP",
            "products": [_product(mid, aid, draft)],
        })


async def _po(db, suffix, mid, aid, qty, unit, cost):
    po_id = f"po-{suffix}"
    line_id = f"line-{suffix}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-{suffix}",
        "supplier": "Supplier",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "delivery_confirmed",
        },
        "lines": [{
            "line_id": line_id,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": aid,
            "item_name": aid,
            "unit": unit,
            "ordered_qty": qty,
            "unit_price": cost,
        }],
    })
    return po_id, line_id


async def _receive(db, suffix, po_id, line_id, qty):
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await _r2(db, "org-seed", 0)
        await db.physical_stock_balances.delete_many({"master_item_id": "org-seed"})
    return await create_purchase_goods_receipt(db, {
        "purchase_order_id": po_id,
        "idempotency_key": f"gr-{suffix}",
        "lines": [{"purchase_order_line_id": line_id, "received_qty": qty}],
    }, USER)


async def _r2(db, mid, qty):
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await db.branches.insert_one({
            "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": "wh-ygy", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        })
    await db.physical_stock_balances.insert_one({
        "id": f"bal-{mid}",
        "master_item_id": mid,
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-ygy",
        "physical_qty": qty,
    })


def _valuation(view, mid):
    return next(row for row in view["rows"] if row["master_item_id"] == mid)


@pytest.mark.anyio
async def test_published_fallback_is_immediate_and_draft_history_are_ignored(db):
    pub_mid, pub_aid = await _master(db, "pub205", 10)
    await _r2(db, pub_mid, 10)
    await _pricing(
        db,
        "pub205",
        pub_mid,
        pub_aid,
        published=205,
        draft=220,
        historical=180,
    )
    draft_mid, draft_aid = await _master(db, "draft-only", 8)
    await _r2(db, draft_mid, 8)
    await _pricing(db, "draft-only", draft_mid, draft_aid, draft=999)
    newer_mid, newer_aid = await _master(db, "pub290", 15999)
    await _r2(db, newer_mid, 15999)
    await _pricing(db, "pub290", newer_mid, newer_aid, published=290, draft=310)
    unsafe_mid, unsafe_aid = await _master(db, "uninitialized", 0)
    await db.product_stock_state.delete_one({"master_item_id": unsafe_mid})
    await _pricing(db, "uninitialized", unsafe_mid, unsafe_aid, published=500)

    view = await build_inventory_valuation_rows(db)
    published = _valuation(view, pub_mid)
    assert published["current_hpp"] == 205
    assert published["inventory_value"] == 2050
    assert published["hpp_status"] == "OK"
    assert published["hpp_source"] == "PUBLISHED_CUSTOMER_PRICING_FALLBACK"
    missing = _valuation(view, draft_mid)
    assert missing["current_hpp"] is None
    assert missing["inventory_value"] is None
    assert missing["hpp_status"] == "MISSING_COST"
    bsm = _valuation(view, newer_mid)
    assert bsm["current_hpp"] == 290
    assert bsm["inventory_value"] == 4_639_710
    unsafe = _valuation(view, unsafe_mid)
    assert unsafe["stock_fisik"] == 0
    assert unsafe["current_hpp"] is None
    assert unsafe["inventory_value"] is None
    assert unsafe["hpp_status"] == "MISSING_COST"
    assert unsafe["hpp_reason"] != "STOCK_STATE_UNAVAILABLE"
    assert view["summary"]["total_inventory_value"] == 4_641_760
    assert view["summary"]["valued_items"] == 2
    assert view["summary"]["problem_items"] == 0


@pytest.mark.anyio
async def test_canonical_layer_wins_and_later_published_change_is_ignored(db):
    mid, aid = await _master(db, "canonical", 10)
    await _r2(db, mid, 10)
    await _pricing(db, "canonical", mid, aid, published=290)
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "active_version": "canonical-v1",
    })
    await db.hpp_cost_versions.insert_one({
        "id": "canonical-v1",
        "status": "ACTIVE",
        "method": "RATA-RATA",
    })
    await db.hpp_cost_layers.insert_one({
        "layer_id": "canonical-layer",
        "calculation_version": "canonical-v1",
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_identity": f"a:{aid}",
        "recognized_at": "2026-09-22T01:00:00+00:00",
        "recognized_qty": 10,
        "remaining_qty": 10,
        "unit_cost": 209.09,
        "total_cost": 2090.9,
        "source_type": "GOODS_RECEIPT",
    })
    first = _valuation(await build_inventory_valuation_rows(db), mid)
    assert first["current_hpp"] == 209.09
    assert first["hpp_source"] == "PURCHASING_MOVING_AVERAGE"
    await db.customer_pricing_versions.update_one(
        {"id": "pub-canonical"},
        {"$set": {"products.0.hpp_snapshot": 230, "revision": 8}},
    )
    second = _valuation(await build_inventory_valuation_rows(db), mid)
    assert second["current_hpp"] == 209.09
    assert second["inventory_value"] == 2090.9


@pytest.mark.anyio
async def test_first_and_second_future_gr_bootstrap_without_historical_replay(db):
    mid, aid = await _master(db, "future", 10_000)
    await _r2(db, mid, 10_000)
    await _pricing(db, "future", mid, aid, published=205)
    await db.purchase_orders.insert_one({
        "id": "historical-po",
        "po_number": "PB-HIST",
        "lines": [{
            "line_id": "historical-line",
            "master_item_id": mid,
            "accurate_item_id": aid,
            "unit": "PCS",
            "ordered_qty": 500,
            "unit_price": 180,
        }],
    })
    await db.purchase_goods_receipts.insert_one({
        "id": "historical-gr",
        "receipt_number": "GR-HIST",
        "purchase_order_id": "historical-po",
        "lines": [{
            "line_id": "historical-gr-line",
            "purchase_order_line_id": "historical-line",
            "master_item_id": mid,
            "accurate_item_id": aid,
            "received_qty": 500,
            "unit_price": 180,
        }],
    })
    po1, line1 = await _po(db, "future-1", mid, aid, 1000, "PCS", 250)
    await _receive(db, "future-1", po1, line1, 1000)
    cutover = await db.hpp_valuation_cutovers.find_one({"status": "ACTIVE"}, {"_id": 0})
    assert cutover["source"] == "PURCHASING_AUTO_BOOTSTRAP"
    first = _valuation(await build_inventory_valuation_rows(db), mid)
    assert first["stock_fisik"] == 11_000
    assert first["valuation_qty"] == 11_000
    assert first["inventory_value"] == 2_750_000
    assert first["hpp_source"] == "PURCHASING_MOVING_AVERAGE"
    recon = await list_reconciliation(db, cutover["cutover_id"])
    assert next(row for row in recon if row["master_item_id"] == mid)["current_hpp"] == 250
    layers = await db.hpp_cost_layers.find(
        {"calculation_version": (await db.app_settings.find_one({"key": "hpp_valuation"}))["active_version"]},
        {"_id": 0},
    ).to_list(10)
    assert len(layers) == 2
    assert all(layer.get("source_id") != "historical-gr" for layer in layers)
    baseline = await db.hpp_valuation_baseline_lines.find_one(
        {"cutover_id": cutover["cutover_id"], "master_item_id": mid},
        {"_id": 0},
    )
    assert baseline["stock_fisik_snapshot"] == 10_000
    assert baseline["opening_unit_cost"] == 250
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST

    po2, line2 = await _po(db, "future-2", mid, aid, 1000, "PCS", 300)
    pricing_before = copy.deepcopy(
        await db.customer_pricing_versions.find_one({"id": "pub-future"}, {"_id": 0})
    )
    await _receive(db, "future-2", po2, line2, 1000)
    second = _valuation(await build_inventory_valuation_rows(db), mid)
    assert second["stock_fisik"] == 12_000
    assert second["valuation_qty"] == 12_000
    assert second["inventory_value"] == 3_050_000
    recon = await list_reconciliation(db, cutover["cutover_id"])
    assert next(row for row in recon if row["master_item_id"] == mid)["exact_average"].startswith("254.166")
    assert await db.customer_pricing_versions.find_one(
        {"id": "pub-future"},
        {"_id": 0},
    ) == pricing_before


@pytest.mark.anyio
async def test_missing_published_future_gr_bootstraps_from_receipt_cost(db):
    mid, aid = await _master(db, "missing", 100)
    await _r2(db, mid, 100)
    po, line = await _po(db, "missing", mid, aid, 10, "PCS", 250)
    receipt = await _receive(db, "missing", po, line, 10)
    event = await db.hpp_recognition_events.find_one(
        {"source_key": {"$regex": f"^gr:{receipt['id']}:"}},
        {"_id": 0},
    )
    assert event["status"] == "RECOGNIZED"
    assert event["unit_cost"] == 250
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 1
    row = _valuation(await build_inventory_valuation_rows(db), mid)
    assert row["stock_fisik"] == 110
    assert row["valuation_qty"] == 110
    assert row["current_hpp"] == 250
    assert row["inventory_value"] == 27500
    baseline = await db.hpp_valuation_baseline_lines.find_one({"master_item_id": mid}, {"_id": 0})
    assert baseline["opening_unit_cost"] == 250
    assert baseline["stock_fisik_snapshot"] == 100
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST


@pytest.mark.anyio
async def test_first_gr_base_uom_uses_receipt_base_cost(db):
    mid, aid = await _master(db, "uom", 1000)
    await _r2(db, mid, 1000)
    await db.product_unit_settings.insert_one({
        "master_item_id": mid,
        "base_uom": "PCS",
        "transaction_uom": "DUS",
        "conversion_factor": 1000,
    })
    po, line = await _po(db, "uom", mid, aid, 1000, "PCS", 500)
    await _receive(db, "uom", po, line, 1000)
    row = _valuation(await build_inventory_valuation_rows(db), mid)
    assert row["stock_fisik"] == 2000
    assert row["valuation_qty"] == 2000
    assert row["current_hpp"] == 500
    assert row["inventory_value"] == 1_000_000
    event = await db.hpp_recognition_events.find_one(
        {"source_type": "GOODS_RECEIPT"},
        {"_id": 0},
    )
    assert event["transaction_qty"] == 1000
    assert event["base_qty"] == 1000
    assert event["unit_cost"] == 500
    baseline = await db.hpp_valuation_baseline_lines.find_one({"master_item_id": mid}, {"_id": 0})
    assert baseline["opening_unit_cost"] == 500
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST
