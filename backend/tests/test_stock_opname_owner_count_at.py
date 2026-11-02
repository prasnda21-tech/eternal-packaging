"""Owner-chosen physical count datetime for operational Stock Opname (UAT numbers)."""
from __future__ import annotations

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
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    WAREHOUSES_COLLECTION,
    ensure_indexes as ensure_physical_indexes,
    get_balance,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active
from services.stock_opname_sessions import (
    COLLECTION as OPNAME_COLL,
    MODE_OPERATIONAL,
    assert_count_at_within_period,
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes,
    parse_physical_count_at,
    record_stock_opname_counts,
    resolve_count_period,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_opname_owner_count_at"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "opname-count-at-tester", "role": "admin"}

# 30 Oct 2026 18:00 WIB == 11:00 UTC; mulai 08:00 WIB == 01:00 UTC
COUNT_AT_UTC = "2026-10-30T11:00:00+00:00"
PERIOD_START_UTC = "2026-10-30T01:00:00+00:00"
BEFORE = "2026-10-30T10:00:00+00:00"
AFTER_IN = "2026-10-30T12:00:00+00:00"
AFTER_OUT = "2026-10-30T13:00:00+00:00"
OPEN_AT = "2026-10-01T00:00:00+00:00"


def _period_fields(end=COUNT_AT_UTC, start=PERIOD_START_UTC):
    return {
        "physical_count_period_start_at": start,
        "physical_count_period_end_at": end,
        "default_physical_count_at": end,
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_physical_indexes(database)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


def _line(session, item_id):
    for line in session["lines"]:
        if line["master_item_id"] == item_id:
            return line
    raise AssertionError(item_id)


async def _location(db):
    branch_id = str(uuid.uuid4())
    warehouse_id = str(uuid.uuid4())
    await db[BRANCHES_COLLECTION].insert_one({
        "id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": warehouse_id, "branch_id": branch_id, "code": "MAIN",
        "name": "Gudang MAIN", "active": True,
    })
    return branch_id, warehouse_id


async def _item(db, *, code="CUP-UAT"):
    item_id = str(uuid.uuid4())
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": "UAT Cup",
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
        "accurate_item_id": f"LOCAL-{item_id}",
    })
    return item_id


def test_parse_rejects_future_and_blank():
    assert parse_physical_count_at(None) is None
    assert parse_physical_count_at("") is None
    assert parse_physical_count_at("2026-10-30T11:00:00+00:00").startswith("2026-10-30T11:00:00")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with pytest.raises(HTTPException) as exc:
        parse_physical_count_at(future)
    assert exc.value.status_code == 400
    detail = str(exc.value.detail).lower()
    assert "masa depan" in detail
    assert "60" in detail


def test_parse_allows_within_60s_clock_skew():
    """Times up to now+60s are accepted; beyond +60s rejected."""
    within = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    assert parse_physical_count_at(within) is not None
    beyond = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    with pytest.raises(HTTPException) as exc:
        parse_physical_count_at(beyond)
    assert exc.value.status_code == 400
    assert "60" in str(exc.value.detail)


async def test_operational_create_requires_default_count_at(db):
    branch_id, warehouse_id = await _location(db)
    with pytest.raises(HTTPException) as exc:
        await create_stock_opname_session(db, {
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "mode": MODE_OPERATIONAL,
        }, USER)
    assert exc.value.status_code == 400
    assert "wajib" in str(exc.value.detail).lower() or "hitung" in str(exc.value.detail).lower()


async def test_uat_owner_numbers_target_78500_adj_129100(db):
    """Mandatory UAT:
    count 76500 @ 30 Oct 2026 18:00 WIB; after +5000/-3000 → target 78500;
    book -50600 → adjustment +129100; replay idempotent.
    """
    branch_id, warehouse_id = await _location(db)
    item = await _item(db)
    # Opening cannot be negative: seed then drive book to -52600 so that after
    # +5000/-3000 post-count movements, book_at_posting == -50600 (UAT figure).
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 1000,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -53600,
        "reference_type": "sales_delivery",
        "reference_id": "del-seed-neg",
        "idempotency_key": "del-seed-neg",
        "occurred_at": "2026-10-15T00:00:00+00:00",
    }, actor_id="tester")
    assert int((await get_balance(db, item, branch_id, warehouse_id))["physical_qty"]) == -52600

    # Delivery before count time is already inside physical count — must NOT
    # enter net_movement_after_count (boundary: occurred_at > count_at only).
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -1000,
        "reference_type": "sales_delivery",
        "reference_id": "del-before",
        "idempotency_key": "del-before",
        "occurred_at": BEFORE,
    }, actor_id="tester")
    # Book now -53600; before-delivery is excluded from net-after, but is in book.
    # Adjust seed so final book_at_posting after ± after-count moves equals -50600:
    # current -53600 +5000 -3000 = -51600. Need one more +1000 before count window.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 1000,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-before-balance",
        "idempotency_key": "gr-before-balance",
        "occurred_at": BEFORE,
    }, actor_id="tester")
    assert int((await get_balance(db, item, branch_id, warehouse_id))["physical_qty"]) == -52600

    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
        "idempotency_key": "uat-create-1",
    }, USER)
    assert created["default_physical_count_at"].startswith("2026-10-30T11:00:00")
    assert created["physical_count_period_start_at"].startswith("2026-10-30T01:00:00")
    assert created["physical_count_period_end_at"].startswith("2026-10-30T11:00:00")
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    assert line["physical_count_at"].startswith("2026-10-30T11:00:00")

    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{
            "line_id": line["line_id"],
            "physical_count_qty": 76500,
            # time already seeded from session default; omit to prove default retention
        }],
    }, USER)

    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 5000,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-after",
        "idempotency_key": "gr-after",
        "occurred_at": AFTER_IN,
    }, actor_id="tester")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -3000,
        "reference_type": "sales_delivery",
        "reference_id": "del-after",
        "idempotency_key": "del-after",
        "occurred_at": AFTER_OUT,
    }, actor_id="tester")

    reviewed = await submit_stock_opname_review(db, counting["id"], USER)
    rev_line = _line(reviewed, item)
    assert rev_line["net_movement_after_count"] == 2000
    assert rev_line["target_qty"] == 78500
    # -52600 +5000 -3000 = -50600 (before-count ±1000 cancel; excluded from net)
    assert rev_line["book_qty_live"] == -50600
    assert rev_line["difference_qty"] == 129100

    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "uat-confirm-1"}, USER,
    )
    conf = _line(confirmed, item)
    assert conf["net_movement_after_count"] == 2000
    assert conf["target_qty"] == 78500
    assert conf["book_qty_at_posting"] == -50600
    assert conf["difference_qty"] == 129100
    bal = await get_balance(db, item, branch_id, warehouse_id)
    assert int(bal["physical_qty"]) == 78500

    replay = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "uat-confirm-1"}, USER,
    )
    assert replay["status"] == "CONFIRMED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    }) == 1
    bal2 = await get_balance(db, item, branch_id, warehouse_id)
    assert int(bal2["physical_qty"]) == 78500


async def test_import_preserves_existing_count_time(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="IMP-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 10,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    assert line["physical_count_at"].startswith("2026-10-30T11:00:00")
    # Qty-only import-style save must not clear seeded time.
    updated = await record_stock_opname_counts(db, counting["id"], {
        "source": "import",
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 12}],
    }, USER)
    u_line = _line(updated, item)
    assert u_line["physical_count_qty"] == 12
    assert u_line["physical_count_at"].startswith("2026-10-30T11:00:00")
    assert u_line.get("imported_at")


async def test_exact_count_at_movement_excluded_from_net(db):
    """occurred_at == physical_count_at uses $gt → excluded from net-after-count.

    Exact-at must be posted before count entry so the existing backdated guard
    (created_at > counted_at AND occurred_at <= count_at) does not fire — that
    guard is covered separately by test_backdated_movement_rejects_confirm /
    test_exact_at_posted_after_count_hits_backdated_guard.
    """
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="EXACT-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 100,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    # Exact boundary before count entry: same timestamp as count — not in net.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -7,
        "reference_type": "sales_delivery",
        "reference_id": "del-exact",
        "idempotency_key": "del-exact",
        "occurred_at": COUNT_AT_UTC,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    # Physical count already saw the -7 (book 93); count qty = 93.
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 93}],
    }, USER)
    # Strictly after: enters net.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 3,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-after-exact",
        "idempotency_key": "gr-after-exact",
        "occurred_at": AFTER_IN,
    }, actor_id="tester")
    reviewed = await submit_stock_opname_review(db, counting["id"], USER)
    rev = _line(reviewed, item)
    assert rev["net_movement_after_count"] == 3
    assert rev["target_qty"] == 96  # 93 + 3; exact -7 not in net
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-exact"}, USER,
    )
    conf = _line(confirmed, item)
    assert conf["net_movement_after_count"] == 3
    assert conf["target_qty"] == 96
    assert conf["book_qty_at_posting"] == 96  # 93 + 3
    assert conf["difference_qty"] == 0
    assert int((await get_balance(db, item, branch_id, warehouse_id))["physical_qty"]) == 96


async def test_exact_at_posted_after_count_hits_backdated_guard(db):
    """Late insert with occurred_at == count_at → existing 409 backdated guard."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="EXACT-BD")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 50,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 50}],
    }, USER)
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 4,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-exact-late",
        "idempotency_key": "gr-exact-late",
        "occurred_at": COUNT_AT_UTC,
    }, actor_id="tester")
    await submit_stock_opname_review(db, counting["id"], USER)
    with pytest.raises(HTTPException) as exc:
        await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "conf-exact-bd"}, USER,
        )
    assert exc.value.status_code == 409
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "reference_id": counting["id"],
    }) == 0


async def test_reversal_cancel_delivery_after_count_in_net(db):
    """REVERSAL of a delivery after count_at contributes to net (cancel delivery)."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="REV-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 100,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    # Delivery before count — already reflected in physical count.
    delivery = await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -20,
        "reference_type": "sales_delivery",
        "reference_id": "del-pre",
        "idempotency_key": "del-pre",
        "occurred_at": BEFORE,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    # Count after before-delivery: book was 80 at count.
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 80}],
    }, USER)
    # Cancel/reverse that delivery AFTER count → stock returns (+20) in net.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_REVERSAL,
        "qty_delta": 20,
        "reference_type": "physical_stock_ledger",
        "reference_id": delivery["id"],
        "reason": "cancel delivery after count",
        "idempotency_key": f"rev-{delivery['id']}",
        "occurred_at": AFTER_IN,
    }, actor_id="tester")
    reviewed = await submit_stock_opname_review(db, counting["id"], USER)
    rev = _line(reviewed, item)
    assert rev["net_movement_after_count"] == 20
    assert rev["target_qty"] == 100  # 80 + 20
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-rev"}, USER,
    )
    conf = _line(confirmed, item)
    assert conf["net_movement_after_count"] == 20
    assert conf["target_qty"] == 100
    assert conf["book_qty_at_posting"] == 100  # 80 + reversal 20
    assert conf["difference_qty"] == 0
    assert int((await get_balance(db, item, branch_id, warehouse_id))["physical_qty"]) == 100


async def test_per_line_different_physical_count_times(db):
    """Two lines with different count times get independent net windows."""
    branch_id, warehouse_id = await _location(db)
    item_a = await _item(db, code="PL-A")
    item_b = await _item(db, code="PL-B")
    for mid in (item_a, item_b):
        await post_movement(db, {
            "master_item_id": mid,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": 50,
            "reference_type": "test",
            "reference_id": f"open-{mid}",
            "idempotency_key": f"open-{mid}",
            "occurred_at": OPEN_AT,
        }, actor_id="tester")
    early = "2026-10-30T10:30:00+00:00"
    late = "2026-10-30T12:30:00+00:00"
    mid_move = "2026-10-30T11:30:00+00:00"
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(end=late, start=early),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line_a = _line(counting, item_a)
    line_b = _line(counting, item_b)
    # Mid movement before count entry: in net for early line A, excluded for late B.
    # (If posted after count entry, B would hit backdated guard — intentional.)
    await post_movement(db, {
        "master_item_id": item_a,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 5,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-a-mid",
        "idempotency_key": "gr-a-mid",
        "occurred_at": mid_move,
    }, actor_id="tester")
    await post_movement(db, {
        "master_item_id": item_b,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 5,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "gr-b-mid",
        "idempotency_key": "gr-b-mid",
        "occurred_at": mid_move,
    }, actor_id="tester")
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [
            {"line_id": line_a["line_id"], "physical_count_qty": 50, "physical_count_at": early},
            # B's book already +5 at mid; count at late still 55 physical if counted then,
            # but we use 50 as "count at late ignoring mid" only for window test —
            # actually book is 55; count qty is independent Owner figure.
            {"line_id": line_b["line_id"], "physical_count_qty": 50, "physical_count_at": late},
        ],
    }, USER)
    reviewed = await submit_stock_opname_review(db, counting["id"], USER)
    ra = _line(reviewed, item_a)
    rb = _line(reviewed, item_b)
    assert ra["physical_count_at"].startswith("2026-10-30T10:30:00")
    assert rb["physical_count_at"].startswith("2026-10-30T12:30:00")
    assert ra["net_movement_after_count"] == 5
    assert ra["target_qty"] == 55
    assert rb["net_movement_after_count"] == 0
    assert rb["target_qty"] == 50
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-per-line"}, USER,
    )
    assert _line(confirmed, item_a)["target_qty"] == 55
    assert _line(confirmed, item_b)["target_qty"] == 50
    assert int((await get_balance(db, item_a, branch_id, warehouse_id))["physical_qty"]) == 55
    assert int((await get_balance(db, item_b, branch_id, warehouse_id))["physical_qty"]) == 50


async def test_preview_then_new_movement_before_confirm_recomputes(db):
    """After REVIEW preview, a new post-count movement must recompute target/adj at confirm."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="PREV-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 100,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    await record_stock_opname_counts(db, counting["id"], {
        "lines": [{"line_id": line["line_id"], "physical_count_qty": 100}],
    }, USER)
    reviewed = await submit_stock_opname_review(db, counting["id"], USER)
    prev = _line(reviewed, item)
    assert prev["net_movement_after_count"] == 0
    assert prev["target_qty"] == 100
    assert prev["difference_qty"] == 0
    # New mutation after preview, before confirm.
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -12,
        "reference_type": "sales_delivery",
        "reference_id": "del-post-preview",
        "idempotency_key": "del-post-preview",
        "occurred_at": AFTER_OUT,
    }, actor_id="tester")
    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "conf-post-preview"}, USER,
    )
    conf = _line(confirmed, item)
    assert conf["net_movement_after_count"] == -12
    assert conf["target_qty"] == 88
    assert conf["book_qty_at_posting"] == 88
    assert conf["difference_qty"] == 0
    assert int((await get_balance(db, item, branch_id, warehouse_id))["physical_qty"]) == 88


def test_resolve_period_rejects_reversed_and_requires_both():
    with pytest.raises(HTTPException) as exc:
        resolve_count_period({
            "physical_count_period_start_at": COUNT_AT_UTC,
            "physical_count_period_end_at": PERIOD_START_UTC,
        }, mode=MODE_OPERATIONAL)
    assert exc.value.status_code == 400
    assert "mulai" in str(exc.value.detail).lower() or "akhir" in str(exc.value.detail).lower()
    with pytest.raises(HTTPException):
        resolve_count_period({"default_physical_count_at": COUNT_AT_UTC}, mode=MODE_OPERATIONAL)
    start, end = resolve_count_period(_period_fields(), mode=MODE_OPERATIONAL)
    assert start.startswith("2026-10-30T01:00:00")
    assert end.startswith("2026-10-30T11:00:00")


def test_wib_wall_clock_maps_to_utc_period():
    """30 Oct 2026 08:00–18:00 WIB → 01:00–11:00 UTC."""
    start, end = resolve_count_period({
        "physical_count_period_start_at": "2026-10-30T08:00:00+07:00",
        "physical_count_period_end_at": "2026-10-30T18:00:00+07:00",
    }, mode=MODE_OPERATIONAL)
    assert start.startswith("2026-10-30T01:00:00")
    assert end.startswith("2026-10-30T11:00:00")


def test_assert_line_time_outside_period():
    with pytest.raises(HTTPException) as before:
        assert_count_at_within_period(
            "2026-10-29T23:00:00+00:00",
            period_start=PERIOD_START_UTC,
            period_end=COUNT_AT_UTC,
        )
    assert before.value.status_code == 400
    with pytest.raises(HTTPException) as after:
        assert_count_at_within_period(
            "2026-10-30T12:00:00+00:00",
            period_start=PERIOD_START_UTC,
            period_end=COUNT_AT_UTC,
        )
    assert after.value.status_code == 400
    assert_count_at_within_period(
        COUNT_AT_UTC, period_start=PERIOD_START_UTC, period_end=COUNT_AT_UTC,
    )


async def test_line_time_outside_period_rejected_on_record(db):
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="OUT-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 10,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    line = _line(counting, item)
    with pytest.raises(HTTPException) as exc:
        await record_stock_opname_counts(db, counting["id"], {
            "lines": [{
                "line_id": line["line_id"],
                "physical_count_qty": 10,
                "physical_count_at": AFTER_IN,  # after akhir
            }],
        }, USER)
    assert exc.value.status_code == 400
    assert "periode" in str(exc.value.detail).lower() or "akhir" in str(exc.value.detail).lower()


async def test_create_replay_idempotent_includes_period(db):
    branch_id, warehouse_id = await _location(db)
    payload = {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
        "idempotency_key": "period-replay-1",
    }
    first = await create_stock_opname_session(db, payload, USER)
    second = await create_stock_opname_session(db, payload, USER)
    assert first["id"] == second["id"]
    with pytest.raises(HTTPException) as exc:
        await create_stock_opname_session(db, {
            **payload,
            "physical_count_period_start_at": "2026-10-30T02:00:00+00:00",
        }, USER)
    assert exc.value.status_code == 409


async def test_legacy_session_readable_without_invented_start(db):
    """Sesi lama tanpa physical_count_period_start_at tetap bisa dibaca; start tetap None."""
    branch_id, warehouse_id = await _location(db)
    item = await _item(db, code="LEG-1")
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 5,
        "reference_type": "test",
        "reference_id": f"open-{item}",
        "idempotency_key": f"open-{item}",
        "occurred_at": OPEN_AT,
    }, actor_id="tester")
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "mode": MODE_OPERATIONAL,
        **_period_fields(),
    }, USER)
    # Simulate legacy doc: strip start only.
    await db[OPNAME_COLL].update_one(
        {"id": created["id"]},
        {"$unset": {"physical_count_period_start_at": ""},
         "$set": {"physical_count_period_end_at": COUNT_AT_UTC}},
    )
    from services.stock_opname_sessions import get_stock_opname_session
    doc = await get_stock_opname_session(db, created["id"], user=USER)
    assert doc.get("physical_count_period_start_at") in (None, "")
    assert doc.get("physical_count_period_end_at", "").startswith("2026-10-30T11:00:00")
    assert doc.get("default_physical_count_at", "").startswith("2026-10-30T11:00:00")
    # Must not invent a start on read.
    assert "physical_count_period_start_at" not in doc or doc["physical_count_period_start_at"] in (None, "")
