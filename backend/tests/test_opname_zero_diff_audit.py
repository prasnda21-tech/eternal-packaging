"""Opname zero-difference reconciliation audit — isolated replica-set DB only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = "eternal_packaging_test_opname_zero_diff_audit"
assert "eternal_packaging_preview" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
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
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    OPNAME_SESSIONS,
    WAREHOUSES_COLLECTION,
    PhysicalStockAbortError,
    _client,
    apply_stock_opname_adjustments,
    ensure_indexes,
    set_fail_after_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

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
    set_fail_after_balance(False)
    try:
        yield database
    finally:
        set_fail_after_balance(False)
        await client.drop_database(TEST_DB)
        client.close()


async def _masters(db):
    item = {"id": f"item-{uuid.uuid4().hex[:8]}", "item_code": "Z0", "item_name": "Zero", "unit": "PCS"}
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "YGY", "active": True}
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
    return item, branch, warehouse


async def _session(db, branch, warehouse, *, session_id=None, mode="operational"):
    """Persist an active REVIEW session so STOCK_OPNAME_ADJUSTMENT guard passes."""
    doc = {
        "id": session_id or f"opn-{uuid.uuid4().hex[:8]}",
        "opname_number": "OP-ZERO-1",
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "mode": mode,
        "status": "REVIEW",
    }
    await db[OPNAME_SESSIONS].insert_one(dict(doc))
    return doc


def _line(item, *, line_id="L1"):
    return {
        "line_id": line_id,
        "master_item_id": item["id"],
        "book_qty": 0,
        "book_qty_at_posting": 0,
        "physical_count_qty": 0,
        "difference_qty": 0,
        "target_qty": 0,
    }


async def test_unreconciled_zero_diff_writes_audit_and_reconciles(db):
    item, branch, warehouse = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    session_doc = await _session(db, branch, warehouse)
    lines = [_line(item)]
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            moved = await apply_stock_opname_adjustments(
                db, session, session_doc, lines, actor_id="op-user", occurred_at="2026-10-13T12:00:00+00:00",
            )
    assert len(moved) == 1
    assert moved[0]["qty_delta"] == 0
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_RECONCILED
    row = await db[LEDGER_COLLECTION].find_one({"id": moved[0]["id"]}, {"_id": 0})
    assert row["movement_type"] == MOVEMENT_STOCK_OPNAME_ADJUSTMENT
    assert row["qty_delta"] == 0
    assert row["balance_before"] == 0
    assert row["balance_after"] == 0
    assert row["actor_id"] == "op-user"
    assert row["occurred_at"] == "2026-10-13T12:00:00+00:00"
    assert row["reference_type"] == "stock_opname_session"
    assert row["reference_id"] == session_doc["id"]
    assert row["metadata"]["reconcile_only"] is True
    assert row["metadata"]["stock_opname_session_id"] == session_doc["id"]
    assert row["metadata"]["stock_opname_line_id"] == "L1"
    assert row["metadata"]["physical_count_qty"] == 0
    assert row["metadata"]["difference_qty"] == 0


async def test_already_reconciled_zero_diff_no_movement(db):
    item, branch, warehouse = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_RECONCILED,
        "version": 1,
    })
    session_doc = await _session(db, branch, warehouse)
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            moved = await apply_stock_opname_adjustments(
                db, session, session_doc, [_line(item)], actor_id="op-user",
                occurred_at="2026-10-13T12:00:00+00:00",
            )
    assert moved == []
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0


async def test_missing_balance_zero_diff_does_not_invent_row(db):
    item, branch, warehouse = await _masters(db)
    session_doc = await _session(db, branch, warehouse)
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            moved = await apply_stock_opname_adjustments(
                db, session, session_doc, [_line(item)], actor_id="op-user",
                occurred_at="2026-10-13T12:00:00+00:00",
            )
    assert moved == []
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0


async def test_opname_zero_diff_idempotent_replay(db):
    item, branch, warehouse = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    session_doc = await _session(db, branch, warehouse)
    lines = [_line(item)]
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            first = await apply_stock_opname_adjustments(
                db, session, session_doc, lines, actor_id="op-user",
                occurred_at="2026-10-13T12:00:00+00:00",
            )
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            second = await apply_stock_opname_adjustments(
                db, session, session_doc, lines, actor_id="op-user",
                occurred_at="2026-10-13T12:00:01+00:00",
            )
    assert first[0]["id"] == second[0]["id"]
    assert await db[LEDGER_COLLECTION].count_documents({
        "idempotency_key": f"stock-opname:{session_doc['id']}:L1",
    }) == 1


async def test_opname_zero_diff_forced_abort_rolls_back(db):
    item, branch, warehouse = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    session_doc = await _session(db, branch, warehouse)
    set_fail_after_balance(True)
    with pytest.raises(PhysicalStockAbortError):
        async with await _client(db).start_session() as session:
            async with session.start_transaction():
                await apply_stock_opname_adjustments(
                    db, session, session_doc, [_line(item)], actor_id="op-user",
                    occurred_at="2026-10-13T12:00:00+00:00",
                )
    set_fail_after_balance(False)
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["baseline_status"] == BASELINE_UNRECONCILED
    assert bal["physical_qty"] == 0
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0


async def test_concurrent_opname_zero_diff_same_key_one_audit(db):
    from pymongo.errors import PyMongoError

    item, branch, warehouse = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    session_doc = await _session(db, branch, warehouse)
    lines = [_line(item)]

    async def once():
        last = None
        for _ in range(5):
            try:
                async with await _client(db).start_session() as session:
                    async with session.start_transaction():
                        return await apply_stock_opname_adjustments(
                            db, session, session_doc, lines, actor_id="op-user",
                            occurred_at="2026-10-13T12:00:00+00:00",
                        )
            except PyMongoError as exc:
                last = exc
                continue
        raise AssertionError(f"concurrent opname reconcile failed: {last}")

    a, b = await asyncio.gather(once(), once())
    ids = {row["id"] for row in (a + b)}
    assert len(ids) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "qty_delta": 0,
    }) == 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["baseline_status"] == BASELINE_RECONCILED
    assert bal["physical_qty"] == 0
