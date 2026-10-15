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
    aggregate_operational_orders,
    list_physical_stock_catalog,
)
from services.r2_go_live import (  # noqa: E402
    require_r2_authority_active,
    set_r2_authority_active,
)
from services.stock_compatibility_read import (  # noqa: E402
    BELUM_STATUS_CUTOVER_PENDING,
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
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


async def _seed_master(db, code="BOX", *, item_type="INVENTORY"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": item_type,
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
    # Created SJ (SELESAI allocation) without confirmed delivery must not reduce Belum.
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "status": "SELESAI",
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
    assert row["qty_belum_terkirim"] == 400  # completed only; SJ create ≠ delivered
    assert row["qty_tersedia"] == 15900 - 600 - 400
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY
    assert row["qty_tersedia_valid"] is False
    assert row["physical_qty_warehouse_valid"] is False
    assert row["baseline_status"] == "unreconciled"
    assert catalog.get("physical_qty_warehouse_valid") is False
    # Must not have written R2
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0

    # Confirmed shipment proves delivery → Belum drops by proven qty only.
    await db.orders.update_one(
        {"id": oid},
        {"$set": {
            "shipments": [{
                "id": "ship-confirmed",
                "sales_delivery_id": "ship-confirmed",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 150,
                "qty_delivered": 150,
                "lines": [{"line_idx": 0, "qty": 150}],
            }],
        }},
    )
    row2 = next(
        r for r in (await list_physical_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row2["qty_belum_terkirim"] == 250  # 400 - 150 proven


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
    # Created SJ without confirmation does not invent delivery; Belum stays completed.
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": oid,
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"qty": 50}],
    })
    catalog = await list_compatibility_stock_catalog(db)
    for mid in (item_a["id"], item_b["id"]):
        row = next(r for r in catalog["items"] if r["master_item_id"] == mid)
        assert row["belum_terkirim_status"] == BELUM_STATUS_READY
        assert row["qty_belum_terkirim"] == 80
        assert row["qty_pesanan"] == 20  # 100-80 still proven

    # Proven multi-line confirmation without line_idx → unproven / BELUM TERVERIFIKASI
    await db.orders.update_one(
        {"id": oid},
        {"$set": {
            "shipments": [{
                "id": "sj-ambig",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 50,
                "lines": [{"qty": 50}],
            }],
        }},
    )
    catalog2 = await list_compatibility_stock_catalog(db)
    for mid in (item_a["id"], item_b["id"]):
        row = next(r for r in catalog2["items"] if r["master_item_id"] == mid)
        assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
        assert row["belum_terkirim_status"] == BELUM_STATUS_CUTOVER_PENDING  # alias
        assert row["qty_belum_terkirim"] is None
        assert row["qty_tersedia_valid"] is False
        assert row["physical_qty_warehouse_valid"] is False


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


async def test_unassigned_splits_not_attributed_when_multiple_print_candidates():
    """Regression: SERVICE print lines remain candidates — do not dump unassigned onto INVENTORY."""
    orders = [{
        "id": "o-unassigned",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "inv-1", "quantity": 1000, "production_process": "print"},
            {"master_item_id": "svc-1", "quantity": 1000, "production_process": "print"},
        ],
        # Ambiguous color progress without line_idx must not become inventory completed.
        "operator_splits": [{"qty_completed": 1000}],
    }]
    progress = aggregate_operational_orders(
        orders, exclude_master_ids={"svc-1"},
    )
    slot = progress["inv-1"]
    assert slot["ordered_qty"] == 1000
    assert slot["completed_qty"] == 0
    assert slot.get("completion_unproven") is True


async def test_case_a_proven_plus_ambiguous_is_unverified_not_ready():
    """Regression: partial confirmed must not keep READY when another ship is ambiguous."""
    orders = [{
        "id": "o-mix",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "m1", "quantity": 100},
            {"master_item_id": "m2", "quantity": 100},
        ],
        "shipments": [
            {
                "id": "ship-ok",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 40,
                "lines": [{"line_idx": 0, "qty": 40}],
            },
            {
                "id": "ship-ambig",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-02T00:00:00+00:00",
                "total_qty": 20,
                "lines": [{"qty": 20}],
            },
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert delivered.get("m1") == 40  # proven qty retained for audit
    assert prov["m1"] == "unproven"
    assert prov["m2"] == "unproven"


async def test_case_b_qty_delivered_fallback_is_per_po_not_global_master():
    """Regression: PO2 qty_delivered must apply even if PO1 already confirmed same master."""
    orders = [
        {
            "id": "po1",
            "cancelled": False,
            "qty_delivered": 0,
            "line_items": [{"master_item_id": "m1", "quantity": 100}],
            "shipments": [{
                "id": "s1",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 50,
                "lines": [{"line_idx": 0, "qty": 50}],
            }],
        },
        {
            "id": "po2",
            "cancelled": False,
            "qty_delivered": 30,
            "line_items": [{"master_item_id": "m1", "quantity": 80}],
            "shipments": [],
        },
    ]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert delivered["m1"] == 80  # 50 confirmed + 30 fallback on other PO
    assert prov["m1"] == "confirmed_shipment"


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
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 100}],
    })
    row2 = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row2["qty_pesanan"] == 0
    # SJ create alone does not prove delivery
    assert row2["qty_belum_terkirim"] == 300
    assert row2["qty_tersedia"] == 1000 - 300


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

    np_doc = await db.orders.find_one(
        {"line_items.production_process": "non_print", "line_items.master_item_id": item["id"]},
        {"_id": 0, "id": 1},
    )
    assert np_doc
    np_oid = np_doc["id"]
    await db.orders.update_one(
        {"id": np_oid},
        {"$set": {
            "shipments": [{
                "id": "sj-np-30",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 30,
                "qty_delivered": 30,
                "lines": [{"line_idx": 0, "qty": 30}],
            }],
        }},
    )
    row2 = next(
        r for r in (await list_compatibility_stock_catalog(db))["items"]
        if r["master_item_id"] == item["id"]
    )
    assert row2["qty_pesanan"] == 60
    assert row2["qty_belum_terkirim"] == 90  # 120 - 30 proven on non_print order
    assert row2["qty_tersedia"] == 1000 - 60 - 90


async def test_fase1_scenario_matrix_po_prod_sj_partial_cancel_multi(db):
    """FASE 1 matrix: PO partial, prod partial, SJ unproven, partial ship, cancel, multi-item."""
    lid = await _seed_master(db, "LID")
    cup = await _seed_master(db, "CUP")
    for item, qty in ((lid, 2000), (cup, 2000)):
        await db.product_stock_state.insert_one({
            "master_item_id": item["id"],
            "total_qty": qty,
            "initialized": True,
            "status": "INITIALIZED",
        })

    # Active multi-item PO: print LID partial complete; CUP non_print siap kirim.
    active_oid = f"so-fase1-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": active_oid,
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-0",
                "master_item_id": lid["id"],
                "quantity": 500,
                "production_process": "print",
                "name": "LID",
            },
            {
                "line_id": "ln-1",
                "master_item_id": cup["id"],
                "quantity": 200,
                "production_process": "non_print",
                "name": "CUP",
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 300}],
    })
    # SJ created SELESAI — allocation only, not customer proof.
    await db.sales_deliveries.insert_one({
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": active_oid,
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [
            {"line_idx": 0, "master_item_id": lid["id"], "qty": 100},
            {"line_idx": 1, "master_item_id": cup["id"], "qty": 50},
        ],
    })
    # Cancelled PO must not contribute.
    await db.orders.insert_one({
        "id": f"so-canc-{uuid.uuid4().hex[:6]}",
        "cancelled": True,
        "line_items": [{
            "master_item_id": lid["id"],
            "quantity": 999,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 999}],
    })

    catalog = await list_compatibility_stock_catalog(db)
    lid_row = next(r for r in catalog["items"] if r["master_item_id"] == lid["id"])
    cup_row = next(r for r in catalog["items"] if r["master_item_id"] == cup["id"])

    # LID: ordered 500, completed 300 → pesanan 200, belum 300 (SJ not proof)
    assert lid_row["qty_pesanan"] == 200
    assert lid_row["qty_belum_terkirim"] == 300
    assert lid_row["belum_terkirim_status"] == BELUM_STATUS_READY
    # CUP non_print: siap kirim = ordered
    assert cup_row["qty_pesanan"] == 0
    assert cup_row["qty_belum_terkirim"] == 200
    # Compatibility never claims warehouse-valid physical / Qty Tersedia
    for row in (lid_row, cup_row):
        assert row["physical_qty_warehouse_valid"] is False
        assert row["qty_tersedia_valid"] is False
        assert row["physical_qty_scope"] == "COMPANY"

    # Partial proven shipment on LID only (line-attributed).
    await db.orders.update_one(
        {"id": active_oid},
        {"$set": {
            "shipments": [{
                "id": "ship-lid-100",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-02T00:00:00+00:00",
                "total_qty": 100,
                "lines": [{"line_idx": 0, "qty": 100}],
            }],
        }},
    )
    catalog2 = await list_compatibility_stock_catalog(db)
    lid2 = next(r for r in catalog2["items"] if r["master_item_id"] == lid["id"])
    cup2 = next(r for r in catalog2["items"] if r["master_item_id"] == cup["id"])
    assert lid2["qty_pesanan"] == 200
    assert lid2["qty_belum_terkirim"] == 200  # 300 - 100 proven
    assert cup2["qty_belum_terkirim"] == 200  # unchanged; no CUP proof

    # Ambiguous multi-SKU partial confirmation without line breakdown → BELUM TERVERIFIKASI
    await db.orders.update_one(
        {"id": active_oid},
        {"$set": {
            "shipments": [{
                "id": "ship-ambig-40",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-03T00:00:00+00:00",
                "total_qty": 40,
                "lines": [{"qty": 40}],
            }],
        }},
    )
    catalog3 = await list_compatibility_stock_catalog(db)
    for mid in (lid["id"], cup["id"]):
        row = next(r for r in catalog3["items"] if r["master_item_id"] == mid)
        assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
        assert row["qty_belum_terkirim"] is None
        assert row["qty_tersedia_valid"] is False


async def test_fase1_addon_service_matrix_and_attribution(db):
    """Add On proses = SERVICE; physical demand stays on INVENTORY parent qty.

    Schema fact: no parent_line_id. Explicit signal is master_items.item_type=SERVICE
    (Accurate mock: SVC-CETAK / Jasa cetak). Colors are process work, not +pcs.
    """
    physical = await _seed_master(db, "BARANG-A", item_type="INVENTORY")
    warna2 = await _seed_master(db, "ADDON-W2", item_type="SERVICE")
    warna3 = await _seed_master(db, "ADDON-W3", item_type="SERVICE")
    await db.product_stock_state.insert_one({
        "master_item_id": physical["id"],
        "total_qty": 5000,
        "initialized": True,
        "status": "INITIALIZED",
    })
    oid = f"so-addon-{uuid.uuid4().hex[:6]}"
    linked_lines = [
        {
            "line_id": "ln-main",
            "master_item_id": physical["id"],
            "quantity": 1000,
            "production_process": "print",
            "name": "Barang A warna 1",
            "color": "PUTIH",
        },
        {
            "line_id": "ln-w2",
            "master_item_id": warna2["id"],
            "parent_line_id": "ln-main",
            "quantity": 1000,
            "production_process": "print",
            "name": "Add On warna 2",
            "color": "HIJAU",
        },
        {
            "line_id": "ln-w3",
            "master_item_id": warna3["id"],
            "parent_line_id": "ln-main",
            "quantity": 1000,
            "production_process": "print",
            "name": "Add On warna 3",
            "color": "PINK",
        },
    ]
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": linked_lines,
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 0},
            {"line_idx": 1, "qty_completed": 0},
            {"line_idx": 2, "qty_completed": 0},
        ],
    })

    # (1) Physical pesanan = 1000, not 3000; SERVICE absent from catalog
    catalog = await list_compatibility_stock_catalog(db)
    mids = {r["master_item_id"] for r in catalog["items"]}
    assert physical["id"] in mids
    assert warna2["id"] not in mids
    assert warna3["id"] not in mids
    row = next(r for r in catalog["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 1000
    assert row["qty_belum_terkirim"] == 0
    assert row["qty_tersedia_valid"] is False

    # (1b) Without parent_line_id → fail-closed even if warna1 complete
    await db.orders.update_one({"id": oid}, {"$set": {
        "line_items": [
            {**linked_lines[0]},
            {k: v for k, v in linked_lines[1].items() if k != "parent_line_id"},
            {k: v for k, v in linked_lines[2].items() if k != "parent_line_id"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 0},
            {"line_idx": 2, "qty_completed": 0},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None

    # Restore explicit parent links for remaining matrix.
    await db.orders.update_one({"id": oid}, {"$set": {"line_items": linked_lines}})

    # (2) Warna1 complete; warna2/3 unfinished — fail-closed: not siap (stay in pesanan)
    await db.orders.update_one({"id": oid}, {"$set": {
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 0},
            {"line_idx": 2, "qty_completed": 0},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 1000
    assert row["qty_belum_terkirim"] == 0
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (3) Warna1+2 done; warna3 unfinished — still not FULLY_COMPLETED
    await db.orders.update_one({"id": oid}, {"$set": {
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 0},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 1000
    assert row["qty_belum_terkirim"] == 0
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (4) All processes complete — FULLY_COMPLETED physical siap 1000 (not 3000)
    await db.orders.update_one({"id": oid}, {"$set": {
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 1000},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 1000
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (5) Partial different progress — fail-closed (no READY via min())
    await db.orders.update_one({"id": oid}, {"$set": {
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 400},
            {"line_idx": 1, "qty_completed": 700},
            {"line_idx": 2, "qty_completed": 200},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 1000
    assert row["qty_belum_terkirim"] == 0
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (6) Cancelled SERVICE Add On ignored for process gate → inventory-only READY again
    await db.orders.update_one({"id": oid}, {"$set": {
        "operator_splits": [{"line_idx": 0, "qty_completed": 400}],
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": physical["id"],
                "quantity": 1000,
                "production_process": "print",
                "name": "Barang A warna 1",
            },
            {
                "line_id": "ln-w2",
                "master_item_id": warna2["id"],
                "quantity": 1000,
                "production_process": "print",
                "name": "Add On warna 2",
                "cancelled": True,
            },
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 600
    assert row["qty_belum_terkirim"] == 400
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (7) Second PO same physical master — independent demand
    await db.orders.insert_one({
        "id": f"so-addon-b-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "line_items": [{
            "master_item_id": physical["id"],
            "quantity": 200,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
        "qty_delivered": 0,
    })
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 800  # 600 + 200
    assert row["qty_belum_terkirim"] == 400
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (8) Proven + ambiguous together → BELUM TERVERIFIKASI
    await db.orders.update_one({"id": oid}, {"$set": {
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": physical["id"],
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "ln-other",
                "master_item_id": (await _seed_master(db, "OTHER", item_type="INVENTORY"))["id"],
                "quantity": 100,
                "production_process": "print",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 50},
        ],
        "shipments": [
            {
                "id": "ship-ok",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 100,
                "lines": [{"line_idx": 0, "qty": 100}],
            },
            {
                "id": "ship-ambig",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-02T00:00:00+00:00",
                "total_qty": 40,
                "lines": [{"qty": 40}],
            },
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None

    # Reset to clean single-line PO for remaining delivery scenarios
    await db.orders.delete_many({})
    await db.orders.insert_one({
        "id": oid,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-main",
            "master_item_id": physical["id"],
            "quantity": 1000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 1000}],
        "shipments": [],
    })
    await db.sales_deliveries.delete_many({})

    # (9) Partial proven ship after process complete
    await db.orders.update_one({"id": oid}, {"$set": {
        "shipments": [{
            "id": "ship-partial",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-01-04T00:00:00+00:00",
            "total_qty": 250,
            "lines": [{"line_idx": 0, "qty": 250}],
        }],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 750
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY

    # (10) SJ SELESAI without proof — Belum stays completed
    await db.orders.update_one({"id": oid}, {"$set": {"shipments": []}})
    await db.sales_deliveries.insert_one({
        "id": "sj-alloc",
        "po_id": oid,
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": physical["id"], "qty": 1000}],
    })
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_belum_terkirim"] == 1000

    # (11) Physical Add On (INVENTORY) vs process Add On (SERVICE)
    extra_phys = await _seed_master(db, "ADDON-PHYS", item_type="INVENTORY")
    await db.product_stock_state.insert_one({
        "master_item_id": extra_phys["id"],
        "total_qty": 100,
        "initialized": True,
        "status": "INITIALIZED",
    })
    await db.orders.update_one({"id": oid}, {"$set": {
        "line_items": [
            {
                "master_item_id": physical["id"],
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "master_item_id": warna2["id"],
                "quantity": 1000,
                "production_process": "print",
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 1000}],
        "shipments": [],
    }})
    # Physical-goods Add On on its own PO (explicit INVENTORY) — counts separately.
    await db.orders.insert_one({
        "id": f"so-phys-addon-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "line_items": [{
            "master_item_id": extra_phys["id"],
            "quantity": 50,
            "production_process": "non_print",
        }],
        "operator_splits": [],
    })
    catalog = await list_compatibility_stock_catalog(db)
    prow = next(r for r in catalog["items"] if r["master_item_id"] == physical["id"])
    xrow = next(r for r in catalog["items"] if r["master_item_id"] == extra_phys["id"])
    assert warna2["id"] not in {r["master_item_id"] for r in catalog["items"]}
    assert prow["qty_pesanan"] == 0
    assert prow["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert prow["qty_belum_terkirim"] is None  # SERVICE process on PO — not READY 1000
    assert xrow["qty_pesanan"] == 0
    assert xrow["qty_belum_terkirim"] == 50
    assert xrow["belum_terkirim_status"] == BELUM_STATUS_READY

    # (12) Incomplete parent–Add On relation: orphan print line without master → unverified
    await db.orders.update_one({"id": oid}, {"$set": {
        "line_items": [
            {
                "master_item_id": physical["id"],
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "name": "Add On Warna misterius",
                "quantity": 1000,
                "production_process": "print",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            {"line_idx": 1, "qty_completed": 1000},
        ],
    }})
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    # No invented link: inventory pesanan from proven line_idx; siap kirim fail-closed
    assert row["qty_pesanan"] == 500
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None

    # (13) Cancelled PO / cancelled SJ — no double count; orphan process still blocks siap
    await db.orders.insert_one({
        "id": "po-canc-addon",
        "cancelled": True,
        "line_items": [{
            "master_item_id": physical["id"],
            "quantity": 999,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 999}],
        "shipments": [{
            "id": "ship-canc",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-01-05T00:00:00+00:00",
            "total_qty": 999,
            "lines": [{"line_idx": 0, "qty": 999}],
        }],
    })
    await db.sales_deliveries.insert_one({
        "id": "sj-canc",
        "po_id": oid,
        "status": "SELESAI",
        "is_cancelled": True,
        "line_items": [{"line_idx": 0, "qty": 500}],
    })
    row = next(r for r in (await list_compatibility_stock_catalog(db))["items"] if r["master_item_id"] == physical["id"])
    assert row["qty_pesanan"] == 500
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None
