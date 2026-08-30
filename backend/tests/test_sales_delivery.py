"""Comprehensive backend test suite for Sales Delivery (Pengiriman Pesanan).

Covers:
- CASE A: PO 1000, create shipment 1000 -> SJ base, remaining 0
- CASE B: PO 1000, shipment 400 -> SJ base, remaining 600
- CASE C: shipment berikutnya 300 -> SJ base-2, remaining 300
- CASE D: shipment berikutnya 300 -> SJ base-3, remaining 0
- CASE E: attempt shipment 301 saat remaining 300 -> backend reject (400)
- CASE F: same item name pada dua PO lines -> exact identity preserved (tidak merge)
- CASE G: double-submit protection
- CASE H: concurrent generate SJ -> unique numbers
- CASE I: unauthorized create -> 403
- CASE J: list/filter/detail -> correct
- CASE K: PDF generation -> valid 200 and application/pdf bytes
- CASE L: PO relation stable -> delivery.po_id matches exact PO
"""
import asyncio
import io
import os
import sys
import uuid
import pytest
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from permissions import ALL_PERMISSIONS
from server import app, current_user
from services.sales_deliveries import derive_base_sj_number, derive_sj_number


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


async def create_helper_po(po_number: str, line_items: list, customer: str = "PT Test Customer") -> dict:
    """Creates a PO document directly in test DB."""
    po_id = str(uuid.uuid4())
    total_q = sum(int(li.get("quantity", 0)) for li in line_items)
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": customer,
        "outlet": "Cabang Utama",
        "recipient": "Pak Budi",
        "address": "Jl. Industri Raya No. 8",
        "delivery_zone": "Jakarta Barat",
        "sales": "Vira",
        "created_by_user": "Admin Test",
        "line_items": line_items,
        "total_quantity": total_q,
        "item_count": len(line_items),
        "current_stage": 0,
        "status_label": "Order Masuk",
        "created_at": "2026-08-30T10:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


@pytest.mark.anyio
async def test_case_a_full_shipment_single_step():
    """CASE A: PO 1000 -> create shipment 1000 -> SJ base, remaining 0."""
    po_num = f"PO/2026/08/30/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 12oz", "quantity": 1000, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "crm@eternalpackaging.com",
        "name": "CRM Sales",
        "role": "crm",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "line_items": [{"line_idx": 0, "qty": 1000}]
            })
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            assert deliv["po_id"] == po["id"]
            assert deliv["shipment_index"] == 1
            expected_sj = derive_base_sj_number(po_num)
            assert deliv["sj_number"] == expected_sj
            assert deliv["total_qty"] == 1000

            # Verify remaining via summary
            sum_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert sum_res.status_code == 200
            summary = sum_res.json()
            assert summary["total_ordered"] == 1000
            assert summary["total_shipped"] == 1000
            assert summary["total_remaining"] == 0
            assert summary["delivery_status"] == "SELESAI PENGIRIMAN"
            assert summary["can_create_delivery"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_b_c_d_partial_shipment_chain():
    """CASE B, C, D: Partial shipments: 400 -> 300 -> 300 with exact SJ base, base-2, base-3."""
    po_num = f"PO-20260830-{uuid.uuid4().hex[:4].upper()}"
    po = await create_helper_po(po_num, [{"name": "Bowl 800ml", "quantity": 1000, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            base_sj = derive_base_sj_number(po_num)

            # CASE B: Shipment 1 (400 pcs)
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 400}]
            })
            assert r1.status_code == 200
            d1 = r1.json()["delivery"]
            assert d1["shipment_index"] == 1
            assert d1["sj_number"] == base_sj
            assert d1["total_qty"] == 400

            sum1 = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert sum1["total_shipped"] == 400
            assert sum1["total_remaining"] == 600
            assert sum1["delivery_status"] == "SEBAGIAN DIKIRIM"
            assert sum1["can_create_delivery"] is True

            # CASE C: Shipment 2 (300 pcs)
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert r2.status_code == 200
            d2 = r2.json()["delivery"]
            assert d2["shipment_index"] == 2
            assert d2["sj_number"] == f"{base_sj}-2"
            assert d2["total_qty"] == 300

            sum2 = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert sum2["total_shipped"] == 700
            assert sum2["total_remaining"] == 300
            assert sum2["delivery_status"] == "SEBAGIAN DIKIRIM"

            # CASE D: Shipment 3 (300 pcs)
            r3 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert r3.status_code == 200
            d3 = r3.json()["delivery"]
            assert d3["shipment_index"] == 3
            assert d3["sj_number"] == f"{base_sj}-3"
            assert d3["total_qty"] == 300

            sum3 = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert sum3["total_shipped"] == 1000
            assert sum3["total_remaining"] == 0
            assert sum3["delivery_status"] == "SELESAI PENGIRIMAN"
            assert sum3["can_create_delivery"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_e_overshipment_rejected():
    """CASE E: attempt shipment 301 when remaining is 300 -> reject (400)."""
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 8oz", "quantity": 1000}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # First ship 700
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 700}]
            })
            assert r1.status_code == 200

            # Attempt to ship 301 (remaining is 300)
            r_over = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 301}]
            })
            assert r_over.status_code == 400
            assert "melebihi sisa PO" in r_over.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_f_same_item_name_separate_lines_no_merge():
    """CASE F: same item name on two PO lines -> exact identity preserved (tidak merge)."""
    po_num = f"PO-MULTI-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [
        {"name": "Cup 8oz", "quantity": 500, "color": "Putih", "notes": "Line 1"},
        {"name": "Cup 8oz", "quantity": 300, "color": "Hitam", "notes": "Line 2"},
    ])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Ship 200 from line 0, 100 from line 1
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "qty": 200},
                    {"line_idx": 1, "qty": 100},
                ]
            })
            assert res.status_code == 200
            deliv = res.json()["delivery"]
            assert len(deliv["line_items"]) == 2
            assert deliv["line_items"][0]["line_idx"] == 0
            assert deliv["line_items"][0]["qty"] == 200
            assert deliv["line_items"][1]["line_idx"] == 1
            assert deliv["line_items"][1]["qty"] == 100

            # Check summary line remaining
            summary = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert summary["lines"][0]["remaining_qty"] == 300  # 500 - 200
            assert summary["lines"][1]["remaining_qty"] == 200  # 300 - 100
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_g_double_submit_protection():
    """CASE G: rapid double-submit -> only one shipment created or both distinct."""
    po_num = f"PO-DBL-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Box Nasi", "quantity": 1000}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Run two concurrent requests for full quantity 1000
            r1, r2 = await asyncio.gather(
                client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 1000}]}),
                client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 1000}]}),
                return_exceptions=True
            )
            # One must succeed and one must be rejected (either 400 over-shipment or 409)
            statuses = [r1.status_code if hasattr(r1, 'status_code') else 500, r2.status_code if hasattr(r2, 'status_code') else 500]
            assert 200 in statuses
            assert any(s in (400, 409) for s in statuses)

            # Exactly 1 delivery document in DB
            count = await server.db.sales_deliveries.count_documents({"po_id": po["id"]})
            assert count == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_h_concurrent_deliveries_unique_sj():
    """CASE H: concurrent partial deliveries -> unique SJ numbers and distinct indices."""
    po_num = f"PO-CONC-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Item A", "quantity": 1000}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Concurrently ship 200 and 300
            resps = await asyncio.gather(
                client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 200}]}),
                client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 300}]}),
            )
            for r in resps:
                assert r.status_code == 200
            
            sj_numbers = [r.json()["delivery"]["sj_number"] for r in resps]
            assert len(set(sj_numbers)) == 2  # All unique!
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_i_unauthorized_rejected():
    """CASE I: unauthorized role (e.g. driver without permission) -> 403."""
    po_num = f"PO-UNAUTH-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Item", "quantity": 100}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "driver@eternalpackaging.com",
        "name": "Driver User",
        "role": "driver",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 50}]
            })
            assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_j_list_filter_and_detail():
    """CASE J: list, search filter, and detail view."""
    po_num = f"PO-LIST-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Barang List", "quantity": 500}], customer="PT Pencarian Sukses")

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r_create = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 250}]
            })
            assert r_create.status_code == 200
            deliv_id = r_create.json()["delivery"]["id"]
            sj_num = r_create.json()["delivery"]["sj_number"]

            # Query list with search
            r_list = await client.get(f"/api/sales-deliveries?q={sj_num}")
            assert r_list.status_code == 200
            items = r_list.json()["deliveries"]
            assert any(x["id"] == deliv_id for x in items)

            # Query single detail
            r_detail = await client.get(f"/api/sales-deliveries/{deliv_id}")
            assert r_detail.status_code == 200
            detail = r_detail.json()
            assert detail["delivery"]["id"] == deliv_id
            assert detail["po_summary"]["po_id"] == po["id"]
            assert len(detail["all_po_deliveries"]) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_k_pdf_generation():
    """CASE K: generate PDF -> returns valid PDF stream."""
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Paper Cup 8oz", "quantity": 1000, "unit": "pcs", "color": "Full Color"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r_create = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 500}]
            })
            assert r_create.status_code == 200
            deliv_id = r_create.json()["delivery"]["id"]

            r_pdf = await client.get(f"/api/sales-deliveries/{deliv_id}/pdf")
            assert r_pdf.status_code == 200
            assert "application/pdf" in r_pdf.headers.get("content-type", "")
            assert r_pdf.content[:5] == b"%PDF-"
            assert len(r_pdf.content) > 1000
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_l_po_relation_stable():
    """CASE L: PO relation stable -> delivery.po_id matches exact PO document."""
    po_num = f"PO-STABLE-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Item Stable", "quantity": 300}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "crm@eternalpackaging.com",
        "name": "CRM Tester",
        "role": "crm",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert res.status_code == 200
            deliv = res.json()["delivery"]
            assert deliv["po_id"] == po["id"]
            assert deliv["po_number"] == po_num
            assert deliv["created_by"] == "crm@eternalpackaging.com"
    finally:
        app.dependency_overrides.pop(current_user, None)
