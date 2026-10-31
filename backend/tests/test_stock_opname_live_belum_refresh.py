"""Operational Opname GET recomputes Belum after SERVICE parent link (no session cancel)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    WAREHOUSES_COLLECTION,
    ensure_indexes as ensure_physical_indexes,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active
from services.stock_opname_sessions import (
    MODE_OPERATIONAL,
    create_stock_opname_session,
    ensure_indexes,
    get_stock_opname_session,
    start_stock_opname_cutoff,
)
from services.stock_compatibility_read import BELUM_STATUS_READY, BELUM_STATUS_UNVERIFIED

TEST_DB = "eternal_packaging_test_opname_live_belum"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "opname-live-belum-tester", "role": "admin", "permissions": ["master_items.view"]}


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


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


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


async def _item(db, *, code, name, item_type="INVENTORY"):
    item_id = str(uuid.uuid4())
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": name,
        "unit": "PCS",
        "is_active": True,
        "item_type": item_type,
        "accurate_item_id": f"LOCAL-{item_id}",
    })
    return item_id


async def test_get_session_refreshes_belum_after_parent_link(db):
    branch_id, warehouse_id = await _location(db)
    print_mid = await _item(db, code="P100", name="Cup print")
    svc_mid = await _item(db, code="SABLON", name="Sablon", item_type="SERVICE")
    open_at = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    await post_movement(db, {
        "master_item_id": print_mid,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 1000,
        "reference_type": "test",
        "reference_id": f"open-{print_mid}",
        "reason": "Saldo awal",
        "idempotency_key": f"open-{print_mid}",
        "occurred_at": open_at,
    }, actor_id=USER["id"])

    order_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": order_id,
        "po_number": "PO/TEST/BELUM/001",
        "branch_id": branch_id,
        "cancelled": False,
        "current_stage": 10,
        "line_items": [
            {
                "line_id": "L0",
                "master_item_id": print_mid,
                "item_code": "P100",
                "item_type": "INVENTORY",
                "production_process": "print",
                "quantity": 500,
            },
            {
                "line_id": "L1",
                "master_item_id": svc_mid,
                "item_code": "SABLON",
                "item_type": "SERVICE",
                "production_process": "print",
                "quantity": 500,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            {"line_idx": 1, "qty_completed": 500},
        ],
    })

    created = await create_stock_opname_session(db, {
        "warehouse_id": warehouse_id,
        "branch_id": branch_id,
        "mode": MODE_OPERATIONAL,
    }, USER)
    session = await start_stock_opname_cutoff(db, created["id"], USER)
    assert session["status"] == "COUNTING"
    line = next(l for l in session["lines"] if l["master_item_id"] == print_mid)
    assert line.get("belum_terkirim_status") == BELUM_STATUS_UNVERIFIED
    assert line.get("qty_belum_terkirim") is None

    await db.orders.update_one(
        {"id": order_id},
        {"$set": {
            "line_items.1.parent_line_idx": 0,
            "line_items.1.parent_line_id": "L0",
        }},
    )

    fresh = await get_stock_opname_session(db, session["id"], user=USER)
    line2 = next(l for l in fresh["lines"] if l["master_item_id"] == print_mid)
    assert line2.get("belum_terkirim_status") == BELUM_STATUS_READY
    assert line2.get("qty_belum_terkirim") is not None

    raw = await db.stock_opname_sessions.find_one({"id": session["id"]}, {"_id": 0})
    raw_line = next(l for l in raw["lines"] if l["master_item_id"] == print_mid)
    assert raw_line.get("belum_terkirim_status") == BELUM_STATUS_UNVERIFIED
