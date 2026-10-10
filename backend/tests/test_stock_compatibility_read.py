"""Pre-cutover Stock Saat Ini compatibility READ adapter."""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_stock_compat_read"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    list_physical_stock_catalog,
)
from services.r2_go_live import (  # noqa: E402
    require_r2_authority_active,
    set_r2_authority_active,
)
from services.stock_compatibility_read import (  # noqa: E402
    BELUM_STATUS_CUTOVER_PENDING,
    BELUM_STATUS_READY,
    MODE_COMPATIBILITY,
    aggregate_legacy_delivered_qty,
    list_compatibility_stock_catalog,
)
import server  # noqa: E402
from server import app, current_user  # noqa: E402
from fastapi import HTTPException  # noqa: E402

TEST_DB = "eternal_packaging_test_stock_compat_read"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.db = database
    server.client = client
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_master(db, code="BOX"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "is_active": True,
    }
    await db.master_items.insert_one(dict(item))
    return item


async def test_compatibility_reads_product_stock_state_and_orders(db):
    item = await _seed_master(db)
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 15900,
        "initialized": True,
        "status": "INITIALIZED",
    })
    oid = f"so-{uuid.uuid4().hex[:8]}"
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 1000,
            "production_process": "print",
            "name": "BOX",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 400}],
        "qty_delivered": 0,
    })
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 150}],
    })

    catalog = await list_physical_stock_catalog(db)
    assert catalog["stock_read_mode"] == MODE_COMPATIBILITY
    assert catalog["r2_authority_active"] is False
    assert catalog["physical_qty_scope"] == "COMPANY"
    assert catalog["branch_filter_affects_physical_qty"] is False
    assert "per cabang" in (catalog.get("physical_qty_note") or "").lower() or "perusahaan" in (
        catalog.get("physical_qty_note") or ""
    ).lower()
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 15900
    assert row["qty_pesanan"] == 600  # 1000 - 400
    assert row["qty_belum_terkirim"] == 250  # 400 - 150
    assert row["qty_tersedia"] == 15900 - 600 - 250
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY
    # Must not have written R2
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


async def test_cancelled_order_excluded_from_qty_pesanan(db):
    item = await _seed_master(db, "CUP")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 100,
        "initialized": True,
        "status": "INITIALIZED",
    })
    await db.orders.insert_one({
        "id": f"so-c-{uuid.uuid4().hex[:6]}",
        "cancelled": True,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 500,
            "production_process": "print",
            "name": "CUP",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 500}],
    })
    row = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 0


async def test_fully_completed_and_delivered(db):
    item = await _seed_master(db, "LID")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 150,
        "initialized": True,
        "status": "INITIALIZED",
    })
    oid = f"so-{uuid.uuid4().hex[:8]}"
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 50,
            "production_process": "print",
            "name": "LID",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 50}],
        "qty_delivered": 50,
    })
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 50}],
    })
    row = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 0
    assert row["qty_tersedia"] == 150


async def test_unproven_delivery_lineage_is_cutover_pending(db):
    item_a = await _seed_master(db, "A")
    item_b = await _seed_master(db, "B")
    for item, qty in ((item_a, 500), (item_b, 500)):
        await db.product_stock_state.insert_one({
            "master_item_id": item["id"],
            "total_qty": qty,
            "initialized": True,
            "status": "INITIALIZED",
        })
    oid = f"so-multi-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-0", "master_item_id": item_a["id"], "quantity": 100, "production_process": "print", "name": "A"},
            {"line_id": "ln-1", "master_item_id": item_b["id"], "quantity": 100, "production_process": "print", "name": "B"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 80},
            {"line_idx": 1, "qty_completed": 80},
        ],
    })
    # Delivery lines without identity and without line_idx → unproven
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "is_cancelled": False,
        "line_items": [{"qty": 50}],
    })
    catalog = await list_compatibility_stock_catalog(db)
    for mid in (item_a["id"], item_b["id"]):
        row = next(r for r in catalog["items"] if r["master_item_id"] == mid)
        assert row["belum_terkirim_status"] == BELUM_STATUS_CUTOVER_PENDING
        assert row["qty_belum_terkirim"] is None
        assert row["qty_pesanan"] == 20  # 100-80 still proven


async def test_authority_switch_to_r2_stops_legacy_physical(db):
    item = await _seed_master(db)
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 999,
        "initialized": True,
        "status": "INITIALIZED",
    })
    compat = await list_physical_stock_catalog(db)
    assert next(r for r in compat["items"] if r["master_item_id"] == item["id"])["physical_qty"] == 999

    await set_r2_authority_active(db, active=True)
    r2 = await list_physical_stock_catalog(db)
    assert r2["stock_read_mode"] == "r2"
    assert r2["r2_authority_active"] is True
    assert r2["physical_qty_scope"] == "BRANCH"
    assert r2["branch_filter_affects_physical_qty"] is True
    # No R2 balances → physical 0 (legacy fallback stopped)
    row = next(r for r in r2["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 0


async def test_r2_mutations_blocked_while_authority_off(db):
    with pytest.raises(HTTPException) as exc:
        await require_r2_authority_active(db)
    assert exc.value.status_code == 409


async def test_http_catalog_compatibility_and_no_adjust(db):
    item = await _seed_master(db)
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 10,
        "initialized": True,
        "status": "INITIALIZED",
    })
    app.dependency_overrides[current_user] = lambda: {
        "id": "u1",
        "email": "admin@test.local",
        "role": "admin",
        "name": "Admin",
        "permissions_override": {},
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            res = await client.get("/api/physical-stock")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["stock_read_mode"] == MODE_COMPATIBILITY
        assert body["can_adjust_physical_stock"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_aggregate_legacy_delivered_single_line_fallback():
    orders = [{
        "id": "o1",
        "cancelled": False,
        "qty_delivered": 30,
        "line_items": [{"master_item_id": "m1", "quantity": 100}],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert delivered["m1"] == 30
    assert prov["m1"] == "order_qty_delivered"


async def test_non_print_demand_is_ready_without_operator_completed(db):
    """Non-print: siap kirim = PO qty; sisa undelivered → Belum; no fake completed writes."""
    item = await _seed_master(db, "NONP")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 1000,
        "initialized": True,
        "status": "INITIALIZED",
    })
    oid = f"so-np-{uuid.uuid4().hex[:8]}"
    order = {
        "id": oid,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 300,
            "production_process": "non_print",
            "name": "NONP",
        }],
        "operator_splits": [],
        "qty_packed": 0,
    }
    await db.orders.insert_one(dict(order))

    row = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 300
    assert row["qty_tersedia"] == 1000 - 300
    stored = await db.orders.find_one({"id": oid}, {"_id": 0})
    assert stored.get("operator_splits") in (None, [])
    assert stored.get("qty_packed") in (None, 0)

    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 100}],
    })
    row2 = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row2["qty_pesanan"] == 0
    assert row2["qty_belum_terkirim"] == 200
    assert row2["qty_tersedia"] == 1000 - 200


async def test_mixed_print_non_print_no_double_count(db):
    print_item = await _seed_master(db, "PRINTX")
    non_item = await _seed_master(db, "NONX")
    for item, qty in ((print_item, 500), (non_item, 500)):
        await db.product_stock_state.insert_one({
            "master_item_id": item["id"],
            "total_qty": qty,
            "initialized": True,
            "status": "INITIALIZED",
        })
    oid = f"so-mix-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-0", "master_item_id": print_item["id"], "quantity": 100, "production_process": "print", "name": "P"},
            {"line_id": "ln-1", "master_item_id": non_item["id"], "quantity": 80, "production_process": "non_print", "name": "N"},
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 40}],
    })
    catalog = await list_compatibility_stock_catalog(db)
    prow = next(r for r in catalog["items"] if r["master_item_id"] == print_item["id"])
    nrow = next(r for r in catalog["items"] if r["master_item_id"] == non_item["id"])
    assert prow["qty_pesanan"] == 60
    assert prow["qty_belum_terkirim"] == 40
    assert nrow["qty_pesanan"] == 0
    assert nrow["qty_belum_terkirim"] == 80


async def test_cancelled_non_print_excluded(db):
    item = await _seed_master(db, "CANC")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 50,
        "initialized": True,
        "status": "INITIALIZED",
    })
    await db.orders.insert_one({
        "id": f"so-canc-{uuid.uuid4().hex[:6]}",
        "cancelled": True,
        "line_items": [{
            "master_item_id": item["id"],
            "quantity": 40,
            "production_process": "non_print",
        }],
    })
    row = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 0


async def test_same_master_print_and_non_print_orders_independent(db):
    """Same master item on two orders: print uses operator; non_print siap kirim; no double-count."""
    item = await _seed_master(db, "DUAL")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 1000,
        "initialized": True,
        "status": "INITIALIZED",
    })
    await db.orders.insert_one({
        "id": f"so-print-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": item["id"],
            "quantity": 100,
            "production_process": "print",
            "name": "DUAL",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 40}],
    })
    await db.orders.insert_one({
        "id": f"so-np-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-n",
            "master_item_id": item["id"],
            "quantity": 80,
            "production_process": "non_print",
            "name": "DUAL",
        }],
        "operator_splits": [],
    })
    row = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    # print: pesanan 60 + belum 40; non_print: pesanan 0 + belum 80
    assert row["qty_pesanan"] == 60
    assert row["qty_belum_terkirim"] == 120
    assert row["qty_tersedia"] == 1000 - 60 - 120

    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": f"so-np-partial",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 30}],
    })
    # Legacy delivery attribution by master only — reduces belum once, not twice.
    row2 = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row2["qty_pesanan"] == 60
    assert row2["qty_belum_terkirim"] == 90
    assert row2["qty_tersedia"] == 1000 - 60 - 90
