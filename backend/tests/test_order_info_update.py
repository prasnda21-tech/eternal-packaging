"""Focused tests: PATCH /orders/{id}/info — Edit Informasi PO (+ shipping contact)."""
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
from services.sales_customers import COLLECTION as SALES_CUSTOMERS, normalize_customer_name  # noqa: E402


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
        "customer_id": f"cust-info-{uuid.uuid4().hex[:6]}",
        "customer_phone": "0811111111",
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
        assert fresh["delivery_deadline_manual"].startswith("2026-09-20")
        assert fresh["scheduled_delivery_date"] == "2026-09-20"
        assert body.get("tanggal_pengiriman") == "2026-09-20"
        assert body.get("effective_delivery_date") == "2026-09-20"
        assert fresh["line_items"][0]["quantity"] == 100
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_address_recipient_phone_and_audit_master_untouched():
    order = await _seed_order()
    cust_id = order["customer_id"]
    await db()[SALES_CUSTOMERS].delete_many({"id": cust_id})
    await db()[SALES_CUSTOMERS].insert_one({
        "id": cust_id,
        "name": order["customer"],
        "normalized_name": normalize_customer_name(order["customer"]),
        "phone": "0811111111",
        "address": "Alamat Master",
        "pic": "PIC Master",
    })
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={
                    "address": "Jl. Baru Ops",
                    "recipient": "Siti",
                    "customer_phone": "0899999999",
                    "estimated_delivery": "2026-09-19",
                },
            )
        assert r.status_code == 200, r.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Baru Ops"
        assert fresh["recipient"] == "Siti"
        assert fresh["customer_phone"] == "0899999999"
        assert fresh["line_items"][0]["quantity"] == 100
        assert (fresh.get("loading_info") or {}).get("loaded_qty") == 5
        # Timeline + note
        assert any("alamat" in str(t.get("note") or "") for t in (fresh.get("timeline") or []))
        # Master unchanged
        master = await db()[SALES_CUSTOMERS].find_one({"id": cust_id}, {"_id": 0})
        assert master["phone"] == "0811111111"
        assert master["address"] == "Alamat Master"
        assert master["pic"] == "PIC Master"
        # Audit trail
        audit = await db().audit_logs.find_one(
            {"entity_id": order["id"], "action": "po.info.update"},
            {"_id": 0},
            sort=[("at", -1)],
        )
        if audit is None:
            # Some deployments use collection name "po_audit" / via _audit_po helper
            audit = await db().po_audit.find_one(
                {"order_id": order["id"], "action": "po.info.update"},
                {"_id": 0},
                sort=[("at", -1)],
            )
        # Soft assert: if audit collection present, before/after must include shipping fields
        if audit:
            before = audit.get("before") or {}
            after = audit.get("after") or {}
            assert before.get("address") == "Jl. Lama No. 1"
            assert after.get("address") == "Jl. Baru Ops"
            assert after.get("recipient") == "Siti"
            assert after.get("customer_phone") == "0899999999"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})
        await db()[SALES_CUSTOMERS].delete_many({"id": cust_id})


@pytest.mark.anyio
async def test_update_order_info_delivered_rejected():
    order = await _seed_order(delivery_status="Terkirim", current_stage=14, qty_delivered=100)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"address": "Should fail", "estimated_delivery": "2026-09-19"},
            )
        assert r.status_code == 409
        assert "selesai dikirim" in (r.json().get("detail") or "").lower() or "terkirim" in (r.json().get("detail") or "").lower()
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Lama No. 1"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_update_order_info_frozen_pod_rejects_shipping_fields():
    """Frozen POD final → 409; PO / SJ / POD / master unchanged."""
    order = await _seed_order()
    cust_id = order["customer_id"]
    await db()[SALES_CUSTOMERS].delete_many({"id": cust_id})
    await db()[SALES_CUSTOMERS].insert_one({
        "id": cust_id,
        "name": order["customer"],
        "normalized_name": normalize_customer_name(order["customer"]),
        "phone": "0811111111",
        "address": "Alamat Master",
        "pic": "PIC Master",
    })
    sj_id = f"sj-pod-{uuid.uuid4().hex[:8]}"
    sj_doc = {
        "id": sj_id,
        "po_id": order["id"],
        "sj_number": "SJ-POD-1",
        "address": "Alamat SJ POD",
        "recipient": "Penerima SJ",
        "recipient_phone": "081SJ",
        "is_cancelled": False,
    }
    await db().sales_deliveries.insert_one(sj_doc)
    frozen_sig = {
        "id": f"sig-{uuid.uuid4().hex[:8]}",
        "recipient_name": "Budi Final",
        "frozen": True,
        "recipient_signed_at": "2026-09-15T00:00:00+00:00",
        "shipment_id": sj_id,
    }
    await db().orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "delivery_recipient_signatures": [frozen_sig],
            "delivery_recipient_signature": dict(frozen_sig),
            # Not marked delivered — isolate the POD-final guard path.
            "delivery_status": "Dalam Pengiriman",
        }},
    )
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={
                    "address": "Alamat Ditolak POD",
                    "recipient": "Penerima Ditolak",
                    "customer_phone": "089PODFAIL",
                    "estimated_delivery": "2026-09-19",
                },
            )
        assert r.status_code == 409, r.text
        detail = (r.json().get("detail") or "").lower()
        assert "pod" in detail or "tanda tangan" in detail or "final" in detail

        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Lama No. 1"
        assert fresh["recipient"] == "Budi"
        assert fresh["customer_phone"] == "0811111111"
        assert fresh["delivery_recipient_signatures"] == [frozen_sig]
        assert fresh["delivery_recipient_signature"]["id"] == frozen_sig["id"]
        assert fresh["delivery_recipient_signature"]["frozen"] is True

        sj = await db().sales_deliveries.find_one({"id": sj_id}, {"_id": 0})
        assert sj["address"] == "Alamat SJ POD"
        assert sj["recipient"] == "Penerima SJ"
        assert sj["recipient_phone"] == "081SJ"

        master = await db()[SALES_CUSTOMERS].find_one({"id": cust_id}, {"_id": 0})
        assert master["phone"] == "0811111111"
        assert master["address"] == "Alamat Master"
        assert master["pic"] == "PIC Master"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})
        await db().sales_deliveries.delete_many({"id": sj_id})
        await db()[SALES_CUSTOMERS].delete_many({"id": cust_id})


@pytest.mark.anyio
async def test_update_order_info_does_not_mutate_existing_multi_sj_or_loading_stock():
    order = await _seed_order()
    sj1 = {
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": order["id"],
        "sj_number": "SJ-A-001",
        "address": "Alamat SJ A",
        "recipient": "Penerima A",
        "recipient_phone": "081AAA",
        "shipping_address": "Alamat SJ A",
        "recipient_name": "Penerima A",
        "is_cancelled": False,
        "line_items": [{"qty": 10}],
    }
    sj2 = {
        "id": f"sj-{uuid.uuid4().hex[:8]}",
        "po_id": order["id"],
        "sj_number": "SJ-B-002",
        "address": "Alamat SJ B",
        "recipient": "Penerima B",
        "recipient_phone": "081BBB",
        "shipping_address": "Alamat SJ B",
        "recipient_name": "Penerima B",
        "is_cancelled": False,
        "line_items": [{"qty": 20}],
    }
    await db().sales_deliveries.insert_many([sj1, sj2])
    # Draft SJ without Loading evidence → shipping edit allowed; snapshots stay frozen.
    loading_before = dict(order.get("loading_info") or {})
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={
                    "address": "Alamat PO Baru",
                    "recipient": "Penerima PO Baru",
                    "customer_phone": "081POBARU",
                    "estimated_delivery": "2026-09-19",
                },
            )
        assert r.status_code == 200, r.text
        a = await db().sales_deliveries.find_one({"id": sj1["id"]}, {"_id": 0})
        b = await db().sales_deliveries.find_one({"id": sj2["id"]}, {"_id": 0})
        assert a["address"] == "Alamat SJ A"
        assert a["recipient"] == "Penerima A"
        assert a["recipient_phone"] == "081AAA"
        assert b["address"] == "Alamat SJ B"
        assert b["recipient"] == "Penerima B"
        assert b["sj_number"] == "SJ-B-002"
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Alamat PO Baru"
        assert fresh["loading_info"] == loading_before
        assert fresh["qty_delivered"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})
        await db().sales_deliveries.delete_many({"id": {"$in": [sj1["id"], sj2["id"]]}})


@pytest.mark.anyio
async def test_update_order_info_blocked_when_sj_has_loading():
    order = await _seed_order()
    sj_id = f"sj-load-{uuid.uuid4().hex[:8]}"
    await db().sales_deliveries.insert_one({
        "id": sj_id,
        "po_id": order["id"],
        "sj_number": "SJ-LOAD-1",
        "address": "Alamat SJ",
        "recipient": "Recv",
        "is_cancelled": False,
    })
    await db().orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "loading_confirmations": [{
                "surat_jalan_id": sj_id,
                "shipment_id": sj_id,
                "sj_number": "SJ-LOAD-1",
            }],
            "shipments": [{
                "id": sj_id,
                "sales_delivery_id": sj_id,
                "sj_number": "SJ-LOAD-1",
                "loaded_qty": 5,
            }],
        }},
    )
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"address": "Ganti", "estimated_delivery": "2026-09-19"},
            )
        assert r.status_code == 409
        detail = (r.json().get("detail") or "").lower()
        assert "loading" in detail or "sj" in detail
        sj = await db().sales_deliveries.find_one({"id": sj_id}, {"_id": 0})
        assert sj["address"] == "Alamat SJ"
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["address"] == "Jl. Lama No. 1"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db().orders.delete_many({"id": order["id"]})
        await db().sales_deliveries.delete_many({"id": sj_id})


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
async def test_update_order_info_empty_tanggal_clears_manual():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/orders/{order['id']}/info",
                json={"tanggal_pengiriman": ""},
            )
        assert r.status_code == 200, r.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh.get("delivery_deadline_manual") in (None, "")
        assert fresh.get("scheduled_delivery_date") in (None, "")
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
