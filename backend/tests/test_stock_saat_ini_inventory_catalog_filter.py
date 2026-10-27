"""Stock Saat Ini Fase 1: INVENTORY-only catalog + honest null condition fields."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_inv_filter"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.inventory_accounting import (  # noqa: E402
    STOCK_CONDITION_STATUS_UNAVAILABLE,
    is_stock_saat_ini_inventory_catalog_item,
    stock_saat_ini_condition_unavailable_fields,
)
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
    post_opening_balance,
    summarize_physical_stock_status,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_compatibility_read import MODE_COMPATIBILITY  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    MODE_WAREHOUSE_STANDALONE,
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_inv_filter"
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
    server.db = database
    server.client = client
    await ensure_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_master(db, code: str, *, item_type):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": item_type,
        "is_active": True,
    }
    await db.master_items.insert_one(dict(item))
    return item


def test_helper_inventory_only_never_defaults_null():
    assert is_stock_saat_ini_inventory_catalog_item("INVENTORY") is True
    assert is_stock_saat_ini_inventory_catalog_item("inventory") is True
    assert is_stock_saat_ini_inventory_catalog_item("SERVICE") is False
    assert is_stock_saat_ini_inventory_catalog_item("NON_INVENTORY") is False
    assert is_stock_saat_ini_inventory_catalog_item(None) is False
    assert is_stock_saat_ini_inventory_catalog_item("") is False
    assert is_stock_saat_ini_inventory_catalog_item("UNKNOWN") is False
    fields = stock_saat_ini_condition_unavailable_fields()
    assert fields["qty_polos_gudang"] is None
    assert fields["qty_sudah_cetak_gudang"] is None
    assert fields["qty_siap_jual"] is None
    assert fields["qty_polos_gudang_status"] == STOCK_CONDITION_STATUS_UNAVAILABLE
    assert "0" not in str(fields["qty_polos_gudang"])


async def test_compatibility_catalog_filters_non_inventory_types(db):
    inv = await _seed_master(db, "INV-1", item_type="INVENTORY")
    await _seed_master(db, "SVC-1", item_type="SERVICE")
    await _seed_master(db, "NON-1", item_type="NON_INVENTORY")
    await _seed_master(db, "NULL-1", item_type=None)
    await db.master_items.insert_one({
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "UNK-1",
        "item_name": "UNK-1",
        "unit": "PCS",
        "is_active": True,
    })
    await db.product_stock_state.insert_one({
        "master_item_id": inv["id"],
        "total_qty": 50,
        "initialized": True,
        "status": "INITIALIZED",
    })

    catalog = await list_physical_stock_catalog(db)
    assert catalog["stock_read_mode"] == MODE_COMPATIBILITY
    codes = {r["item_code"] for r in catalog["items"]}
    assert codes == {"INV-1"}
    assert catalog["total"] == 1
    row = catalog["items"][0]
    assert row["qty_polos_gudang"] is None
    assert row["qty_sudah_cetak_gudang"] is None
    assert row["qty_siap_jual"] is None
    assert row["qty_polos_gudang_status"] == STOCK_CONDITION_STATUS_UNAVAILABLE
    # Legacy demand fields retained
    assert "qty_pesanan" in row
    assert "qty_belum_terkirim" in row
    assert "qty_tersedia" in row
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


async def _seed_branch_warehouse(db):
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "code": "GDG1",
        "name": "Gudang 1",
        "branch_id": branch["id"],
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    return branch, wh


async def test_r2_catalog_and_summary_follow_inventory_filter(db):
    await set_r2_authority_active(db, active=True)
    branch, wh = await _seed_branch_warehouse(db)
    inv = await _seed_master(db, "INV-R2", item_type="INVENTORY")
    svc = await _seed_master(db, "SVC-R2", item_type="SERVICE")
    non = await _seed_master(db, "NON-R2", item_type="NON_INVENTORY")
    await post_opening_balance(
        db,
        inv["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "physical_qty": 10,
            "idempotency_key": f"open-{uuid.uuid4().hex}",
            "reason": "opening",
        },
        actor_id="test",
    )
    # SERVICE/NON must not appear even if balances somehow exist.
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": svc["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "physical_qty": 99,
        "baseline_status": "reconciled",
    })
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": non["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "physical_qty": 88,
        "baseline_status": "reconciled",
    })

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    assert catalog["r2_authority_active"] is True
    assert catalog["stock_read_mode"] == "r2"
    assert [r["item_code"] for r in catalog["items"]] == ["INV-R2"]
    row = catalog["items"][0]
    assert row["physical_qty"] == 10
    # Owner Excel SSI: condition columns are computed (Stock Dapat Dijual etc.), not null.
    assert row["stock"] == 10
    assert row["stock_dapat_dijual"] == row["qty_siap_jual"]
    assert row["qty_siap_jual_status"] == "READY"
    assert row["baseline_status"] == "reconciled"

    # Missing balance stays non-zero-forced: second INVENTORY without opening.
    await _seed_master(db, "INV-MISS", item_type="INVENTORY")
    catalog2 = await list_physical_stock_catalog(db, branch_id=branch["id"])
    miss = next(r for r in catalog2["items"] if r["item_code"] == "INV-MISS")
    whb = miss["warehouse_balances"][0]
    assert whb["balance_present"] is False
    assert whb["physical_qty"] is None
    assert miss["qty_tersedia_valid"] is False

    summary = await summarize_physical_stock_status(db, branch_id=branch["id"])
    assert summary["items_total"] == 2
    assert summary["items_scanned"] == 2
    assert summary["counts"]["items_physical_ok"] == 1
    assert summary["counts"]["items_physical_missing"] == 1


async def test_standalone_catalog_inventory_filter(db):
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    branch, wh = await _seed_branch_warehouse(db)
    inv = await _seed_master(db, "INV-SA", item_type="INVENTORY")
    await _seed_master(db, "SVC-SA", item_type="SERVICE")
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": inv["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "physical_qty": 7,
        "baseline_status": "reconciled",
    })
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    assert catalog["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert catalog["warehouse_stock_standalone"] is True
    assert [r["item_code"] for r in catalog["items"]] == ["INV-SA"]
    # Standalone path still inventory-only; Excel columns attach when balance present.
    assert catalog["items"][0]["physical_qty"] == 7
    assert str(catalog["items"][0]["ssi_formula_version"] or "").startswith("ssi_excel_v1")
    assert catalog["items"][0]["qty_polos_gudang"] == 7
    assert catalog["items"][0]["stock_dapat_dijual"] == 7
