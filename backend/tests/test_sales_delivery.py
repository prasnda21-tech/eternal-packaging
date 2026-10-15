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
            assert summary["delivery_status"] == "SELESAI"
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
            assert sum1["delivery_status"] == "SEBAGIAN"
            assert sum1["can_create_delivery"] is True

            # CASE C: Shipment 2 (300 pcs)
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert r2.status_code == 200
            d2 = r2.json()["delivery"]
            assert d2["shipment_index"] == 2
            assert d2["sj_number"] == f"{base_sj}_2"
            assert d2["total_qty"] == 300

            sum2 = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert sum2["total_shipped"] == 700
            assert sum2["total_remaining"] == 300
            assert sum2["delivery_status"] == "SEBAGIAN"

            # CASE D: Shipment 3 (300 pcs)
            r3 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert r3.status_code == 200
            d3 = r3.json()["delivery"]
            assert d3["shipment_index"] == 3
            assert d3["sj_number"] == f"{base_sj}_3"
            assert d3["total_qty"] == 300

            sum3 = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert sum3["total_shipped"] == 1000
            assert sum3["total_remaining"] == 0
            assert sum3["delivery_status"] == "SELESAI"
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
async def test_case_a_po_prefill_contains_all_header_fields():
    """CASE A: PO with customer/outlet/sales/recipient/address/zone -> delivery prefill has all."""
    po_num = f"PO-PREFILL-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 12oz", "quantity": 1000, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"email": "crm@test.com", "role": "crm"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert res.status_code == 200
            data = res.json()
            assert data["po_id"] == po["id"]
            assert data["po_number"] == po_num
            assert data["customer"] == "PT Test Customer"
            assert data["outlet"] == "Cabang Utama"
            assert data["recipient"] == "Pak Budi"
            assert data["address"] == "Jl. Industri Raya No. 8"
            assert data["delivery_zone"] == "Jakarta Barat"
            assert data["sales"] == "Vira"
            assert len(data["lines"]) == 1
            assert data["lines"][0]["remaining_qty"] == 1000
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_b_edit_delivery_saves_edits_without_mutating_po():
    """CASE B: user edits address/recipient/sales/zone/expedition/notes -> delivery saves edits -> PO unmutated."""
    po_num = f"PO-EDIT-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Bowl 600ml", "quantity": 500}])

    app.dependency_overrides[current_user] = lambda: {"email": "crm@test.com", "role": "crm"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient": "Pak Joko (Edited PIC)",
                "address": "Jl. Gatot Subroto No. 99 (Edited Address)",
                "delivery_zone": "Jakarta Selatan",
                "sales": "Rian Sales",
                "expedition": "Lalamove Van",
                "notes": "Handle with care",
                "line_items": [{"line_idx": 0, "qty": 200}]
            })
            assert res.status_code == 200
            deliv = res.json()["delivery"]
            assert deliv["recipient"] == "Pak Joko (Edited PIC)"
            assert deliv["address"] == "Jl. Gatot Subroto No. 99 (Edited Address)"
            assert deliv["delivery_zone"] == "Jakarta Selatan"
            assert deliv["sales"] == "Rian Sales"
            assert deliv["expedition"] == "Lalamove Van"
            assert deliv["notes"] == "Handle with care"

            # Verify original PO document in DB is UNMUTATED
            po_in_db = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_in_db["recipient"] == "Pak Budi"
            assert po_in_db["address"] == "Jl. Industri Raya No. 8"
            assert po_in_db["delivery_zone"] == "Jakarta Barat"
            assert po_in_db["sales"] == "Vira"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_c_empty_po_field_fallback_to_master():
    """CASE C: PO has empty address/outlet -> fallback to master delivery_outlets if available."""
    cust_name = f"PT Master Fallback {uuid.uuid4().hex[:4]}"
    # Create master outlet
    await server.db.delivery_outlets.insert_one({
        "id": str(uuid.uuid4()),
        "customer_name": cust_name,
        "name": "Cabang Master Fallback",
        "formatted_address": "Jl. Master Outlet No. 100",
        "recipient": "Bu Susi Master",
        "delivery_zone": "Tangerang",
    })

    # Create PO without address and recipient
    po_id = str(uuid.uuid4())
    po_doc = {
        "id": po_id,
        "po_number": f"PO-FALLBACK-{uuid.uuid4().hex[:4]}",
        "customer": cust_name,
        "outlet": "",
        "recipient": "",
        "address": "",
        "delivery_zone": "",
        "sales": "",
        "line_items": [{"name": "Item Master", "quantity": 100}],
        "total_quantity": 100,
        "created_at": "2026-08-30T10:00:00Z",
    }
    await server.db.orders.insert_one(po_doc)

    app.dependency_overrides[current_user] = lambda: {"email": "crm@test.com", "role": "crm"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/sales-deliveries/po/{po_id}/summary")
            assert res.status_code == 200
            data = res.json()
            assert data["address"] == "Jl. Master Outlet No. 100"
            assert data["outlet"] == "Cabang Master Fallback"
            assert data["recipient"] == "Bu Susi Master"
            assert data["delivery_zone"] == "Tangerang"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_d_po_snapshot_wins_over_different_master():
    """CASE D: PO snapshot exists -> use PO snapshot as default even if master is different."""
    cust_name = f"PT Snapshot Priority {uuid.uuid4().hex[:4]}"
    await server.db.delivery_outlets.insert_one({
        "id": str(uuid.uuid4()),
        "customer_name": cust_name,
        "name": "Cabang Baru Master",
        "formatted_address": "Jl. Master Baru No. 999",
    })

    po_id = str(uuid.uuid4())
    po_doc = {
        "id": po_id,
        "po_number": f"PO-SNAP-{uuid.uuid4().hex[:4]}",
        "customer": cust_name,
        "outlet": "Cabang Lama PO",
        "address": "Jl. Snapshot Khusus PO No. 1",
        "line_items": [{"name": "Item Snapshot", "quantity": 200}],
        "total_quantity": 200,
        "created_at": "2026-08-30T10:00:00Z",
    }
    await server.db.orders.insert_one(po_doc)

    app.dependency_overrides[current_user] = lambda: {"email": "crm@test.com", "role": "crm"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/sales-deliveries/po/{po_id}/summary")
            assert res.status_code == 200
            data = res.json()
            assert data["outlet"] == "Cabang Lama PO"
            assert data["address"] == "Jl. Snapshot Khusus PO No. 1"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_e_invalid_poid_rejected():
    """CASE E: client attempting invalid po_id -> 404."""
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": "non-existent-po-id-12345",
                "line_items": [{"line_idx": 0, "qty": 10}]
            })
            assert res.status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_f_altered_line_id_rejected():
    """CASE F: client attempting to alter line_id -> reject (400)."""
    po = await create_helper_po(f"PO-LINEID-{uuid.uuid4().hex[:4]}", [{"name": "Item", "quantity": 100}])

    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "line_id": "spoofed_line_id", "qty": 50}]
            })
            assert res.status_code == 400
            assert "tidak cocok dengan PO" in res.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_h_partial_second_shipment_prefills_from_po_snapshot():
    """CASE H: 2nd shipment pre-fills header from original PO snapshot, not 1st shipment's edits."""
    po_num = f"PO-CHAIN-{uuid.uuid4().hex[:4].upper()}"
    po = await create_helper_po(po_num, [{"name": "Item Chain", "quantity": 1000}])

    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1st shipment with edited address
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "address": "Jl. Sementara Edit No. 12",
                "line_items": [{"line_idx": 0, "qty": 400}]
            })
            assert r1.status_code == 200

            # 2nd shipment prefill request -> should still reflect PO snapshot as default
            sum_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert sum_res.status_code == 200
            summary = sum_res.json()
            assert summary["address"] == "Jl. Industri Raya No. 8"  # Original PO snapshot!
            assert summary["next_shipment_index"] == 2
            expected_sj = derive_sj_number(po_num, 2)
            assert summary["suggested_sj_number"] == expected_sj
    finally:
        app.dependency_overrides.pop(current_user, None)


# ==================================================
# MANDATORY STATUS TEST SUITE: CASES 1 - 9
# ==================================================

@pytest.mark.anyio
async def test_case_1_po_1000_sj_400_is_sebagian():
    """CASE 1: PO 1000, SJ 400 -> SEBAGIAN."""
    po = await create_helper_po(f"PO-M1-{uuid.uuid4().hex[:4]}", [{"name": "Cup 12oz", "quantity": 1000}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 400}]
            })
            assert r1.status_code == 200
            deliv = r1.json()["delivery"]
            assert deliv["status"] == "SEBAGIAN"

            # Check summary endpoint
            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert s_res.status_code == 200
            assert s_res.json()["delivery_status"] == "SEBAGIAN"
            assert s_res.json()["total_shipped"] == 400
            assert s_res.json()["total_remaining"] == 600

            # Check list endpoint with filter
            l_res = await client.get(f"/api/sales-deliveries?po_id={po['id']}")
            assert l_res.status_code == 200
            assert l_res.json()["deliveries"][0]["status"] == "SEBAGIAN"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_2_po_1000_sj_400_then_300_is_sebagian():
    """CASE 2: PO 1000, SJ 400 + 300 -> SEBAGIAN."""
    po = await create_helper_po(f"PO-M2-{uuid.uuid4().hex[:4]}", [{"name": "Cup 12oz", "quantity": 1000}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # SJ 1
            await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 400}]
            })
            # SJ 2
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}]
            })
            assert r2.status_code == 200
            assert r2.json()["delivery"]["status"] == "SEBAGIAN"

            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert s_res.json()["delivery_status"] == "SEBAGIAN"
            assert s_res.json()["total_shipped"] == 700
            assert s_res.json()["total_remaining"] == 300
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_3_po_1000_sj_400_300_300_is_selesai():
    """CASE 3: PO 1000, SJ 400 + 300 + 300 -> SELESAI."""
    po = await create_helper_po(f"PO-M3-{uuid.uuid4().hex[:4]}", [{"name": "Cup 12oz", "quantity": 1000}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 400}]})
            await client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 300}]})
            r3 = await client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 300}]})
            assert r3.status_code == 200
            assert r3.json()["delivery"]["status"] == "SELESAI"

            # Both summary and list now report SELESAI
            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert s_res.json()["delivery_status"] == "SELESAI"
            assert s_res.json()["total_remaining"] == 0
            assert s_res.json()["can_create_delivery"] is False

            l_res = await client.get(f"/api/sales-deliveries?po_id={po['id']}")
            for d in l_res.json()["deliveries"]:
                assert d["status"] == "SELESAI"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_4_attempt_cumulative_over_1000_rejected():
    """CASE 4: attempt cumulative > 1000 -> reject (400)."""
    po = await create_helper_po(f"PO-M4-{uuid.uuid4().hex[:4]}", [{"name": "Cup 12oz", "quantity": 1000}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 700}]})
            # Attempt 301 when remaining is 300
            r_over = await client.post("/api/sales-deliveries", json={"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 301}]})
            assert r_over.status_code == 400
            assert "melebihi sisa PO" in r_over.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_5_same_item_name_different_line_independent():
    """CASE 5: same item name different line -> line identity independent."""
    lines = [
        {"name": "Cup 16oz", "quantity": 500, "production_process": "print"},
        {"name": "Cup 16oz", "quantity": 300, "production_process": "non_print"},
    ]
    po = await create_helper_po(f"PO-M5-{uuid.uuid4().hex[:4]}", lines)
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Ship Line 0 fully (500), Line 1 partially (100)
            r = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "qty": 500},
                    {"line_idx": 1, "qty": 100},
                ]
            })
            assert r.status_code == 200
            assert r.json()["delivery"]["status"] == "SEBAGIAN"

            # Check lines summary
            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            summary = s_res.json()
            assert summary["lines"][0]["remaining_qty"] == 0
            assert summary["lines"][1]["remaining_qty"] == 200
            assert summary["total_remaining"] == 200
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_6_one_line_fulfilled_another_not_is_sebagian():
    """CASE 6: one line fulfilled, another not fulfilled -> SEBAGIAN."""
    lines = [
        {"name": "Item A", "quantity": 600},
        {"name": "Item B", "quantity": 400},
    ]
    po = await create_helper_po(f"PO-M6-{uuid.uuid4().hex[:4]}", lines)
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "qty": 600},
                    {"line_idx": 1, "qty": 0},
                ]
            })
            assert r.status_code == 200
            assert r.json()["delivery"]["status"] == "SEBAGIAN"

            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert s_res.json()["delivery_status"] == "SEBAGIAN"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_7_all_exact_lines_fulfilled_is_selesai():
    """CASE 7: all exact lines fulfilled -> SELESAI."""
    lines = [
        {"name": "Item A", "quantity": 600},
        {"name": "Item B", "quantity": 400},
    ]
    po = await create_helper_po(f"PO-M7-{uuid.uuid4().hex[:4]}", lines)
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1st shipment: Line 1 = 300, Line 2 = 200
            await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}, {"line_idx": 1, "qty": 200}]
            })
            # 2nd shipment: Line 1 = 300, Line 2 = 200
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 300}, {"line_idx": 1, "qty": 200}]
            })
            assert r2.status_code == 200
            assert r2.json()["delivery"]["status"] == "SELESAI"

            s_res = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert s_res.json()["delivery_status"] == "SELESAI"
            assert s_res.json()["total_remaining"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_8_direct_api_create_after_selesai_rejected():
    """CASE 8: direct API create additional SJ after SELESAI -> reject (400)."""
    po = await create_helper_po(f"PO-M8-{uuid.uuid4().hex[:4]}", [{"name": "Item A", "quantity": 500}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Fulfill PO completely
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 500}]
            })
            assert r1.status_code == 200

            # Attempt creating another SJ
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 100}]
            })
            assert r2.status_code == 400
            assert "sudah selesai dibuatkan" in r2.text or "melebihi sisa PO" in r2.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_9_existing_partial_historical_sj_derived_correctly():
    """CASE 9: existing partial historical SJ -> derived status correct."""
    po = await create_helper_po(f"PO-M9-{uuid.uuid4().hex[:4]}", [{"name": "Item H", "quantity": 1000}])
    app.dependency_overrides[current_user] = lambda: {"email": "admin@test.com", "role": "admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1st shipment (400)
            await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 400}]
            })

            # Check list: should have status SEBAGIAN
            l1 = await client.get(f"/api/sales-deliveries?po_id={po['id']}&status=SEBAGIAN")
            assert len(l1.json()["deliveries"]) == 1
            assert l1.json()["deliveries"][0]["status"] == "SEBAGIAN"

            # Filter SELESAI: should be empty
            l_empty = await client.get(f"/api/sales-deliveries?po_id={po['id']}&status=SELESAI")
            assert len(l_empty.json()["deliveries"]) == 0

            # Complete the remaining 600
            await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 600}]
            })

            # Now both deliveries show derived SELESAI
            l2 = await client.get(f"/api/sales-deliveries?po_id={po['id']}&status=SELESAI")
            assert len(l2.json()["deliveries"]) == 2
            for d in l2.json()["deliveries"]:
                assert d["status"] == "SELESAI"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_early_create_allowed_when_packing_incomplete():
    """Pengiriman Pesanan may be created before Packing/Loading are complete."""
    admin = {"id": "u-early", "email": "early@test.com", "name": "Early", "role": "admin", "permissions": list(ALL_PERMISSIONS)}
    app.dependency_overrides[current_user] = lambda: admin
    try:
        po = await create_helper_po("PO-EARLY-1", [
            {"name": "Cup", "quantity": 10000, "unit": "pcs", "line_id": "L0", "production_process": "print"},
        ])
        # Partial packing only — previously blocked create when packing lines existed
        await server.db.orders.update_one(
            {"id": po["id"]},
            {"$set": {
                "packing_info": {
                    "qty_packed": 2000,
                    "lines": [{"line_idx": 0, "packed_qty": 2000}],
                }
            }},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 4000}],
            })
            assert r.status_code == 200, r.text
            body = r.json()
            delivery = body.get("delivery") or body
            assert int(delivery.get("total_qty") or 0) == 4000
            # Physical packing unchanged by mere process creation
            fresh = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert fresh["packing_info"]["lines"][0]["packed_qty"] == 2000
            assert not fresh.get("loading_confirmations")
            assert int(fresh.get("qty_delivered") or 0) == 0
            assert fresh.get("delivery_status") not in ("Terkirim", "Sebagian Terkirim")
            # Early create must NOT make Pengiriman eligible / skip Loading
            from quantity_rules import eligible_delivery_qty, loading_status_semantic
            assert eligible_delivery_qty(fresh) == 0
            assert loading_status_semantic(fresh) != "completed"
            assert any(
                str(sh.get("sales_delivery_id") or "") == str(delivery.get("id") or delivery.get("delivery_id") or "")
                for sh in (fresh.get("shipments") or [])
            )
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": "PO-EARLY-1"})
        await server.db.sales_deliveries.delete_many({"po_number": {"$regex": "^PO-EARLY-1"}})


@pytest.mark.anyio
async def test_create_persists_sj_location_without_mutating_po():
    loc_a = {"lat": -6.2, "lng": 106.8, "formatted_address": "PO-A"}
    loc_b = {"lat": -6.35, "lng": 106.95, "formatted_address": "SJ-B"}
    po_num = f"PO/2026/09/15/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 12oz", "quantity": 1000, "unit": "pcs"}])
    await server.db.orders.update_one({"id": po["id"]}, {"$set": {"delivery_location": loc_a}})
    admin = {"email": "crm@eternalpackaging.com", "name": "CRM Sales", "role": "crm"}
    app.dependency_overrides[current_user] = lambda: admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            summary = await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")
            assert summary.status_code == 200
            assert summary.json()["delivery_location"]["lat"] == -6.2

            created = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-09-15",
                "line_items": [{"line_idx": 0, "qty": 400}],
                "delivery_location": loc_b,
            })
            assert created.status_code == 200, created.text
            delivery = created.json()["delivery"]
            assert delivery["delivery_location"]["lat"] == -6.35
            assert delivery["delivery_location"]["lng"] == 106.95

            stored = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert stored["delivery_location"]["lat"] == -6.35
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["delivery_location"]["lat"] == -6.2
            ships = order.get("shipments") or []
            assert len(ships) == 1
            assert ships[0]["delivery_location"]["lat"] == -6.35

            no_override = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-09-16",
                "line_items": [{"line_idx": 0, "qty": 300}],
            })
            assert no_override.status_code == 200, no_override.text
            d2 = no_override.json()["delivery"]
            assert "delivery_location" not in d2 or d2.get("delivery_location") in (None, {})
            order2 = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order2["delivery_location"]["lat"] == -6.2
            ships2 = order2.get("shipments") or []
            assert len(ships2) == 2
            second = next(s for s in ships2 if s["id"] == d2["id"])
            assert "delivery_location" not in second or second.get("delivery_location") in (None, {})

            invalid = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-09-17",
                "line_items": [{"line_idx": 0, "qty": 100}],
                "delivery_location": {"lat": -6.9},
            })
            assert invalid.status_code == 200, invalid.text
            d3 = invalid.json()["delivery"]
            assert "delivery_location" not in d3 or d3.get("delivery_location") in (None, {})
            order3 = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order3["delivery_location"]["lat"] == -6.2
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"id": po["id"]})
        await server.db.sales_deliveries.delete_many({"po_id": po["id"]})


def _phone_user():
    return {"email": "crm-phone@eternalpackaging.com", "name": "CRM Phone", "role": "crm"}


async def _po_with_customer_phone(phone="081234567890", qty=1000):
    po_num = f"PO/2026/08/30/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(
        po_num,
        [{"name": "Cup Phone", "quantity": qty, "unit": "pcs"}],
        customer="PT Phone Snapshot",
    )
    master_id = f"cust-{po['id']}"
    await server.db.orders.update_one(
        {"id": po["id"]},
        {"$set": {"customer_phone": phone, "customer_id": master_id}},
    )
    po["customer_phone"] = phone
    po["customer_id"] = master_id
    master = {"id": master_id, "name": "PT Phone Snapshot", "phone": "089000000000"}
    await server.db.sales_customers.insert_one(dict(master))
    return po, master


async def _cleanup_phone_po(po):
    await server.db.orders.delete_many({"id": po["id"]})
    await server.db.sales_deliveries.delete_many({"po_id": po["id"]})
    await server.db.sales_customers.delete_many({"id": po.get("customer_id")})


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


@pytest.mark.anyio
async def test_recipient_phone_customer_mode_snapshots_po_phone_not_master():
    po, master = await _po_with_customer_phone("0812-3456-7890")
    app.dependency_overrides[current_user] = _phone_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            summary = (await client.get(f"/api/sales-deliveries/po/{po['id']}/summary")).json()
            assert summary["customer_phone"] == "0812-3456-7890"
            assert summary["total_ordered"] == 1000
            assert summary["can_create_delivery"] is True
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "recipient_phone_source": "customer",
                "recipient_phone": "089999999999",
                "line_items": [{"line_idx": 0, "qty": 400}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            assert delivery["recipient_phone"] == "0812-3456-7890"
            assert delivery["recipient_phone_wa"] == "6281234567890"
            assert delivery["total_qty"] == 400
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["customer_phone"] == "0812-3456-7890"
            assert "recipient_phone" not in order
            ship = order["shipments"][0]
            assert ship["id"] == delivery["id"]
            assert ship["recipient_phone"] == delivery["recipient_phone"]
            assert ship["recipient_phone_wa"] == delivery["recipient_phone_wa"]
            stored_master = await server.db.sales_customers.find_one({"id": master["id"]}, {"_id": 0})
            assert stored_master["phone"] == "089000000000"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await _cleanup_phone_po(po)


@pytest.mark.anyio
async def test_recipient_phone_manual_empty_invalid_and_customer_invalid():
    po, _master = await _po_with_customer_phone("abc-bukan-nomor")
    app.dependency_overrides[current_user] = _phone_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "manual",
                "recipient_phone": "123",
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert bad.status_code == 400
            assert await server.db.sales_deliveries.count_documents({"po_id": po["id"]}) == 0

            empty = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "manual",
                "recipient_phone": "   ",
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert empty.status_code == 200, empty.text
            first = empty.json()["delivery"]
            assert first["recipient_phone"] is None
            assert first["recipient_phone_wa"] is None

            kept = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "customer",
                "recipient_phone": "081234567890",
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert kept.status_code == 200, kept.text
            second = kept.json()["delivery"]
            assert second["recipient_phone"] == "abc-bukan-nomor"
            assert second["recipient_phone_wa"] is None
            assert second["id"] != first["id"]
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["customer_phone"] == "abc-bukan-nomor"
            phones = {sh["id"]: sh["recipient_phone"] for sh in order["shipments"]}
            assert phones[first["id"]] is None
            assert phones[second["id"]] == "abc-bukan-nomor"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await _cleanup_phone_po(po)


@pytest.mark.anyio
async def test_recipient_phone_two_sj_and_retry_keeps_first_snapshot(monkeypatch):
    po, _master = await _po_with_customer_phone("081111111111")

    async def _no_pdf(*_args, **_kwargs):
        return None, "pdf_generate_failed"

    monkeypatch.setattr("services.sales_deliveries.ensure_generated_sj_pdf", _no_pdf)
    app.dependency_overrides[current_user] = _phone_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "manual",
                "recipient_phone": "081211110000",
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert first.status_code == 200, first.text
            sj_a = first.json()["delivery"]
            retry = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "manual",
                "recipient_phone": "081222220000",
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert retry.status_code == 200, retry.text
            assert retry.json()["delivery"]["id"] == sj_a["id"]
            assert retry.json()["delivery"]["recipient_phone"] == "081211110000"
            assert retry.json()["delivery"]["recipient_phone_wa"] == "6281211110000"
            second = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "recipient_phone_source": "manual",
                "recipient_phone": "+62 822-3333-0000",
                "line_items": [{"line_idx": 0, "qty": 300}],
            })
            assert second.status_code == 200, second.text
            sj_b = second.json()["delivery"]
            assert sj_b["id"] != sj_a["id"]
            assert sj_b["recipient_phone"] == "+62 822-3333-0000"
            assert sj_b["recipient_phone_wa"] == "6282233330000"
            stored_a = await server.db.sales_deliveries.find_one({"id": sj_a["id"]}, {"_id": 0})
            assert stored_a["recipient_phone_wa"] == "6281211110000"
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            by_id = {sh["id"]: sh for sh in order["shipments"]}
            assert by_id[sj_a["id"]]["recipient_phone_wa"] == "6281211110000"
            assert by_id[sj_b["id"]]["recipient_phone_wa"] == "6282233330000"
            assert order["customer_phone"] == "081111111111"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await _cleanup_phone_po(po)


@pytest.mark.anyio
async def test_recipient_phone_survives_reschedule_cancel_and_supersede(monkeypatch):
    po, _master = await _po_with_customer_phone("081234567890")

    async def _no_pdf(*_args, **_kwargs):
        return None, "pdf_generate_failed"

    monkeypatch.setattr("services.sales_deliveries.ensure_generated_sj_pdf", _no_pdf)
    app.dependency_overrides[current_user] = _phone_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "recipient_phone_source": "manual",
                "recipient_phone": "081234567890",
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
        from services.sales_deliveries import sync_sj_numbers_after_reschedule
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        await sync_sj_numbers_after_reschedule(
            server.db,
            order,
            previous_date="2026-08-30",
            new_date="2026-09-02",
            shipment_id=delivery["id"],
            user=_phone_user(),
        )
        fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
        assert fresh["recipient_phone"] == "081234567890"
        assert fresh["recipient_phone_wa"] == "6281234567890"
        ship = next(sh for sh in order["shipments"] if sh["id"] == delivery["id"])
        assert ship["recipient_phone_wa"] == "6281234567890"

        from services.sj_integrity import supersede_shipment
        supersede_shipment(
            order,
            target_id=delivery["id"],
            actor={"email": "crm-phone@eternalpackaging.com"},
            reason="uji",
            now="2026-09-03T00:00:00Z",
        )
        ship = next(sh for sh in order["shipments"] if sh["id"] == delivery["id"])
        assert ship["status"] == "superseded"
        assert ship["recipient_phone"] == "081234567890"
        assert ship["recipient_phone_wa"] == "6281234567890"

        from motor.motor_asyncio import AsyncIOMotorCollection
        from services.physical_stock import cancel_sales_delivery_document

        async def _txn_ok(_client):
            return None

        class _Txn:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_args):
                return False

        class _Session:
            def start_transaction(self):
                return _Txn()

        class _SessionCM:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *_args):
                return False

        async def _start_session():
            return _SessionCM()

        orig_find_one = AsyncIOMotorCollection.find_one
        orig_update_one = AsyncIOMotorCollection.update_one
        orig_find = AsyncIOMotorCollection.find

        async def _find_one(self, *args, **kwargs):
            kwargs.pop("session", None)
            return await orig_find_one(self, *args, **kwargs)

        async def _update_one(self, *args, **kwargs):
            kwargs.pop("session", None)
            return await orig_update_one(self, *args, **kwargs)

        def _find(self, *args, **kwargs):
            kwargs.pop("session", None)
            return orig_find(self, *args, **kwargs)

        monkeypatch.setattr("services.physical_stock.require_transaction_support", _txn_ok)
        monkeypatch.setattr(AsyncIOMotorCollection, "find_one", _find_one)
        monkeypatch.setattr(AsyncIOMotorCollection, "update_one", _update_one)
        monkeypatch.setattr(AsyncIOMotorCollection, "find", _find)
        monkeypatch.setattr(server.db.client, "start_session", _start_session)
        await cancel_sales_delivery_document(
            server.db, fresh, actor_id="crm-phone@eternalpackaging.com", reason="uji batal",
        )
        cancelled = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
        assert cancelled["is_cancelled"] is True
        assert cancelled["recipient_phone"] == "081234567890"
        assert cancelled["recipient_phone_wa"] == "6281234567890"
        order_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        ship_after = next(sh for sh in order_after["shipments"] if sh["id"] == delivery["id"])
        assert ship_after["recipient_phone"] == "081234567890"
        assert ship_after["recipient_phone_wa"] == "6281234567890"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await _cleanup_phone_po(po)


def test_recipient_phone_pdf_legacy_generic_and_signed_snapshot():
    from services.print_design_settings import resolve_print_design
    from services.print_field_registry import default_delivery_header_elements
    from services.sales_deliveries import generate_sales_delivery_pdf_bytes

    base = {
        "sj_number": "SJ/2026/08/30/0001",
        "delivery_date": "2026-08-30",
        "customer": "PT SJ Phone",
        "address": "Jl. Snapshot 1",
        "outlet": "Utama",
        "sales": "Vira",
        "expedition": "Internal",
        "delivery_zone": "Barat",
        "created_by_name": "Admin",
        "po_number": "PO/2026/08/30/PHONE",
        "recipient": "Pak Budi",
        "notes": "Catatan",
        "line_items": [{"item_code": "C1", "name": "Cup Phone", "qty": 4, "unit": "pcs"}],
        "total_qty": 4,
    }
    legacy = resolve_print_design(None)
    generic = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "surat_jalan": {"header_elements": default_delivery_header_elements()[:4]},
    })
    without = dict(base)
    legacy_without = _pdf_text(generate_sales_delivery_pdf_bytes(without, design=legacy))
    generic_without = _pdf_text(generate_sales_delivery_pdf_bytes(without, design=generic))
    assert "081234567890" not in legacy_without
    assert "081234567890" not in generic_without
    assert "Penerima" in legacy_without
    assert "Penerima" in generic_without
    assert "Cup Phone" in legacy_without
    assert "Cup Phone" in generic_without

    with_phone = dict(base, recipient_phone="081234567890", recipient_phone_wa="6281234567890")
    legacy_text = _pdf_text(generate_sales_delivery_pdf_bytes(with_phone, design=legacy))
    generic_text = _pdf_text(generate_sales_delivery_pdf_bytes(with_phone, design=generic))
    assert "081234567890" in legacy_text
    assert "081234567890" in generic_text
    assert "089000000000" not in legacy_text

    signed = _pdf_text(generate_sales_delivery_pdf_bytes(
        with_phone,
        None,
        recipient_name="Ibu Sari",
        design=legacy,
    ))
    assert "Ibu Sari" in signed
    assert "081234567890" in signed
    assert "089000000000" not in signed

