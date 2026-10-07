"""R2 go-live safety: cutover baseline, legacy guards, delivery reverse, readiness."""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_r2_go_live_safety"
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
    confirm_sales_delivery,
    ensure_indexes,
    list_physical_stock_catalog,
    operational_availability,
    post_opening_balance,
    reverse_confirmed_sales_delivery,
)
from services.r2_go_live import (  # noqa: E402
    OpeningCutoverError,
    apply_holding_team_permission_top_up,
    check_r2_production_readiness,
    dry_run_legacy_to_r2_opening,
    initialize_delivery_cutover_baseline,
    preview_delivery_cutover_baseline,
    preview_holding_team_permission_top_up,
    require_legacy_stock_mutation_allowed,
    set_r2_authority_active,
)
import server  # noqa: E402
from server import app, current_user  # noqa: E402
from fastapi import HTTPException  # noqa: E402

TEST_DB = "eternal_packaging_test_r2_go_live_safety"
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
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    server.db = database
    server.client = client
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_ygy(db):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "BOX",
        "item_name": "Box",
        "unit": "PCS",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    warehouse = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "Utama",
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
    return item, branch, warehouse


# ---- 1. Historical Belum Terkirim cutover ----


async def test_cutover_baseline_zeros_historical_belum_terkirim(db):
    item, branch, warehouse = await _seed_ygy(db)
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": 1000,
            "reason": "open",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="t",
    )
    await db.orders.insert_one({
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 500,
            "production_process": "print",
            "name": "Box",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 500}],
    })
    before = await list_physical_stock_catalog(db)
    row = next(r for r in before["items"] if r["master_item_id"] == item["id"])
    assert row["qty_belum_terkirim"] == 500

    preview = await preview_delivery_cutover_baseline(db)
    assert preview["item_count"] == 1
    assert preview["baseline_qty_total"] == 500

    init = await initialize_delivery_cutover_baseline(db, actor={"email": "a@test"})
    assert init["initialized"] is True
    replay = await initialize_delivery_cutover_baseline(db)
    assert replay["replayed"] is True

    after = await list_physical_stock_catalog(db)
    row2 = next(r for r in after["items"] if r["master_item_id"] == item["id"])
    assert row2["qty_belum_terkirim"] == 0
    assert row2["physical_qty"] == 1000


async def test_post_cutover_new_completion_creates_belum_terkirim(db):
    item, branch, warehouse = await _seed_ygy(db)
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": 1000,
            "reason": "open",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="t",
    )
    order_id = f"so-{uuid.uuid4().hex[:8]}"
    await db.orders.insert_one({
        "id": order_id,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 500,
            "production_process": "print",
            "name": "Box",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 200}],
    })
    await initialize_delivery_cutover_baseline(db)
    # New completion after cutover
    await db.orders.update_one(
        {"id": order_id},
        {"$set": {"operator_splits": [{"line_idx": 0, "qty_completed": 350}]}},
    )
    catalog = await list_physical_stock_catalog(db)
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["qty_belum_terkirim"] == 150


async def test_operational_availability_formula():
    buckets = operational_availability(
        physical_qty=100,
        ordered_qty=80,
        completed_qty=50,
        r2_delivered_qty=20,
    )
    assert buckets["qty_pesanan"] == 30
    assert buckets["qty_belum_terkirim"] == 30
    assert buckets["qty_tersedia"] == 40


# ---- 2. Legacy mutation guard ----


async def test_legacy_mutation_blocked_when_r2_authority_active(db):
    await set_r2_authority_active(db, active=True, actor={"email": "a@test"})
    with pytest.raises(HTTPException) as exc:
        await require_legacy_stock_mutation_allowed(db)
    assert exc.value.status_code == 409


async def test_legacy_mutation_allowed_before_activation(db):
    await require_legacy_stock_mutation_allowed(db)


async def test_legacy_stock_opname_http_blocked_after_activation(db):
    await set_r2_authority_active(db, active=True)
    app.dependency_overrides[current_user] = lambda: {
        "id": "u1",
        "email": "holding@test.local",
        "role": "holding_team",
        "name": "Holding",
        "permissions_override": {},
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            res = await client.post("/api/stock-opname", json={
                "master_item_id": "x",
                "target_stock_current_qty": 1,
                "note": "test",
                "client_request_id": str(uuid.uuid4()),
            })
        assert res.status_code == 409
        assert "legacy" in res.json()["detail"].lower() or "R2" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(current_user, None)


# ---- 3. Delivery reverse ----


async def test_r2_delivery_confirm_and_reverse_idempotent(db):
    item, branch, warehouse = await _seed_ygy(db)
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": 500,
            "reason": "open",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="t",
    )
    order = {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 200,
            "production_process": "print",
            "name": "Box",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 200}],
    }
    await db.orders.insert_one(dict(order))
    conf_id = f"conf-{uuid.uuid4().hex[:8]}"
    result = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 100, "master_item_id": item["id"], "line_id": "ln-0"}],
        actor_id="t",
        warehouse_id=warehouse["id"],
        branch_id=branch["id"],
        reason="kirim",
    )
    assert result["replayed"] is False
    bal = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]})
    assert int(bal["physical_qty"]) == 400

    rev = await reverse_confirmed_sales_delivery(
        db, confirmation_id=conf_id, actor_id="t", reason="batal",
    )
    assert rev["reversed"] is True
    assert rev["replayed"] is False
    bal2 = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]})
    assert int(bal2["physical_qty"]) == 500

    rev2 = await reverse_confirmed_sales_delivery(
        db, confirmation_id=conf_id, actor_id="t", reason="batal",
    )
    assert rev2["replayed"] is True
    bal3 = await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]})
    assert int(bal3["physical_qty"]) == 500

    # Pre-cutover / missing confirmation is no-op
    missing = await reverse_confirmed_sales_delivery(
        db, confirmation_id="never-existed", actor_id="t",
    )
    assert missing["reversed"] is False


async def test_cutover_blocks_r2_redelivery_of_historical_completion(db):
    item, branch, warehouse = await _seed_ygy(db)
    await post_opening_balance(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": 500,
            "reason": "open",
            "idempotency_key": f"open-{item['id']}",
        },
        actor_id="t",
    )
    order = {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 200,
            "production_process": "print",
            "name": "Box",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 200}],
    }
    await db.orders.insert_one(dict(order))
    await initialize_delivery_cutover_baseline(db)
    from services.physical_stock import PhysicalStockValidationError
    with pytest.raises(PhysicalStockValidationError):
        await confirm_sales_delivery(
            db,
            order_id=order["id"],
            confirmation_id=f"conf-{uuid.uuid4().hex[:8]}",
            lines=[{"line_idx": 0, "qty": 50, "master_item_id": item["id"], "line_id": "ln-0"}],
            actor_id="t",
            warehouse_id=warehouse["id"],
            branch_id=branch["id"],
        )


# ---- 4. HOLDING_TEAM top-up ----


async def test_holding_team_top_up_idempotent(db):
    await db.role_permissions.insert_one({
        "role": "holding_team",
        "permissions": ["purchase_orders.view", "current_stock.view"],
    })
    preview = await preview_holding_team_permission_top_up(db)
    assert "org.branches.view" in preview["missing"]
    assert "nav.inventory.branches_warehouses" in preview["missing"]
    assert "purchase_hpp.view" in preview["missing"]

    dry = await apply_holding_team_permission_top_up(db, apply=False)
    assert dry["dry_run"] is True
    doc = await db.role_permissions.find_one({"role": "holding_team"})
    assert "org.branches.view" not in (doc.get("permissions") or [])

    applied = await apply_holding_team_permission_top_up(db, apply=True)
    assert applied["applied"] is True
    assert applied["ready"] is True
    doc2 = await db.role_permissions.find_one({"role": "holding_team"})
    perms = set(doc2.get("permissions") or [])
    assert "org.branches.view" in perms
    assert "org.warehouses.manage" in perms
    assert "nav.purchasing.hpp" in perms
    assert "nav.purchasing.advances" in perms
    assert "nav.purchasing.returns" in perms

    again = await apply_holding_team_permission_top_up(db, apply=True)
    assert again["added"] == []
    assert again["ready"] is True


async def test_holding_top_up_does_not_grant_non_holding_roles(db):
    await db.role_permissions.insert_one({
        "role": "admin",
        "permissions": ["po.view"],
    })
    await apply_holding_team_permission_top_up(db, apply=True)
    admin = await db.role_permissions.find_one({"role": "admin"})
    assert admin["permissions"] == ["po.view"]


# ---- 5. Readiness checker ----


async def test_readiness_blocked_without_cutover_and_ygy(db):
    report = await check_r2_production_readiness(db)
    assert report["MONGO_TRANSACTION_READY"] is True
    assert report["YGY_BRANCH_READY"] is False
    assert report["OPERATIONAL_WAREHOUSE_READY"] is False
    assert report["CUTOVER_BASELINE_READY"] is False
    assert report["STATUS"] == "BLOCKED"
    assert "YGY_BRANCH_READY" in report["blockers"]


async def test_readiness_ready_when_all_present(db):
    await _seed_ygy(db)
    await initialize_delivery_cutover_baseline(db)
    # No override → builtin_only_ok
    report = await check_r2_production_readiness(db)
    assert report["MONGO_TRANSACTION_READY"] is True
    assert report["YGY_BRANCH_READY"] is True
    assert report["OPERATIONAL_WAREHOUSE_READY"] is True
    assert report["CUTOVER_BASELINE_READY"] is True
    assert report["HOLDING_TEAM_PERMISSION_READY"] is True
    assert report["READY"] is True
    assert report["STATUS"] == "READY"


# ---- 6. Opening cutover dry-run ----


async def test_opening_cutover_dry_run(db):
    item, branch, warehouse = await _seed_ygy(db)
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 15900,
        "unprinted_qty": 15900,
        "printed_qty": 0,
        "base_uom": "PCS",
        "initialized": True,
    })
    await db.product_stock_state.insert_one({
        "master_item_id": f"zero-{uuid.uuid4().hex[:6]}",
        "total_qty": 0,
        "initialized": True,
    })
    plan = await dry_run_legacy_to_r2_opening(db, warehouse_id=warehouse["id"])
    assert plan["dry_run"] is True
    assert plan["apply"] is False
    assert plan["planned_count"] == 1
    assert plan["planned_qty_total"] == 15900
    assert plan["plans"][0]["qty"] == 15900
    assert plan["plans"][0]["branch_id"] == branch["id"]
    assert "PENDING_VALUATION" in plan["plans"][0]["hpp_note"]
    # Legacy unchanged
    legacy = await db.product_stock_state.find_one({"master_item_id": item["id"]})
    assert legacy["total_qty"] == 15900
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


async def test_opening_cutover_refuses_without_ygy(db):
    with pytest.raises(OpeningCutoverError):
        await dry_run_legacy_to_r2_opening(db)
