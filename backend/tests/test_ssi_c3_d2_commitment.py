"""SSI C3/D2 — active SO commitment attribution + Qty Polos incomplete print."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_c3_d2"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    confirm_sales_delivery,
    ensure_indexes,
    list_physical_stock_catalog,
    reverse_confirmed_sales_delivery,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_saat_ini_excel import REASON_WIP_COMPOSITION_UNPROVEN  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_c3_d2"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "preview" not in TEST_DB
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
        "item_code": "BOX-C3D2",
        "item_name": "Box C3D2",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "Utama",
        "active": True,
        "is_operational": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "UAT adj",
            "idempotency_key": f"adj-{item['id']}",
        },
        actor_id="t",
    )
    return item, branch, wh


async def test_cancelled_so_a_does_not_reduce_active_so_b(db):
    item, branch, wh = await _seed(db)
    so_a = {
        "id": f"so-a-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "cabang_id": branch["id"],
        "line_items": [{
            "line_id": "ln-a",
            "master_item_id": item["id"],
            "quantity": 4_000,
            "item_type": "INVENTORY",
            "production_process": "non_print",
        }],
    }
    so_b = {
        "id": f"so-b-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "cabang_id": branch["id"],
        "line_items": [{
            "line_id": "ln-b",
            "master_item_id": item["id"],
            "quantity": 10_000,
            "item_type": "INVENTORY",
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_many([dict(so_a), dict(so_b)])
    conf = f"conf-{uuid.uuid4().hex[:8]}"
    await confirm_sales_delivery(
        db,
        order_id=so_a["id"],
        confirmation_id=conf,
        lines=[{"line_idx": 0, "qty": 4_000, "master_item_id": item["id"], "line_id": "ln-a"}],
        actor_id="t",
        warehouse_id=wh["id"],
        branch_id=branch["id"],
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock book",
            "idempotency_key": f"restock-{item['id']}",
        },
        actor_id="t",
    )
    await db.orders.update_one({"id": so_a["id"]}, {"$set": {"cancelled": True}})

    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh["id"], q="BOX-C3D2",
    )
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["stock"] == 10_000
    assert row["open_so_commitment"] == 10_000
    assert row["stock_dapat_dijual"] == 0
    # Physical delivery history still present
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "master_item_id": item["id"],
    }) == 1


async def test_delivery_reversal_restores_commitment(db):
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-r-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-r",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    conf = f"conf-r-{uuid.uuid4().hex[:6]}"
    await confirm_sales_delivery(
        db,
        order_id=so["id"],
        confirmation_id=conf,
        lines=[{"line_idx": 0, "qty": 1_000, "master_item_id": item["id"], "line_id": "ln-r"}],
        actor_id="t",
        warehouse_id=wh["id"],
        branch_id=branch["id"],
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"r2-{item['id']}",
        },
        actor_id="t",
    )
    cat1 = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row1 = next(r for r in cat1["items"] if r["master_item_id"] == item["id"])
    assert row1["open_so_commitment"] == 4_000

    await reverse_confirmed_sales_delivery(
        db, confirmation_id=conf, actor_id="t", reason="batal",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock2",
            "idempotency_key": f"r3-{item['id']}",
        },
        actor_id="t",
    )
    cat2 = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row2 = next(r for r in cat2["items"] if r["master_item_id"] == item["id"])
    assert row2["open_so_commitment"] == 5_000
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
    }) == 1


async def test_incomplete_print_polos_unverified(db):
    item, branch, wh = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-p-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-p",
            "name": "Cetak",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "item_type": "INVENTORY",
            "production_process": "print",
        }],
        "operator_splits": [
            {"line_idx": 0, "quantity": 5_000, "qty_completed": 3_000},
        ],
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_tercetak"] == 3_000
    assert row["qty_polos"] is None
    assert row["qty_polos_status"] == "UNVERIFIED"
    assert row["qty_polos_reason"] == REASON_WIP_COMPOSITION_UNPROVEN


async def test_complete_print_polos_ready(db):
    item, branch, wh = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-c-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-c",
            "master_item_id": item["id"],
            "quantity": 3_000,
            "production_process": "print",
        }],
        "operator_splits": [
            {"line_idx": 0, "quantity": 3_000, "qty_completed": 3_000},
        ],
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_tercetak"] == 3_000
    assert row["qty_polos"] == 7_000
    assert row["qty_polos_status"] == "READY"


async def test_multiwarna_parent_dedupe_and_genuine_negative_sdd(db):
    item, branch, wh = await _seed(db)
    svc = {
        "id": f"svc-{uuid.uuid4().hex[:6]}",
        "item_code": "W2",
        "item_name": "Warna 2",
        "item_type": "SERVICE",
        "unit": "PCS",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(svc))
    await db.orders.insert_one({
        "id": f"so-mw-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": item["id"],
                "quantity": 3_000,
                "production_process": "print",
                "item_type": "INVENTORY",
            },
            {
                "line_id": "ln-w",
                "master_item_id": svc["id"],
                "parent_line_id": "ln-p",
                "quantity": 3_000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "quantity": 3_000, "qty_completed": 3_000},
            {"line_idx": 1, "quantity": 3_000, "qty_completed": 3_000},
        ],
    })
    # Extra demand for negative sdd
    await db.orders.insert_one({
        "id": f"so-dem-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-d",
            "master_item_id": item["id"],
            "quantity": 32_000,
            "production_process": "non_print",
        }],
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_tercetak"] == 3_000  # not doubled by warna
    assert row["stock"] == 10_000
    # commitment = 3000 (print fully done still ordered) + 32000 = 35000
    assert row["open_so_commitment"] == 35_000
    assert row["stock_dapat_dijual"] == 10_000 - 35_000
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
    }) >= 1


async def test_multi_warehouse_isolation(db):
    item, branch, wh_a = await _seed(db)
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "SMG",
        "name": "Semarang",
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh_b))
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "target_physical_qty": 2_500,
            "reason": "smg",
            "idempotency_key": f"smg-{item['id']}",
        },
        actor_id="t",
    )
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], q="BOX-C3D2")
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    by = {w["warehouse_code"]: w for w in row["warehouse_balances"]}
    assert by["MAIN"]["physical_qty"] == 10_000
    assert by["SMG"]["physical_qty"] == 2_500
    assert row["stock"] == 12_500
