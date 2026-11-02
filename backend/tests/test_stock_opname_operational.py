"""Operational stock-opname mode (warehouse stays open). Isolated RS test DB only."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    WAREHOUSES_COLLECTION,
    StockOpnameInProgressError,
    ensure_indexes as ensure_physical_indexes,
    get_balance,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active
from services.stock_opname_sessions import (
    MODE_OPERATIONAL,
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes,
    record_stock_opname_counts,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_stock_opname_operational"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "opname-operational-tester"}


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
    yield database
    await client.drop_database(TEST_DB)
    client.close()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def _location(db, *, code="YGY", name="Yogyakarta", warehouse_code="MAIN"):
    branch_id = str(uuid.uuid4())
    warehouse_id = str(uuid.uuid4())
    await db[BRANCHES_COLLECTION].insert_one({
        "id": branch_id, "code": code, "name": name, "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": warehouse_id, "branch_id": branch_id, "code": warehouse_code,
        "name": f"Gudang {warehouse_code}", "active": True,
    })
    return branch_id, warehouse_id


async def _item(db, *, name, code):
    item_id = str(uuid.uuid4())
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": name,
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
        "accurate_item_id": f"LOCAL-{item_id}",
    })
    return item_id


async def _open(db, item_id, branch_id, warehouse_id, qty, *, when=None, key=None):
    payload = {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": qty,
        "reference_type": "test",
        "reference_id": f"open-{item_id}-{warehouse_id}-{key or qty}",
        "reason": "Saldo awal",
        "idempotency_key": f"open-{item_id}-{warehouse_id}-{key or qty}",
    }
    if when:
        payload["occurred_at"] = when
    return await post_movement(db, payload, actor_id="tester")


async def _qty(db, item_id, branch_id, warehouse_id):
    doc = await get_balance(db, item_id, branch_id, warehouse_id)
    return int(doc["physical_qty"]) if doc else 0


def _line(session, item_id):
    for line in session["lines"]:
        if line["master_item_id"] == item_id:
            return line
    raise AssertionError(item_id)


async def _open_operational(db, branch_id, warehouse_id, item_id, book_qty=100, *, open_at=None, count_at=None, period_start=None, period_end=None):
    # Opening must be dated before physical_count_at so it is not treated as post-count net.
    # Period end defaults near now so later per-test line count_at overrides still fit mulai–akhir.
    stamp = open_at or _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    # End near now+30s (within 60s future skew) so subsequent line count_at overrides stay in-range.
    end_stamp = period_end or count_at or _iso(datetime.now(timezone.utc) + timedelta(seconds=30))
    start_stamp = period_start or _iso(
        datetime.fromisoformat(str(end_stamp).replace("Z", "+00:00")) - timedelta(hours=2)
    )
    await _open(db, item_id, branch_id, warehouse_id, book_qty, when=stamp)
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        "physical_count_period_start_at": start_stamp,
        "physical_count_period_end_at": end_stamp,
        "default_physical_count_at": end_stamp,
        "notes": "operational",
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    assert counting["mode"] == MODE_OPERATIONAL
    assert counting["status"] == "COUNTING"
    return counting


async def test_operational_allows_delivery_and_receipt_during_count(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="Cup", code="CUP-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)

    # Warehouse is NOT frozen: receipt and delivery may post.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 20,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-live-1",
        "idempotency_key": "gr-live-1",
    }, actor_id="tester")
    assert await _qty(db, item, branch_id, warehouse_id) == 120

    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -10,
        "reference_type": "sales_delivery",
        "reference_id": "del-live-1",
        "idempotency_key": "del-live-1",
    }, actor_id="tester")
    assert await _qty(db, item, branch_id, warehouse_id) == 110
    assert counting["id"]


async def test_ambiguous_count_time_rejects_confirm_without_posting(db):
    from services.stock_opname_sessions import COLLECTION as OPNAME_COLL

    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="Lid", code="LID-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 50)
    # Simulate wiped/legacy lines without count time (import must not invent one).
    raw = await db[OPNAME_COLL].find_one({"id": counting["id"]}, {"_id": 0})
    wiped = []
    for row in raw.get("lines") or []:
        row = dict(row)
        row["physical_count_at"] = None
        wiped.append(row)
    await db[OPNAME_COLL].update_one({"id": counting["id"]}, {"$set": {"lines": wiped}})
    line = _line({**counting, "lines": wiped}, item)
    counted = await record_stock_opname_counts(db, counting["id"], {
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 48}],
        "source": "import",
    }, USER)
    assert _line(counted, item)["imported_at"]
    # Session default still applies when line time empty — clear default too.
    await db[OPNAME_COLL].update_one(
        {"id": counting["id"]},
        {"$set": {
            "default_physical_count_at": None,
            "lines": [
                {**dict(row), "physical_count_at": None}
                for row in (await db[OPNAME_COLL].find_one({"id": counting["id"]}))["lines"]
            ],
        }},
    )
    with pytest.raises(HTTPException) as exc:
        await submit_stock_opname_review(db, counting["id"], USER)
    assert exc.value.status_code == 400
    assert "waktu hitung" in str(exc.value.detail).lower() or "physical_count_at" in str(exc.value.detail)
    assert await _qty(db, item, branch_id, warehouse_id) == 50
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0


async def test_delivery_after_count_time_adjusts_target(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="A", code="A-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    after = count_at + timedelta(minutes=2)

    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 100,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)

    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -15,
        "reference_type": "sales_delivery",
        "reference_id": "del-after-1",
        "idempotency_key": "del-after-1",
        "occurred_at": _iso(after),
    }, actor_id="tester")
    assert await _qty(db, item, branch_id, warehouse_id) == 85

    await submit_stock_opname_review(db, counting["id"], USER)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-after-del"}, USER,
    )
    assert confirmed["status"] == "CONFIRMED"
    conf_line = _line(confirmed, item)
    assert conf_line["net_movement_after_count"] == -15
    assert conf_line["target_qty"] == 85  # 100 + (-15)
    assert conf_line["book_qty_at_posting"] == 85
    assert conf_line["difference_qty"] == 0
    assert await _qty(db, item, branch_id, warehouse_id) == 85
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    }) == 0


async def test_receipt_after_count_time_zero_adjust_when_book_matches_target(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="B", code="B-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    after = count_at + timedelta(minutes=1)

    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 100,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 25,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-after",
        "idempotency_key": "gr-after",
        "occurred_at": _iso(after),
    }, actor_id="tester")
    await submit_stock_opname_review(db, counting["id"], USER)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-after-gr"}, USER,
    )
    conf_line = _line(confirmed, item)
    assert conf_line["net_movement_after_count"] == 25
    assert conf_line["target_qty"] == 125
    assert conf_line["difference_qty"] == 0
    assert await _qty(db, item, branch_id, warehouse_id) == 125


async def test_count_variance_posts_adjustment_only(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="C", code="C-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 90,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    await submit_stock_opname_review(db, counting["id"], USER)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-var"}, USER,
    )
    conf_line = _line(confirmed, item)
    assert conf_line["target_qty"] == 90
    assert conf_line["difference_qty"] == -10
    assert await _qty(db, item, branch_id, warehouse_id) == 90
    adj = await db[LEDGER_COLLECTION].find_one({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item,
    }, {"_id": 0})
    assert adj["qty_delta"] == -10
    assert adj["metadata"]["mode"] == MODE_OPERATIONAL
    # No re-posted receipt/delivery invented by confirm.
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": {"$in": [MOVEMENT_PURCHASE_RECEIPT, MOVEMENT_SALES_DELIVERY]},
        "reference_id": counting["id"],
    }) == 0


async def test_confirm_replay_idempotent(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="D", code="D-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 40)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 42,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    await submit_stock_opname_review(db, counting["id"], USER)
    first = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "replay-1"}, USER,
    )
    second = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "replay-1"}, USER,
    )
    assert first["status"] == second["status"] == "CONFIRMED"
    assert await _qty(db, item, branch_id, warehouse_id) == 42
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    }) == 1
    with pytest.raises(HTTPException) as clash:
        await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "replay-other"}, USER,
        )
    assert clash.value.status_code == 409


async def test_confirm_concurrent_with_receipt_no_double_count(db):
    """Confirm racing a post-count receipt must not overwrite or double-count."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="D2", code="D2-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    after = count_at + timedelta(minutes=1)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 100,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    await submit_stock_opname_review(db, counting["id"], USER)

    async def _confirm():
        return await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "conf-race-1"}, USER,
        )

    async def _receipt():
        return await post_movement(db, {
            "master_item_id": item,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 25,
            "reference_type": "purchase_goods_receipt",
            "reference_id": "gr-race-1",
            "idempotency_key": "gr-race-1",
            "occurred_at": _iso(after),
        }, actor_id="tester")

    results = await asyncio.gather(_confirm(), _receipt(), return_exceptions=True)
    errors = [row for row in results if isinstance(row, BaseException)]
    # Transient txn conflicts may retry inside confirm; hard failures must not leave dual adj.
    assert not any(
        isinstance(err, HTTPException) and err.status_code >= 500 for err in errors
    ), errors

    # One real receipt only; confirm never re-posts receipt/delivery.
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "idempotency_key": "gr-race-1",
    }) == 1
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "reference_id": counting["id"],
    }) == 0
    adj_count = await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    })
    assert adj_count <= 1

    # Physical end state = opening 100 + receipt 25 + opname delta (0 or compensating once).
    final_qty = await _qty(db, item, branch_id, warehouse_id)
    adj_rows = await db[LEDGER_COLLECTION].find(
        {"movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT, "reference_id": counting["id"]},
        {"_id": 0, "qty_delta": 1},
    ).to_list(5)
    adj_delta = sum(int(row.get("qty_delta") or 0) for row in adj_rows)
    assert final_qty == 100 + 25 + adj_delta
    # Target for count 100 with net including the +25 receipt is 125 → no lasting skew.
    assert final_qty == 125
    # Replay must not invent a second adjustment.
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-race-1"}, USER,
    )
    assert confirmed["status"] == "CONFIRMED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    }) == adj_count
    assert await _qty(db, item, branch_id, warehouse_id) == 125


async def test_confirm_concurrent_with_delivery_no_overwrite(db):
    """Confirm racing a post-count delivery keeps a single coherent warehouse qty."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="D3", code="D3-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 100)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    after = count_at + timedelta(minutes=1)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 100,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    await submit_stock_opname_review(db, counting["id"], USER)

    async def _confirm():
        return await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "conf-race-del"}, USER,
        )

    async def _delivery():
        return await post_movement(db, {
            "master_item_id": item,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "qty_delta": -10,
            "reference_type": "sales_delivery",
            "reference_id": "del-race-1",
            "idempotency_key": "del-race-1",
            "occurred_at": _iso(after),
        }, actor_id="tester")

    await asyncio.gather(_confirm(), _delivery(), return_exceptions=True)
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "idempotency_key": "del-race-1",
    }) == 1
    adj_count = await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    })
    assert adj_count <= 1
    adj_rows = await db[LEDGER_COLLECTION].find(
        {"movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT, "reference_id": counting["id"]},
        {"_id": 0, "qty_delta": 1},
    ).to_list(5)
    adj_delta = sum(int(row.get("qty_delta") or 0) for row in adj_rows)
    final_qty = await _qty(db, item, branch_id, warehouse_id)
    assert final_qty == 100 - 10 + adj_delta
    assert final_qty == 90


async def test_backdated_movement_rejects_confirm(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="E", code="E-1")
    counting = await _open_operational(db, branch_id, warehouse_id, item, 70)
    line = _line(counting, item)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 70,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    # Late insert dated before count time.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 5,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "backdated-gr",
        "idempotency_key": "backdated-gr",
        "occurred_at": _iso(count_at - timedelta(minutes=1)),
    }, actor_id="tester")
    await submit_stock_opname_review(db, counting["id"], USER)
    with pytest.raises(HTTPException) as exc:
        await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "conf-backdated"}, USER,
        )
    assert exc.value.status_code == 409
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0


async def test_warehouse_scope_ignores_other_warehouse_movements(db):
    branch_a, wh_a = await _location(db, warehouse_code="A")
    branch_b, wh_b = await _location(db, code="SMG", name="Semarang", warehouse_code="B")
    # Same branch warehouses for clean scope: recreate B under branch_a
    wh_b = str(uuid.uuid4())
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": wh_b, "branch_id": branch_a, "code": "B2",
        "name": "Gudang B2", "active": True,
    })
    item = await _item(db, name="F", code="F-1")
    open_at = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    await _open(db, item, branch_a, wh_a, 100, key="a", when=open_at)
    await _open(db, item, branch_a, wh_b, 100, key="b", when=open_at)
    count_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    counting = await create_stock_opname_session(db, {
        "branch_id": branch_a,
        "warehouse_id": wh_a,
        "mode": MODE_OPERATIONAL,
        "physical_count_period_start_at": _iso(count_at - timedelta(hours=1)),
        "physical_count_period_end_at": _iso(count_at),
        "default_physical_count_at": _iso(count_at),
    }, USER)
    counting = await start_stock_opname_cutoff(db, counting["id"], USER)
    line = _line(counting, item)
    after = count_at + timedelta(minutes=1)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 100,
            "physical_count_at": _iso(count_at),
        }],
    }, USER)
    # Movement on warehouse B must not affect A target.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_a,
        "warehouse_id": wh_b,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 50,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-b-only",
        "idempotency_key": "gr-b-only",
        "occurred_at": _iso(after),
    }, actor_id="tester")
    await submit_stock_opname_review(db, counting["id"], USER)
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-scope"}, USER,
    )
    conf_line = _line(confirmed, item)
    assert conf_line["net_movement_after_count"] == 0
    assert conf_line["difference_qty"] == 0
    assert await _qty(db, item, branch_a, wh_a) == 100
    assert await _qty(db, item, branch_a, wh_b) == 150


async def test_demand_without_location_remains_unallocated(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="G", code="G-1")
    await _open(db, item, branch_id, warehouse_id, 80)
    await db.orders.insert_one({
        "id": "order-unalloc",
        "status": "open",
        "branch_id": branch_id,
        "line_items": [{
            "line_id": "l1",
            "master_item_id": item,
            "quantity": 30,
            "production_process": "non_print",
        }],
        # no warehouse / location — must stay visible as unallocated demand
    })
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        "physical_count_period_start_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
        "physical_count_period_end_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=10)),
        "default_physical_count_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=10)),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    assert line["demand_allocation"] == "unallocated"
    assert line["qty_pesanan"] == 0  # non_print siap kirim → belum, not pesanan
    assert line["qty_belum_terkirim"] == 30
    # Must not invent a warehouse assignment on the order.
    order = await db.orders.find_one({"id": "order-unalloc"}, {"_id": 0})
    assert order.get("warehouse_id") in (None, "")
    assert "warehouse_id" not in (order.get("line_items") or [{}])[0]


async def test_freeze_mode_still_blocks_movements(db):
    """Legacy freeze regression: default mode freezes warehouse."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, name="H", code="H-1")
    await _open(db, item, branch_id, warehouse_id, 10)
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
    }, USER)
    assert created["mode"] == "freeze"
    await start_stock_opname_cutoff(db, created["id"], USER)
    with pytest.raises(StockOpnameInProgressError):
        await post_movement(db, {
            "master_item_id": item,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 1,
            "reference_type": "purchase_goods_receipt",
            "reference_id": "frozen-gr",
            "idempotency_key": "frozen-gr",
        }, actor_id="tester")
