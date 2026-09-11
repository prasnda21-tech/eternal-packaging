"""Focused tests: PATCH /orders/{id}/info — Edit Informasi PO."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]


def db():
    return server_mod.db


def _admin():
    return {
        "id": "u-admin-info",
        "email": "admin@eternalpackaging.com",
        "role": "admin",
        "name": "Admin",
    }


def _viewer():
    return {
        "id": "u-viewer-info",
        "email": "viewer-info@eternalpackaging.com",
        "role": "crm",
        "name": "Viewer",
        "permissions_override": {"po.edit": False, "po.view": True},
    }


async def _seed_order(**extra):
    oid = f"ord-info-{uuid.uuid4().hex[:8]}"
    po = f"PO-INFO-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": oid,
        "po_number": po,
        "customer": "Fixture Customer Info",
        "outlet": "Outlet A",
        "recipient": "Budi",
        "sales": "Sales A",
        "address": "Jl. Lama No. 1",
        "deadline": "2026-09-10T00:00:00+00:00",
        "estimated_delivery": "2026-09-12T00:00:00+00:00",
        "expedition": "Internal",
        "delivery_zone": "Jabodetabek",
        "payment_terms": "NET 14",
        "payment_status": "Waiting",
        "design_status": "Waiting",
        "accurate_id": "ACC-INFO-1",
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "cancelled": False,
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "print"}],
        "total_quantity": 100,
        "qty_delivered": 0,
        "packing_info": {"packed_qty": 10},
        "loading_info": {"loaded_qty": 5},
        "delivery_status": "Belum Terkirim",
        "delivery_location": {"lat": -6.2, "lng": 106.8, "formatted_address": "Geo Lama"},
        "delivery_outlet_id": "outlet-geo-1",
        "delivery_deadline_manual": "2026-09-11T03:00:00+00:00",
        "timeline": [],
        "operator_splits": [],
        "sample_files": [],
        "shipments": [],
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    doc.update(extra)
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


@pytest.mark.anyio
async def test_update_order_info_requires_po_edit():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _viewer
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={
                    "address": "Jl. Ditolak",
                    "deadline": "2026-09-20",
                    "estimated_delivery": "2026-09-19",
                },
            )
        assert r.status_code == 403
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Lama No. 1"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_persists_canonical_fields_only():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={
                    "address": "Jl. Baru No. 9",
                    "deadline": "2026-09-20",
                    "estimated_delivery": "2026-09-19",
                    "customer": "HACK",
                    "current_stage": 14,
                    "qty_delivered": 999,
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()["order"]
        assert body["address"] == "Jl. Baru No. 9"
        assert body["deadline"].startswith("2026-09-20")
        assert body["estimated_delivery"].startswith("2026-09-19")

        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Baru No. 9"
        assert fresh["deadline"].startswith("2026-09-20")
        assert fresh["estimated_delivery"].startswith("2026-09-19")

        # CASE 7 / 8 — unrelated fields + workflow untouched
        assert fresh["customer"] == "Fixture Customer Info"
        assert fresh["outlet"] == "Outlet A"
        assert fresh["recipient"] == "Budi"
        assert fresh["sales"] == "Sales A"
        assert fresh["expedition"] == "Internal"
        assert fresh["payment_terms"] == "NET 14"
        assert fresh["payment_status"] == "Waiting"
        assert fresh["design_status"] == "Waiting"
        assert fresh["accurate_id"] == "ACC-INFO-1"
        assert fresh["current_stage"] == 5
        assert fresh["qty_delivered"] == 0
        assert (fresh.get("packing_info") or {}).get("packed_qty") == 10
        assert (fresh.get("loading_info") or {}).get("loaded_qty") == 5
        assert fresh["delivery_status"] == "Belum Terkirim"
        assert fresh["delivery_location"]["lat"] == -6.2
        assert fresh["delivery_outlet_id"] == "outlet-geo-1"
        assert fresh["delivery_deadline_manual"] == "2026-09-11T03:00:00+00:00"
        assert fresh["line_items"][0]["quantity"] == 100
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_address_only_partial():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"address": "Alamat Partial Saja"},
            )
        assert r.status_code == 200, r.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Alamat Partial Saja"
        assert fresh["deadline"] == "2026-09-10T00:00:00+00:00"
        assert fresh["estimated_delivery"] == "2026-09-12T00:00:00+00:00"
        assert fresh["current_stage"] == 5
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_rejects_empty_deadline():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"deadline": ""},
            )
        assert r.status_code == 400
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["deadline"] == "2026-09-10T00:00:00+00:00"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_cancelled_blocked():
    order = await _seed_order(cancelled=True, cancel_reason="x")
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"address": "Should fail"},
            )
        assert r.status_code in (400, 409)
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Lama No. 1"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})
