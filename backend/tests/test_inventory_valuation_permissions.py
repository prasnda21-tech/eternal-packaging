"""Dedicated Nilai Persediaan permission enforcement."""
import os
import sys
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_inventory_valuation_permission_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client["eternal_inventory_valuation_permission_test"]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    yield
    app.dependency_overrides.pop(current_user, None)
    await client.drop_database(database.name)
    client.close()


async def _get_as(role: str, path: str):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "name": role,
        "email": f"{role}@test.local",
        "role": role,
        "permissions_override": {},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get(path)


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["admin", "holding_team"])
async def test_allowed_roles_can_view_and_export(role):
    listing = await _get_as(role, "/api/inventory-valuation")
    assert listing.status_code == 200, listing.text
    exported = await _get_as(role, "/api/inventory-valuation/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "role",
    [
        "head_production",
        "head_operations",
        "operator",
        "helper",
        "screener",
        "designer",
        "driver",
    ],
)
async def test_production_roles_are_forbidden_from_direct_api(role):
    listing = await _get_as(role, "/api/inventory-valuation")
    assert listing.status_code == 403
    exported = await _get_as(role, "/api/inventory-valuation/export")
    assert exported.status_code == 403


@pytest.mark.anyio
async def test_export_ignores_ui_pagination_and_keeps_filter_search_semantics():
    version_id = "valuation-export-380"
    await server.db.app_settings.insert_one({
        "key": "hpp_valuation",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "active_version": version_id,
    })
    await server.db.hpp_cost_versions.insert_one({
        "id": version_id,
        "status": "ACTIVE",
        "method": "RATA-RATA",
    })
    masters = []
    states = []
    layers = []
    for index in range(380):
        mid = f"bulk-{index:03d}"
        aid = f"aid-{mid}"
        masters.append({
            "id": mid,
            "accurate_item_id": aid,
            "item_code": f"BULK-{index:03d}",
            "item_name": (
                f"Needle Item {index:03d}"
                if index < 3
                else f"Bulk Item {index:03d}"
            ),
            "item_type": "INVENTORY",
            "unit": "PCS",
            "is_active": True,
        })
        states.append({
            "_id": mid,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "base_uom": "PCS",
            "total_qty": 0,
            "status": "INITIALIZED",
            "initialized": True,
        })
        if index < 100:
            layers.append({
                "layer_id": f"layer-{mid}",
                "calculation_version": version_id,
                "master_item_id": mid,
                "accurate_item_id": aid,
                "item_identity": f"a:{aid}",
                "recognized_at": "2026-09-22T00:00:00+00:00",
                "recognized_qty": 0,
                "remaining_qty": 0,
                "unit_cost": 500,
                "total_cost": 0,
                "source_type": "OPENING_HPP",
            })
    await server.db.master_items.insert_many(masters)
    await server.db.product_stock_state.insert_many(states)
    await server.db.hpp_cost_layers.insert_many(layers)

    page = await _get_as(
        "admin",
        "/api/inventory-valuation?page=1&page_size=50",
    )
    assert page.status_code == 200
    assert page.json()["total"] == 380
    assert len(page.json()["items"]) == 50

    complete = await _get_as("admin", "/api/inventory-valuation/export")
    assert complete.status_code == 200
    complete_sheet = load_workbook(
        BytesIO(complete.content),
        data_only=True,
    )["Nilai Persediaan"]
    assert complete_sheet.max_row - 1 == 380

    filtered = await _get_as(
        "admin",
        "/api/inventory-valuation/export?status_filter=ok",
    )
    filtered_sheet = load_workbook(
        BytesIO(filtered.content),
        data_only=True,
    )["Nilai Persediaan"]
    assert filtered_sheet.max_row - 1 == 100

    searched = await _get_as(
        "admin",
        "/api/inventory-valuation/export?q=Needle",
    )
    searched_sheet = load_workbook(
        BytesIO(searched.content),
        data_only=True,
    )["Nilai Persediaan"]
    assert searched_sheet.max_row - 1 == 3

    missing = await _get_as(
        "admin",
        "/api/inventory-valuation?q=BULK-379",
    )
    assert missing.status_code == 200
    row = missing.json()["items"][0]
    assert row["current_hpp"] is None
    assert row["inventory_value"] is None
    assert row["hpp_status"] == "MISSING_COST"

    valid_zero = await _get_as(
        "admin",
        "/api/inventory-valuation?q=BULK-000",
    )
    row = valid_zero.json()["items"][0]
    assert row["current_hpp"] == 500
    assert row["inventory_value"] == 0
    assert row["hpp_status"] == "OK"
