"""Stock Saat Ini catalog: warehouse filter + per-warehouse book breakdown.

Isolated DB only — never touches eternal_packaging_uat_r2_manual / preview.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = "eternal_packaging_test_stock_wh_visibility"
assert "eternal_packaging_preview" not in TEST_DB
assert "uat_r2_manual" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
    post_movement,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_SALES_DELIVERY,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "VIS-ADJ-001",
        "item_name": "Visibility Box",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
        "accurate_item_id": f"LOCAL-{uuid.uuid4().hex[:6]}",
    }
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "Yogyakarta", "active": True}
    wh_a = {
        "id": f"wh-a-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "GUD-A",
        "name": "Gudang A",
        "active": True,
        "is_operational": True,
    }
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "GUD-B",
        "name": "Gudang B",
        "active": True,
        "is_operational": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    await post_movement(db, {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 11,
        "reference_type": "opening",
        "reference_id": "vis-open-a",
        "idempotency_key": "vis-open-a",
    }, actor_id="t")
    await post_movement(db, {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_b["id"],
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 5,
        "reference_type": "opening",
        "reference_id": "vis-open-b",
        "idempotency_key": "vis-open-b",
    }, actor_id="t")
    await post_movement(db, {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -15,
        "reference_type": "sales_delivery_confirmation",
        "reference_id": "vis-sd-1",
        "idempotency_key": "vis-sd-1",
    }, actor_id="t")
    return item, branch, wh_a, wh_b


async def test_all_warehouses_aggregate_and_breakdown(db):
    item, branch, wh_a, wh_b = await _seed(db)
    before_ledger = await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]})
    before_bal = await db[BALANCE_COLLECTION].count_documents({"master_item_id": item["id"]})

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="VIS-ADJ-001")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert catalog["physical_qty_scope"] == "BRANCH"
    assert catalog["demand_scope"] == "BRANCH"
    assert row["physical_qty"] == 1
    assert row["physical_qty_is_book"] is True
    assert row["saldo_buku_negatif"] is False
    by_code = {w["warehouse_code"]: w for w in row["warehouse_balances"]}
    assert by_code["GUD-A"]["physical_qty"] == -4
    assert by_code["GUD-A"]["saldo_buku_negatif"] is True
    assert by_code["GUD-A"]["baseline_status"] == BASELINE_RECONCILED
    assert by_code["GUD-B"]["physical_qty"] == 5
    assert by_code["GUD-B"]["saldo_buku_negatif"] is False

    # Read-only: filtering/listing must not write balances or ledger.
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == before_ledger
    assert await db[BALANCE_COLLECTION].count_documents({"master_item_id": item["id"]}) == before_bal


async def test_warehouse_filter_shows_actual_book_including_negative(db):
    item, branch, wh_a, wh_b = await _seed(db)
    cat_a = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"], q="VIS-ADJ-001",
    )
    row_a = next(r for r in cat_a["items"] if r["master_item_id"] == item["id"])
    assert cat_a["physical_qty_scope"] == "WAREHOUSE"
    assert row_a["physical_qty"] == -4
    assert row_a["saldo_buku_negatif"] is True
    assert row_a["warehouse_code"] == "GUD-A"
    assert row_a["demand_scope"] == "BRANCH"
    assert row_a["availability_scope"] == "BRANCH_DEMAND_VS_WAREHOUSE_BOOK"

    cat_b = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_b["id"], q="VIS-ADJ-001",
    )
    row_b = next(r for r in cat_b["items"] if r["master_item_id"] == item["id"])
    assert row_b["physical_qty"] == 5
    assert row_b["warehouse_code"] == "GUD-B"


async def test_missing_balance_not_treated_as_verified_zero(db):
    item, branch, wh_a, wh_b = await _seed(db)
    wh_c = {
        "id": f"wh-c-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "GUD-C",
        "name": "Gudang C",
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh_c))
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="VIS-ADJ-001")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    by_code = {w["warehouse_code"]: w for w in row["warehouse_balances"]}
    assert by_code["GUD-C"]["balance_present"] is False
    assert by_code["GUD-C"]["physical_qty"] is None
    assert by_code["GUD-C"]["baseline_label"]
