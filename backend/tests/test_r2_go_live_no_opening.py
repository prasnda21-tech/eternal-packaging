"""R2 go-live without Saldo Awal — demand buckets, unreconciled book, opname reconcile.

Isolated replica-set DB only. Never touches production / OWNER UAT.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_r2_go_live_no_opening"
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
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    confirm_sales_delivery,
    ensure_indexes,
    get_balance,
    list_physical_stock_catalog,
    post_movement,
    reverse_confirmed_sales_delivery,
)
from services.r2_go_live import (  # noqa: E402
    check_r2_production_readiness,
    initialize_delivery_cutover_baseline,
    set_r2_authority_active,
)

TEST_DB = "eternal_packaging_test_r2_go_live_no_opening"
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
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_ygy(db, *, with_opening: bool = False, opening_qty: int = 0):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "BOX",
        "item_name": "Box",
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
        "name": "Utama",
        "active": True,
        "is_operational": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    if with_opening:
        from services.physical_stock import post_opening_balance

        await post_opening_balance(
            db,
            item["id"],
            {
                "branch_id": branch["id"],
                "warehouse_id": warehouse["id"],
                "physical_qty": opening_qty,
                "reason": "open",
                "idempotency_key": f"open-{item['id']}",
            },
            actor_id="t",
        )
    return item, branch, warehouse


def _print_order(item_id, *, ordered, completed, order_id=None, shipments=None, confirmations=None):
    oid = order_id or f"so-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": oid,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item_id,
            "quantity": ordered,
            "production_process": "print",
            "name": "Box",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }
    if shipments is not None:
        doc["shipments"] = shipments
    if confirmations is not None:
        doc["delivery_confirmations"] = confirmations
    return doc


async def test_r2_active_no_opening_demand_buckets_and_tersedia_invalid(db):
    """R2 ON, no Saldo Awal: Pesanan/Belum correct; Tersedia not definitive."""
    await set_r2_authority_active(db, active=True)
    item, branch, warehouse = await _seed_ygy(db, with_opening=False)
    await db.orders.insert_one(_print_order(item["id"], ordered=1000, completed=600))
    # Historical shipped 200 proven
    order = await db.orders.find_one({"line_items.master_item_id": item["id"]})
    sj = f"sj-{uuid.uuid4().hex[:8]}"
    await db.orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "shipments": [{
                "id": sj,
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 200,
                "qty_delivered": 200,
                "lines": [{"line_idx": 0, "qty": 200}],
            }],
        }},
    )
    await initialize_delivery_cutover_baseline(db)

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_pesanan"] == 400  # 1000 - 600
    assert row["qty_belum_terkirim"] == 400  # 600 - 200 cutover
    assert row["baseline_status"] == BASELINE_UNRECONCILED
    assert row["qty_tersedia_valid"] is False
    assert row["baseline_label"]
    # No OPENING_BALANCE invented
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_OPENING_BALANCE,
    }) == 0
    # PO history untouched
    stored = await db.orders.find_one({"id": order["id"]})
    assert stored["operator_splits"][0]["qty_completed"] == 600


async def test_readiness_does_not_require_opening_balance(db):
    await _seed_ygy(db, with_opening=False)
    await initialize_delivery_cutover_baseline(db)
    report = await check_r2_production_readiness(db)
    assert "OPENING" not in " ".join(report.get("blockers") or []).upper()
    assert report["R2_BALANCE_COUNT"] == 0
    # Cutover + YGY + warehouse can be READY without any opening rows
    assert report["CUTOVER_BASELINE_READY"] is True
    assert report["YGY_BRANCH_READY"] is True


async def test_delivery_and_cancel_before_opname_no_double_count(db):
    await set_r2_authority_active(db, active=True)
    item, branch, warehouse = await _seed_ygy(db, with_opening=False)
    order = _print_order(item["id"], ordered=100, completed=80)
    await db.orders.insert_one(dict(order))
    await initialize_delivery_cutover_baseline(db)

    conf = f"conf-{uuid.uuid4().hex[:8]}"
    result = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf,
        lines=[{"line_idx": 0, "qty": 30, "master_item_id": item["id"], "line_id": "ln-0"}],
        actor_id="t",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
    )
    assert result["replayed"] is False
    bal = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal["physical_qty"] == -30
    assert bal["baseline_status"] == BASELINE_UNRECONCILED

    rev = await reverse_confirmed_sales_delivery(
        db, confirmation_id=conf, actor_id="t", reason="batal",
    )
    assert rev["reversed"] is True
    bal2 = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal2["physical_qty"] == 0
    assert bal2["baseline_status"] == BASELINE_UNRECONCILED
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
    }) == 1

    # Replay cancel does not double
    rev2 = await reverse_confirmed_sales_delivery(
        db, confirmation_id=conf, actor_id="t", reason="batal",
    )
    assert rev2["replayed"] is True
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
    }) == 1


async def test_purchase_receipt_then_delivery_then_first_opname(db):
    """Transactions before opname stay unreconciled; first opname reconciles running book."""
    await set_r2_authority_active(db, active=True)
    item, branch, warehouse = await _seed_ygy(db, with_opening=False)
    order = _print_order(item["id"], ordered=50, completed=40)
    await db.orders.insert_one(dict(order))
    await initialize_delivery_cutover_baseline(db)

    await post_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 100,
            "reason": "GRN sebelum opname",
            "idempotency_key": f"grn-{item['id']}",
        },
        actor_id="t",
    )
    bal = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal["physical_qty"] == 100
    assert bal["baseline_status"] == BASELINE_UNRECONCILED

    await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=f"conf-{uuid.uuid4().hex[:8]}",
        lines=[{"line_idx": 0, "qty": 25, "master_item_id": item["id"], "line_id": "ln-0"}],
        actor_id="t",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
    )
    bal2 = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal2["physical_qty"] == 75
    assert bal2["baseline_status"] == BASELINE_UNRECONCILED

    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_pesanan"] == 10  # 50 - 40
    assert row["qty_belum_terkirim"] == 15  # 40 - 25
    assert row["qty_tersedia_valid"] is False

    # First Stock Opname after transactions — reconcile to counted 80 (delta +5)
    adj = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "target_physical_qty": 80,
            "reason": "Opname pertama setelah transaksi",
            "idempotency_key": f"opname-{item['id']}",
        },
        actor_id="t",
    )
    assert adj["previous_physical_qty"] == 75
    assert adj["qty_delta"] == 5
    bal3 = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal3["physical_qty"] == 80
    assert bal3["baseline_status"] == BASELINE_RECONCILED

    catalog2 = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    row2 = next(r for r in catalog2["items"] if r["master_item_id"] == item["id"])
    assert row2["qty_tersedia_valid"] is True
    assert row2["qty_pesanan"] == 10
    assert row2["qty_belum_terkirim"] == 15
    # Opname adjusts running book — does not re-add GRN/delivery
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
    }) == 1


async def test_verified_zero_opname_vs_unknown_missing_balance(db):
    await set_r2_authority_active(db, active=True)
    item, branch, warehouse = await _seed_ygy(db, with_opening=False)
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "WHB",
        "name": "Cadangan",
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh_b))

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    by_code = {w["warehouse_code"]: w for w in row["warehouse_balances"]}
    assert by_code["MAIN"]["balance_present"] is False
    assert by_code["MAIN"]["physical_qty"] is None
    assert by_code["WHB"]["balance_present"] is False
    assert row["baseline_status"] == BASELINE_UNRECONCILED
    assert row["qty_tersedia_valid"] is False

    # Opname count=0 on missing balance must not invent a zero row (verified elsewhere);
    # when unreconciled zero book exists, zero-diff reconciles.
    from services.physical_stock import post_opening_balance

    # Create unreconciled zero via outbound path first on MAIN only
    order = _print_order(item["id"], ordered=10, completed=10)
    await db.orders.insert_one(dict(order))
    await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=f"conf-{uuid.uuid4().hex[:8]}",
        lines=[{"line_idx": 0, "qty": 1, "master_item_id": item["id"], "line_id": "ln-0"}],
        actor_id="t",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
    )
    # Reverse to book 0 unreconciled
    # (simpler: force balance 0 unreconciled via delivery then reverse)
    confs = await db["physical_sales_delivery_confirmations"].find({}, {"_id": 0, "id": 1}).to_list(5)
    assert confs
    await reverse_confirmed_sales_delivery(
        db, confirmation_id=confs[0]["id"], actor_id="t", reason="batal",
    )
    bal = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_UNRECONCILED

    adj = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "target_physical_qty": 0,
            "reason": "Opname verified zero",
            "idempotency_key": f"zero-{item['id']}",
        },
        actor_id="t",
    )
    assert adj["qty_delta"] == 0
    bal2 = await get_balance(db, item["id"], branch["id"], warehouse["id"])
    assert bal2["physical_qty"] == 0
    assert bal2["baseline_status"] == BASELINE_RECONCILED
    # WHB still unknown / no balance invented
    assert await get_balance(db, item["id"], branch["id"], wh_b["id"]) is None


async def test_warehouses_not_mixed_on_pre_opname_movements(db):
    await set_r2_authority_active(db, active=True)
    item, branch, wh_a = await _seed_ygy(db, with_opening=False)
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "WHB",
        "name": "Cadangan",
        "active": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh_b))
    await post_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 40,
            "reason": "GRN A",
            "idempotency_key": f"grn-a-{item['id']}",
        },
        actor_id="t",
    )
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 40
    assert await get_balance(db, item["id"], branch["id"], wh_b["id"]) is None
    cat_a = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"],
    )
    cat_b = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_b["id"],
    )
    row_a = next(r for r in cat_a["items"] if r["master_item_id"] == item["id"])
    row_b = next(r for r in cat_b["items"] if r["master_item_id"] == item["id"])
    assert row_a["physical_qty"] == 40
    assert row_b["physical_qty"] == 0
    assert row_b["baseline_status"] == BASELINE_UNRECONCILED
    assert row_b["qty_tersedia_valid"] is False
