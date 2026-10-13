"""STATUS_CETAK / operator completion vs R2 physical demand — isolated DB only.

Proves: after R2 is the official stock source, finishing print updates
order progress / operational demand buckets while warehouse physical total
stays unchanged until R2 SALES_DELIVERY. Does not invent BOM or new SKUs;
does not post PRODUCTION_OUTPUT / PRODUCTION_CONSUMPTION.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_status_cetak_r2_demand"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_PRODUCTION_CONSUMPTION,
    MOVEMENT_PRODUCTION_OUTPUT,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    confirm_sales_delivery,
    ensure_indexes,
    get_balance,
    list_physical_stock_catalog,
    operational_availability,
    post_opening_balance,
)
from services.physical_stock_engine import (  # noqa: E402
    MOVEMENT_STATUS_CETAK,
    handle_operator_progress,
)
from services.product_stock_state import initialize_opening_balance  # noqa: E402
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_status_cetak_r2_demand"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


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
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db, *, physical=1000, ordered=300):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "CETAK-SKU",
        "item_name": "Cetak SKU",
        "unit": "PCS",
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
        "name": "MAIN",
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": physical,
            "reason": "Saldo awal",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="tester",
    )
    order = {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "po_number": "PO-CETAK-1",
        "cancelled": False,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery",
        },
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": ordered,
            "production_process": "print",
            "unit": "PCS",
            "name": "Cetak SKU",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 0, "operator_name": "Op"}],
    }
    await db.orders.insert_one(dict(order))
    return item, branch, warehouse, order


async def _row(db, item_id):
    catalog = await list_physical_stock_catalog(db)
    return next(r for r in catalog["items"] if r["master_item_id"] == item_id)


async def test_print_completion_moves_demand_without_changing_r2_physical(db):
    """Selesai cetak: progres/kebutuhan bergerak; fisik gudang tetap sampai SJ."""
    item, branch, warehouse, order = await _seed(db, physical=1000, ordered=300)

    before = await _row(db, item["id"])
    assert (before["physical_qty"], before["qty_pesanan"], before["qty_belum_terkirim"], before["qty_tersedia"]) == (
        1000, 300, 0, 700,
    )
    bal_before = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal_before["physical_qty"] == 1000
    ledger_before = await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]})
    prod_before = await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": {"$in": [MOVEMENT_PRODUCTION_OUTPUT, MOVEMENT_PRODUCTION_CONSUMPTION]},
    })
    assert prod_before == 0

    # R2 SSI demand source: operator_splits.qty_completed (same SKU; no new master).
    await db.orders.update_one(
        {"id": order["id"]},
        {"$set": {"operator_splits": [{"line_idx": 0, "qty_completed": 200, "operator_name": "Op"}]}},
    )

    after_print = await _row(db, item["id"])
    assert (after_print["physical_qty"], after_print["qty_pesanan"], after_print["qty_belum_terkirim"], after_print["qty_tersedia"]) == (
        1000, 100, 200, 700,
    )
    bal_after_print = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal_after_print["physical_qty"] == 1000
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == ledger_before
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": {"$in": [MOVEMENT_PRODUCTION_OUTPUT, MOVEMENT_PRODUCTION_CONSUMPTION]},
    }) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 1

    # Formula contract: completion moves pesanan → belum; tersedia uses same physical.
    buckets = operational_availability(
        physical_qty=1000, ordered_qty=300, completed_qty=200, r2_delivered_qty=0,
    )
    assert buckets == {
        "physical_qty": 1000.0,
        "qty_pesanan": 100.0,
        "qty_belum_terkirim": 200.0,
        "qty_tersedia": 700.0,
    }

    # Only R2 SALES_DELIVERY reduces physical once.
    result = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=f"conf-{uuid.uuid4().hex[:8]}",
        lines=[{"line_idx": 0, "qty": 200}],
        actor_id="tester",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
    )
    assert result["replayed"] is False
    assert len(result["movements"]) == 1
    assert result["movements"][0]["movement_type"] == MOVEMENT_SALES_DELIVERY
    assert result["movements"][0]["qty_delta"] == -200

    after_sd = await _row(db, item["id"])
    assert (after_sd["physical_qty"], after_sd["qty_pesanan"], after_sd["qty_belum_terkirim"], after_sd["qty_tersedia"]) == (
        800, 100, 0, 700,
    )
    bal_after_sd = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal_after_sd["physical_qty"] == 800
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": {"$in": [MOVEMENT_PRODUCTION_OUTPUT, MOVEMENT_PRODUCTION_CONSUMPTION]},
    }) == 0


async def test_legacy_status_cetak_same_sku_total_delta_zero_no_r2_prod(db):
    """Legacy STATUS_CETAK: same master_item_id bucket convert; total Δ0; no R2 PRODUCTION_*."""
    item, branch, warehouse, order = await _seed(db, physical=1000, ordered=300)
    await initialize_opening_balance(
        db,
        item["id"],
        {"unprinted_qty": 1000.0, "printed_qty": 0.0, "base_uom": "PCS"},
        user={"email": "tester@test.local"},
    )
    r2_before = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    r2_ledger_before = await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]})

    split = {"line_idx": 0, "qty_completed": 0, "operator_name": "Op"}
    mov = await handle_operator_progress(
        db, order, split, increment=200, action_id="act-cetak-1", live_enabled=True,
    )
    assert mov is not None
    assert mov["movement_type"] == MOVEMENT_STATUS_CETAK
    assert float(mov.get("delta", 0) or 0) == 0.0
    assert float(mov.get("delta_unprinted", 0) or 0) == -200.0
    assert float(mov.get("delta_printed", 0) or 0) == 200.0
    state = await db.product_stock_state.find_one({"master_item_id": item["id"]}, {"_id": 0})
    assert float(state["total_qty"]) == 1000.0
    assert float(state["unprinted_qty"]) == 800.0
    assert float(state["printed_qty"]) == 200.0

    r2_after = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert r2_after["physical_qty"] == r2_before["physical_qty"] == 1000
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == r2_ledger_before
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": {"$in": [MOVEMENT_PRODUCTION_OUTPUT, MOVEMENT_PRODUCTION_CONSUMPTION]},
    }) == 0
