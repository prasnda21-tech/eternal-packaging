"""Synthetic regression for opname count_at + opening occurred_at (real engine).

No Owner operational exports. Safe for clean checkout / CI.
Dry-run 72/256 Owner proof stays outside this module (local scripts only).
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_opname_count_at_synth"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    OPNAME_SESSIONS,
    PhysicalStockConflictError,
    WAREHOUSES_COLLECTION,
    apply_stock_opname_adjustments,
    ensure_indexes,
    post_movement,
    post_opening_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_opname_count_at_synth"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

COUNT_AT = "2026-10-30T10:00:00Z"
BR = "br-synth-ygy"
WH = "wh-synth-main"

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
    await database[BRANCHES_COLLECTION].insert_one(
        {"id": BR, "code": "YGY", "name": "Yogyakarta", "active": True}
    )
    await database[WAREHOUSES_COLLECTION].insert_one(
        {"id": WH, "branch_id": BR, "code": "YGY-MAIN", "name": "YGY-MAIN", "active": True}
    )
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _item(db, *, kode: str) -> str:
    mid = f"item-{kode}-{uuid.uuid4().hex[:8]}"
    await db[MASTER_ITEMS_COLLECTION].insert_one(
        {
            "id": mid,
            "name": kode,
            "code": kode,
            "item_code": kode,
            "item_name": kode,
            "unit": "PCS",
            "is_active": True,
            "item_type": "INVENTORY",
            "accurate_item_id": f"LOCAL-{kode}",
        }
    )
    return mid


async def _qty(db, mid: str):
    doc = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": mid, "branch_id": BR, "warehouse_id": WH},
        {"_id": 0, "physical_qty": 1},
    )
    return None if not doc else int(doc["physical_qty"])


async def test_opening_zero_and_positive_at_count_at_idempotent(db):
    mid0 = await _item(db, kode="OPEN0")
    mid1 = await _item(db, kode="OPEN100")
    body0 = {
        "branch_id": BR,
        "warehouse_id": WH,
        "physical_qty": 0,
        "reason": "synth opening zero",
        "idempotency_key": "synth-open-0",
        "occurred_at": COUNT_AT,
    }
    body1 = {
        "branch_id": BR,
        "warehouse_id": WH,
        "physical_qty": 100,
        "reason": "synth opening positive",
        "idempotency_key": "synth-open-100",
        "occurred_at": COUNT_AT,
    }
    r0 = await post_opening_balance(db, mid0, body0, actor_id="synth")
    r1 = await post_opening_balance(db, mid1, body1, actor_id="synth")
    assert r0["movement"]["occurred_at"] == COUNT_AT
    assert r0["movement"]["qty_delta"] == 0
    assert r1["movement"]["occurred_at"] == COUNT_AT
    assert r1["movement"]["qty_delta"] == 100
    # retry
    assert (await post_opening_balance(db, mid0, body0, actor_id="synth"))["movement"]["id"] == r0["movement"]["id"]
    assert (await post_opening_balance(db, mid1, body1, actor_id="synth"))["movement"]["id"] == r1["movement"]["id"]
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 2
    with pytest.raises(PhysicalStockConflictError):
        await post_opening_balance(
            db,
            mid1,
            {**body1, "idempotency_key": "synth-open-100-dup"},
            actor_id="synth",
        )


async def test_opname_at_count_at_retains_post_count_and_trial_separate(db):
    """One item with post-count delivery + one 100089-like trial pair."""
    mid = await _item(db, kode="OP72")
    mid089 = await _item(db, kode="100089")

    # Seed books before count_at
    await post_movement(
        db,
        {
            "master_item_id": mid,
            "branch_id": BR,
            "warehouse_id": WH,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": 50,
            "reference_type": "synth",
            "reference_id": "seed-op72",
            "reason": "seed book",
            "occurred_at": "2026-10-29T12:00:00Z",
            "idempotency_key": "synth-seed-op72",
        },
        actor_id="synth",
    )
    await post_movement(
        db,
        {
            "master_item_id": mid089,
            "branch_id": BR,
            "warehouse_id": WH,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": 600,
            "reference_type": "synth",
            "reference_id": "seed-089",
            "reason": "seed book 089",
            "occurred_at": "2026-10-29T12:00:00Z",
            "idempotency_key": "synth-seed-089",
        },
        actor_id="synth",
    )

    # Post-count delivery on OP72
    await post_movement(
        db,
        {
            "master_item_id": mid,
            "branch_id": BR,
            "warehouse_id": WH,
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "qty_delta": -10,
            "reference_type": "synth_delivery",
            "reference_id": "del-1",
            "reason": "post-count delivery",
            "occurred_at": "2026-10-31T08:00:00Z",
            "idempotency_key": "synth-del-op72",
        },
        actor_id="synth",
    )
    assert await _qty(db, mid) == 40

    # Trial pair after count_at on 100089 (net +1300)
    t1 = await post_movement(
        db,
        {
            "master_item_id": mid089,
            "branch_id": BR,
            "warehouse_id": WH,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": 1900,
            "reference_type": "synth_trial",
            "reference_id": "trial-plus",
            "reason": "trial +1900",
            "occurred_at": "2026-10-31T08:15:49Z",
            "idempotency_key": "synth-trial-plus",
        },
        actor_id="synth",
    )
    t2 = await post_movement(
        db,
        {
            "master_item_id": mid089,
            "branch_id": BR,
            "warehouse_id": WH,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": -600,
            "reference_type": "synth_trial",
            "reference_id": "trial-minus",
            "reason": "trial -600",
            "occurred_at": "2026-10-31T08:16:11Z",
            "idempotency_key": "synth-trial-minus",
        },
        actor_id="synth",
    )
    assert await _qty(db, mid089) == 1900

    session_id = f"synth-opname-{uuid.uuid4().hex[:8]}"
    await db[OPNAME_SESSIONS].insert_one(
        {
            "id": session_id,
            "branch_id": BR,
            "warehouse_id": WH,
            "status": "COUNTING",
            "mode": "operational",
            "opname_number": "SYNTH-001",
        }
    )
    session_doc = {
        "id": session_id,
        "branch_id": BR,
        "warehouse_id": WH,
        "status": "COUNTING",
        "mode": "operational",
        "opname_number": "SYNTH-001",
    }

    # OP72: fisik 55, book 50 → delta +5; post-count -10 retained
    line72 = {
        "line_id": "l-op72",
        "master_item_id": mid,
        "book_qty": 50,
        "book_qty_at_posting": 40,
        "physical_count_qty": 55,
        "difference_qty": 5,
        "physical_count_at": COUNT_AT,
        "net_movement_after_count": -10,
        "target_qty": 45,
    }
    # 100089: fisik 1000, book 600 → delta +400
    line089 = {
        "line_id": "l-089",
        "master_item_id": mid089,
        "book_qty": 600,
        "book_qty_at_posting": 1900,
        "physical_count_qty": 1000,
        "difference_qty": 400,
        "physical_count_at": COUNT_AT,
        "net_movement_after_count": 1300,
        "target_qty": 2300,
    }

    async with await db.client.start_session() as mongo_session:
        async with mongo_session.start_transaction():
            moves = await apply_stock_opname_adjustments(
                db,
                mongo_session,
                session_doc,
                [line72, line089],
                actor_id="synth",
                occurred_at=COUNT_AT,
            )
    assert len(moves) == 2
    assert all(m["occurred_at"] == COUNT_AT for m in moves)
    assert all(m["movement_type"] == MOVEMENT_STOCK_OPNAME_ADJUSTMENT for m in moves)

    # Idempotent retry
    async with await db.client.start_session() as mongo_session:
        async with mongo_session.start_transaction():
            moves2 = await apply_stock_opname_adjustments(
                db,
                mongo_session,
                session_doc,
                [line72, line089],
                actor_id="synth",
                occurred_at=COUNT_AT,
            )
    assert {m["id"] for m in moves2} == {m["id"] for m in moves}
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT}
    ) == 2

    assert await _qty(db, mid) == 45  # 40 + 5
    assert await _qty(db, mid089) == 2300  # 1900 + 400
    # post-count delivery still present
    assert await db[LEDGER_COLLECTION].count_documents(
        {
            "master_item_id": mid,
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "occurred_at": {"$gt": COUNT_AT},
        }
    ) == 1

    # Separate trial correction -1300 (not folded into opname)
    before = await _qty(db, mid089)
    for src in (t1, t2):
        await post_movement(
            db,
            {
                "master_item_id": mid089,
                "branch_id": BR,
                "warehouse_id": WH,
                "movement_type": MOVEMENT_REVERSAL,
                "qty_delta": -int(src["qty_delta"]),
                "reference_type": LEDGER_COLLECTION,
                "reference_id": src["id"],
                "reason": "synth trial correction separate from opname",
                "occurred_at": "2026-11-02T00:00:00Z",
                "idempotency_key": f"synth-rev-{src['id']}",
                "metadata": {"reverses_movement_id": src["id"], "separate_from_opname": True},
            },
            actor_id="synth",
        )
    after = await _qty(db, mid089)
    assert before == 2300
    assert after == 1000
    assert after == before - 1300
    assert await db[LEDGER_COLLECTION].count_documents(
        {"master_item_id": mid089, "movement_type": MOVEMENT_REVERSAL}
    ) == 2
