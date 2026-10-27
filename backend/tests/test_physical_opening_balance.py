"""R2 opening balance. Isolated test database only."""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_physical_opening"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    WAREHOUSES_COLLECTION,
    PhysicalStockAbortError,
    PhysicalStockConflictError,
    PhysicalStockValidationError,
    ensure_indexes,
    list_physical_mutations,
    post_movement,
    post_opening_balance,
    read_opening_balance,
    read_physical_stock,
    set_fail_after_balance,
)
import server  # noqa: E402
from server import app, current_user  # noqa: E402

import httpx  # noqa: E402

TEST_DB = "eternal_packaging_test_physical_opening"
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
    server.db = database
    set_fail_after_balance(False)
    # HTTP opening/adjustment routes require R2 authority (or warehouse standalone).
    from services.r2_go_live import set_r2_authority_active

    await set_r2_authority_active(database, active=True)
    try:
        yield database
    finally:
        set_fail_after_balance(False)
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db, *, branch_active=True, warehouse_active=True, branch_code="YGY", warehouse_code="MAIN"):
    item = {"id": f"item-{uuid.uuid4().hex[:8]}", "name": "Box", "unit": "PCS"}
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": branch_code,
        "name": branch_code,
        "active": branch_active,
    }
    warehouse = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": warehouse_code,
        "name": warehouse_code,
        "active": warehouse_active,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    return item, branch, warehouse


def _body(branch, warehouse, qty=100, key=None, reason="Cutover"):
    return {
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": qty,
        "reason": reason,
        "idempotency_key": key or f"open-{uuid.uuid4().hex}",
    }


async def test_opening_100_writes_one_ledger_and_balance(db):
    item, branch, warehouse = await _seed(db)
    await db.product_stock_state.insert_one({"id": "legacy-state", "stock": 1})
    await db.stock_movements.insert_one({"id": "legacy-move", "qty": 1})
    result = await post_opening_balance(db, item["id"], _body(branch, warehouse, 100), actor_id="actor-1")
    movement = result["movement"]
    assert movement["movement_type"] == MOVEMENT_OPENING_BALANCE
    assert movement["qty_delta"] == 100
    assert movement["balance_before"] == 0
    assert movement["balance_after"] == 100
    assert movement["reference_type"] == MOVEMENT_OPENING_BALANCE
    assert movement["actor_id"] == "actor-1"
    assert movement["reason"] == "Cutover"
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 1
    balance = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "branch_id": branch["id"], "warehouse_id": warehouse["id"]},
        {"_id": 0},
    )
    assert balance["physical_qty"] == 100
    assert await db.product_stock_state.count_documents({}) == 1
    assert await db.stock_movements.count_documents({}) == 1
    assert (await db.product_stock_state.find_one({"id": "legacy-state"}))["stock"] == 1


async def test_zero_opening_is_established(db):
    item, branch, warehouse = await _seed(db)
    result = await post_opening_balance(db, item["id"], _body(branch, warehouse, 0), actor_id="actor-1")
    assert result["movement"]["balance_before"] == 0
    assert result["movement"]["balance_after"] == 0
    status = await read_opening_balance(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert status["established"] is True
    assert status["opening_qty"] == 0
    assert status["opening_movement_id"] == result["movement"]["id"]


async def test_invalid_masters_and_inactive_rejected(db):
    item, branch, warehouse = await _seed(db)
    other, _other_branch, other_warehouse = await _seed(db, branch_code="SMG", warehouse_code="ALT")
    inactive_item, inactive_branch, _inactive_wh = await _seed(db, branch_active=False, branch_code="OFF")
    _item2, active_branch, inactive_warehouse = await _seed(
        db, warehouse_active=False, branch_code="YGY2", warehouse_code="OFF",
    )
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(db, "missing", _body(branch, warehouse), actor_id="actor-1")
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(
            db, item["id"], _body(branch, warehouse) | {"branch_id": "missing"}, actor_id="actor-1",
        )
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(
            db, item["id"], _body(branch, warehouse) | {"warehouse_id": "missing"}, actor_id="actor-1",
        )
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(
            db, item["id"], _body(other, warehouse), actor_id="actor-1",
        )
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(
            db, inactive_item["id"], _body(inactive_branch, warehouse), actor_id="actor-1",
        )
    with pytest.raises(PhysicalStockValidationError):
        await post_opening_balance(
            db, item["id"], _body(active_branch, inactive_warehouse), actor_id="actor-1",
        )
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert other_warehouse["id"]


async def test_second_opening_rejected_and_idempotency(db):
    item, branch, warehouse = await _seed(db)
    key = "same-opening"
    first = await post_opening_balance(db, item["id"], _body(branch, warehouse, 100, key=key), actor_id="actor-1")
    second = await post_opening_balance(db, item["id"], _body(branch, warehouse, 100, key=key), actor_id="actor-1")
    assert first["movement"]["id"] == second["movement"]["id"]
    assert await db[LEDGER_COLLECTION].count_documents({}) == 1
    balance = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]})
    assert balance["physical_qty"] == 100
    with pytest.raises(PhysicalStockConflictError):
        await post_opening_balance(
            db, item["id"], _body(branch, warehouse, 40, key=key), actor_id="actor-1",
        )
    assert balance["physical_qty"] == 100
    with pytest.raises(PhysicalStockConflictError):
        await post_opening_balance(db, item["id"], _body(branch, warehouse, 100), actor_id="actor-1")
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 1
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 100


async def test_concurrent_opening_creates_one_movement(db):
    item, branch, warehouse = await _seed(db)
    start = asyncio.Event()

    async def once(index):
        await start.wait()
        return await post_opening_balance(
            db, item["id"], _body(branch, warehouse, 100, key=f"race-{index}"), actor_id="actor-1",
        )

    tasks = [asyncio.create_task(once(i)) for i in range(2)]
    await asyncio.sleep(0.05)
    start.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = [row for row in results if not isinstance(row, Exception)]
    conflicts = [row for row in results if isinstance(row, PhysicalStockConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 1
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 100


async def test_existing_non_opening_history_rejects_opening(db):
    item, branch, warehouse = await _seed(db)
    await post_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 40,
            "reference_type": "TEST",
            "reference_id": "po-1",
            "reason": "receipt",
            "idempotency_key": "receipt-1",
        },
        actor_id="actor-1",
    )
    with pytest.raises(PhysicalStockConflictError):
        await post_opening_balance(db, item["id"], _body(branch, warehouse, 100), actor_id="actor-1")
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 0
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 40


async def test_read_status_writes_nothing_and_reads_integrate(db):
    item, branch, warehouse = await _seed(db)
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    empty = await read_opening_balance(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert empty["established"] is False
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    created = await post_opening_balance(db, item["id"], _body(branch, warehouse, 100), actor_id="actor-9")
    after_ledger = await db[LEDGER_COLLECTION].count_documents({})
    after_balance = await db[BALANCE_COLLECTION].count_documents({})
    status = await read_opening_balance(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert status["established"] is True
    assert status["opening_movement_id"] == created["movement"]["id"]
    assert status["opening_qty"] == 100
    assert status["actor_id"] == "actor-9"
    assert status["occurred_at"]
    assert await db[LEDGER_COLLECTION].count_documents({}) == after_ledger
    assert await db[BALANCE_COLLECTION].count_documents({}) == after_balance
    stock = await read_physical_stock(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert stock["items"][0]["physical_qty"] == 100
    mutations = await list_physical_mutations(db, item["id"])
    assert mutations["total"] == 1
    assert mutations["items"][0]["movement_type"] == MOVEMENT_OPENING_BALANCE
    assert mutations["items"][0]["balance_before"] == 0
    assert mutations["items"][0]["balance_after"] == 100
    source = inspect.getsource(post_opening_balance) + inspect.getsource(read_opening_balance)
    for name in ("product_stock_state", "stock_movements", "item_stock_manual", "initialize_opening_balance"):
        assert name not in source


async def test_forced_abort_leaves_no_partial_write(db):
    item, branch, warehouse = await _seed(db)
    set_fail_after_balance(True)
    with pytest.raises(PhysicalStockAbortError):
        await post_opening_balance(db, item["id"], _body(branch, warehouse, 100), actor_id="actor-1")
    set_fail_after_balance(False)
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


def _admin():
    return {
        "id": "admin-open",
        "email": "admin@example.com",
        "name": "Admin",
        "role": "admin",
        "status": "active",
        "user_id": "admin-open",
        "permissions_override": {},
    }


async def test_http_opening_and_status(db):
    item, branch, warehouse = await _seed(db)
    app.dependency_overrides[current_user] = lambda: _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            f"/api/master-items/{item['id']}/physical-opening-balance",
            json=_body(branch, warehouse, 25, key="http-open"),
        )
        again = await client.post(
            f"/api/master-items/{item['id']}/physical-opening-balance",
            json=_body(branch, warehouse, 25, key="http-open"),
        )
        status = await client.get(
            f"/api/master-items/{item['id']}/physical-opening-balance",
            params={"branch_id": branch["id"], "warehouse_id": warehouse["id"]},
        )
        stock = await client.get(
            f"/api/master-items/{item['id']}/physical-stock",
            params={"branch_id": branch["id"], "warehouse_id": warehouse["id"]},
        )
    app.dependency_overrides.clear()
    assert created.status_code == 200
    assert again.status_code == 200
    assert created.json()["movement"]["id"] == again.json()["movement"]["id"]
    assert status.status_code == 200
    assert status.json()["established"] is True
    assert status.json()["opening_qty"] == 25
    assert stock.json()["items"][0]["physical_qty"] == 25
    assert await db[LEDGER_COLLECTION].count_documents({}) == 1
