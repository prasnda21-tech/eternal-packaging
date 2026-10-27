"""R2 book qty may go negative; missing baseline is unreconciled — isolated RS DB."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_r2_allow_negative_book"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BASELINE_UNRECONCILED,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    confirm_sales_delivery,
    ensure_indexes,
    get_balance,
    list_physical_stock_catalog,
    operational_availability,
    post_movement,
    post_opening_balance,
    require_r2_delivery_stock_ready,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_r2_allow_negative_book"
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


async def _seed(db, *, physical_a=None, physical_b=500):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "NEG-BOX",
        "item_name": "Neg Box",
        "unit": "PCS",
        # Stock Saat Ini catalog is INVENTORY-only; fixture must be explicit.
        "item_type": "INVENTORY",
        "is_active": True,
    }
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "Yogyakarta", "active": True}
    wh_a = {"id": f"wh-a-{uuid.uuid4().hex[:6]}", "branch_id": branch["id"], "code": "MAIN", "name": "MAIN", "active": True}
    wh_b = {"id": f"wh-b-{uuid.uuid4().hex[:6]}", "branch_id": branch["id"], "code": "WHB", "name": "WHB", "active": True}
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    if physical_a is not None:
        await post_opening_balance(
            db, item["id"],
            {
                "branch_id": branch["id"], "warehouse_id": wh_a["id"],
                "physical_qty": physical_a, "reason": "open-a",
                "idempotency_key": f"open-a-{item['id']}",
            },
            actor_id="t",
        )
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"], "warehouse_id": wh_b["id"],
            "physical_qty": physical_b, "reason": "open-b",
            "idempotency_key": f"open-b-{item['id']}",
        },
        actor_id="t",
    )
    return item, branch, wh_a, wh_b


def _order(item, *, ordered=100, completed=100):
    return {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": ordered,
            "production_process": "print",
            "unit": "PCS",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }


async def _deliver(db, order, qty, *, warehouse, branch, confirmation_id=None):
    return await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=confirmation_id or f"conf-{uuid.uuid4().hex[:8]}",
        lines=[{"line_idx": 0, "qty": qty}],
        actor_id="tester",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
    )


async def test_ship_25_from_10_leaves_book_minus_15(db):
    item, branch, wh_a, wh_b = await _seed(db, physical_a=10)
    order = _order(item, ordered=100, completed=100)
    await db.orders.insert_one(dict(order))
    legacy_sd = await db.stock_movements.count_documents({}) if "stock_movements" in await db.list_collection_names() else 0
    result = await _deliver(db, order, 25, warehouse=wh_a, branch=branch, confirmation_id="ship-25")
    assert result["replayed"] is False
    assert result["movements"][0]["qty_delta"] == -25
    assert result["movements"][0]["balance_before"] == 10
    assert result["movements"][0]["balance_after"] == -15
    warn = result["stock_warnings"][0]
    assert warn["stok_tercatat"] == 10
    assert warn["kebutuhan"] == 25
    assert warn["kekurangan"] == 15
    assert warn["blocks_delivery"] is False
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == -15
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY, "warehouse_id": wh_a["id"],
    }) == 1


async def test_ship_from_zero_and_already_negative(db):
    item, branch, wh_a, wh_b = await _seed(db, physical_a=0)
    order = _order(item)
    await db.orders.insert_one(dict(order))
    first = await _deliver(db, order, 5, warehouse=wh_a, branch=branch, confirmation_id="from-zero")
    assert first["movements"][0]["balance_after"] == -5
    second = await _deliver(db, order, 3, warehouse=wh_a, branch=branch, confirmation_id="from-neg")
    assert second["movements"][0]["balance_before"] == -5
    assert second["movements"][0]["balance_after"] == -8
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500


async def test_negative_available_does_not_block_demand_math(db):
    buckets = operational_availability(
        physical_qty=-15, ordered_qty=100, completed_qty=40, r2_delivered_qty=0,
    )
    assert buckets["physical_qty"] == -15
    assert buckets["qty_pesanan"] == 60
    assert buckets["qty_belum_terkirim"] == 40
    assert buckets["qty_tersedia"] == -115


async def test_receipt_after_negative_increases_book(db):
    item, branch, wh_a, _wh_b = await _seed(db, physical_a=10)
    order = _order(item)
    await db.orders.insert_one(dict(order))
    await _deliver(db, order, 25, warehouse=wh_a, branch=branch, confirmation_id="pre-gr")
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == -15
    await post_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 40,
            "reference_type": "purchase_goods_receipt",
            "reference_id": f"gr-{item['id']}",
            "reason": "GR after minus",
            "idempotency_key": f"gr-{item['id']}",
        },
        actor_id="t",
    )
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 25


async def test_opname_corrects_negative_to_physical_count(db):
    item, branch, wh_a, _wh_b = await _seed(db, physical_a=10)
    order = _order(item)
    await db.orders.insert_one(dict(order))
    await _deliver(db, order, 25, warehouse=wh_a, branch=branch, confirmation_id="pre-opname")
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == -15
    result = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 12,
            "reason": "Opname koreksi dari minus",
            "idempotency_key": f"adj-{item['id']}",
        },
        actor_id="t",
    )
    assert result["previous_physical_qty"] == -15
    assert result["target_physical_qty"] == 12
    assert result["qty_delta"] == 27
    bal = await get_balance(db, item["id"], branch["id"], wh_a["id"])
    assert bal["physical_qty"] == 12
    assert bal.get("baseline_status") == "reconciled"


async def test_missing_baseline_delivery_unreconciled_no_fake_zero_claim(db):
    item, branch, wh_a, wh_b = await _seed(db, physical_a=None)
    order = _order(item, ordered=50, completed=50)
    await db.orders.insert_one(dict(order))
    _br, _wh, warnings = await require_r2_delivery_stock_ready(
        db, order, warehouse_id=wh_a["id"], branch_id=branch["id"],
        lines=[{"master_item_id": item["id"], "qty": 7}],
    )
    assert warnings[0]["baseline_status"] == BASELINE_UNRECONCILED
    assert warnings[0]["stok_tercatat_uncertain"] is True
    assert warnings[0]["blocks_delivery"] is False

    result = await _deliver(db, order, 7, warehouse=wh_a, branch=branch, confirmation_id="no-base")
    mov = result["movements"][0]
    assert mov["qty_delta"] == -7
    assert mov["balance_after"] == -7
    assert (mov.get("metadata") or {}).get("baseline_status") == BASELINE_UNRECONCILED
    bal = await get_balance(db, item["id"], branch["id"], wh_a["id"])
    assert bal is not None
    assert bal["physical_qty"] == -7
    assert bal.get("baseline_status") == BASELINE_UNRECONCILED
    # No OPENING_BALANCE invented
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "warehouse_id": wh_a["id"],
        "movement_type": "OPENING_BALANCE",
    }) == 0
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], warehouse_id=wh_a["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["baseline_status"] == BASELINE_UNRECONCILED
    assert row["saldo_buku_negatif"] is True
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500

    # Replay does not double
    replay = await _deliver(db, order, 7, warehouse=wh_a, branch=branch, confirmation_id="no-base")
    assert replay["replayed"] is True
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1
