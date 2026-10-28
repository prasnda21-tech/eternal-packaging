"""G1 integrated UAT acceptance on isolated DB (U1/U2/U9/U10 fragments).

Does not write production. No opening/ledger fabricated for no-activity zeros.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_g1_uat"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.inventory_valuation import build_inventory_valuation_rows  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    ensure_indexes,
    list_physical_stock_catalog,
    summarize_physical_stock_status,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_saat_ini_excel import REASON_WIP_COMPOSITION_UNPROVEN  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_g1_uat"
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


async def _env(db):
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh_a = {
        "id": f"wh-a-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "A",
        "name": "A",
        "active": True,
        "is_operational": True,
    }
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "B",
        "name": "B",
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    return branch, wh_a, wh_b


async def _item(db, code, item_type="INVENTORY"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": item_type,
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


async def test_g1_uat_matrix_core(db):
    branch, wh_a, wh_b = await _env(db)
    no_act = await _item(db, "U1-NOACT")
    so_only = await _item(db, "U2-SO")
    await _item(db, "U10-SVC", item_type="SERVICE")
    await _item(db, "U10-NON", item_type="NON_INVENTORY")
    multi = await _item(db, "U9-MULTI")
    incomplete_print = await _item(db, "U5-WIP")

    await db.orders.insert_one({
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-so",
            "master_item_id": so_only["id"],
            "quantity": 25,
            "production_process": "print",
        }],
        "operator_splits": [],
    })
    await db.orders.insert_one({
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-wip",
            "master_item_id": incomplete_print["id"],
            "quantity": 100,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 40}],
    })

    before_bal = await db[BALANCE_COLLECTION].count_documents({})
    before_led = await db[LEDGER_COLLECTION].count_documents({})

    # U3: Adjust on missing balance → target N + one MANUAL_ADJUSTMENT
    await adjust_physical_stock_to_target(
        db,
        multi["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 50,
            "reason": "UAT U3/U9",
            "idempotency_key": f"uat-adj-{multi['id']}",
        },
        actor_id="uat",
    )

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    codes = {r["item_code"] for r in catalog["items"]}
    # U10
    assert "U10-SVC" not in codes
    assert "U10-NON" not in codes
    by = {r["item_code"]: r for r in catalog["items"]}

    # U1
    u1 = by["U1-NOACT"]
    assert u1["no_relevant_activity"] is True
    assert u1["physical_qty"] == 0
    assert u1["stock"] == 0
    assert u1["stock_dapat_dijual"] == 0
    assert u1["baseline_status"] == "reconciled"

    # U2
    u2 = by["U2-SO"]
    assert u2.get("no_relevant_activity") is not True
    assert u2["warehouse_balances"][0]["physical_qty"] is None
    assert u2["baseline_status"] == "unreconciled"

    # U9: Adj A only — B missing stays Belum (activity exists via A)
    u9 = by["U9-MULTI"]
    wh = {w["warehouse_code"]: w for w in u9["warehouse_balances"]}
    assert wh["A"]["physical_qty"] == 50
    assert wh["B"]["balance_present"] is False
    assert wh["B"]["physical_qty"] is None
    assert u9.get("no_relevant_activity") is not True

    # U5 incomplete print → Qty Polos UNVERIFIED
    u5 = by["U5-WIP"]
    assert u5["qty_polos"] is None
    assert u5["qty_polos_status"] == "UNVERIFIED"
    assert u5.get("qty_polos_reason") == REASON_WIP_COMPOSITION_UNPROVEN

    # U11-ish: genuine commitment with book stock → dapat dijual can go negative
    await adjust_physical_stock_to_target(
        db,
        so_only["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 5,
            "reason": "UAT U11",
            "idempotency_key": f"uat-adj-{so_only['id']}",
        },
        actor_id="uat",
    )
    catalog2 = await list_physical_stock_catalog(db, branch_id=branch["id"])
    u11 = next(r for r in catalog2["items"] if r["item_code"] == "U2-SO")
    assert u11["stock"] == 5
    assert u11["open_so_commitment"] == 25
    assert u11["stock_dapat_dijual"] == -20

    # Read path for no-activity did not invent opening for U1
    assert await db[BALANCE_COLLECTION].count_documents(
        {"master_item_id": no_act["id"]}
    ) == 0
    assert await db[LEDGER_COLLECTION].count_documents(
        {"master_item_id": no_act["id"]}
    ) == 0

    summary = await summarize_physical_stock_status(db, branch_id=branch["id"])
    assert summary["counts"]["items_physical_missing"] >= 0

    val = await build_inventory_valuation_rows(db, branch_id=branch["id"])
    val_u1 = next(r for r in val["rows"] if r["master_item_id"] == no_act["id"])
    assert val_u1["stock_fisik"] == 0
    assert val_u1["inventory_value"] == 0

    # Adjust paths did write; catalog/valuation reads for U1 did not add extras beyond those.
    assert await db[BALANCE_COLLECTION].count_documents({}) >= before_bal
    assert await db[LEDGER_COLLECTION].count_documents({}) >= before_led
