"""Nilai Persediaan reads the same Yogyakarta R2 physical quantity as Stock Saat Ini."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.inventory_valuation import build_inventory_valuation_rows
from services.physical_stock import (
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    list_physical_stock_catalog,
    post_movement,
    MOVEMENT_OPENING_BALANCE,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_opname_sessions import (
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes,
    record_stock_opname_counts,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_inventory_valuation_r2"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "valuation-r2"}


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
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _place(db):
    branch_id = "branch-ygy"
    warehouse_id = "wh-ygy"
    if await db.branches.count_documents({"id": branch_id}) == 0:
        await db.branches.insert_one({
            "id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": warehouse_id, "branch_id": branch_id, "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        })
    return branch_id, warehouse_id


async def _item(db, code):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
        "accurate_item_id": f"LOCAL-{code}",
    })
    return item_id


async def _row(db, item_id):
    view = await build_inventory_valuation_rows(db)
    return next(row for row in view["rows"] if row["master_item_id"] == item_id)


async def _catalog_qty(db, item_id):
    catalog = await list_physical_stock_catalog(db, limit=50)
    return next(row["physical_qty"] for row in catalog["items"] if row["master_item_id"] == item_id)


async def test_valuation_stock_follows_r2_not_legacy(db):
    branch_id, warehouse_id = await _place(db)
    priced = await _item(db, "PRICED")
    bare = await _item(db, "BARE")
    empty = await _item(db, "EMPTY")
    legacy = await _item(db, "LEGACY")
    await post_movement(db, {
        "master_item_id": priced,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 10,
        "idempotency_key": f"open-{priced}",
    }, actor_id="tester")
    await post_movement(db, {
        "master_item_id": bare,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 1050,
        "idempotency_key": f"open-{bare}",
    }, actor_id="tester")
    await db.product_stock_state.insert_one({
        "_id": legacy,
        "master_item_id": legacy,
        "total_qty": 999,
        "status": "INITIALIZED",
        "initialized": True,
    })
    await db.item_stock_manual.insert_one({
        "master_item_id": legacy,
        "manual_current_qty": 50,
    })
    await post_movement(db, {
        "master_item_id": legacy,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 4,
        "idempotency_key": f"open-{legacy}",
    }, actor_id="tester")
    await db.hpp_cost_layers.insert_one({
        "layer_id": "layer-priced",
        "calculation_version": "ver-r2",
        "master_item_id": priced,
        "recognized_at": "2026-10-05T00:00:00+00:00",
        "remaining_qty": 8,
        "unit_cost": 1000,
        "total_cost": 8000,
        "source_type": "GOODS_RECEIPT",
    })
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "active_version": "ver-r2",
        "method": "RATA-RATA",
    })
    await db.hpp_cost_versions.insert_one({"id": "ver-r2", "status": "ACTIVE"})

    priced_row = await _row(db, priced)
    bare_row = await _row(db, bare)
    empty_row = await _row(db, empty)
    legacy_row = await _row(db, legacy)

    assert priced_row["stock_fisik"] == 10
    assert priced_row["current_hpp"] == 1000
    assert priced_row["inventory_value"] == 10000
    assert priced_row["hpp_status"] == "OK"
    assert bare_row["stock_fisik"] == 1050
    assert bare_row["current_hpp"] is None
    assert bare_row["inventory_value"] is None
    assert bare_row["hpp_status"] == "MISSING_COST"
    assert bare_row["hpp_reason"] != "STOCK_STATE_UNAVAILABLE"
    assert empty_row["stock_fisik"] == 0
    assert legacy_row["stock_fisik"] == 4
    assert await _catalog_qty(db, priced) == priced_row["stock_fisik"]
    assert await _catalog_qty(db, bare) == bare_row["stock_fisik"]
    assert await _catalog_qty(db, empty) == 0
    assert await _catalog_qty(db, legacy) == 4

    session = await create_stock_opname_session(db, {
        "branch_id": branch_id, "warehouse_id": warehouse_id,
    }, USER)
    counting = await start_stock_opname_cutoff(db, session["id"], USER)
    lines = []
    for line in counting["lines"]:
        qty = 15 if line["master_item_id"] == priced else line["book_qty"]
        lines.append({"line_id": line["line_id"], "physical_count_qty": qty})
    await record_stock_opname_counts(db, session["id"], {"lines": lines}, USER)
    await submit_stock_opname_review(db, session["id"], USER)
    await confirm_stock_opname_session(db, session["id"], {"idempotency_key": "val-opname"}, USER)
    after_opname = await _row(db, priced)
    assert after_opname["stock_fisik"] == 15
    assert after_opname["inventory_value"] == 15000
    assert await _catalog_qty(db, priced) == 15

    ledger_after = await db[LEDGER_COLLECTION].count_documents({})
    balance_after = await db[BALANCE_COLLECTION].count_documents({})
    hpp_after = await db.hpp_cost_layers.count_documents({})
    journals_after = await db.journal_entries.count_documents({})
    await build_inventory_valuation_rows(db)
    assert await db[LEDGER_COLLECTION].count_documents({}) == ledger_after
    assert await db[BALANCE_COLLECTION].count_documents({}) == balance_after
    assert await db.hpp_cost_layers.count_documents({}) == hpp_after
    assert await db.journal_entries.count_documents({}) == journals_after
    assert ledger_after == 4
