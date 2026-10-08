"""Coordination fence: repair vs wage-claim mutual exclusion. Local Mongo only."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_gaji_coord_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

from motor.motor_asyncio import AsyncIOMotorClient

import services.operator_output_coord as coord
from services.operator_output_coord import (
    COORD_INDEX,
    COORD_LOCKS,
    coordinate_source_key,
    ensure_coord_indexes,
    require_coord_holder,
)
from services.operator_output_repair import repair_work_date
from services.operator_wage_claims import WAGE_CLAIMS, WAGE_INDEX, _claim_wage, ensure_piece_indexes


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await ensure_piece_indexes(database)
    await ensure_coord_indexes(database)
    await database[COORD_LOCKS].delete_many({})
    await database[WAGE_CLAIMS].delete_many({})
    await database.orders.delete_many({})
    yield database
    await database[COORD_LOCKS].delete_many({})
    client.close()


@pytest.mark.anyio
async def test_coord_fence_serializes_concurrent_holders(db):
    key = f"po-x:split-1:act-{uuid.uuid4().hex[:6]}"
    order = []

    async def hold(label: str, delay: float):
        async with coordinate_source_key(db, key, purpose=label, wait_seconds=5):
            order.append(f"{label}-enter")
            await asyncio.sleep(delay)
            order.append(f"{label}-exit")

    await asyncio.gather(hold("a", 0.15), hold("b", 0.05))
    assert order in (
        ["a-enter", "a-exit", "b-enter", "b-exit"],
        ["b-enter", "b-exit", "a-enter", "a-exit"],
    )


@pytest.mark.anyio
async def test_expired_lease_is_recoverable(db):
    key = "po-y:split-1:act-stale"
    stale = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    await db[COORD_LOCKS].insert_one({
        "source_key": key,
        "holder": "dead-holder",
        "purpose": "crashed",
        "acquired_at": stale,
        "expires_at": stale,
        "generation": 0,
    })
    async with coordinate_source_key(db, key, purpose="recover", wait_seconds=3):
        row = await db[COORD_LOCKS].find_one({"source_key": key})
        assert row["holder"] != "dead-holder"
        assert row["purpose"] == "recover"
        assert int(row.get("generation") or 0) >= 1


@pytest.mark.anyio
async def test_claim_blocked_while_repair_holds_fence(db):
    key = "po-z:split-1:act-1"
    claim_result = {"status": None}

    async def repair_then_sleep():
        async with coordinate_source_key(db, key, purpose="repair_work_date", wait_seconds=5):
            await asyncio.sleep(0.2)

    async def claim_while_held():
        await asyncio.sleep(0.05)
        try:
            await _claim_wage(
                db,
                {"id": "per-1", "group_id": "g1"},
                {
                    "source_key": key,
                    "order_id": "po-z",
                    "split_id": "split-1",
                    "action_id": "act-1",
                    "employee_id": "emp-1",
                    "work_date": "2026-10-05",
                    "qty": 5,
                    "rate_per_pcs": 100,
                    "amount": 500,
                    "operator_user_id": "u1",
                },
                attempt_id="att-1",
            )
            claim_result["status"] = "ok"
        except HTTPException as exc:
            claim_result["status"] = exc.status_code

    await asyncio.gather(repair_then_sleep(), claim_while_held())
    assert claim_result["status"] in ("ok", 409)
    if claim_result["status"] == "ok":
        row = await db[WAGE_CLAIMS].find_one({"source_key": key})
        assert row and row["status"] == "held"


@pytest.mark.anyio
async def test_repair_rejects_when_claim_already_held_under_fence(db):
    key = "po-w:split-1:act-1"
    await db.orders.insert_one({
        "id": "po-w",
        "cancelled": False,
        "po_number": "PO-W",
        "operator_splits": [{
            "id": "split-1",
            "operator_name": "A",
            "operator_user_id": "u1",
            "progress_entries": [{
                "action_id": "act-1",
                "po_id": "po-w",
                "split_id": "split-1",
                "operator_user_id": "u1",
                "qty": 3,
            }],
        }],
    })
    await db[WAGE_CLAIMS].insert_one({
        "id": "cl-1",
        "source_key": key,
        "status": "held",
        "period_id": "per-1",
    })

    async def audit(*a, **k):
        return {"ok": True}

    with pytest.raises(HTTPException) as exc:
        await repair_work_date(
            db,
            order_id="po-w",
            split_id="split-1",
            action_id="act-1",
            work_date="2026-10-05",
            expected_work_date="",
            reason="Tanggal dari kartu absensi",
            user={"email": "head@x.com"},
            write_audit=audit,
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_stale_holder_cannot_write_after_takeover(db, monkeypatch):
    """Lease expires while original still 'running'; takeover; original require fails."""
    monkeypatch.setattr(coord, "LEASE_SECONDS", 1)
    monkeypatch.setattr(coord, "HEARTBEAT_SECONDS", 30.0)  # disable renew during sleep
    key = f"po-stale:split-1:act-{uuid.uuid4().hex[:6]}"
    events = []

    async def original():
        async with coordinate_source_key(db, key, purpose="original", wait_seconds=5) as holder:
            events.append(("original-acquired", holder))
            await asyncio.sleep(1.3)  # past 1s lease, no heartbeat renew
            with pytest.raises(HTTPException) as exc:
                await require_coord_holder(db, key, holder)
            events.append(("original-require-status", exc.value.status_code))
            row = await db[COORD_LOCKS].find_one({"source_key": key})
            events.append(("lock-holder-after", (row or {}).get("holder")))

    async def takeover():
        await asyncio.sleep(1.15)
        async with coordinate_source_key(db, key, purpose="takeover", wait_seconds=5) as holder2:
            events.append(("takeover-acquired", holder2))
            await require_coord_holder(db, key, holder2)
            events.append(("takeover-require-ok", True))
            await asyncio.sleep(0.4)

    await asyncio.gather(original(), takeover())
    assert ("original-require-status", 409) in events
    assert ("takeover-require-ok", True) in events
    # Original holder token must not equal current lock holder after takeover.
    orig = next(h for tag, h in events if tag == "original-acquired")
    after = next(h for tag, h in events if tag == "lock-holder-after")
    assert after != orig


@pytest.mark.anyio
async def test_crash_before_release_allows_recovery(db):
    key = f"po-crash:split-1:act-{uuid.uuid4().hex[:6]}"
    # Simulate crash: fence left behind with expired lease, no delete.
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    await db[COORD_LOCKS].insert_one({
        "source_key": key,
        "holder": "crashed-pid",
        "purpose": "wage_claim",
        "acquired_at": past,
        "expires_at": past,
        "generation": 0,
    })
    async with coordinate_source_key(db, key, purpose="repair_work_date", wait_seconds=3) as holder:
        await require_coord_holder(db, key, holder)
        row = await db[COORD_LOCKS].find_one({"source_key": key})
        assert row["holder"] == holder
        assert row.get("recovered_from") == "crashed-pid"


@pytest.mark.anyio
async def test_duplicate_active_claim_unique_index(db):
    key = f"po-dup:split-1:act-{uuid.uuid4().hex[:6]}"
    await db[WAGE_CLAIMS].insert_one({
        "id": str(uuid.uuid4()),
        "source_key": key,
        "status": "held",
        "period_id": "per-a",
    })
    with pytest.raises(DuplicateKeyError):
        await db[WAGE_CLAIMS].insert_one({
            "id": str(uuid.uuid4()),
            "source_key": key,
            "status": "held",
            "period_id": "per-b",
        })


@pytest.mark.anyio
async def test_repeated_claim_same_period_adopts_held(db):
    key = f"po-rep:split-1:act-{uuid.uuid4().hex[:6]}"
    source = {
        "source_key": key,
        "order_id": "po-rep",
        "split_id": "split-1",
        "action_id": "act-1",
        "employee_id": "emp-1",
        "work_date": "2026-10-05",
        "qty": 5,
        "rate_per_pcs": 100,
        "amount": 500,
        "operator_user_id": "u1",
    }
    period = {"id": "per-1", "group_id": "g1"}
    state1, row1 = await _claim_wage(db, period, source, "att-1")
    assert state1 == "inserted"
    state2, row2 = await _claim_wage(db, period, source, "att-2")
    assert state2 == "owned"
    assert row2["id"] == row1["id"]
    count = await db[WAGE_CLAIMS].count_documents({"source_key": key, "status": "held"})
    assert count == 1


@pytest.mark.anyio
async def test_heartbeat_keeps_holder_alive_past_lease(db, monkeypatch):
    """Claim/work lasting longer than LEASE_SECONDS stays owned via heartbeat."""
    monkeypatch.setattr(coord, "LEASE_SECONDS", 1)
    monkeypatch.setattr(coord, "HEARTBEAT_SECONDS", 0.3)
    key = f"po-long:split-1:act-{uuid.uuid4().hex[:6]}"
    async with coordinate_source_key(db, key, purpose="wage_claim", wait_seconds=5) as holder:
        await asyncio.sleep(1.6)  # > lease; heartbeat should renew
        await require_coord_holder(db, key, holder)
        row = await db[COORD_LOCKS].find_one({"source_key": key})
        assert row["holder"] == holder


async def _audit_ok(*a, **k):
    return {"ok": True}


@pytest.mark.anyio
async def test_repair_and_claim_race_same_source_key(db):
    key = "po-race:split-1:act-1"
    await db.orders.insert_one({
        "id": "po-race",
        "po_number": "PO-RACE",
        "cancelled": False,
        "operator_splits": [{
            "id": "split-1",
            "operator_user_id": "u1",
            "operator_name": "A",
            "progress_entries": [{
                "action_id": "act-1",
                "po_id": "po-race",
                "split_id": "split-1",
                "operator_user_id": "u1",
                "qty": 4,
            }],
        }],
    })
    outcomes = {"repair": None, "claim": None}

    async def do_repair():
        try:
            await repair_work_date(
                db,
                order_id="po-race",
                split_id="split-1",
                action_id="act-1",
                work_date="2026-10-05",
                expected_work_date="",
                reason="Tanggal dari kartu absensi",
                user={"email": "head@x.com"},
                write_audit=_audit_ok,
            )
            outcomes["repair"] = "ok"
        except HTTPException as exc:
            outcomes["repair"] = exc.status_code

    async def do_claim():
        await asyncio.sleep(0.01)
        try:
            # Claim path for undated key still acquires fence; insert may succeed
            # only after repair fills date in collect_outputs, but direct _claim_wage
            # is the write under test.
            await _claim_wage(
                db,
                {"id": "per-1", "group_id": "g1"},
                {
                    "source_key": key,
                    "order_id": "po-race",
                    "split_id": "split-1",
                    "action_id": "act-1",
                    "employee_id": "emp-1",
                    "work_date": "2026-10-05",
                    "qty": 4,
                    "rate_per_pcs": 100,
                    "amount": 400,
                    "operator_user_id": "u1",
                },
                attempt_id="race-1",
            )
            outcomes["claim"] = "ok"
        except HTTPException as exc:
            outcomes["claim"] = exc.status_code

    await asyncio.gather(do_repair(), do_claim())
    # Both may succeed sequentially under the fence; never overlapping corrupt writes.
    assert outcomes["repair"] in ("ok", 409)
    assert outcomes["claim"] in ("ok", 409)
    # At most one active claim.
    assert await db[WAGE_CLAIMS].count_documents({"source_key": key, "status": {"$in": ["held", "posted"]}}) <= 1
    entry = (await db.orders.find_one({"id": "po-race"}))["operator_splits"][0]["progress_entries"][0]
    # If repair won, date is set; if claim ran first then repair, date may still be set.
    if outcomes["repair"] == "ok":
        assert entry.get("work_date") == "2026-10-05"


@pytest.mark.anyio
async def test_coord_and_wage_unique_indexes_exist(db):
    coord_indexes = await db[COORD_LOCKS].index_information()
    wage_indexes = await db[WAGE_CLAIMS].index_information()
    assert any(COORD_INDEX in name or name == COORD_INDEX for name in coord_indexes)
    # Motor names index by name key
    assert COORD_INDEX in coord_indexes or any(
        (info.get("unique") and info.get("key") == [("source_key", 1)])
        for info in coord_indexes.values()
    )
    assert WAGE_INDEX in wage_indexes or any(
        info.get("unique") and "source_key" in str(info.get("key"))
        for name, info in wage_indexes.items()
    )
    # Duplicate coord insert fails
    await db[COORD_LOCKS].insert_one({
        "source_key": "idx-test:1:1", "holder": "a",
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    })
    with pytest.raises(DuplicateKeyError):
        await db[COORD_LOCKS].insert_one({
            "source_key": "idx-test:1:1", "holder": "b",
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
        })
