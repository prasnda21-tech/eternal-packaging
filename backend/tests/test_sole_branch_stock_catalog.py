"""Sole non-YGY branch must default for R2 Stock Saat Ini (UAT adjust blocker)."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = "eternal_packaging_test_sole_branch_stock_catalog"
assert "eternal_packaging_preview" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("APP_ENV", "preview")

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
    post_opening_balance,
    resolve_operational_branch,
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
    branch = {
        "id": str(uuid.uuid4()),
        "code": "UAT-YGY",
        "name": "Cabang UAT Adjust",
        "active": True,
    }
    wh_a = {
        "id": str(uuid.uuid4()),
        "branch_id": branch["id"],
        "code": "GUD-A",
        "name": "Gudang A",
        "active": True,
        "is_operational": True,
    }
    wh_b = {
        "id": str(uuid.uuid4()),
        "branch_id": branch["id"],
        "code": "GUD-B",
        "name": "Gudang B",
        "active": True,
        "is_operational": True,
    }
    item = {
        "id": str(uuid.uuid4()),
        "item_code": "ADJ-UAT-001",
        "item_name": "Barang Uji Adjustment Owner",
        "unit": "PCS",
        "is_active": True,
    }
    await database.branches.insert_one(dict(branch))
    await database.warehouses.insert_many([dict(wh_a), dict(wh_b)])
    await database.master_items.insert_one(dict(item))
    actor = "seeder"
    await post_opening_balance(
        database,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 10,
            "reason": "open A",
            "idempotency_key": f"open-a-{item['id']}",
        },
        actor_id=actor,
    )
    await post_opening_balance(
        database,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": 5,
            "reason": "open B",
            "idempotency_key": f"open-b-{item['id']}",
        },
        actor_id=actor,
    )
    yield database, branch, wh_a, wh_b, item
    await client.drop_database(TEST_DB)
    client.close()


async def test_resolve_operational_branch_sole_uat_ygy(db):
    database, branch, *_ = db
    rows = await database.branches.find({}, {"_id": 0}).to_list(10)
    resolved = resolve_operational_branch(rows)
    assert resolved is not None
    assert resolved["id"] == branch["id"]
    assert resolved["code"] == "UAT-YGY"


async def test_catalog_without_branch_id_sums_sole_branch_warehouses(db):
    database, branch, wh_a, wh_b, item = db
    catalog = await list_physical_stock_catalog(database, q="ADJ-UAT-001")
    assert catalog["warehouse_count"] == 2
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 15.0
    assert await database[BALANCE_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "warehouse_id": {"$in": [wh_a["id"], wh_b["id"]]},
    }) == 2
