"""Critical Purchasing/Inventory branch authorization gaps.

Covers physical stock location auth, GR/return location access,
Nilai Persediaan branch-aware qty × company-wide HPP, and HPP Pembelian
stock branch context. Does NOT claim Sales/Production/Delivery isolation.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")

from services.branch_access import (  # noqa: E402
    authorize_stock_location,
    can_switch_branches,
    resolve_branch_access,
    resolve_stock_catalog_branch_id,
)
from services.inventory_valuation import build_inventory_valuation_rows  # noqa: E402
from services.org_locations import ensure_indexes  # noqa: E402
from services.physical_stock import operational_physical_qty_by_item  # noqa: E402
from services.purchase_hpp import list_purchase_hpp  # noqa: E402
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_pi_branch_gaps"
pytestmark = pytest.mark.anyio

PROD = {"app_env": "production", "mongo_url": "mongodb://prod.example.com:27017"}
LOCAL = {"app_env": "local", "mongo_url": "mongodb://127.0.0.1:27017"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod.example.com:27017")
    client = AsyncIOMotorClient(
        os.environ["MONGO_URL"].replace("prod.example.com", "127.0.0.1")
    )
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    await database.branches.insert_many([
        {"id": "br-ygy", "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": "br-smg", "code": "SMG", "name": "Semarang", "active": True},
    ])
    await database.warehouses.insert_many([
        {
            "id": "wh-ygy", "branch_id": "br-ygy", "code": "YGY-UTAMA",
            "name": "Gudang Utama Yogyakarta", "active": True, "is_operational": True,
        },
        {
            "id": "wh-smg", "branch_id": "br-smg", "code": "SMG-UTAMA",
            "name": "Gudang Utama Semarang", "active": True, "is_operational": True,
        },
    ])
    await database.master_items.insert_one({
        "id": "item-1",
        "item_code": "BR-GAP-001",
        "name": "Branch Gap Item",
        "item_name": "Branch Gap Item",
        "is_active": True,
        "item_type": "INVENTORY",
        "base_unit": "PCS",
    })
    await database.physical_stock_balances.insert_many([
        {
            "master_item_id": "item-1",
            "branch_id": "br-ygy",
            "warehouse_id": "wh-ygy",
            "physical_qty": 10,
        },
        {
            "master_item_id": "item-1",
            "branch_id": "br-smg",
            "warehouse_id": "wh-smg",
            "physical_qty": 3,
        },
    ])
    # Company-wide canonical HPP layer (not branch-specific)
    await database.hpp_cost_layers.insert_one({
        "master_item_id": "item-1",
        "unit_cost": 1000,
        "qty_remaining": 13,
        "active": True,
    })
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def test_physical_stock_query_tamper_denied(db):
    ygy = {"id": "u1", "role": "sales", "branch_ids": ["br-ygy"]}
    await authorize_stock_location(db, ygy, branch_id="br-ygy", warehouse_id="wh-ygy", **PROD)
    with pytest.raises(HTTPException) as exc:
        await authorize_stock_location(
            db, ygy, branch_id="br-smg", warehouse_id="wh-smg", **PROD,
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc2:
        await authorize_stock_location(db, ygy, warehouse_id="wh-smg", **PROD)
    assert exc2.value.status_code == 403


async def test_holding_team_may_access_all_stock_locations(db):
    holding = {"id": "h1", "role": "holding_team"}
    access = await resolve_branch_access(db, holding, **PROD)
    assert can_switch_branches(access) is True
    await authorize_stock_location(
        db, holding, branch_id="br-ygy", warehouse_id="wh-ygy", **PROD,
    )
    await authorize_stock_location(
        db, holding, branch_id="br-smg", warehouse_id="wh-smg", **PROD,
    )


async def test_local_admin_unrestricted_branch_switch(db):
    admin = {"id": "a1", "role": "admin", "is_super_admin": True}
    access = await resolve_branch_access(db, admin, **LOCAL)
    assert can_switch_branches(access) is True
    resolved = await resolve_stock_catalog_branch_id(db, admin, "br-smg", **LOCAL)
    assert resolved["branch"]["id"] == "br-smg"


async def test_operational_qty_is_branch_specific(db):
    ygy_qty = await operational_physical_qty_by_item(db, ["item-1"], branch_id="br-ygy")
    smg_qty = await operational_physical_qty_by_item(db, ["item-1"], branch_id="br-smg")
    assert ygy_qty.get("item-1") == 10
    assert smg_qty.get("item-1") == 3
    assert ygy_qty.get("item-1") != smg_qty.get("item-1")


async def test_nilai_persediaan_branch_qty_company_wide_hpp(db, monkeypatch):
    """Same item: different branch qty, same company-wide HPP, correct value."""
    # Stub canonical positions so valuation does not depend on full HPP cutover setup.
    async def fake_positions(_db):
        return (
            {"item-1": {"current_hpp": 1000, "valuation_qty": 0, "hpp_status": "OK", "hpp_source": "TEST"}},
            {"method": "WMA"},
            {"id": "v1"},
        )

    monkeypatch.setattr(
        "services.inventory_valuation._canonical_positions",
        fake_positions,
    )

    async def fake_financial(_db):
        return {}

    monkeypatch.setattr(
        "services.financial_inventory_opening.financial_display_by_item",
        fake_financial,
    )

    ygy_view = await build_inventory_valuation_rows(db, branch_id="br-ygy")
    smg_view = await build_inventory_valuation_rows(db, branch_id="br-smg")
    ygy_row = next(r for r in ygy_view["rows"] if r["master_item_id"] == "item-1")
    smg_row = next(r for r in smg_view["rows"] if r["master_item_id"] == "item-1")
    assert float(ygy_row["stock_fisik"]) == 10
    assert float(smg_row["stock_fisik"]) == 3
    assert ygy_row["current_hpp"] == smg_row["current_hpp"] == 1000
    assert float(ygy_row["inventory_value"]) == 10000
    assert float(smg_row["inventory_value"]) == 3000


async def test_purchase_hpp_stock_branch_context(db, monkeypatch):
    async def empty_cutover(_db):
        return None

    monkeypatch.setattr("services.purchase_hpp.get_active_cutover", empty_cutover)
    ygy = await list_purchase_hpp(db, branch_id="br-ygy")
    smg = await list_purchase_hpp(db, branch_id="br-smg")
    assert ygy["hpp_cost_scope"] == "COMPANY_WIDE"
    assert smg["hpp_cost_scope"] == "COMPANY_WIDE"
    ygy_row = next(r for r in ygy["rows"] if r["master_item_id"] == "item-1")
    smg_row = next(r for r in smg["rows"] if r["master_item_id"] == "item-1")
    assert float(ygy_row["stock_fisik"]) == 10
    assert float(smg_row["stock_fisik"]) == 3


async def test_smg_user_denied_ygy_catalog_switch(db):
    user = {"id": "u2", "role": "sales", "branch_ids": ["br-smg"]}
    with pytest.raises(HTTPException) as exc:
        await resolve_stock_catalog_branch_id(db, user, "br-ygy", **PROD)
    assert exc.value.status_code == 403


async def test_goods_receipt_and_return_location_auth(db):
    """GR/return use authorize_stock_location on authoritative receiving location."""
    from services.purchase_goods_receipts import _authorize_receipt_location
    from services.purchase_returns import _authorize_return_locations

    ygy_user = {"id": "u1", "role": "sales", "branch_ids": ["br-ygy"]}
    holding = {"id": "h1", "role": "holding_team"}
    smg_receipt = {"id": "gr1", "branch_id": "br-smg", "warehouse_id": "wh-smg"}
    ygy_receipt = {"id": "gr2", "branch_id": "br-ygy", "warehouse_id": "wh-ygy"}
    await _authorize_receipt_location(db, holding, smg_receipt)
    await _authorize_receipt_location(db, ygy_user, ygy_receipt)
    with pytest.raises(HTTPException) as exc:
        await _authorize_receipt_location(db, ygy_user, smg_receipt)
    assert exc.value.status_code == 403

    smg_return = {
        "id": "rt1",
        "lines": [{"branch_id": "br-smg", "warehouse_id": "wh-smg"}],
    }
    await _authorize_return_locations(db, holding, smg_return)
    with pytest.raises(HTTPException) as exc2:
        await _authorize_return_locations(db, ygy_user, smg_return)
    assert exc2.value.status_code == 403


async def test_enterprise_limitation_documented():
    from pathlib import Path
    text = Path("services/branch_access.py").read_text(encoding="utf-8")
    assert "does NOT claim full Sales" in text or "does NOT claim full Sales / Production" in text
    assert "Purchasing stock mutations" in text
