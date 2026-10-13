"""Physical stock ledger + balance. Isolated test database only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    InsufficientPhysicalStockError,
    PhysicalStockAbortError,
    PhysicalStockConflictError,
    PhysicalStockUnavailableError,
    PhysicalStockValidationError,
    ensure_indexes,
    get_balance,
    post_movement,
    reconcile_balance,
    require_transaction_support,
    reverse_movement,
    set_fail_after_balance,
)

TEST_DB = "eternal_packaging_test_physical_stock"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
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
    yield database
    set_fail_after_balance(False)
    await client.drop_database(TEST_DB)
    client.close()


async def _seed(db):
    item_id = str(uuid.uuid4())
    branch_a = str(uuid.uuid4())
    branch_b = str(uuid.uuid4())
    branch_off = str(uuid.uuid4())
    wh_a1 = str(uuid.uuid4())
    wh_a2 = str(uuid.uuid4())
    wh_b1 = str(uuid.uuid4())
    wh_off = str(uuid.uuid4())
    await db[MASTER_ITEMS_COLLECTION].insert_one({"id": item_id, "item_name": "Fixture Item"})
    await db[BRANCHES_COLLECTION].insert_many([
        {"id": branch_a, "code": "AAA", "name": "Branch A", "active": True},
        {"id": branch_b, "code": "BBB", "name": "Branch B", "active": True},
        {"id": branch_off, "code": "OFF", "name": "Inactive", "active": False},
    ])
    await db[WAREHOUSES_COLLECTION].insert_many([
        {"id": wh_a1, "branch_id": branch_a, "code": "A1", "name": "Warehouse A1", "active": True},
        {"id": wh_a2, "branch_id": branch_a, "code": "A2", "name": "Warehouse A2", "active": True},
        {"id": wh_b1, "branch_id": branch_b, "code": "B1", "name": "Warehouse B1", "active": True},
        {"id": wh_off, "branch_id": branch_a, "code": "OFF", "name": "Inactive WH", "active": False},
    ])
    return {
        "item": item_id,
        "branch_a": branch_a,
        "branch_b": branch_b,
        "branch_off": branch_off,
        "wh_a1": wh_a1,
        "wh_a2": wh_a2,
        "wh_b1": wh_b1,
        "wh_off": wh_off,
    }


def _move(fx, **overrides):
    payload = {
        "master_item_id": fx["item"],
        "branch_id": fx["branch_a"],
        "warehouse_id": fx["wh_a1"],
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 100,
        "reference_type": "test",
        "reference_id": "ref",
        "reason": "fixture",
        "idempotency_key": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return payload


async def _qty(db, fx, warehouse_id=None, branch_id=None):
    doc = await get_balance(
        db,
        fx["item"],
        branch_id or fx["branch_a"],
        warehouse_id or fx["wh_a1"],
    )
    return int(doc["physical_qty"]) if doc else 0


@pytest.mark.anyio
async def test_transaction_preflight_fails_without_replica_set():
    class Admin:
        async def command(self, *args, **kwargs):
            return {"isWritablePrimary": True}

    class Client:
        admin = Admin()

    class DB:
        client = Client()

        def __getitem__(self, name):
            raise AssertionError(f"collection {name} must not be touched")

    with pytest.raises(PhysicalStockUnavailableError):
        await post_movement(
            DB(),
            {
                "master_item_id": "x",
                "branch_id": "b",
                "warehouse_id": "w",
                "movement_type": MOVEMENT_OPENING_BALANCE,
                "qty_delta": 1,
                "idempotency_key": "no-txn",
            },
            actor_id="tester",
        )


@pytest.mark.anyio
async def test_transaction_preflight_passes_on_local_replica(db):
    await require_transaction_support(db.client)


@pytest.mark.anyio
async def test_opening_receipt_and_delivery(db):
    fx = await _seed(db)
    await post_movement(db, _move(fx, qty_delta=100), actor_id="tester")
    assert await _qty(db, fx) == 100
    await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=50),
        actor_id="tester",
    )
    assert await _qty(db, fx) == 150
    await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_SALES_DELIVERY, qty_delta=-20),
        actor_id="tester",
    )
    assert await _qty(db, fx) == 130


@pytest.mark.anyio
async def test_outbound_above_physical_writes_negative_book(db):
    fx = await _seed(db)
    await post_movement(db, _move(fx, qty_delta=100), actor_id="tester")
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    mov = await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_SALES_DELIVERY, qty_delta=-101),
        actor_id="tester",
    )
    assert mov["balance_before"] == 100
    assert mov["balance_after"] == -1
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger + 1
    assert await _qty(db, fx) == -1


@pytest.mark.anyio
async def test_idempotency_and_payload_conflict(db):
    fx = await _seed(db)
    key = "same-key"
    first = await post_movement(db, _move(fx, qty_delta=100, idempotency_key=key), actor_id="tester")
    second = await post_movement(db, _move(fx, qty_delta=100, idempotency_key=key), actor_id="tester")
    assert first["id"] == second["id"]
    assert await _qty(db, fx) == 100
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": key}) == 1
    with pytest.raises(PhysicalStockConflictError):
        await post_movement(db, _move(fx, qty_delta=40, idempotency_key=key), actor_id="tester")
    assert await _qty(db, fx) == 100


@pytest.mark.anyio
async def test_locations_stay_separate_and_invalid_locations_rejected(db):
    fx = await _seed(db)
    await post_movement(db, _move(fx, qty_delta=10, warehouse_id=fx["wh_a1"]), actor_id="tester")
    await post_movement(db, _move(fx, qty_delta=4, warehouse_id=fx["wh_a2"]), actor_id="tester")
    await post_movement(
        db,
        _move(fx, qty_delta=7, branch_id=fx["branch_b"], warehouse_id=fx["wh_b1"]),
        actor_id="tester",
    )
    assert await _qty(db, fx, fx["wh_a1"]) == 10
    assert await _qty(db, fx, fx["wh_a2"]) == 4
    assert await _qty(db, fx, fx["wh_b1"], fx["branch_b"]) == 7
    with pytest.raises(PhysicalStockValidationError):
        await post_movement(
            db,
            _move(fx, branch_id=fx["branch_a"], warehouse_id=fx["wh_b1"]),
            actor_id="tester",
        )
    with pytest.raises(PhysicalStockValidationError):
        await post_movement(db, _move(fx, warehouse_id=fx["wh_off"]), actor_id="tester")
    with pytest.raises(PhysicalStockValidationError):
        await post_movement(
            db,
            _move(fx, branch_id=fx["branch_off"], warehouse_id=fx["wh_a1"]),
            actor_id="tester",
        )


@pytest.mark.anyio
async def test_concurrent_outbound_both_may_succeed_negative_book(db):
    fx = await _seed(db)
    await post_movement(db, _move(fx, qty_delta=100), actor_id="tester")

    async def outbound(qty):
        return await post_movement(
            db,
            _move(fx, movement_type=MOVEMENT_SALES_DELIVERY, qty_delta=qty),
            actor_id="tester",
        )

    results = await asyncio.gather(outbound(-80), outbound(-50), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    # Shortage no longer serializes to a single winner — both postings may commit.
    assert len(successes) >= 1
    assert len(successes) + len(failures) == 2
    final_qty = await _qty(db, fx)
    assert final_qty == 100 + sum(int(s["qty_delta"]) for s in successes)
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == len(successes)


@pytest.mark.anyio
async def test_concurrent_same_idempotency_key_posts_once(db):
    fx = await _seed(db)
    key = "concurrent-key"

    async def once():
        return await post_movement(
            db,
            _move(fx, qty_delta=30, idempotency_key=key),
            actor_id="tester",
        )

    first, second = await asyncio.gather(once(), once())
    assert first["id"] == second["id"]
    assert await _qty(db, fx) == 30
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": key}) == 1


@pytest.mark.anyio
async def test_reversal_keeps_original_and_rejects_a_second_reversal(db):
    fx = await _seed(db)
    opening = await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=100),
        actor_id="tester",
    )
    receipt = await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=50),
        actor_id="tester",
    )
    reversal = await reverse_movement(
        db,
        receipt["id"],
        actor_id="tester",
        idempotency_key="rev-1",
        reason="undo receipt",
    )
    assert reversal["movement_type"] == "REVERSAL"
    assert reversal["qty_delta"] == -50
    assert reversal["reference_id"] == receipt["id"]
    assert await _qty(db, fx) == 100
    stored_receipt = await db[LEDGER_COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0})
    assert stored_receipt["qty_delta"] == 50
    assert stored_receipt["id"] == receipt["id"]
    assert opening["id"] != reversal["id"]
    with pytest.raises(PhysicalStockConflictError):
        await reverse_movement(
            db,
            receipt["id"],
            actor_id="tester",
            idempotency_key="rev-2",
        )
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": "REVERSAL",
        "reference_id": receipt["id"],
    }) == 1
    assert await _qty(db, fx) == 100


@pytest.mark.anyio
async def test_reconciliation_detects_altered_balance(db):
    fx = await _seed(db)
    await post_movement(db, _move(fx, qty_delta=100), actor_id="tester")
    await post_movement(
        db,
        _move(fx, movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=50),
        actor_id="tester",
    )
    matched = await reconcile_balance(db, fx["item"], fx["branch_a"], fx["wh_a1"])
    assert matched["match"] is True
    assert matched["ledger_qty"] == 150
    assert matched["balance_qty"] == 150
    await db[BALANCE_COLLECTION].update_one(
        {
            "master_item_id": fx["item"],
            "branch_id": fx["branch_a"],
            "warehouse_id": fx["wh_a1"],
        },
        {"$set": {"physical_qty": 1}},
    )
    broken = await reconcile_balance(db, fx["item"], fx["branch_a"], fx["wh_a1"])
    assert broken["match"] is False
    assert broken["ledger_qty"] == 150
    assert broken["balance_qty"] == 1
    assert broken["discrepancy"] == -149


@pytest.mark.anyio
async def test_forced_abort_leaves_no_partial_write(db):
    fx = await _seed(db)
    set_fail_after_balance(True)
    try:
        with pytest.raises(PhysicalStockAbortError):
            await post_movement(db, _move(fx, qty_delta=100), actor_id="tester")
    finally:
        set_fail_after_balance(False)
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
