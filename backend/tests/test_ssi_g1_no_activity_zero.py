"""G1 Owner rule — proven no relevant activity → operational numeric 0 (read model).

No OPENING / ledger / balance writes on catalog or valuation reads.
Incomplete history (SO or ledger without balance) stays Belum / UNAVAILABLE.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_g1_zero"
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
    MOVEMENT_PURCHASE_RECEIPT,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
    master_has_relevant_stock_activity,
    summarize_physical_stock_status,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_g1_zero"
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


async def _seed_branch_wh(db):
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
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    return branch, wh


async def _seed_item(db, code: str):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


def test_activity_helper_requires_relevant_signals():
    assert master_has_relevant_stock_activity(
        has_balance=False, has_ledger=False, ordered_qty=0, open_commit=0, delivered_qty=0,
    ) is False
    assert master_has_relevant_stock_activity(
        has_balance=True, has_ledger=False,
    ) is True
    assert master_has_relevant_stock_activity(
        has_balance=False, has_ledger=True,
    ) is True
    assert master_has_relevant_stock_activity(
        has_balance=False, has_ledger=False, ordered_qty=10,
    ) is True
    assert master_has_relevant_stock_activity(
        has_balance=False,
        has_ledger=False,
        demand={"has_print_line": True, "ordered_qty": 0},
    ) is True


async def test_u1_no_activity_catalog_shows_zero_without_writes(db):
    branch, _wh = await _seed_branch_wh(db)
    item = await _seed_item(db, "G1-ZERO")
    before_bal = await db[BALANCE_COLLECTION].count_documents({})
    before_led = await db[LEDGER_COLLECTION].count_documents({})

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])

    assert row["no_relevant_activity"] is True
    assert row["operational_qty_proven"] is True
    assert row["physical_qty"] == 0
    assert row["stock"] == 0
    assert row["stock_dapat_dijual"] == 0
    assert row["open_so_commitment"] == 0
    assert row["baseline_status"] == "reconciled"
    assert row["qty_tersedia_valid"] is True
    assert row["qty_tercetak"] == 0
    assert row["qty_tercetak_status"] == "READY"
    assert row["qty_polos"] == 0
    assert row["qty_polos_status"] == "READY"
    assert row["cetak_sudah_dikirim"] == 0
    whb = row["warehouse_balances"][0]
    assert whb["balance_present"] is False
    assert whb["physical_qty"] == 0
    assert whb["no_relevant_activity"] is True
    assert whb["baseline_status"] == "reconciled"

    assert await db[BALANCE_COLLECTION].count_documents({}) == before_bal
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_led

    summary = await summarize_physical_stock_status(db, branch_id=branch["id"])
    assert summary["counts"]["items_physical_missing"] == 0
    assert summary["counts"]["items_physical_ok"] >= 1


async def test_u2_active_so_without_balance_stays_belum(db):
    branch, _wh = await _seed_branch_wh(db)
    item = await _seed_item(db, "G1-SO")
    await db.orders.insert_one({
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-1",
            "master_item_id": item["id"],
            "quantity": 40,
            "production_process": "print",
        }],
        "operator_splits": [],
    })
    before_bal = await db[BALANCE_COLLECTION].count_documents({})
    before_led = await db[LEDGER_COLLECTION].count_documents({})

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])

    assert row.get("no_relevant_activity") is not True
    whb = row["warehouse_balances"][0]
    assert whb["balance_present"] is False
    assert whb["physical_qty"] is None
    assert row["baseline_status"] == "unreconciled"
    assert row["qty_tersedia_valid"] is False
    # Commitment present → Dapat Dijual must not invent from fake stock 0 book.
    assert row["ssi_excel_ready"] is False or row["stock"] is None or row.get("qty_siap_jual_status") == "BELUM_TERSEDIA"

    assert await db[BALANCE_COLLECTION].count_documents({}) == before_bal
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_led


async def test_ledger_without_balance_stays_incomplete(db):
    branch, wh = await _seed_branch_wh(db)
    item = await _seed_item(db, "G1-LED")
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"led-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 12,
        "balance_after": 12,
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row.get("no_relevant_activity") is not True
    assert row["warehouse_balances"][0]["physical_qty"] is None
    assert row["baseline_status"] == "unreconciled"


async def test_g1_valuation_no_history_is_rp0(db):
    branch, _wh = await _seed_branch_wh(db)
    item = await _seed_item(db, "G1-VAL")
    before_bal = await db[BALANCE_COLLECTION].count_documents({})
    before_led = await db[LEDGER_COLLECTION].count_documents({})

    view = await build_inventory_valuation_rows(db, branch_id=branch["id"])
    row = next(r for r in view["rows"] if r["master_item_id"] == item["id"])
    assert row["stock_fisik"] == 0
    assert row["stock_fisik_status"] == "AVAILABLE"
    assert row["inventory_value"] == 0

    assert await db[BALANCE_COLLECTION].count_documents({}) == before_bal
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_led


async def test_g1_valuation_ledger_without_balance_unavailable(db):
    branch, wh = await _seed_branch_wh(db)
    item = await _seed_item(db, "G1-VAL-LED")
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"led-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 3,
        "balance_after": 3,
    })
    view = await build_inventory_valuation_rows(db, branch_id=branch["id"])
    row = next(r for r in view["rows"] if r["master_item_id"] == item["id"])
    assert row["stock_fisik"] is None
    assert row["stock_fisik_status"] == "UNAVAILABLE"
    assert row["inventory_value"] is None


async def test_multi_wh_partial_balance_missing_slot_not_zero(db):
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh_a = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "A",
        "name": "A",
        "active": True,
        "is_operational": True,
    }
    wh_b = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "B",
        "name": "B",
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    item = await _seed_item(db, "G1-MULTI")
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "physical_qty": 25,
        "baseline_status": "reconciled",
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row.get("no_relevant_activity") is not True
    by = {w["warehouse_code"]: w for w in row["warehouse_balances"]}
    assert by["A"]["physical_qty"] == 25
    assert by["A"]["balance_present"] is True
    assert by["B"]["balance_present"] is False
    assert by["B"]["physical_qty"] is None
    assert by["B"].get("no_relevant_activity") is not True
    assert row["baseline_status"] == "unreconciled"
