"""R2 txn fail-closed on non-RS + Add On parent completion on RS.

Isolated replica-set DB only. No production writes.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_r2_txn_addon"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BASELINE_RECONCILED,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    PhysicalStockUnavailableError,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    ensure_indexes,
    get_balance,
    list_physical_stock_catalog,
    post_opening_balance,
)
from services.r2_go_live import (  # noqa: E402
    initialize_delivery_cutover_baseline,
    set_r2_authority_active,
)
TEST_DB = "eternal_packaging_test_r2_txn_addon"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert TEST_DB != "eternal_packaging"
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
        "item_code": "CUP",
        "item_name": "Cup",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    svc = {
        "id": f"svc-{uuid.uuid4().hex[:8]}",
        "item_code": "ADDON",
        "item_name": "Add On Warna",
        "unit": "PCS",
        "item_type": "SERVICE",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    warehouse = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "Utama",
        "active": True,
        "is_operational": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(svc))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": 2000,
            "reason": "open",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="t",
    )
    return item, svc, branch, warehouse


@pytest.mark.anyio
async def test_adjust_rejects_without_replica_set():
    class Admin:
        async def command(self, *args, **kwargs):
            return {"isWritablePrimary": True}  # no setName

    class Client:
        admin = Admin()

    class DB:
        client = Client()

        def __getitem__(self, name):
            raise AssertionError(f"collection {name} must not be touched")

    with pytest.raises(PhysicalStockUnavailableError):
        await adjust_physical_stock_to_target(
            DB(),
            "mid",
            {
                "branch_id": "b",
                "warehouse_id": "w",
                "target_physical_qty": 10,
                "reason": "koreksi",
                "idempotency_key": "no-rs",
            },
            actor_id="t",
        )


@pytest.mark.anyio
async def test_r2_parent_linked_addon_completion_and_adjust_idempotent(db):
    item, svc, branch, warehouse = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": item["id"],
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "ln-w2",
                "master_item_id": svc["id"],
                "parent_line_id": "ln-main",
                "quantity": 1000,
                "production_process": "print",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
        ],
    })
    await initialize_delivery_cutover_baseline(db)
    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert svc["id"] not in {r["master_item_id"] for r in catalog["items"]}
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 1000
    assert row["belum_terkirim_status"] == "READY"
    assert row["qty_tersedia_valid"] is True
    assert row["baseline_status"] == BASELINE_RECONCILED

    first = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "target_physical_qty": 1900,
            "reason": "koreksi fisik",
            "idempotency_key": f"adj-{item['id']}",
        },
        actor_id="t",
    )
    assert first["previous_physical_qty"] == 2000
    assert first["physical_qty"] == 1900
    second = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "target_physical_qty": 1900,
            "reason": "koreksi fisik",
            "idempotency_key": f"adj-{item['id']}",
        },
        actor_id="t",
    )
    assert second["physical_qty"] == 1900
    assert (await get_balance(db, item["id"], branch["id"], warehouse["id"]))["physical_qty"] == 1900
    assert await db[LEDGER_COLLECTION].count_documents({
        "idempotency_key": f"adj-{item['id']}",
    }) == 1
