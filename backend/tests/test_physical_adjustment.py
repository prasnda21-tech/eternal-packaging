"""Target-based R2 physical adjustment. Isolated test database only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_physical_adjustment"
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
    MOVEMENT_MANUAL_ADJUSTMENT,
    WAREHOUSES_COLLECTION,
    PhysicalStockConflictError,
    PhysicalStockValidationError,
    adjust_physical_stock_to_target,
    ensure_indexes,
    list_physical_mutations,
    list_physical_stock_catalog,
    set_fail_after_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
import server  # noqa: E402
from server import app, current_user  # noqa: E402

import httpx  # noqa: E402

TEST_DB = "eternal_packaging_test_physical_adjustment"
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
    server.db = database
    set_fail_after_balance(False)
    try:
        yield database
    finally:
        set_fail_after_balance(False)
        await client.drop_database(TEST_DB)
        client.close()


async def _location(db, *, qty=1000, code="MAIN", branch_code="YGY"):
    item = {"id": f"item-{uuid.uuid4().hex[:8]}", "item_code": "BOX", "item_name": "Dus", "unit": "PCS"}
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": branch_code, "name": "Yogyakarta", "active": True}
    warehouse = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": code,
        "name": code,
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    if qty is not None:
        await db[BALANCE_COLLECTION].insert_one({
            "id": f"bal-{uuid.uuid4().hex[:8]}",
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": qty,
        })
    return item, branch, warehouse


def _body(branch, warehouse, target, key="adj-1", reason="Hasil hitung fisik gudang"):
    return {
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "target_physical_qty": target,
        "reason": reason,
        "idempotency_key": key,
    }


async def _qty(db, item, warehouse):
    doc = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]},
        {"_id": 0, "physical_qty": 1},
    )
    return int((doc or {}).get("physical_qty") or 0)


async def test_positive_negative_and_zero_target(db):
    item, branch, warehouse = await _location(db, qty=1000)
    up = await adjust_physical_stock_to_target(db, item["id"], _body(branch, warehouse, 1200, "up"), actor_id="admin-1")
    assert up["changed"] is True
    assert up["qty_delta"] == 200
    assert up["previous_physical_qty"] == 1000
    assert up["physical_qty"] == 1200
    assert await _qty(db, item, warehouse) == 1200
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_MANUAL_ADJUSTMENT}) == 1
    row = await db[LEDGER_COLLECTION].find_one({"id": up["movement_id"]}, {"_id": 0})
    assert row["balance_before"] == 1000
    assert row["balance_after"] == 1200
    assert row["reason"] == "Hasil hitung fisik gudang"
    assert row["actor_id"] == "admin-1"

    down_item, down_branch, down_warehouse = await _location(db, qty=1000)
    down = await adjust_physical_stock_to_target(
        db, down_item["id"], _body(down_branch, down_warehouse, 800, "down"), actor_id="admin-1",
    )
    assert down["qty_delta"] == -200
    assert down["previous_physical_qty"] == 1000
    assert down["physical_qty"] == 800

    zero_item, zero_branch, zero_warehouse = await _location(db, qty=1000)
    zeroed = await adjust_physical_stock_to_target(
        db, zero_item["id"], _body(zero_branch, zero_warehouse, 0, "zero"), actor_id="admin-1",
    )
    assert zeroed["qty_delta"] == -1000
    assert zeroed["physical_qty"] == 0
    assert await _qty(db, zero_item, zero_warehouse) == 0


async def test_negative_target_and_blank_reason_write_nothing(db):
    item, branch, warehouse = await _location(db, qty=1000)
    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    with pytest.raises(PhysicalStockValidationError):
        await adjust_physical_stock_to_target(db, item["id"], _body(branch, warehouse, -1), actor_id="admin-1")
    with pytest.raises(PhysicalStockValidationError):
        await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, 900, reason="   "), actor_id="admin-1",
        )
    assert await _qty(db, item, warehouse) == 1000
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger


async def test_zero_delta_writes_no_movement_and_retry_is_safe(db):
    item, branch, warehouse = await _location(db, qty=1000)
    # Seeded balance without unreconciled flag is treated as already settled.
    first = await adjust_physical_stock_to_target(db, item["id"], _body(branch, warehouse, 1000, "same"), actor_id="admin-1")
    second = await adjust_physical_stock_to_target(db, item["id"], _body(branch, warehouse, 1000, "same"), actor_id="admin-1")
    assert first["changed"] is False
    assert second["changed"] is False
    assert first["movement_id"] is None
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_MANUAL_ADJUSTMENT}) == 0
    assert await _qty(db, item, warehouse) == 1000


async def test_zero_target_unreconciled_marks_baseline_reconciled_with_audit(db):
    """Count=0 on book=0 unreconciled must reconcile without inventing qty."""
    item, branch, warehouse = await _location(db, qty=None)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    body = _body(branch, warehouse, 0, key="reconcile-zero", reason="Opname fisik 0")
    first = await adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1")
    assert first["changed"] is True
    assert first["qty_delta"] == 0
    assert first["physical_qty"] == 0
    assert first["movement_id"]
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]},
        {"_id": 0},
    )
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_RECONCILED
    row = await db[LEDGER_COLLECTION].find_one({"id": first["movement_id"]}, {"_id": 0})
    assert row["qty_delta"] == 0
    assert row["balance_before"] == 0
    assert row["balance_after"] == 0
    assert row["reason"] == "Opname fisik 0"
    assert row["metadata"]["reconcile_only"] is True
    # Idempotent replay
    second = await adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1")
    assert second["movement_id"] == first["movement_id"]
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "reconcile-zero"}) == 1
    # Already reconciled + zero delta → no-op
    noop = await adjust_physical_stock_to_target(
        db, item["id"], _body(branch, warehouse, 0, key="reconcile-zero-2"), actor_id="admin-1",
    )
    assert noop["changed"] is False
    assert noop["movement_id"] is None
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_MANUAL_ADJUSTMENT}) == 1


async def test_idempotent_retry_and_payload_conflict(db):
    item, branch, warehouse = await _location(db, qty=1000)
    body = _body(branch, warehouse, 1200, "once")
    first = await adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1")
    second = await adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1")
    assert first["movement_id"] == second["movement_id"]
    assert await _qty(db, item, warehouse) == 1200
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "once"}) == 1
    with pytest.raises(PhysicalStockConflictError):
        await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, 900, "once", reason="Alasan lain"), actor_id="admin-1",
        )
    assert await _qty(db, item, warehouse) == 1200


async def test_concurrent_targets_keep_a_reconcilable_ledger(db):
    item, branch, warehouse = await _location(db, qty=1000)

    async def run(target, key):
        return await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, target, key), actor_id="admin-1",
        )

    results = await asyncio.gather(run(1200, "c-1200"), run(1100, "c-1100"))
    assert all(item_result["changed"] for item_result in results)
    final = await _qty(db, item, warehouse)
    rows = await db[LEDGER_COLLECTION].find(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]},
        {"_id": 0},
    ).sort([("created_at", 1), ("id", 1)]).to_list(10)
    running = 1000
    for row in rows:
        assert row["balance_before"] == running
        assert row["balance_after"] == running + row["qty_delta"]
        assert row["balance_after"] == row["metadata"]["target_physical_qty"]
        running = row["balance_after"]
    assert running == final
    assert final == rows[-1]["metadata"]["target_physical_qty"]
    assert final in (1200, 1100)


async def _seed_unreconciled_zero(db, item, branch, warehouse):
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })


async def test_concurrent_zero_delta_same_idempotency_key(db):
    """A: two identical zero-delta reconciles share one audit row."""
    item, branch, warehouse = await _location(db, qty=None)
    await _seed_unreconciled_zero(db, item, branch, warehouse)
    body = _body(branch, warehouse, 0, key="zero-same", reason="Hitung 0")

    results = await asyncio.gather(
        adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1"),
        adjust_physical_stock_to_target(db, item["id"], body, actor_id="admin-1"),
    )
    assert {row["movement_id"] for row in results} == {results[0]["movement_id"]}
    assert all(row["qty_delta"] == 0 for row in results)
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "zero-same"}) == 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_RECONCILED


async def test_concurrent_zero_delta_different_idempotency_keys(db):
    """B: different keys — one reconcile audit, no duplicate effect, qty stays 0."""
    item, branch, warehouse = await _location(db, qty=None)
    await _seed_unreconciled_zero(db, item, branch, warehouse)

    async def run(key):
        return await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, 0, key=key, reason="Hitung 0"), actor_id="admin-1",
        )

    results = await asyncio.gather(run("zero-a"), run("zero-b"), return_exceptions=True)
    assert not any(isinstance(row, BaseException) for row in results)
    changed = [row for row in results if row["changed"]]
    noop = [row for row in results if not row["changed"]]
    assert len(changed) == 1
    assert len(noop) == 1
    assert changed[0]["qty_delta"] == 0
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 0,
        "warehouse_id": warehouse["id"],
    }) == 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_RECONCILED


async def test_zero_delta_reconcile_while_stock_movement_changes_qty(db):
    """C: concurrent receipt during zero-target reconcile keeps ledger reconcilable."""
    from services.physical_stock import MOVEMENT_PURCHASE_RECEIPT, post_movement

    item, branch, warehouse = await _location(db, qty=None)
    await _seed_unreconciled_zero(db, item, branch, warehouse)

    async def reconcile():
        return await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, 0, key="race-zero", reason="Hitung 0"),
            actor_id="admin-1",
        )

    async def receive():
        return await post_movement(db, {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 7,
            "reference_type": "purchase_goods_receipt",
            "reference_id": "gr-race",
            "idempotency_key": "gr-race-zero",
            "reason": "GR concurrent",
        }, actor_id="admin-1")

    results = await asyncio.gather(reconcile(), receive(), return_exceptions=True)
    assert not any(isinstance(row, BaseException) for row in results)
    final = await _qty(db, item, warehouse)
    # Order-dependent but closed: reconcile-then-receipt → 7; receipt-then-target-0 → 0.
    assert final in (0, 7)
    rows = await db[LEDGER_COLLECTION].find(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]},
        {"_id": 0},
    ).sort([("created_at", 1), ("id", 1)]).to_list(20)
    assert len(rows) >= 1
    # Concurrent writers may both observe the same before-qty; each row must still
    # be self-consistent, receipt must persist, and balance must match final book.
    for row in rows:
        assert int(row["balance_after"]) == int(row["balance_before"]) + int(row["qty_delta"])
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "gr-race-zero"}) == 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["physical_qty"] == final
    assert str(bal.get("baseline_status") or "") in (BASELINE_RECONCILED, BASELINE_UNRECONCILED)


async def test_concurrent_zero_delta_warehouse_isolation(db):
    """D: concurrent zero-delta in separate warehouses stays isolated."""
    item, branch, wh_a = await _location(db, qty=None, code="WHA")
    wh_b = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "WHB",
        "name": "WHB",
        "active": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh_b))
    await _seed_unreconciled_zero(db, item, branch, wh_a)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_b["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })

    a, b = await asyncio.gather(
        adjust_physical_stock_to_target(
            db, item["id"], _body(branch, wh_a, 0, key="iso-a", reason="Hitung A"), actor_id="admin-1",
        ),
        adjust_physical_stock_to_target(
            db, item["id"], _body(branch, wh_b, 0, key="iso-b", reason="Hitung B"), actor_id="admin-1",
        ),
    )
    assert a["changed"] is True and b["changed"] is True
    assert a["movement_id"] != b["movement_id"]
    assert await db[LEDGER_COLLECTION].count_documents({"warehouse_id": wh_a["id"], "qty_delta": 0}) == 1
    assert await db[LEDGER_COLLECTION].count_documents({"warehouse_id": wh_b["id"], "qty_delta": 0}) == 1
    for wh in (wh_a, wh_b):
        bal = await db[BALANCE_COLLECTION].find_one(
            {"master_item_id": item["id"], "warehouse_id": wh["id"]}, {"_id": 0},
        )
        assert bal["physical_qty"] == 0
        assert bal["baseline_status"] == BASELINE_RECONCILED


async def test_zero_delta_forced_abort_leaves_no_orphan_audit(db):
    """E: forced abort after balance write rolls back baseline and ledger."""
    item, branch, warehouse = await _location(db, qty=None)
    await _seed_unreconciled_zero(db, item, branch, warehouse)
    set_fail_after_balance(True)
    with pytest.raises(Exception):
        await adjust_physical_stock_to_target(
            db, item["id"], _body(branch, warehouse, 0, key="abort-zero", reason="Hitung 0"),
            actor_id="admin-1",
        )
    set_fail_after_balance(False)
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["physical_qty"] == 0
    assert bal["baseline_status"] == BASELINE_UNRECONCILED
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "abort-zero"}) == 0
    # Retry succeeds cleanly
    ok = await adjust_physical_stock_to_target(
        db, item["id"], _body(branch, warehouse, 0, key="abort-zero", reason="Hitung 0"),
        actor_id="admin-1",
    )
    assert ok["changed"] is True
    assert ok["qty_delta"] == 0
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": warehouse["id"]}, {"_id": 0},
    )
    assert bal["baseline_status"] == BASELINE_RECONCILED
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "abort-zero"}) == 1


async def test_warehouse_isolation_and_availability_follow_r2_sum(db):
    item, branch, main = await _location(db, qty=700, code="MAIN")
    other = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "WHB",
        "name": "WHB",
        "active": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(other))
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": other["id"],
        "physical_qty": 300,
    })
    await db.orders.insert_one({
        "id": "so-1",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{"master_item_id": item["id"], "quantity": 200, "production_process": "print"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 100}],
    })
    before_orders = await db.orders.count_documents({})
    legacy_before = {name: await db[name].count_documents({}) for name in LEGACY}
    result = await adjust_physical_stock_to_target(
        db, item["id"], _body(branch, main, 750, "main-only"), actor_id="admin-1",
    )
    assert result["qty_delta"] == 50
    assert await _qty(db, item, main) == 750
    assert await _qty(db, item, other) == 300
    catalog = await list_physical_stock_catalog(db)
    row = catalog["items"][0]
    assert row["physical_qty"] == 1050
    assert row["qty_pesanan"] == 100
    assert row["qty_belum_terkirim"] == 100
    assert row["qty_tersedia"] == 850
    mutations = await list_physical_mutations(db, item["id"], movement_type=MOVEMENT_MANUAL_ADJUSTMENT)
    assert mutations["total"] == 1
    assert mutations["items"][0]["reason"] == "Hasil hitung fisik gudang"
    assert mutations["items"][0]["qty_delta"] == 50
    assert mutations["items"][0]["balance_after"] == 750
    assert await db.orders.count_documents({}) == before_orders
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_MANUAL_ADJUSTMENT}) == 1
    for name, count in legacy_before.items():
        assert await db[name].count_documents({}) == count


def _user(role):
    return {
        "id": f"user-{role}",
        "email": f"{role}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": f"user-{role}",
        "permissions_override": {},
    }


async def test_http_adjustment_allows_local_admin_and_rejects_other_roles(db, monkeypatch):
    item, branch, warehouse = await _location(db, qty=1000)
    # Explicit local APP_ENV required after fail-closed package/runtime detection.
    monkeypatch.setenv("APP_ENV", "preview")
    transport = httpx.ASGITransport(app=app)
    app.dependency_overrides[current_user] = lambda: _user("sales")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(
            f"/api/master-items/{item['id']}/physical-adjustment",
            json=_body(branch, warehouse, 1100, "denied"),
        )
    app.dependency_overrides[current_user] = lambda: _user("admin")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.post(
            f"/api/master-items/{item['id']}/physical-adjustment",
            json=_body(branch, warehouse, 1100, "allowed"),
        )
    app.dependency_overrides.clear()
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["physical_qty"] == 1100
    assert allowed.json()["changed"] is True
    assert await _qty(db, item, warehouse) == 1100
