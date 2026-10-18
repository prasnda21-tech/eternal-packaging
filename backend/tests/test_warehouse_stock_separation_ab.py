"""Warehouse A/B separation — same master, isolated RS test DB + R2 fixture only.

Does not touch eternal_packaging_preview or production data.

Arithmetic notes (do not mix):
- Sequential full flow: open A=1000 → opname 950 → GR +40 → 990 → SD −25 → 965.
- Receipt/delivery-only (no opname): 1000 + 40 − 25 = 1015.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.branch_access import CROSS_BRANCH_DENIED, require_warehouse_access
from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    WAREHOUSES_COLLECTION,
    confirm_sales_delivery,
    get_balance,
    list_physical_stock_catalog,
    post_movement,
    ensure_indexes as ensure_physical_indexes,
)
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_orders import create_purchase_order
from services.r2_go_live import set_r2_authority_active
from services.stock_opname_sessions import (
    MODE_OPERATIONAL,
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes,
    get_stock_opname_session,
    list_stock_opname_sessions,
    record_stock_opname_counts,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_warehouse_ab_sep"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
ADMIN = {"id": "ab-admin", "email": "ab-admin@test.local", "role": "admin", "name": "AB Admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_physical_indexes(database)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def _fixture(db):
    """Same master item in warehouse A (YGY) and B (SMG) — IDs, not names."""
    branch_a = str(uuid.uuid4())
    branch_b = str(uuid.uuid4())
    wh_a = str(uuid.uuid4())
    wh_b = str(uuid.uuid4())
    item = str(uuid.uuid4())
    await db[BRANCHES_COLLECTION].insert_many([
        {"id": branch_a, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": branch_b, "code": "SMG", "name": "Semarang", "active": True},
    ])
    await db[WAREHOUSES_COLLECTION].insert_many([
        {
            "id": wh_a, "branch_id": branch_a, "code": "A", "name": "Gudang A",
            "active": True, "is_operational": True,
        },
        {
            "id": wh_b, "branch_id": branch_b, "code": "B", "name": "Gudang B",
            "active": True, "is_operational": True,
        },
    ])
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item,
        "item_code": "AB-SAME-1",
        "item_name": "Karton Shared",
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
        "accurate_item_id": f"LOCAL-{item}",
    })
    # Opening must be dated BEFORE physical_count_at so operational net-after-count
    # does not treat the opening itself as a post-count movement.
    open_at = _iso(datetime.now(timezone.utc) - timedelta(hours=3))
    for wid, bid, qty, key in ((wh_a, branch_a, 1000, "a"), (wh_b, branch_b, 500, "b")):
        await post_movement(db, {
            "master_item_id": item,
            "branch_id": bid,
            "warehouse_id": wid,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": qty,
            "reference_type": "opening_balance",
            "reference_id": f"open-{key}",
            "idempotency_key": f"open-{key}",
            "occurred_at": open_at,
            "reason": f"Saldo awal {key}",
        }, actor_id="tester")
    return {
        "branch_a": branch_a,
        "branch_b": branch_b,
        "wh_a": wh_a,
        "wh_b": wh_b,
        "item": item,
    }


async def _qty(db, item, branch, warehouse):
    doc = await get_balance(db, item, branch, warehouse)
    return float((doc or {}).get("physical_qty") or 0)


def _line(session, item_id):
    for row in session.get("lines") or []:
        if str(row.get("master_item_id")) == str(item_id):
            return row
    raise AssertionError("line missing")


async def _catalog_row(db, item_id, *, branch_id=None, warehouse_id=None):
    catalog = await list_physical_stock_catalog(
        db, branch_id=branch_id, warehouse_id=warehouse_id,
    )
    return next(
        (row for row in catalog["items"] if row["master_item_id"] == item_id),
        None,
    )


async def test_ab_sequential_opname_receipt_delivery_keeps_b(
    db,
):
    """One continuous sequence — not the receipt/delivery-only 1015 fixture.

    A: 1000 → opname 950 → GR +40 → 990 → SD −25 → 965. B stays 500.
    Uses operational GR + sales-delivery confirm (not raw ledger helpers).
    """
    fx = await _fixture(db)
    item, ba, bb, wa, wb = fx["item"], fx["branch_a"], fx["branch_b"], fx["wh_a"], fx["wh_b"]
    assert await _qty(db, item, ba, wa) == 1000
    assert await _qty(db, item, bb, wb) == 500

    # Branch-scoped demand (no warehouse allocation); does not move physical qty.
    await db.orders.insert_one({
        "id": "order-ab-seq-unalloc",
        "status": "open",
        "cancelled": False,
        "branch_id": ba,
        "line_items": [{
            "line_id": "l1",
            "master_item_id": item,
            "quantity": 120,
            "production_process": "non_print",
            "name": "Karton Shared",
        }],
    })

    created = await create_stock_opname_session(db, {
        "branch_id": ba,
        "warehouse_id": wa,
        "mode": MODE_OPERATIONAL,
        "idempotency_key": "ab-seq-opname",
    }, ADMIN)
    counting = await start_stock_opname_cutoff(db, created["id"], ADMIN)
    line = _line(counting, item)
    assert line["demand_allocation"] == "unallocated"
    assert float(line.get("qty_belum_terkirim") or 0) == 120
    # Count time after opening (T-3h), before any later GR/SD on this sequence.
    count_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 950,
            "physical_count_at": _iso(count_at),
        }],
    }, ADMIN)
    await submit_stock_opname_review(db, counting["id"], ADMIN)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "ab-seq-conf"}, ADMIN,
    )
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["warehouse_id"] == wa
    # Replay confirm must not double-adjust
    replay = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "ab-seq-conf"}, ADMIN,
    )
    assert replay["status"] == "CONFIRMED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "warehouse_id": wa,
        "master_item_id": item,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "warehouse_id": wb,
    }) == 0
    assert await _qty(db, item, ba, wa) == 950
    assert await _qty(db, item, bb, wb) == 500

    cat_a = await _catalog_row(db, item, branch_id=ba, warehouse_id=wa)
    assert cat_a is not None
    assert float(cat_a["physical_qty"]) == 950
    # Branch A demand reduces tersedia on A; never invents demand onto branch B.
    demand_a = float(cat_a.get("qty_pesanan") or 0) + float(cat_a.get("qty_belum_terkirim") or 0)
    assert demand_a >= 120
    assert float(cat_a["qty_tersedia"]) == (
        float(cat_a["physical_qty"])
        - float(cat_a.get("qty_pesanan") or 0)
        - float(cat_a.get("qty_belum_terkirim") or 0)
    )

    cat_b = await _catalog_row(db, item, branch_id=bb, warehouse_id=wb)
    assert cat_b is not None
    assert float(cat_b["physical_qty"]) == 500
    demand_b = float(cat_b.get("qty_pesanan") or 0) + float(cat_b.get("qty_belum_terkirim") or 0)
    assert demand_b == 0

    # Operational purchase goods receipt on A only
    po = await create_purchase_order(db, {
        "supplier": "Supplier AB",
        "lines": [{
            "master_item_id": item,
            "item_name": "Karton Shared",
            "item_code": "AB-SAME-1",
            "unit": "PCS",
            "ordered_qty": 40,
            "unit_price": 1000,
        }],
        "idempotency_key": "ab-seq-po",
    }, ADMIN)
    gr_key = "ab-seq-gr"
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wa,
        "lines": [{
            "purchase_order_line_id": po["lines"][0]["line_id"],
            "received_qty": 40,
        }],
        "idempotency_key": gr_key,
    }, ADMIN)
    assert receipt["warehouse_id"] == wa
    assert receipt["branch_id"] == ba
    # Idempotent replay
    receipt2 = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wa,
        "lines": [{
            "purchase_order_line_id": po["lines"][0]["line_id"],
            "received_qty": 40,
        }],
        "idempotency_key": gr_key,
    }, ADMIN)
    assert receipt2["id"] == receipt["id"]
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "warehouse_id": wa,
        "master_item_id": item,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "warehouse_id": wb,
    }) == 0
    assert await _qty(db, item, ba, wa) == 990
    assert await _qty(db, item, bb, wb) == 500

    # Operational sales delivery confirm on A only
    await db.orders.insert_one({
        "id": "order-ab-seq-deliver",
        "status": "open",
        "cancelled": False,
        "branch_id": ba,
        "line_items": [{
            "line_id": "ld1",
            "master_item_id": item,
            "quantity": 25,
            "production_process": "non_print",
            "name": "Karton Shared",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 25}],
    })
    conf_id = "ab-seq-sd"
    delivered = await confirm_sales_delivery(
        db,
        order_id="order-ab-seq-deliver",
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25, "master_item_id": item}],
        actor_id=ADMIN["id"],
        warehouse_id=wa,
        branch_id=ba,
    )
    assert delivered["replayed"] is False
    assert delivered["movements"][0]["qty_delta"] == -25
    assert delivered["movements"][0]["warehouse_id"] == wa
    replay_sd = await confirm_sales_delivery(
        db,
        order_id="order-ab-seq-deliver",
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25, "master_item_id": item}],
        actor_id=ADMIN["id"],
        warehouse_id=wa,
        branch_id=ba,
    )
    assert replay_sd["replayed"] is True
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wa,
        "master_item_id": item,
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wb,
    }) == 0

    assert await _qty(db, item, ba, wa) == 965
    assert await _qty(db, item, bb, wb) == 500

    final_a = await _catalog_row(db, item, branch_id=ba, warehouse_id=wa)
    final_b = await _catalog_row(db, item, branch_id=bb, warehouse_id=wb)
    assert float(final_a["physical_qty"]) == 965
    assert float(final_b["physical_qty"]) == 500
    assert await db[BALANCE_COLLECTION].count_documents({"master_item_id": item}) == 2
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item,
        "movement_type": MOVEMENT_OPENING_BALANCE,
    }) == 2


async def test_ab_receipt_delivery_only_without_opname_is_1015(db):
    """Separate fixture: opening + GR + SD only → 1000+40-25=1015. Not after opname."""
    fx = await _fixture(db)
    item, ba, bb, wa, wb = fx["item"], fx["branch_a"], fx["branch_b"], fx["wh_a"], fx["wh_b"]
    po = await create_purchase_order(db, {
        "supplier": "Supplier AB-only",
        "lines": [{
            "master_item_id": item,
            "item_name": "Karton Shared",
            "item_code": "AB-SAME-1",
            "unit": "PCS",
            "ordered_qty": 40,
            "unit_price": 1000,
        }],
        "idempotency_key": "ab-only-po",
    }, ADMIN)
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wa,
        "lines": [{
            "purchase_order_line_id": po["lines"][0]["line_id"],
            "received_qty": 40,
        }],
        "idempotency_key": "ab-only-gr",
    }, ADMIN)
    await db.orders.insert_one({
        "id": "order-ab-only-deliver",
        "status": "open",
        "cancelled": False,
        "branch_id": ba,
        "line_items": [{
            "line_id": "ld1",
            "master_item_id": item,
            "quantity": 25,
            "production_process": "non_print",
            "name": "Karton Shared",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 25}],
    })
    await confirm_sales_delivery(
        db,
        order_id="order-ab-only-deliver",
        confirmation_id="ab-only-sd",
        lines=[{"line_idx": 0, "qty": 25, "master_item_id": item}],
        actor_id=ADMIN["id"],
        warehouse_id=wa,
        branch_id=ba,
    )
    assert await _qty(db, item, ba, wa) == 1015
    assert await _qty(db, item, bb, wb) == 500


async def test_ab_demand_without_location_unallocated_and_not_assigned_to_b(db):
    """YGY demand must not appear on SMG opname; order still has no warehouse assign."""
    fx = await _fixture(db)
    await db.orders.insert_one({
        "id": "order-ab-unalloc",
        "status": "open",
        "branch_id": fx["branch_a"],
        "line_items": [{
            "line_id": "l1",
            "master_item_id": fx["item"],
            "quantity": 120,
            "production_process": "non_print",
        }],
    })
    # Branch B (SMG) must not see branch A demand.
    created_b = await create_stock_opname_session(db, {
        "branch_id": fx["branch_b"],
        "warehouse_id": fx["wh_b"],
        "mode": MODE_OPERATIONAL,
    }, ADMIN)
    counting_b = await start_stock_opname_cutoff(db, created_b["id"], ADMIN)
    line_b = _line(counting_b, fx["item"])
    assert line_b["demand_allocation"] == "unallocated"
    assert float(line_b.get("qty_belum_terkirim") or 0) == 0
    assert float(line_b.get("qty_pesanan") or 0) == 0
    # Branch A (YGY) sees its own demand (no warehouse assignment invented).
    created_a = await create_stock_opname_session(db, {
        "branch_id": fx["branch_a"],
        "warehouse_id": fx["wh_a"],
        "mode": MODE_OPERATIONAL,
    }, ADMIN)
    counting_a = await start_stock_opname_cutoff(db, created_a["id"], ADMIN)
    line_a = _line(counting_a, fx["item"])
    assert float(line_a.get("qty_belum_terkirim") or 0) == 120
    order = await db.orders.find_one({"id": "order-ab-unalloc"}, {"_id": 0})
    assert order.get("warehouse_id") in (None, "")
    assert "warehouse_id" not in (order.get("line_items") or [{}])[0]
    assert await _qty(db, fx["item"], fx["branch_b"], fx["wh_b"]) == 500
    assert await _qty(db, fx["item"], fx["branch_a"], fx["wh_a"]) == 1000


async def test_ab_user_for_a_cannot_read_or_mutate_b(db):
    fx = await _fixture(db)
    user_a = {
        "id": "user-a-only",
        "role": "sales",
        "branch_ids": [fx["branch_a"]],
    }
    with pytest.raises(HTTPException) as denied:
        await require_warehouse_access(
            db, user_a, fx["wh_b"], app_env="production", mongo_url="mongodb://prod.example",
        )
    assert denied.value.status_code == 403
    assert CROSS_BRANCH_DENIED in str(denied.value.detail)

    with pytest.raises(HTTPException) as create_denied:
        await create_stock_opname_session(db, {
            "branch_id": fx["branch_b"],
            "warehouse_id": fx["wh_b"],
            "mode": MODE_OPERATIONAL,
        }, user_a)
    assert create_denied.value.status_code in (403, 400)

    b_session = await create_stock_opname_session(db, {
        "branch_id": fx["branch_b"],
        "warehouse_id": fx["wh_b"],
        "mode": MODE_OPERATIONAL,
    }, ADMIN)
    listed = await list_stock_opname_sessions(db, user=user_a)
    ids = {str(row.get("id")) for row in listed}
    assert b_session["id"] not in ids

    with pytest.raises(HTTPException) as get_denied:
        await get_stock_opname_session(db, b_session["id"], user=user_a)
    assert get_denied.value.status_code in (403, 404)
