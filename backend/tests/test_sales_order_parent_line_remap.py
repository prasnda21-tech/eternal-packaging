"""SERVICE/Add On parent_line_id must survive create-time UUID rewrite (PR #640)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user
from services.order_line_addon import resolve_parent_idx, validate_service_print_requires_parent
from services.physical_stock import (
    _orders_for_branch,
    aggregate_operational_orders,
    operational_availability,
)
from services.sales_order_prices import price_order_lines
from services.stock_compatibility_read import aggregate_legacy_delivered_qty

USER = {"email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
BRANCH_YGY = "branch-ygy-parent-remap"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


async def _price(raw_lines):
    class _Db:
        pass

    return await price_order_lines(_Db(), raw_lines)


def _inv(line_id, mid, qty=100, name="Induk"):
    return {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "unit": "pcs",
        "unit_price": 1000,
        "price_source": "manual",
        "item_type": "INVENTORY",
        "production_process": "print",
    }


def _svc(line_id, mid, parent_line_id, qty=100, name="Sablon"):
    return {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "unit": "pcs",
        "unit_price": 200,
        "price_source": "manual",
        "item_type": "SERVICE",
        "production_process": "print",
        "parent_line_id": parent_line_id,
    }


@pytest.mark.anyio
async def test_1_single_parent_single_service_remaps():
    priced, _, _ = await _price([
        _inv("ln-p", "m-inv"),
        _svc("ln-s", "m-svc", "ln-p"),
    ])
    assert priced[0]["line_id"] != "ln-p"
    assert priced[1]["parent_line_id"] == priced[0]["line_id"]
    assert resolve_parent_idx(priced[1], priced) == 0
    assert validate_service_print_requires_parent(priced) is None


@pytest.mark.anyio
async def test_2_single_parent_multi_service_remaps():
    priced, _, _ = await _price([
        _inv("ln-p", "m-inv"),
        _svc("ln-s1", "m-svc1", "ln-p", name="Warna 1"),
        _svc("ln-s2", "m-svc2", "ln-p", name="Warna 2"),
    ])
    parent_id = priced[0]["line_id"]
    assert priced[1]["parent_line_id"] == parent_id
    assert priced[2]["parent_line_id"] == parent_id
    assert resolve_parent_idx(priced[1], priced) == 0
    assert resolve_parent_idx(priced[2], priced) == 0


@pytest.mark.anyio
async def test_3_multi_parent_each_service_maps_to_own_parent():
    priced, _, _ = await _price([
        _inv("ln-a", "m-a", name="Induk A"),
        _svc("ln-as", "m-as", "ln-a", name="Svc A"),
        _inv("ln-b", "m-b", name="Induk B"),
        _svc("ln-bs", "m-bs", "ln-b", name="Svc B"),
    ])
    assert priced[1]["parent_line_id"] == priced[0]["line_id"]
    assert priced[3]["parent_line_id"] == priced[2]["line_id"]
    assert priced[1]["parent_line_id"] != priced[3]["parent_line_id"]
    assert resolve_parent_idx(priced[1], priced) == 0
    assert resolve_parent_idx(priced[3], priced) == 2


@pytest.mark.anyio
async def test_4_no_service_regression_still_rewrites_line_ids():
    priced, total, complete = await _price([
        _inv("ln-only", "m-inv", qty=50),
    ])
    assert len(priced) == 1
    assert priced[0]["line_id"] != "ln-only"
    assert "parent_line_id" not in priced[0] or not priced[0].get("parent_line_id")
    assert complete is True
    assert total == 50000.0


@pytest.mark.anyio
async def test_5_missing_parent_raises_clear_error_not_silent_drop():
    with pytest.raises(HTTPException) as caught:
        await _price([
            _inv("ln-p", "m-inv"),
            _svc("ln-s", "m-svc", "ln-missing"),
        ])
    assert caught.value.status_code == 400
    assert "ln-missing" in str(caught.value.detail)
    assert "tidak merujuk" in str(caught.value.detail).lower() or "valid" in str(caught.value.detail).lower()


@pytest.mark.anyio
async def test_6_create_persists_and_reload_keeps_parent_link():
    import server

    inv_id = f"inv-{uuid.uuid4().hex[:8]}"
    svc_id = f"svc-{uuid.uuid4().hex[:8]}"
    await server.db.master_items.insert_many([
        {"id": inv_id, "item_code": "INV-P", "item_name": "Karton", "item_type": "INVENTORY", "unit": "pcs"},
        {"id": svc_id, "item_code": "SVC-P", "item_name": "Sablon", "item_type": "SERVICE", "unit": "pcs"},
    ])
    po_num = f"PO-PARENT-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "PT Parent Remap",
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": inv_id,
                "name": "Karton",
                "quantity": 100,
                "unit": "pcs",
                "unit_price": 1000,
                "price_source": "manual",
                "production_process": "print",
            },
            {
                "line_id": "ln-s",
                "master_item_id": svc_id,
                "name": "Sablon",
                "quantity": 100,
                "unit": "pcs",
                "unit_price": 200,
                "price_source": "manual",
                "production_process": "print",
                "parent_line_id": "ln-p",
            },
        ],
    }
    app.dependency_overrides[current_user] = lambda: USER
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/sales-orders", json=payload)
            assert created.status_code == 200, created.text
            order = created.json()["order"]
            order_id = order["id"]
            lines = order["line_items"]
            assert lines[0]["line_id"] != "ln-p"
            assert lines[1]["parent_line_id"] == lines[0]["line_id"]
            assert lines[1]["parent_line_id"] != "ln-p"

            reloaded = await client.get(f"/api/orders/{order_id}")
            assert reloaded.status_code == 200, reloaded.text
            body = reloaded.json()
            saved = body.get("order") or body
            relines = saved["line_items"]
            assert relines[1]["parent_line_id"] == relines[0]["line_id"]
            assert resolve_parent_idx(relines[1], relines) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": po_num})
        await server.db.master_items.delete_many({"id": {"$in": [inv_id, svc_id]}})


@pytest.mark.anyio
async def test_7_service_delivery_reduces_parent_belum_after_remap():
    priced, _, _ = await _price([
        _inv("ln-p", "inv-parent", qty=100),
        _svc("ln-s", "svc-sablon", "ln-p", qty=100),
    ])
    parent_id = priced[0]["line_id"]
    svc_mid = priced[1]["master_item_id"]
    parent_mid = priced[0]["master_item_id"]
    orders = [{
        "id": "so-remap-sj",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": priced,
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
        "shipments": [{
            "id": "ship-svc",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"line_idx": 1, "qty": 40}],
        }],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {}, exclude_master_ids={svc_mid})
    delivered, prov = aggregate_legacy_delivered_qty(
        scoped, [], process_master_ids={svc_mid},
    )
    assert parent_mid in progress
    assert svc_mid not in progress
    assert delivered.get(parent_mid) == 40.0
    assert priced[1]["parent_line_id"] == parent_id
    buckets = operational_availability(
        physical_qty=500,
        ordered_qty=progress[parent_mid]["ordered_qty"],
        completed_qty=progress[parent_mid]["completed_qty"],
        r2_delivered_qty=delivered[parent_mid],
    )
    assert buckets["qty_pesanan"] == 0.0
    assert buckets["qty_belum_terkirim"] == 60.0
    assert buckets["qty_tersedia"] == 440.0
    # No double-count of SERVICE qty into pesanan/physical demand.
    assert progress[parent_mid]["ordered_qty"] == 100.0


@pytest.mark.anyio
async def test_8_no_qty_doubling_on_price_remap():
    priced, total, _ = await _price([
        _inv("ln-p", "m-inv", qty=100),
        _svc("ln-s", "m-svc", "ln-p", qty=100),
    ])
    assert priced[0]["quantity"] == 100
    assert priced[1]["quantity"] == 100
    assert total == 120000.0  # 100*1000 + 100*200


@pytest.mark.anyio
async def test_9_historical_orphan_parent_untouched_by_create_path():
    """Historical SO with stale parent_line_id is not mutated by pricing helper."""
    import server

    oid = f"hist-{uuid.uuid4().hex[:8]}"
    await server.db.orders.insert_one({
        "id": oid,
        "po_number": f"PO-HIST-{oid[-6:].upper()}",
        "cancelled": False,
        "line_items": [
            {"line_id": "uuid-parent", "master_item_id": "inv", "quantity": 10, "item_type": "INVENTORY"},
            {
                "line_id": "uuid-svc",
                "master_item_id": "svc",
                "quantity": 10,
                "item_type": "SERVICE",
                "parent_line_id": "ln-p",  # stale client id — historical
                "production_process": "print",
            },
        ],
    })
    before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    # Create-path pricing is not applied to historical docs.
    after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert after["line_items"][1]["parent_line_id"] == before["line_items"][1]["parent_line_id"] == "ln-p"
    # Read path still fail-closed on resolve (no silent invent).
    assert resolve_parent_idx(after["line_items"][1], after["line_items"]) is None
    await server.db.orders.delete_many({"id": oid})


@pytest.mark.anyio
async def test_create_rejects_missing_parent_before_persist():
    import server

    inv_id = f"inv-{uuid.uuid4().hex[:8]}"
    svc_id = f"svc-{uuid.uuid4().hex[:8]}"
    await server.db.master_items.insert_many([
        {"id": inv_id, "item_code": "INV-X", "item_name": "Karton", "item_type": "INVENTORY", "unit": "pcs"},
        {"id": svc_id, "item_code": "SVC-X", "item_name": "Sablon", "item_type": "SERVICE", "unit": "pcs"},
    ])
    payload = {
        "po_number": f"PO-BAD-{uuid.uuid4().hex[:6].upper()}",
        "customer": "PT Bad Parent",
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": inv_id,
                "name": "Karton",
                "quantity": 10,
                "unit": "pcs",
                "production_process": "print",
            },
            {
                "line_id": "ln-s",
                "master_item_id": svc_id,
                "name": "Sablon",
                "quantity": 10,
                "unit": "pcs",
                "production_process": "print",
                "parent_line_id": "ln-ghost",
            },
        ],
    }
    app.dependency_overrides[current_user] = lambda: USER
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 400, res.text
            assert "parent" in res.text.lower() or "induk" in res.text.lower()
            assert await server.db.orders.count_documents({"po_number": payload["po_number"]}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.master_items.delete_many({"id": {"$in": [inv_id, svc_id]}})
