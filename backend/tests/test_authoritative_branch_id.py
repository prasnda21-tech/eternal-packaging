"""Phase 4A: persist Accurate branchId. Outlet text is not a branch id."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_branch_id_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.accurate import (  # noqa: E402
    _normalize_accurate_so,
    _promote_persisted_branch_id,
)


def test_accurate_branch_id_persisted_independent_of_outlet_name():
    so = _normalize_accurate_so({
        "id": 1,
        "number": "SO-BR",
        "customer": {"name": "A"},
        "branchId": 50,
        "shipment": {"id": "150", "name": "OUTLET"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    })
    assert so["_branch_id"] == "50"
    assert so.get("outlet") in (None, "", "-")
    _promote_persisted_branch_id(so)
    assert so["branch_id"] == "50"
    assert "_branch_id" not in so
    assert so.get("outlet") in (None, "", "-")
    assert so["expedition"] == "OUTLET"


def test_outlet_text_is_not_promoted_to_branch_id():
    so = {"outlet": "Yogyakarta", "cabang": "Semarang"}
    _promote_persisted_branch_id(so)
    assert "branch_id" not in so
    assert so["outlet"] == "Yogyakarta"


def test_preview_and_file_import_do_not_invent_branch_id():
    import server
    preview = server._build_preview({
        "po_number": "PO/FILE/1",
        "customer": "PT File",
        "outlet": "Cabang Teks Import",
        "line_items": [],
        "order_date": "2026-01-01",
    })
    assert preview["outlet"] == "Cabang Teks Import"
    assert not str(preview.get("branch_id") or "").strip().isdigit()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_invoice_and_delivery_copy_order_branch_not_session():
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient
    import server
    from server import app, current_user

    client_m = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=1500)
    try:
        await client_m.admin.command("ping")
    except Exception:
        pytest.skip("local mongo unavailable")
    server.client = client_m
    server.db = client_m[os.environ["DB_NAME"]]
    db = server.db

    po_id = str(uuid.uuid4())
    po_number = f"PO/BR/{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": po_id,
        "po_number": po_number,
        "customer": "PT Branch",
        "outlet": "Nama Outlet Bukan Id",
        "branch_id": "50",
        "address": "Jl. Test",
        "recipient": "Budi",
        "delivery_zone": "Sleman",
        "sales": "Sales A",
        "line_items": [{
            "line_idx": 0,
            "line_id": "l0",
            "name": "Cup",
            "item_code": "C1",
            "quantity": 10,
            "unit": "pcs",
        }],
        "total_quantity": 10,
        "current_stage": 0,
        "status_label": "Order Masuk",
        "payment_status": "Waiting",
        "shipments": [],
        "created_at": "2026-01-01T00:00:00Z",
    })

    session_user = {
        "id": "user-semarang",
        "email": "semarang@eternalpackaging.com",
        "name": "User Semarang",
        "role": "admin",
        "branch_id": "77",
        "cabang": "Semarang",
    }
    app.dependency_overrides[current_user] = lambda: session_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv_res = await client.post("/api/sales-invoices", json={
                "po_id": po_id,
                "invoice_date": "2026-01-02",
                "discount_amount": 0,
                "ppn_percent": 0,
                "branch_id": "77",
                "lines": [{"line_idx": 0, "quantity": 4, "unit_price": 1000}],
            })
            assert inv_res.status_code == 200, inv_res.text
            invoice = inv_res.json()["invoice"]
            assert invoice["branch_id"] == "50"
            assert invoice["outlet"] == "Nama Outlet Bukan Id"
            assert invoice["branch_id"] != session_user["branch_id"]

            stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
            assert stored["branch_id"] == "50"
            detail = await client.get(f"/api/sales-invoices/{invoice['id']}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["invoice"]["branch_id"] == "50"

            del_res = await client.post("/api/sales-deliveries", json={
                "po_id": po_id,
                "delivery_date": "2026-01-03",
                "outlet": "Outlet Customer Diedit",
                "branch_id": "77",
                "line_items": [{"line_idx": 0, "qty": 4}],
            })
            assert del_res.status_code == 200, del_res.text
            delivery = del_res.json().get("delivery") or del_res.json()
            delivery_id = delivery.get("id") or delivery.get("delivery_id")
            stored_del = await db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0})
            assert stored_del["branch_id"] == "50"
            assert stored_del["outlet"] == "Outlet Customer Diedit"
            order = await db.orders.find_one({"id": po_id}, {"_id": 0})
            shipment = next(s for s in order["shipments"] if s["id"] == delivery_id)
            assert shipment["branch_id"] == "50"
            assert shipment["outlet"] == "Outlet Customer Diedit"

            await db.sales_deliveries.update_one(
                {"id": delivery_id},
                {"$set": {"outlet": "Outlet Lain Lagi"}},
            )
            again = await db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0, "branch_id": 1, "outlet": 1})
            assert again["outlet"] == "Outlet Lain Lagi"
            assert again["branch_id"] == "50"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.orders.delete_many({"id": po_id})
        await db.sales_invoices.delete_many({"po_id": po_id})
        await db.sales_deliveries.delete_many({"po_id": po_id})


@pytest.mark.anyio
async def test_old_order_without_branch_id_still_creates_invoice_and_delivery():
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient
    import server
    from server import app, current_user

    client_m = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=1500)
    try:
        await client_m.admin.command("ping")
    except Exception:
        pytest.skip("local mongo unavailable")
    server.client = client_m
    server.db = client_m[os.environ["DB_NAME"]]
    db = server.db

    po_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO/OLD/{uuid.uuid4().hex[:6]}",
        "customer": "PT Lama",
        "outlet": "Outlet Lama",
        "address": "Jl. Lama",
        "line_items": [{
            "line_idx": 0,
            "line_id": "l0",
            "name": "Cup",
            "quantity": 5,
            "unit": "pcs",
        }],
        "total_quantity": 5,
        "current_stage": 0,
        "payment_status": "Waiting",
        "shipments": [],
        "created_at": "2026-01-01T00:00:00Z",
    })
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin",
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "branch_id": "77",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv_res = await client.post("/api/sales-invoices", json={
                "po_id": po_id,
                "invoice_date": "2026-01-02",
                "lines": [{"line_idx": 0, "quantity": 2, "unit_price": 500}],
            })
            assert inv_res.status_code == 200, inv_res.text
            invoice = inv_res.json()["invoice"]
            assert not invoice.get("branch_id")
            del_res = await client.post("/api/sales-deliveries", json={
                "po_id": po_id,
                "line_items": [{"line_idx": 0, "qty": 2}],
            })
            assert del_res.status_code == 200, del_res.text
            body = del_res.json()
            delivery = body.get("delivery") or body
            stored = await db.sales_deliveries.find_one({"id": delivery.get("id")}, {"_id": 0})
            assert stored
            assert not stored.get("branch_id")
            assert stored.get("outlet") == "Outlet Lama"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.orders.delete_many({"id": po_id})
        await db.sales_invoices.delete_many({"po_id": po_id})
        await db.sales_deliveries.delete_many({"po_id": po_id})
