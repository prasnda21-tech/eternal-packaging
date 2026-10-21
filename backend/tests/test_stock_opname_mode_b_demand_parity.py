"""Opname demand snapshot Mode B parity with Stock Saat Ini Standalone.

Locks: null-branch historical orders enter Opname Qty Pesanan only when Mode B
legacy policy attributes them (enabled + org_branch_id + include_null_branch +
created_at < cutoff). Mode B OFF / post-cutoff null → excluded. Never rewrites
orders.branch_id. Isolated test DB only.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_stock_opname_mode_b_demand"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.mode_b_legacy_demand import configure_mode_b_legacy_demand  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    WAREHOUSES_COLLECTION,
    ensure_indexes as ensure_physical_indexes,
    list_physical_stock_catalog,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_opname_sessions import (  # noqa: E402
    _snapshot_order_buckets,
    ensure_indexes as ensure_opname_indexes,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)

TEST_DB = "eternal_packaging_test_stock_opname_mode_b_demand"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

CUTOFF = "2026-06-01T00:00:00+00:00"
HISTORIC = "2026-01-15T00:00:00+00:00"
POST_CUTOFF = "2026-07-01T00:00:00+00:00"


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
    await ensure_opname_indexes(database)
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db):
    branch_id = f"br-{uuid.uuid4().hex[:8]}"
    warehouse_id = f"wh-{uuid.uuid4().hex[:8]}"
    item_id = f"item-{uuid.uuid4().hex[:8]}"
    await db[BRANCHES_COLLECTION].insert_one({
        "id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": warehouse_id, "branch_id": branch_id, "code": "MAIN",
        "name": "Main", "active": True,
    })
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": "MODE-B-CUP",
        "item_name": "Mode B Cup",
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
        "accurate_item_id": f"ACC-{item_id}",
    })
    await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 1000,
        "reference_type": "test",
        "reference_id": f"open-{item_id}",
        "reason": "Saldo awal",
        "idempotency_key": f"open-{item_id}",
    }, actor_id="tester")
    # Attributed open print order: 50
    await db.orders.insert_one({
        "id": f"so-attr-{uuid.uuid4().hex[:6]}",
        "branch_id": branch_id,
        "cancelled": False,
        "created_at": HISTORIC,
        "line_items": [{
            "master_item_id": item_id,
            "quantity": 50,
            "production_process": "print",
        }],
        "operator_splits": [],
    })
    # Historical null-branch print: 120
    await db.orders.insert_one({
        "id": f"so-null-hist-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "created_at": HISTORIC,
        "line_items": [{
            "master_item_id": item_id,
            "quantity": 120,
            "production_process": "print",
        }],
        "operator_splits": [],
    })
    # Post-cutoff null-branch print: 999 — must stay excluded
    await db.orders.insert_one({
        "id": f"so-null-new-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "created_at": POST_CUTOFF,
        "line_items": [{
            "master_item_id": item_id,
            "quantity": 999,
            "production_process": "print",
        }],
        "operator_splits": [],
    })
    return branch_id, warehouse_id, item_id


async def _opname_pesanan(db, branch_id, warehouse_id, item_id) -> float:
    lines = [{"master_item_id": item_id, "book_qty": 1000}]
    await _snapshot_order_buckets(
        db, None, lines, branch_id=branch_id, warehouse_id=warehouse_id,
    )
    return float(lines[0]["qty_pesanan"])


async def _catalog_pesanan(db, branch_id, warehouse_id, item_id) -> float:
    cat = await list_physical_stock_catalog(
        db, branch_id=branch_id, warehouse_id=warehouse_id, q="MODE-B-CUP",
    )
    row = next(
        (r for r in cat.get("items") or [] if r.get("master_item_id") == item_id),
        None,
    )
    assert row is not None
    return float(row["qty_pesanan"])


async def test_mode_b_off_excludes_null_branch_from_opname_and_catalog(db):
    branch_id, warehouse_id, item_id = await _seed(db)
    # Mode B default OFF
    opname = await _opname_pesanan(db, branch_id, warehouse_id, item_id)
    catalog = await _catalog_pesanan(db, branch_id, warehouse_id, item_id)
    assert opname == 50.0
    assert catalog == 50.0
    assert opname == catalog


async def test_mode_b_on_includes_historical_null_excludes_post_cutoff(db):
    branch_id, warehouse_id, item_id = await _seed(db)
    await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=branch_id,
        historical_cutoff_at=CUTOFF,
        include_null_branch=True,
        user={"id": "tester", "name": "Tester"},
    )
    opname = await _opname_pesanan(db, branch_id, warehouse_id, item_id)
    catalog = await _catalog_pesanan(db, branch_id, warehouse_id, item_id)
    # 50 attributed + 120 historical null; 999 post-cutoff null excluded
    assert opname == 170.0
    assert catalog == 170.0
    assert opname == catalog


async def test_mode_b_on_wrong_org_branch_keeps_null_excluded(db):
    branch_id, warehouse_id, item_id = await _seed(db)
    other = f"br-other-{uuid.uuid4().hex[:6]}"
    await db[BRANCHES_COLLECTION].insert_one({
        "id": other, "code": "SMG", "name": "Semarang", "active": True,
    })
    await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=other,
        historical_cutoff_at=CUTOFF,
        include_null_branch=True,
        user={"id": "tester", "name": "Tester"},
    )
    opname = await _opname_pesanan(db, branch_id, warehouse_id, item_id)
    catalog = await _catalog_pesanan(db, branch_id, warehouse_id, item_id)
    assert opname == 50.0
    assert catalog == 50.0


async def test_mode_b_does_not_rewrite_order_branch_ids(db):
    branch_id, warehouse_id, item_id = await _seed(db)
    await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=branch_id,
        historical_cutoff_at=CUTOFF,
        include_null_branch=True,
        user={"id": "tester", "name": "Tester"},
    )
    await _opname_pesanan(db, branch_id, warehouse_id, item_id)
    nulls = await db.orders.find(
        {"branch_id": {"$exists": False}},
        {"_id": 0, "id": 1, "branch_id": 1, "cabang_id": 1},
    ).to_list(20)
    assert len(nulls) >= 2
    for row in nulls:
        assert not row.get("branch_id")
        assert not row.get("cabang_id")
