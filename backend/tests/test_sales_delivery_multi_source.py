"""Stage 1: multi-source PO → Surat Jalan allocations."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_multi_sj")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from server import app, current_user
from services.sales_delivery_allocations import (
    ALLOCATION_COUNTERS,
    normalize_delivery_source_allocations,
    validate_orders_merge_compatible,
)
from fastapi import HTTPException


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "id": "admin-1",
    }


async def _po(
    *,
    po_number: str,
    qty: int,
    customer_id: str,
    customer: str = "PT Gabung",
    address: str = "Jl. Satu Tujuan No. 1",
    branch: str = "br-ygy",
    price: float = 1000.0,
    name: str = "Produk X",
) -> dict:
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": customer,
        "customer_id": customer_id,
        "outlet": "Cabang Utama",
        "recipient": "Pak Budi",
        "address": address,
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "operational_branch_id": branch,
        "branch_id": "1",
        "currency": "IDR",
        "line_items": [{
            "line_id": line_id,
            "name": name,
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
            "master_item_id": "item-x",
            "production_process": "print",
        }],
        "total_quantity": qty,
        "current_stage": 11,
        "cancelled": False,
        "created_at": "2026-08-30T10:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


@pytest.mark.anyio
async def test_t1_single_po_still_works_and_stamps_allocations():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/MS/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "line_items": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": 1000}],
            })
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            assert deliv["po_id"] == po["id"]
            assert deliv.get("source_po_ids") == [po["id"]]
            assert deliv.get("is_multi_source") is False
            alloc = deliv.get("source_allocations") or []
            assert len(alloc) == 1
            assert alloc[0]["allocated_qty"] == 1000
            assert alloc[0]["source_line_id"] == po["line_items"][0]["line_id"]
            assert alloc[0]["unit_price"] == 1000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t2_partial_chain_single_po():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/MS/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 400}],
            })
            assert r1.status_code == 200, r1.text
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 600}],
            })
            assert r2.status_code == 200, r2.text
            summary = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert summary["total_remaining"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t3_t5_t6_two_po_one_sj_same_product_different_price():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(
        po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}",
        qty=3000, customer_id=cust, price=1500.0,
    )
    po_b = await _po(
        po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}",
        qty=2000, customer_id=cust, price=1800.0,
    )
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po_a["id"],
                "delivery_date": "2026-08-30",
                "source_allocations": [
                    {
                        "source_po_id": po_a["id"],
                        "source_line_id": po_a["line_items"][0]["line_id"],
                        "source_line_idx": 0,
                        "allocated_qty": 3000,
                    },
                    {
                        "source_po_id": po_b["id"],
                        "source_line_id": po_b["line_items"][0]["line_id"],
                        "source_line_idx": 0,
                        "allocated_qty": 2000,
                    },
                ],
            })
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            assert deliv["is_multi_source"] is True
            assert deliv["total_qty"] == 5000
            assert set(deliv["source_po_ids"]) == {po_a["id"], po_b["id"]}
            lines = deliv["line_items"]
            assert len(lines) == 2
            prices = sorted(float(l.get("unit_price") or 0) for l in lines)
            assert prices == [1500.0, 1800.0]
            # Remaining per PO
            sa = (await client.get(f"/api/sales-deliveries/po/{po_a['id']}/summary")).json()
            sb = (await client.get(f"/api/sales-deliveries/po/{po_b['id']}/summary")).json()
            assert sa["total_remaining"] == 0
            assert sb["total_remaining"] == 0
            # Detail exposes sources
            detail = (await client.get(f"/api/sales-deliveries/{deliv['id']}")).json()
            assert len(detail.get("source_po_summaries") or []) == 2
            # Both orders got shipment mirrors with sliced qty
            oa = await server.db.orders.find_one({"id": po_a["id"]}, {"_id": 0})
            ob = await server.db.orders.find_one({"id": po_b["id"]}, {"_id": 0})
            assert any(sh.get("sales_delivery_id") == deliv["id"] for sh in (oa.get("shipments") or []))
            assert any(sh.get("sales_delivery_id") == deliv["id"] for sh in (ob.get("shipments") or []))
            sh_a = next(sh for sh in oa["shipments"] if sh.get("sales_delivery_id") == deliv["id"])
            sh_b = next(sh for sh in ob["shipments"] if sh.get("sales_delivery_id") == deliv["id"])
            assert sh_a["total_qty"] == 3000
            assert sh_b["total_qty"] == 2000
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t4_two_po_to_two_sj_partial():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=5000, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=4000, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {"source_po_id": po_a["id"], "source_line_idx": 0,
                     "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 2000},
                    {"source_po_id": po_b["id"], "source_line_idx": 0,
                     "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 1000},
                ],
            })
            assert r1.status_code == 200, r1.text
            r2 = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {"source_po_id": po_a["id"], "source_line_idx": 0,
                     "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 3000},
                    {"source_po_id": po_b["id"], "source_line_idx": 0,
                     "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 3000},
                ],
            })
            assert r2.status_code == 200, r2.text
            sa = (await client.get(f"/api/sales-deliveries/po/{po_a['id']}/summary")).json()
            sb = (await client.get(f"/api/sales-deliveries/po/{po_b['id']}/summary")).json()
            assert sa["total_remaining"] == 0
            assert sb["total_remaining"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t7_different_customer_rejected():
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id="cust-a")
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id="cust-b")
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {"source_po_id": po_a["id"], "source_line_idx": 0,
                     "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 50},
                    {"source_po_id": po_b["id"], "source_line_idx": 0,
                     "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 50},
                ],
            })
            assert res.status_code == 400
            assert "customer" in str(res.json().get("detail") or "").lower()
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t8_different_address_rejected():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust, address="Jl. A")
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust, address="Jl. B")
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {"source_po_id": po_a["id"], "source_line_idx": 0,
                     "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 50},
                    {"source_po_id": po_b["id"], "source_line_idx": 0,
                     "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 50},
                ],
            })
            assert res.status_code == 400
            assert "alamat" in str(res.json().get("detail") or "").lower()
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t9_over_remaining_rejected():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {"source_po_id": po_a["id"], "source_line_idx": 0,
                     "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 150},
                    {"source_po_id": po_b["id"], "source_line_idx": 0,
                     "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 10},
                ],
            })
            assert res.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t10_concurrent_multi_po_no_overship():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    await server.db[ALLOCATION_COUNTERS].delete_many({})
    payload = {
        "source_allocations": [
            {"source_po_id": po_a["id"], "source_line_idx": 0,
             "source_line_id": po_a["line_items"][0]["line_id"], "allocated_qty": 1000},
            {"source_po_id": po_b["id"], "source_line_idx": 0,
             "source_line_id": po_b["line_items"][0]["line_id"], "allocated_qty": 1000},
        ],
    }
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def _one():
                return await client.post("/api/sales-deliveries", json=payload)

            results = await asyncio.gather(_one(), _one(), return_exceptions=True)
            codes = []
            for r in results:
                if isinstance(r, Exception):
                    codes.append(500)
                else:
                    codes.append(r.status_code)
            assert 200 in codes
            # Second must not also succeed as full 1000+1000 (overship)
            assert codes.count(200) == 1
            sa = (await client.get(f"/api/sales-deliveries/po/{po_a['id']}/summary")).json()
            assert sa["total_shipped"] == 1000
            assert sa["total_remaining"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_t11_legacy_normalize_read():
    delivery = {
        "po_id": "po-1",
        "po_number": "PO/1",
        "line_items": [{"line_idx": 0, "line_id": "l1", "name": "X", "qty": 5, "unit": "pcs"}],
    }
    alloc = normalize_delivery_source_allocations(delivery)
    assert len(alloc) == 1
    assert alloc[0]["source_po_id"] == "po-1"
    assert alloc[0]["allocated_qty"] == 5


@pytest.mark.anyio
async def test_t15_legacy_surat_jalan_still_409():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/L/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(f"/api/orders/{po['id']}/surat-jalan", json={})
            assert res.status_code == 409
            body = res.json()
            detail = body.get("detail") or body
            assert detail.get("code") == "LEGACY_SJ_CREATE_DISABLED"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_cancelled_po_rejected_in_merge():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    await server.db.orders.update_one({"id": po_b["id"]}, {"$set": {"cancelled": True}})
    with pytest.raises(HTTPException) as exc:
        validate_orders_merge_compatible([po_a, {**po_b, "cancelled": True}])
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_missing_customer_id_fail_closed():
    po_a = {"id": "1", "po_number": "A", "customer": "X", "address": "Jl", "operational_branch_id": "b"}
    po_b = {"id": "2", "po_number": "B", "customer": "X", "address": "Jl", "operational_branch_id": "b", "customer_id": "c1"}
    with pytest.raises(HTTPException) as exc:
        validate_orders_merge_compatible([po_a, po_b])
    assert "customer_id" in str(exc.value.detail).lower()


async def _post_multi(client, po_a, po_b, *, sales_payload=None, qty_a=1, qty_b=1):
    body = {
        "po_id": po_a["id"],
        "delivery_date": "2026-08-30",
        "source_allocations": [
            {
                "source_po_id": po_a["id"],
                "source_line_id": po_a["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "allocated_qty": qty_a,
            },
            {
                "source_po_id": po_b["id"],
                "source_line_id": po_b["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "allocated_qty": qty_b,
            },
        ],
    }
    if sales_payload is not None:
        body["sales"] = sales_payload
    return await client.post("/api/sales-deliveries", json=body)


@pytest.mark.anyio
async def test_multi_po_same_legacy_sales_accepted_and_spoof_ignored():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await _post_multi(client, po_a, po_b, sales_payload="HACKER", qty_a=2, qty_b=3)
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            assert deliv["is_multi_source"] is True
            assert deliv["sales"] == "Vira"
            assert deliv["sales_name"] == "Vira"
            assert deliv["sales"] != "HACKER"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_multi_po_different_legacy_sales_rejected_before_write():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    await server.db.orders.update_one({"id": po_b["id"]}, {"$set": {"sales": "Alfi"}})
    before = await server.db.sales_deliveries.count_documents({})
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await _post_multi(client, po_a, po_b, sales_payload="Vira")
            assert res.status_code == 400, res.text
            assert "Sales" in res.text or "sales" in res.text.lower()
    finally:
        app.dependency_overrides.pop(current_user, None)
    after = await server.db.sales_deliveries.count_documents({})
    assert after == before


@pytest.mark.anyio
async def test_multi_po_different_sales_master_id_rejected():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    await server.db.orders.update_one(
        {"id": po_a["id"]}, {"$set": {"sales_master_id": "sm-vira", "sales": "Vira"}}
    )
    await server.db.orders.update_one(
        {"id": po_b["id"]}, {"$set": {"sales_master_id": "sm-alfi", "sales": "Alfi"}}
    )
    before = await server.db.sales_deliveries.count_documents({})
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await _post_multi(client, po_a, po_b)
            assert res.status_code == 400, res.text
            assert "sales_master_id" in res.text
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert await server.db.sales_deliveries.count_documents({}) == before


@pytest.mark.anyio
async def test_multi_po_blank_sales_rejected():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    await server.db.orders.update_one({"id": po_a["id"]}, {"$set": {"sales": ""}})
    await server.db.orders.update_one({"id": po_b["id"]}, {"$set": {"sales": ""}})
    before = await server.db.sales_deliveries.count_documents({})
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await _post_multi(client, po_a, po_b, sales_payload="Guess")
            assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert await server.db.sales_deliveries.count_documents({}) == before


@pytest.mark.anyio
async def test_multi_po_same_master_id_accepted():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=10, customer_id=cust)
    await server.db.sales_master.insert_one(
        {"id": "sm-vira", "sales_name": "Vira Official", "active": True}
    )
    await server.db.orders.update_one(
        {"id": po_a["id"]}, {"$set": {"sales_master_id": "sm-vira", "sales": "Vira Old"}}
    )
    await server.db.orders.update_one(
        {"id": po_b["id"]}, {"$set": {"sales_master_id": "sm-vira", "sales": "Vira Snapshot"}}
    )
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await _post_multi(client, po_a, po_b, sales_payload="Spoof", qty_a=1, qty_b=1)
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            assert deliv["sales"] == "Vira Official"
    finally:
        app.dependency_overrides.pop(current_user, None)
