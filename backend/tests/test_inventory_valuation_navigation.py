"""Deep link into Nilai Persediaan by exact master_item_id. Read-only."""
import os
import sys
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")

from services.inventory_valuation import (
    build_inventory_valuation_rows,
    filter_sort_page,
    lookup_inventory_valuation_item,
)
from services.current_stock import build_stock_rows
from services.stock_engine_v2 import build_stock_rows_v2

DB_NAME = "eternal_inventory_valuation_navigation_test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    yield database
    await client.drop_database(DB_NAME)
    client.close()


async def _master(db, mid, *, code, name, item_type="INVENTORY", active=True, stock=None, aid=None):
    await db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": aid if aid is not None else f"aid-{mid}",
        "item_code": code,
        "item_name": name,
        "item_type": item_type,
        "unit": "PCS",
        "is_active": active,
    })
    if stock is None:
        return
    await db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "base_uom": "PCS",
        "total_qty": stock,
        "status": "INITIALIZED",
        "initialized": True,
    })


async def _counts(db):
    names = [
        "master_items",
        "product_stock_state",
        "stock_movements",
        "hpp_cost_layers",
        "journal_entries",
        "financial_inventory_openings",
        "item_stock_manual",
    ]
    return {name: await db[name].count_documents({}) for name in names}


@pytest.mark.anyio
async def test_lookup_uses_builder_row_beyond_first_page_and_ignores_similar_identity(db):
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "active_version": "nav-version",
    })
    await db.app_settings.insert_one({"key": "physical_stock", "enabled": False})
    await db.item_stock_manual.insert_one({
        "master_item_id": "mid-target",
        "manual_current_qty": 99,
        "manual_purchase_qty": 50,
    })
    for index in range(54):
        await _master(
            db,
            f"mid-{index:03d}",
            code=f"CUP-{index:03d}",
            name=f"Cup Polos {index:03d}",
            stock=1,
        )
    await _master(
        db,
        "mid-target",
        code="CUP-1000",
        name="ZZZ Cup Polos",
        stock=4,
        aid="acc-target",
    )
    await _master(
        db,
        "mid-twin",
        code="CUP-100",
        name="ZZZ Cup Polos",
        stock=8,
        aid="acc-twin",
    )
    await _master(db, "mid-no-hpp", code="NOHPP", name="Cup tanpa HPP", stock=3)
    await _master(db, "mid-service", code="SVC", name="Jasa Cetak", item_type="SERVICE", stock=5)
    await _master(db, "mid-non", code="NON", name="Non stok", item_type="NON_INVENTORY", stock=6)
    await _master(db, "mid-off", code="OFF", name="Cup nonaktif", active=False, stock=7)
    await _master(db, "mid-widget", code="WID", name="Widget", item_type="WIDGET", stock=2)
    await db.hpp_cost_layers.insert_one({
        "layer_id": "layer-target",
        "calculation_version": "nav-version",
        "master_item_id": "mid-target",
        "recognized_at": "2026-09-22T10:00:00+00:00",
        "recognized_qty": 4,
        "remaining_qty": 4,
        "unit_cost": 2500,
        "total_cost": 10000,
        "source_type": "GOODS_RECEIPT",
    })

    before = await _counts(db)
    view = await build_inventory_valuation_rows(db)
    built = {row["master_item_id"]: row for row in view["rows"]}
    page = filter_sort_page(view["rows"], sort_by="name", sort_dir="asc", page=1, page_size=50)
    page_ids = {row["master_item_id"] for row in page["items"]}
    assert "mid-target" not in page_ids
    assert "mid-twin" not in page_ids

    target = await lookup_inventory_valuation_item(db, "mid-target")
    twin = await lookup_inventory_valuation_item(db, "mid-twin")
    missing_hpp = await lookup_inventory_valuation_item(db, "mid-no-hpp")
    service = await lookup_inventory_valuation_item(db, "mid-service")
    non_inventory = await lookup_inventory_valuation_item(db, "mid-non")
    inactive = await lookup_inventory_valuation_item(db, "mid-off")
    by_code = await lookup_inventory_valuation_item(db, "CUP-1000")
    by_accurate = await lookup_inventory_valuation_item(db, "acc-target")
    by_name = await lookup_inventory_valuation_item(db, "ZZZ Cup Polos")
    missing_id = await lookup_inventory_valuation_item(db, "  ")
    after = await _counts(db)

    assert target["covered"] is True
    assert target["matched_by"] == "master_item_id"
    assert target["item"] == built["mid-target"]
    assert target["inventory_value"] == built["mid-target"]["inventory_value"]
    assert target["inventory_value"] == 10000
    assert target["item"]["stock_fisik"] == 4
    assert target["item"]["stock_fisik"] != 99
    assert target["book_value_per_item"] is None
    assert target["writes"] == 0

    assert twin["item"]["master_item_id"] == "mid-twin"
    assert twin["item"]["item_code"] == "CUP-100"
    assert twin["inventory_value"] != target["inventory_value"]

    assert missing_hpp["item"] == built["mid-no-hpp"]
    assert missing_hpp["inventory_value"] is None
    assert missing_hpp["item"]["hpp_status"] == "MISSING_COST"

    assert "mid-service" not in built
    assert "mid-widget" in built
    assert "mid-non" in built
    assert service["covered"] is False
    assert service["item"] is None
    assert service["inventory_value"] is None
    assert service["reason_code"] == "NOT_COVERED"
    assert "Jasa" in service["reason"]
    assert service["book_value_per_item"] is None

    assert non_inventory["item"] == built["mid-non"]
    assert non_inventory["inventory_value"] is None

    assert "mid-off" not in built
    assert inactive["covered"] is False
    assert inactive["reason_code"] == "INACTIVE"
    assert inactive["inventory_value"] is None
    assert inactive["item"] is None

    for missed in (by_code, by_accurate, by_name):
        assert missed["covered"] is False
        assert missed["reason_code"] == "NOT_FOUND"
        assert missed["inventory_value"] is None
        assert missed["item"] is None

    assert missing_id["reason_code"] == "MISSING_MASTER_ITEM_ID"
    assert missing_id["inventory_value"] is None
    assert after == before


def test_stock_rows_keep_formula_and_expose_type_for_navigation():
    legacy = build_stock_rows(
        [{
            "id": "m1",
            "accurate_item_id": "a1",
            "item_code": "C",
            "item_name": "Cup",
            "unit": "PCS",
            "item_type": "INVENTORY",
            "is_active": True,
        }],
        [],
        [{"master_item_id": "m1", "manual_current_qty": 10, "manual_purchase_qty": 2}],
    )
    assert legacy[0]["stock_current"] == 12
    assert legacy[0]["item_type"] == "INVENTORY"
    assert legacy[0]["is_active"] is True

    v2 = build_stock_rows_v2(
        [
            {"id": "m1", "item_code": "A", "item_name": "A", "unit": "PCS", "item_type": "INVENTORY", "is_active": True},
            {"id": "", "accurate_item_id": "only-aid", "item_code": "D", "item_name": "D", "unit": "PCS", "item_type": "INVENTORY"},
            {"id": "svc", "item_code": "S", "item_name": "S", "unit": "PCS", "item_type": "SERVICE", "is_active": True},
            {"id": "non", "item_code": "N", "item_name": "N", "unit": "PCS", "item_type": "NON_INVENTORY", "is_active": True},
        ],
        state_by_master={"m1": {"initialized": True, "status": "INITIALIZED", "total_qty": 5, "unprinted_qty": 5, "printed_qty": 0}},
        outstanding_bucket={"m:m1": 2.0},
        outstanding_alias={"m:m1": "m:m1"},
    )
    by_code = {row["item_code"]: row for row in v2}
    assert by_code["A"]["stock_current"] == 3
    assert by_code["A"]["available_qty"] == 3
    assert by_code["A"]["physical_qty"] == 5
    assert by_code["A"]["item_type"] == "INVENTORY"
    assert by_code["D"]["master_item_id"] is None
    assert by_code["S"]["item_type"] == "SERVICE"
    assert by_code["N"]["item_type"] == "NON_INVENTORY"
