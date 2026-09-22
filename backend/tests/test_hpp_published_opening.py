"""Published-only Customer Pricing source for one-time inventory HPP opening."""
import asyncio
import copy
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")

from services.hpp_cutover import ensure_indexes, list_reconciliation
from services.hpp_published_opening import (
    SOURCE,
    STATUS_INVALID_HPP,
    STATUS_MISSING_PUBLISHED,
    STATUS_READY,
    STATUS_STOCK_PROBLEM,
    build_preview,
    confirm_published_opening,
)
from services.hpp_valuation import recalculate_hpp
from services.purchase_goods_receipts import create_purchase_goods_receipt

DB_NAME = "eternal_hpp_published_opening_test"
USER = {"id": "holding", "name": "Holding", "email": "holding@test", "role": "holding_team"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    await ensure_indexes(database)
    await database.hpp_cost_versions.create_index("id", unique=True)
    await database.hpp_cost_layers.create_index(
        [("calculation_version", 1), ("layer_id", 1)],
        unique=True,
    )
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.app_settings.create_index("key", unique=True)
    yield database
    await client.drop_database(DB_NAME)
    client.close()


async def _master(db, suffix, stock, *, initialized=True):
    mid = f"mid-{suffix}"
    aid = f"aid-{suffix}"
    await db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": aid,
        "item_code": f"CODE-{suffix}",
        "item_name": f"Product {suffix}",
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    })
    if initialized:
        await db.product_stock_state.insert_one({
            "_id": mid,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "base_uom": "PCS",
            "total_qty": stock,
            "unprinted_qty": max(float(stock), 0),
            "printed_qty": 0,
            "status": "INITIALIZED",
            "initialized": True,
            "pending_event": None,
            "version": 1,
        })
    return mid, aid


def _product(mid, aid, snapshot, *, manual=None):
    return {
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": aid,
        "display_name": aid,
        "manual_hpp": snapshot if manual is None else manual,
        "hpp_snapshot": snapshot,
        "hpp_status": "OK" if snapshot else "MISSING",
        "tier_prices": [999],
    }


async def _pricing(
    db,
    suffix,
    mid,
    aid,
    *,
    published_hpp=None,
    draft_hpp=None,
    old_published_hpp=None,
    hpp_source="MANUAL_HPP",
    published_master_id=None,
):
    group_id = f"group-{suffix}"
    published_id = f"published-{suffix}" if published_hpp is not None else None
    draft_id = f"draft-{suffix}" if draft_hpp is not None else None
    await db.customer_pricing_groups.insert_one({
        "id": group_id,
        "group_name": f"Group {suffix}",
        "enabled": True,
        "current_published_version_id": published_id,
        "current_draft_version_id": draft_id,
    })
    if old_published_hpp is not None:
        await db.customer_pricing_versions.insert_one({
            "id": f"published-old-{suffix}",
            "pricing_group_id": group_id,
            "version_number": 1,
            "revision": 1,
            "status": "PUBLISHED",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": "MANUAL_HPP",
            "products": [_product(mid, aid, old_published_hpp)],
        })
    if published_id:
        product = _product(
            published_master_id if published_master_id is not None else mid,
            aid,
            published_hpp,
            manual=(published_hpp - 1 if isinstance(published_hpp, (int, float)) else published_hpp),
        )
        await db.customer_pricing_versions.insert_one({
            "id": published_id,
            "pricing_group_id": group_id,
            "group_name": f"Group {suffix}",
            "version_number": 2,
            "revision": 7,
            "status": "PUBLISHED",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": hpp_source,
            "published_at": "2026-09-22T00:00:00+00:00",
            "products": [product],
        })
    if draft_id:
        await db.customer_pricing_versions.insert_one({
            "id": draft_id,
            "pricing_group_id": group_id,
            "version_number": 3,
            "revision": 2,
            "status": "DRAFT",
            "pricing_mode": "HPP_FORMULA",
            "hpp_source": "MANUAL_HPP",
            "products": [_product(mid, aid, draft_hpp)],
        })
    return group_id, published_id, draft_id


def _row(preview, mid):
    return next(row for row in preview["rows"] if row["master_item_id"] == mid)


@pytest.mark.anyio
async def test_current_published_snapshot_wins_over_newer_draft_and_history(db):
    mid, aid = await _master(db, "published", 10)
    await _pricing(
        db,
        "published",
        mid,
        aid,
        published_hpp=205,
        draft_hpp=220,
        old_published_hpp=180,
    )
    preview = await build_preview(db)
    row = _row(preview, mid)
    assert row["status"] == STATUS_READY
    assert row["published_hpp"] == 205
    assert row["opening_hpp"] == 205
    assert row["opening_value"] == 2050
    assert row["published_pricing_version_id"] == "published-published"
    assert row["published_version_number"] == 2
    assert row["published_revision"] == 7


@pytest.mark.anyio
async def test_draft_only_zero_negative_invalid_auto_and_stable_mapping(db):
    pos_mid, pos_aid = await _master(db, "draft-only", 5)
    await _pricing(db, "draft-only", pos_mid, pos_aid, draft_hpp=220)
    zero_mid, _ = await _master(db, "zero-missing", 0)
    neg_mid, neg_aid = await _master(db, "negative", -1)
    await _pricing(db, "negative", neg_mid, neg_aid, published_hpp=100)
    auto_mid, auto_aid = await _master(db, "auto", 3)
    await _pricing(
        db,
        "auto",
        auto_mid,
        auto_aid,
        published_hpp=999,
        hpp_source="AUTO_HPP",
    )
    map_mid, map_aid = await _master(db, "mapping", 2)
    await _pricing(
        db,
        "mapping",
        map_mid,
        map_aid,
        published_hpp=300,
        published_master_id="different-master",
    )
    preview = await build_preview(db)
    assert _row(preview, pos_mid)["status"] == STATUS_MISSING_PUBLISHED
    assert _row(preview, pos_mid)["blocking"] is True
    assert _row(preview, zero_mid)["status"] == STATUS_MISSING_PUBLISHED
    assert _row(preview, zero_mid)["blocking"] is False
    assert _row(preview, zero_mid)["opening_hpp"] is None
    assert _row(preview, neg_mid)["status"] == STATUS_STOCK_PROBLEM
    assert _row(preview, auto_mid)["status"] == STATUS_INVALID_HPP
    assert _row(preview, auto_mid)["published_hpp"] is None
    assert _row(preview, map_mid)["status"] == STATUS_INVALID_HPP
    assert preview["can_confirm"] is False


@pytest.mark.anyio
async def test_preview_is_pure_read_and_stale_stock_or_published_price_blocks(db):
    mid, aid = await _master(db, "stale", 10)
    await _pricing(db, "stale", mid, aid, published_hpp=500)
    before = {
        name: await db[name].count_documents({})
        for name in (
            "hpp_cost_versions",
            "hpp_cost_layers",
            "hpp_valuation_cutovers",
            "hpp_valuation_baseline_lines",
            "hpp_recognition_events",
        )
    }
    preview = await build_preview(db)
    assert before == {
        name: await db[name].count_documents({})
        for name in before
    }
    await db.product_stock_state.update_one(
        {"master_item_id": mid},
        {"$set": {"total_qty": 11, "unprinted_qty": 11}},
    )
    with pytest.raises(HTTPException) as stock_error:
        await confirm_published_opening(db, {
            "preview_token": preview["preview_token"],
            "client_request_id": "stale-stock",
        }, USER)
    assert stock_error.value.status_code == 409
    assert stock_error.value.detail["code"] == "STALE_PREVIEW"
    assert await db.hpp_valuation_cutovers.count_documents({}) == 0

    preview = await build_preview(db)
    await db.customer_pricing_versions.update_one(
        {"id": "published-stale"},
        {"$set": {"products.0.hpp_snapshot": 525}},
    )
    with pytest.raises(HTTPException) as pricing_error:
        await confirm_published_opening(db, {
            "preview_token": preview["preview_token"],
            "client_request_id": "stale-pricing",
        }, USER)
    assert pricing_error.value.detail["code"] == "STALE_PREVIEW"
    assert await db.hpp_valuation_cutovers.count_documents({}) == 0


@pytest.mark.anyio
async def test_confirm_is_idempotent_auditable_and_never_replays_historical_gr(db, monkeypatch):
    from services import hpp_valuation

    mid, aid = await _master(db, "confirm", 100)
    group_id, published_id, _ = await _pricing(
        db,
        "confirm",
        mid,
        aid,
        published_hpp=1000,
    )
    await db.purchase_orders.insert_one({
        "id": "historical-po",
        "po_number": "PB-HIST",
        "lines": [{
            "line_id": "historical-line",
            "master_item_id": mid,
            "accurate_item_id": aid,
            "unit": "PCS",
            "ordered_qty": 50,
            "unit_price": 800,
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
            "received_qty": 50,
            "unit_price": 800,
        }],
    })

    async def replay_forbidden(*_args, **_kwargs):
        raise AssertionError("historical GR replayed")

    monkeypatch.setattr(hpp_valuation, "_load_source_docs", replay_forbidden)
    preview = await build_preview(db)
    result = await confirm_published_opening(db, {
        "preview_token": preview["preview_token"],
        "client_request_id": "confirm-once",
    }, USER)
    assert result["cutover"]["status"] == "ACTIVE"
    assert result["cutover"]["source"] == SOURCE
    version_id = result["settings"]["active_version"]
    layers = await db.hpp_cost_layers.find(
        {"calculation_version": version_id},
        {"_id": 0},
    ).to_list(10)
    assert len(layers) == 1
    assert layers[0]["source_type"] == "OPENING_HPP"
    assert layers[0]["remaining_qty"] == 100
    assert layers[0]["unit_cost"] == 1000
    assert layers[0]["total_cost"] == 100000
    baseline = await db.hpp_valuation_baseline_lines.find_one(
        {"cutover_id": result["cutover"]["cutover_id"]},
        {"_id": 0},
    )
    assert baseline["provenance_source"] == SOURCE
    assert baseline["published_pricing_group_id"] == group_id
    assert baseline["published_pricing_version_id"] == published_id
    assert baseline["published_revision"] == 7
    assert baseline["published_hpp_snapshot"] == 1000
    assert baseline["stock_fisik_snapshot"] == 100

    retried = await confirm_published_opening(db, {
        "preview_token": preview["preview_token"],
        "client_request_id": "confirm-once",
    }, USER)
    assert retried["idempotent"] is True
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 1
    assert await db.hpp_valuation_baseline_lines.count_documents({}) == 1


@pytest.mark.anyio
async def test_two_concurrent_confirms_have_one_winner(db):
    mid, aid = await _master(db, "concurrent", 10)
    await _pricing(db, "concurrent", mid, aid, published_hpp=200)
    preview = await build_preview(db)

    async def submit(request_id):
        try:
            return await confirm_published_opening(db, {
                "preview_token": preview["preview_token"],
                "client_request_id": request_id,
            }, USER)
        except HTTPException as exc:
            return exc

    results = await asyncio.gather(submit("concurrent-a"), submit("concurrent-b"))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, HTTPException) and row.status_code == 409 for row in results) == 1
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 1


@pytest.mark.anyio
async def test_future_gr_updates_average_and_pricing_stays_independent(db):
    mid, aid = await _master(db, "future", 100)
    await _pricing(db, "future", mid, aid, published_hpp=1000)
    preview = await build_preview(db)
    opened = await confirm_published_opening(db, {
        "preview_token": preview["preview_token"],
        "client_request_id": "future-opening",
    }, USER)
    version_before = opened["settings"]["active_version"]
    layer_before = await db.hpp_cost_layers.find_one(
        {"calculation_version": version_before, "master_item_id": mid},
        {"_id": 0},
    )
    await db.customer_pricing_versions.update_one(
        {"id": "published-future"},
        {"$set": {"products.0.hpp_snapshot": 1100, "revision": 8}},
    )
    layer_after_publish = await db.hpp_cost_layers.find_one(
        {"calculation_version": version_before, "master_item_id": mid},
        {"_id": 0},
    )
    assert layer_after_publish == layer_before

    po_line_id = "future-po-line"
    await db.purchase_orders.insert_one({
        "id": "future-po",
        "po_number": "PB-FUTURE",
        "supplier": "Supplier",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery",
        },
        "lines": [{
            "line_id": po_line_id,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": aid,
            "item_name": aid,
            "unit": "PCS",
            "ordered_qty": 50,
            "unit_price": 1200,
        }],
    })
    pricing_before_gr = copy.deepcopy(
        await db.customer_pricing_versions.find_one(
            {"id": "published-future"},
            {"_id": 0},
        )
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "future-po",
        "idempotency_key": "future-gr",
        "lines": [{"purchase_order_line_id": po_line_id, "received_qty": 50}],
    }, USER)
    pricing_after_gr = await db.customer_pricing_versions.find_one(
        {"id": "published-future"},
        {"_id": 0},
    )
    assert pricing_after_gr == pricing_before_gr
    reconciliation = await list_reconciliation(
        db,
        opened["cutover"]["cutover_id"],
    )
    row = next(item for item in reconciliation if item["master_item_id"] == mid)
    assert row["stock_fisik"] == 150
    assert row["valuation_qty"] == 150
    assert row["valuation_value"] == 160000
    assert row["exact_average"].startswith("1066.666")

    pricing_before_recalc = copy.deepcopy(pricing_after_gr)
    await recalculate_hpp(
        db,
        method="RATA-RATA",
        recognition_point="goods_receipt",
        user=USER,
    )
    assert await db.customer_pricing_versions.find_one(
        {"id": "published-future"},
        {"_id": 0},
    ) == pricing_before_recalc
