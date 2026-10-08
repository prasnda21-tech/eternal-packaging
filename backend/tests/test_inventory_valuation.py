"""Read-only Nilai Persediaan over local Stock V2 and HPP fixtures."""
import os
import sys
from io import BytesIO
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")

from services.inventory_valuation import (
    build_inventory_valuation_rows,
    export_xlsx,
    filter_sort_page,
)

DB_NAME = "eternal_inventory_valuation_test"


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


async def _master_stock(db, mid, code, stock, *, with_official_balance=True):
    await db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": f"aid-{mid}",
        "item_code": code,
        "item_name": f"Item {code}",
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    })
    await db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "accurate_item_id": f"aid-{mid}",
        "base_uom": "PCS",
        "total_qty": stock,
        "status": "INITIALIZED",
        "initialized": True,
        "updated_at": "2026-09-22T10:00:00+00:00",
    })
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await db.branches.insert_one({
            "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": "wh-ygy", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        })
    # Official R2 presence is independent of legacy product_stock_state.
    if with_official_balance:
        await db.physical_stock_balances.insert_one({
            "id": f"bal-{mid}",
            "master_item_id": mid,
            "branch_id": "branch-ygy",
            "warehouse_id": "wh-ygy",
            "physical_qty": stock,
        })


def _baseline(cutover_id, mid, qty, cost):
    return {
        "cutover_id": cutover_id,
        "master_item_id": mid,
        "item_identity": f"a:aid-{mid}",
        "accurate_item_id": f"aid-{mid}",
        "item_code": mid,
        "item_name": mid,
        "stock_fisik_snapshot": qty,
        "opening_unit_cost": cost,
        "opening_total_cost": qty * cost,
        "valuation_qty": qty,
        "base_uom": "PCS",
        "source": "OPENING_HPP",
        "hpp_status": "OK",
        "reconciliation_reason": None,
    }


@pytest.mark.anyio
async def test_read_model_summary_statuses_and_exact_value(db):
    cutover_id = "cutover-ui"
    await db.hpp_valuation_cutovers.insert_one({
        "cutover_id": cutover_id,
        "status": "ACTIVE",
        "active_marker": "ACTIVE",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "created_at": "2026-09-22T09:00:00+00:00",
        "activated_at": "2026-09-22T09:00:00+00:00",
    })
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "active_version": "version-ui",
    })
    await db.hpp_cost_versions.insert_one({
        "id": "version-ui",
        "status": "ACTIVE",
        "method": "RATA-RATA",
        "recalculated_at": "2026-09-22T11:00:00+00:00",
    })

    await _master_stock(db, "ok", "OK-001", 150)
    await _master_stock(db, "missing", "MISS-001", 10)
    await _master_stock(db, "uom", "UOM-001", 1)
    await _master_stock(db, "mismatch", "QTY-001", 5)
    await _master_stock(db, "zero", "ZERO-001", 0)
    await _master_stock(db, "zero-valid", "ZERO-HPP-001", 0)
    await _master_stock(db, "negative", "NEG-001", -2)
    await db.master_items.insert_one({
        "id": "service",
        "item_code": "SERVICE",
        "item_name": "Jasa",
        "item_type": "SERVICE",
        "unit": "JASA",
        "is_active": True,
    })

    await db.hpp_valuation_baseline_lines.insert_many([
        _baseline(cutover_id, "ok", 100, 1000),
        _baseline(cutover_id, "uom", 0, 1000),
        _baseline(cutover_id, "mismatch", 4, 100),
        _baseline(cutover_id, "zero", 0, 1000),
        _baseline(cutover_id, "negative", 0, 1000),
    ])
    await db.hpp_recognition_events.insert_many([
        {
            "event_id": "event-ok",
            "source_type": "GOODS_RECEIPT",
            "source_key": "gr:ok:line",
            "master_item_id": "ok",
            "item_identity": "a:aid-ok",
            "accurate_item_id": "aid-ok",
            "qty": 50,
            "unit_cost": 1200,
            "total_cost": 60000,
            "status": "RECOGNIZED",
            "recognized_at": "2026-09-22T10:30:00+00:00",
            "cutover_id": cutover_id,
        },
        {
            "event_id": "event-uom",
            "source_type": "GOODS_RECEIPT",
            "source_key": "gr:uom:line",
            "master_item_id": "uom",
            "item_identity": "a:aid-uom",
            "qty": None,
            "unit_cost": None,
            "total_cost": None,
            "status": "NEEDS_UOM",
            "recognized_at": "2026-09-22T10:40:00+00:00",
            "cutover_id": cutover_id,
        },
    ])
    await db.hpp_cost_layers.insert_one({
        "layer_id": "zero-valid-layer",
        "calculation_version": "version-ui",
        "master_item_id": "zero-valid",
        "item_identity": "a:aid-zero-valid",
        "recognized_at": "2026-09-22T10:50:00+00:00",
        "recognized_qty": 0,
        "remaining_qty": 0,
        "unit_cost": 500,
        "total_cost": 0,
        "source_type": "OPENING_HPP",
    })

    view = await build_inventory_valuation_rows(db)
    rows = {row["master_item_id"]: row for row in view["rows"]}
    assert "service" not in rows
    assert rows["ok"]["stock_fisik"] == 150
    assert rows["ok"]["valuation_qty"] == 150
    assert rows["ok"]["current_hpp"] == 1066.67
    assert rows["ok"]["inventory_value"] == 160000
    assert rows["ok"]["hpp_status"] == "OK"
    assert rows["missing"]["hpp_status"] == "MISSING_COST"
    assert rows["missing"]["current_hpp"] is None
    assert rows["missing"]["inventory_value"] is None
    assert rows["uom"]["hpp_status"] == "NEEDS_UOM"
    assert rows["uom"]["inventory_value"] is None
    assert rows["mismatch"]["stock_fisik"] == 5
    assert rows["mismatch"]["hpp_status"] == "OK"
    assert rows["mismatch"]["inventory_value"] == 500
    assert rows["mismatch"]["difference"] == 1
    assert rows["zero"]["stock_fisik"] == 0
    assert rows["zero"]["stock_fisik_status"] == "AVAILABLE"
    assert rows["zero"]["inventory_value"] is None
    assert rows["zero"]["hpp_status"] == "MISSING_COST"
    assert rows["zero"]["hpp_reason"] is None
    assert rows["zero-valid"]["stock_fisik"] == 0
    assert rows["zero-valid"]["stock_fisik_status"] == "AVAILABLE"
    assert rows["zero-valid"]["current_hpp"] == 500
    assert rows["zero-valid"]["inventory_value"] == 0
    assert rows["zero-valid"]["hpp_status"] == "OK"
    assert rows["negative"]["stock_fisik"] == 0
    assert rows["negative"]["stock_fisik_status"] == "AVAILABLE"
    assert rows["negative"]["hpp_status"] == "MISSING_COST"
    assert rows["negative"]["inventory_value"] is None
    assert view["physical_stock_source"] == "physical_stock_balances"
    assert view["physical_stock_mode"] == "r2_official"

    assert view["summary"] == {
        "total_inventory_value": 160500,
        "valued_items": 3,
        "items_without_hpp": 4,
        "problem_items": 1,
        "items_without_official_stock": 0,
    }


@pytest.mark.anyio
async def test_unavailable_official_stock_is_null_not_zero(db):
    await _master_stock(db, "legacy-only", "LEG-001", 999, with_official_balance=False)
    await _master_stock(db, "proven-zero", "ZERO-002", 0, with_official_balance=True)
    await db.hpp_cost_layers.insert_one({
        "layer_id": "legacy-hpp",
        "calculation_version": "ver-unavail",
        "master_item_id": "legacy-only",
        "item_identity": "a:aid-legacy-only",
        "recognized_at": "2026-09-22T10:50:00+00:00",
        "recognized_qty": 1,
        "remaining_qty": 1,
        "unit_cost": 100,
        "total_cost": 100,
        "source_type": "OPENING_HPP",
    })
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "active_version": "ver-unavail",
        "method": "RATA-RATA",
    })
    await db.hpp_cost_versions.insert_one({"id": "ver-unavail", "status": "ACTIVE"})

    view = await build_inventory_valuation_rows(db)
    rows = {row["master_item_id"]: row for row in view["rows"]}
    assert rows["legacy-only"]["stock_fisik"] is None
    assert rows["legacy-only"]["stock_fisik_status"] == "UNAVAILABLE"
    assert rows["legacy-only"]["inventory_value"] is None
    assert rows["legacy-only"]["hpp_status"] == "OK"
    assert rows["legacy-only"]["current_hpp"] == 100
    assert "belum tersedia" in (rows["legacy-only"]["reconciliation_explanation"] or "").lower()
    assert rows["proven-zero"]["stock_fisik"] == 0
    assert rows["proven-zero"]["stock_fisik_status"] == "AVAILABLE"
    assert rows["proven-zero"]["inventory_value"] is None
    assert view["summary"]["items_without_official_stock"] == 1

    payload = export_xlsx([rows["legacy-only"], rows["proven-zero"]])
    workbook = load_workbook(BytesIO(payload), data_only=True)
    sheet = workbook["Nilai Persediaan"]
    assert sheet["D2"].value is None
    assert sheet["E2"].value == "Belum tersedia"
    assert sheet["G2"].value is None
    assert sheet["D3"].value == 0
    assert sheet["E3"].value == "Tersedia"


@pytest.mark.anyio
async def test_search_filter_pagination_and_export_are_read_only(db):
    await _master_stock(db, "a", "AA-001", 0)
    await _master_stock(db, "b", "BB-001", 0)
    before_stock = await db.product_stock_state.find({}, {"_id": 0}).sort("_id", 1).to_list(10)
    before_movements = await db.stock_movements.count_documents({})
    before_pricing = await db.customer_pricing_versions.count_documents({})

    view = await build_inventory_valuation_rows(db)
    page = filter_sort_page(
        view["rows"],
        q="BB",
        status_filter="missing",
        sort_by="code",
        sort_dir="asc",
        page=1,
        page_size=25,
    )
    assert page["total"] == 1
    assert page["items"][0]["item_code"] == "BB-001"

    payload = export_xlsx(page["items"])
    workbook = load_workbook(BytesIO(payload), data_only=True)
    sheet = workbook["Nilai Persediaan"]
    assert [cell.value for cell in sheet[1]] == [
        "Kode",
        "Nama",
        "Satuan",
        "Stock Fisik",
        "Ketersediaan Stok",
        "HPP Rata-Rata",
        "Nilai Persediaan",
        "Status HPP",
        "Sumber HPP",
        "Posting Finansial",
        "Opening Disetujui",
        "Selisih",
        "Penjelasan Selisih",
        "Hambatan",
        "Terakhir Update",
    ]
    assert sheet["A2"].value == "BB-001"
    assert sheet["D2"].value == 0
    assert sheet["E2"].value == "Tersedia"
    assert sheet["F2"].value is None
    assert sheet["G2"].value is None

    after_stock = await db.product_stock_state.find({}, {"_id": 0}).sort("_id", 1).to_list(10)
    assert after_stock == before_stock
    assert await db.stock_movements.count_documents({}) == before_movements
    assert await db.customer_pricing_versions.count_documents({}) == before_pricing
