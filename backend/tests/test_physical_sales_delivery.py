"""Confirmed sales delivery posts one R2 SALES_DELIVERY per line."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_physical_sales_delivery"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_SALES_DELIVERY,
    SALES_DELIVERY_CONFIRMATIONS,
    WAREHOUSES_COLLECTION,
    InsufficientPhysicalStockError,
    PhysicalStockAbortError,
    PhysicalStockConflictError,
    PhysicalStockValidationError,
    confirm_sales_delivery,
    ensure_indexes,
    list_physical_mutations,
    list_physical_stock_catalog,
    post_opening_balance,
    set_fail_after_balance,
    set_fail_sales_delivery_persist,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_physical_sales_delivery"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
LEGACY = ("product_stock_state", "stock_movements", "item_stock_manual")


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
    set_fail_after_balance(False)
    set_fail_sales_delivery_persist(False)
    try:
        yield database
    finally:
        set_fail_after_balance(False)
        set_fail_sales_delivery_persist(False)
        await client.drop_database(TEST_DB)
        client.close()


async def _place(db, *, physical, ordered, completed, code="BOX", branch_code="YGY", warehouse_code="MAIN"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": branch_code,
        "name": "Yogyakarta" if branch_code == "YGY" else branch_code,
        "active": True,
    }
    warehouse = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": warehouse_code,
        "name": warehouse_code,
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    if physical is not None:
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
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": ordered,
            "production_process": "print",
            "name": code,
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }
    await db.orders.insert_one(dict(order))
    return item, branch, warehouse, order


async def _buckets(db, code):
    rows = await list_physical_stock_catalog(db)
    return {row["item_code"]: row for row in rows["items"]}[code]


async def _deliver(db, order, qty, *, warehouse=None, branch=None, confirmation_id=None, line_idx=0, master_item_id=None):
    return await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=confirmation_id or f"conf-{uuid.uuid4().hex[:8]}",
        lines=[{
            "line_idx": line_idx,
            "qty": qty,
            "master_item_id": master_item_id,
        }],
        actor_id="tester",
        warehouse_id=None if warehouse is None else warehouse["id"],
        branch_id=None if branch is None else branch["id"],
    )


async def _legacy_counts(db):
    return {name: await db[name].count_documents({}) for name in LEGACY}


async def test_full_delivery_keeps_available_stock(db):
    item, branch, warehouse, order = await _place(db, physical=1000, ordered=300, completed=200, code="FULL")
    before = await _buckets(db, "FULL")
    assert (before["physical_qty"], before["qty_pesanan"], before["qty_belum_terkirim"], before["qty_tersedia"]) == (1000, 100, 200, 700)
    legacy = await _legacy_counts(db)
    result = await _deliver(db, order, 200, warehouse=warehouse, branch=branch, confirmation_id="full-1")
    assert result["replayed"] is False
    assert len(result["movements"]) == 1
    movement = result["movements"][0]
    assert movement["movement_type"] == MOVEMENT_SALES_DELIVERY
    assert movement["qty_delta"] == -200
    assert movement["reference_type"] == "sales_delivery_confirmation"
    assert movement["reference_id"] == "full-1"
    assert movement["metadata"]["order_id"] == order["id"]
    assert movement["metadata"]["line_id"] == "ln-0"
    after = await _buckets(db, "FULL")
    assert (after["physical_qty"], after["qty_pesanan"], after["qty_belum_terkirim"], after["qty_tersedia"]) == (800, 100, 0, 700)
    assert await _legacy_counts(db) == legacy
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY, "master_item_id": item["id"]}) == 1


async def test_partial_deliveries_do_not_double_count(db):
    _item, _branch, warehouse, order = await _place(db, physical=1000, ordered=300, completed=300, code="PART")
    first = await _deliver(db, order, 120, warehouse=warehouse, confirmation_id="part-1")
    assert first["movements"][0]["qty_delta"] == -120
    mid = await _buckets(db, "PART")
    assert (mid["physical_qty"], mid["qty_pesanan"], mid["qty_belum_terkirim"], mid["qty_tersedia"]) == (880, 0, 180, 700)
    second = await _deliver(db, order, 100, warehouse=warehouse, confirmation_id="part-2")
    assert second["movements"][0]["qty_delta"] == -100
    assert second["movements"][0]["balance_before"] == 880
    assert second["movements"][0]["balance_after"] == 780
    end = await _buckets(db, "PART")
    assert (end["physical_qty"], end["qty_pesanan"], end["qty_belum_terkirim"], end["qty_tersedia"]) == (780, 0, 80, 700)


async def test_delivery_over_ready_is_rejected(db):
    item, _branch, warehouse, order = await _place(db, physical=1000, ordered=100, completed=100, code="READY")
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    before_balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    with pytest.raises(PhysicalStockValidationError):
        await _deliver(db, order, 150, warehouse=warehouse, confirmation_id="over-ready")
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db[SALES_DELIVERY_CONFIRMATIONS].count_documents({}) == 0
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    assert balance["physical_qty"] == before_balance["physical_qty"]
    row = await _buckets(db, "READY")
    assert row["qty_belum_terkirim"] == 100


async def test_insufficient_physical_posts_negative_book_with_warning(db):
    item, _branch, warehouse, order = await _place(db, physical=50, ordered=100, completed=100, code="SHORT")
    result = await _deliver(db, order, 80, warehouse=warehouse, confirmation_id="short-phys")
    assert result["movements"][0]["balance_before"] == 50
    assert result["movements"][0]["balance_after"] == -30
    warn = result["stock_warnings"][0]
    assert warn["stok_tercatat"] == 50
    assert warn["kebutuhan"] == 80
    assert warn["kekurangan"] == 30
    assert warn["blocks_delivery"] is False
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    assert balance["physical_qty"] == -30
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 1
    row = await _buckets(db, "SHORT")
    assert row["qty_belum_terkirim"] == 20
    assert row["physical_qty"] == -30


async def test_exact_physical_depletion(db):
    _item, _branch, warehouse, order = await _place(db, physical=100, ordered=100, completed=100, code="ZERO")
    result = await _deliver(db, order, 100, warehouse=warehouse, confirmation_id="exact")
    assert result["movements"][0]["balance_after"] == 0
    row = await _buckets(db, "ZERO")
    assert (row["physical_qty"], row["qty_belum_terkirim"], row["qty_tersedia"]) == (0, 0, 0)


async def test_idempotent_retry_and_conflict(db):
    item, _branch, warehouse, order = await _place(db, physical=500, ordered=500, completed=500, code="IDEM")
    first = await _deliver(db, order, 120, warehouse=warehouse, confirmation_id="same")
    second = await _deliver(db, order, 120, warehouse=warehouse, confirmation_id="same")
    assert second["replayed"] is True
    assert second["movements"][0]["id"] == first["movements"][0]["id"]
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY, "master_item_id": item["id"]}) == 1
    row = await _buckets(db, "IDEM")
    assert (row["physical_qty"], row["qty_belum_terkirim"]) == (380, 380)
    with pytest.raises(PhysicalStockConflictError):
        await _deliver(db, order, 90, warehouse=warehouse, confirmation_id="same")
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    assert balance["physical_qty"] == 380


async def test_movement_failure_rolls_back_confirmation(db):
    item, _branch, warehouse, order = await _place(db, physical=400, ordered=200, completed=200, code="ABORT")
    set_fail_after_balance(True)
    with pytest.raises(PhysicalStockAbortError):
        await _deliver(db, order, 50, warehouse=warehouse, confirmation_id="abort-move")
    set_fail_after_balance(False)
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    assert balance["physical_qty"] == 400
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 0
    assert await db[SALES_DELIVERY_CONFIRMATIONS].count_documents({}) == 0
    stored = await db.orders.find_one({"id": order["id"]}, {"_id": 0, "r2_confirmed_delivery_ids": 1})
    assert "abort-move" not in (stored.get("r2_confirmed_delivery_ids") or [])


async def test_delivery_persist_failure_rolls_back_stock(db):
    item, _branch, warehouse, order = await _place(db, physical=400, ordered=200, completed=200, code="PERSIST")
    set_fail_sales_delivery_persist(True)
    with pytest.raises(PhysicalStockAbortError):
        await _deliver(db, order, 40, warehouse=warehouse, confirmation_id="abort-save")
    set_fail_sales_delivery_persist(False)
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0, "physical_qty": 1})
    assert balance["physical_qty"] == 400
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 0
    assert await db[SALES_DELIVERY_CONFIRMATIONS].count_documents({}) == 0
    row = await _buckets(db, "PERSIST")
    assert row["qty_belum_terkirim"] == 200


async def test_multi_line_is_atomic(db):
    item_a = {"id": "item-a", "item_code": "AA", "item_name": "A", "unit": "PCS", "is_active": True}
    item_b = {"id": "item-b", "item_code": "BB", "item_name": "B", "unit": "PCS", "is_active": True}
    branch = {"id": "br-ml", "code": "YGY", "name": "Yogyakarta", "active": True}
    warehouse = {"id": "wh-ml", "branch_id": "br-ml", "code": "MAIN", "name": "MAIN", "active": True}
    await db[MASTER_ITEMS_COLLECTION].insert_many([item_a, item_b])
    await db[BRANCHES_COLLECTION].insert_one(branch)
    await db[WAREHOUSES_COLLECTION].insert_one(warehouse)
    for item, qty in ((item_a, 500), (item_b, 30)):
        await post_opening_balance(
            db, item["id"],
            {
                "branch_id": branch["id"],
                "warehouse_id": warehouse["id"],
                "physical_qty": qty,
                "reason": "Saldo awal",
                "idempotency_key": f"open-{item['id']}",
            },
            actor_id="tester",
        )
    order = {
        "id": "so-ml",
        "cancelled": False,
        "line_items": [
            {"line_id": "a", "master_item_id": item_a["id"], "quantity": 100, "production_process": "print"},
            {"line_id": "b", "master_item_id": item_b["id"], "quantity": 100, "production_process": "print"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
    }
    await db.orders.insert_one(order)
    # Line B only has 30 physical — still posts; book may go negative. Atomic for both lines.
    result = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id="multi-neg",
        lines=[{"line_idx": 0, "qty": 100}, {"line_idx": 1, "qty": 50}],
        actor_id="tester",
        warehouse_id=warehouse["id"],
    )
    assert len(result["movements"]) == 2
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 2
    assert await db[SALES_DELIVERY_CONFIRMATIONS].count_documents({}) == 1
    bal_a = await db[BALANCE_COLLECTION].find_one({"master_item_id": item_a["id"]}, {"_id": 0, "physical_qty": 1})
    bal_b = await db[BALANCE_COLLECTION].find_one({"master_item_id": item_b["id"]}, {"_id": 0, "physical_qty": 1})
    assert bal_a["physical_qty"] == 400
    assert bal_b["physical_qty"] == -20


async def test_warehouse_isolation_and_explicit_choice(db):
    item = {"id": "item-wh", "item_code": "WH", "item_name": "WH", "unit": "PCS", "is_active": True}
    branch = {"id": "br-wh", "code": "YGY", "name": "Yogyakarta", "active": True}
    wh_a = {"id": "wh-a", "branch_id": branch["id"], "code": "A", "name": "A", "active": True}
    wh_b = {"id": "wh-b", "branch_id": branch["id"], "code": "B", "name": "B", "active": True}
    await db[MASTER_ITEMS_COLLECTION].insert_one(item)
    await db[BRANCHES_COLLECTION].insert_one(branch)
    await db[WAREHOUSES_COLLECTION].insert_many([wh_a, wh_b])
    await post_opening_balance(db, item["id"], {
        "branch_id": branch["id"], "warehouse_id": wh_a["id"], "physical_qty": 700,
        "reason": "Saldo awal", "idempotency_key": "open-a",
    }, actor_id="tester")
    await post_opening_balance(db, item["id"], {
        "branch_id": branch["id"], "warehouse_id": wh_b["id"], "physical_qty": 300,
        "reason": "Saldo awal", "idempotency_key": "open-b",
    }, actor_id="tester")
    order = {
        "id": "so-wh",
        "cancelled": False,
        "line_items": [{"line_id": "ln", "master_item_id": item["id"], "quantity": 200, "production_process": "print"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 200}],
    }
    await db.orders.insert_one(order)
    with pytest.raises(PhysicalStockValidationError):
        await confirm_sales_delivery(
            db, order_id=order["id"], confirmation_id="no-wh",
            lines=[{"line_idx": 0, "qty": 100}], actor_id="tester",
        )
    await confirm_sales_delivery(
        db, order_id=order["id"], confirmation_id="from-a",
        lines=[{"line_idx": 0, "qty": 100}], actor_id="tester", warehouse_id=wh_a["id"],
    )
    bal_a = await db[BALANCE_COLLECTION].find_one({"warehouse_id": wh_a["id"]}, {"_id": 0, "physical_qty": 1})
    bal_b = await db[BALANCE_COLLECTION].find_one({"warehouse_id": wh_b["id"]}, {"_id": 0, "physical_qty": 1})
    assert bal_a["physical_qty"] == 600
    assert bal_b["physical_qty"] == 300
    row = await _buckets(db, "WH")
    assert row["physical_qty"] == 900
    assert row["qty_belum_terkirim"] == 100
    assert row["qty_tersedia"] == 800


async def test_mutation_history_shows_sales_delivery(db):
    item, branch, warehouse, order = await _place(db, physical=100, ordered=40, completed=40, code="HIST")
    await _deliver(db, order, 15, warehouse=warehouse, branch=branch, confirmation_id="hist-1")
    history = await list_physical_mutations(db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"])
    types = [row["movement_type"] for row in history["items"]]
    assert MOVEMENT_SALES_DELIVERY in types
    delivery = next(row for row in history["items"] if row["movement_type"] == MOVEMENT_SALES_DELIVERY)
    assert delivery["qty_delta"] == -15
    assert delivery["balance_after"] == 85
    assert delivery["reference_id"] == "hist-1"


async def test_does_not_borrow_ready_qty_from_another_order(db):
    item, _branch, warehouse, order = await _place(db, physical=1000, ordered=100, completed=50, code="OWN")
    other = {
        "id": "so-other",
        "cancelled": False,
        "line_items": [{"line_id": "x", "master_item_id": item["id"], "quantity": 80, "production_process": "print"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 80}],
    }
    await db.orders.insert_one(other)
    with pytest.raises(PhysicalStockValidationError):
        await _deliver(db, order, 60, warehouse=warehouse, confirmation_id="borrow")
    row = await _buckets(db, "OWN")
    assert row["qty_belum_terkirim"] == 130
    assert row["qty_pesanan"] == 50


async def test_unresolved_master_item_stops_confirmation(db):
    _item, _branch, warehouse, order = await _place(db, physical=100, ordered=10, completed=10, code="MISS")
    await db.orders.update_one({"id": order["id"]}, {"$unset": {"line_items.0.master_item_id": ""}})
    with pytest.raises(PhysicalStockValidationError):
        await _deliver(db, order, 5, warehouse=warehouse, confirmation_id="missing-master")
    assert await db[SALES_DELIVERY_CONFIRMATIONS].count_documents({}) == 0


async def test_sales_shortage_stays_allowed(db):
    await _place(db, physical=0, ordered=500, completed=0, code="NEG")
    row = await _buckets(db, "NEG")
    assert (row["physical_qty"], row["qty_pesanan"], row["qty_belum_terkirim"], row["qty_tersedia"]) == (0, 500, 0, -500)
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 0
