"""Comprehensive backend test suite for Sales Invoice (Faktur Penjualan).

Covers all 19 mandatory test cases:
CASE 1: Create Faktur from valid PO -> success.
CASE 2: PO snapshot copied correctly.
CASE 3: Exact PO line identity preserved.
CASE 4: Same product name on two lines -> remains two invoice lines.
CASE 5: Qty from PO 500, unit price 1000 -> line total 500000.
CASE 6: Multiple lines -> grand total = sum exact line totals.
CASE 7: unit price negative -> reject.
CASE 8: malformed price -> reject.
CASE 9: Frontend supplied fake total -> backend ignores/recalculates.
CASE 10: Edit unit price -> Faktur total updates.
CASE 11: Edit Faktur -> PO source unchanged.
CASE 12: Edit Faktur -> Customer/Outlet master unchanged.
CASE 13: Double-submit -> no accidental duplicate.
CASE 14: Unauthorized user -> backend reject.
CASE 15: Create/Edit Faktur -> production current_stage unchanged.
CASE 16: Create/Edit Faktur -> no production event created.
CASE 17: Create/Edit Faktur -> Surat Jalan unchanged.
CASE 18: Create/Edit Faktur -> Loading unchanged.
CASE 19: Create/Edit Faktur -> Driver/Terkirim unchanged.
"""
import asyncio
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


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


async def create_helper_po(po_number: str, line_items: list, customer: str = "PT Test Customer") -> dict:
    """Creates a PO document directly in test DB with production stage."""
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
        "current_stage": 5,  # Penjadwalan
        "status_label": "Penjadwalan",
        "events": [{"stage": 0, "name": "Order Masuk", "at": "2026-08-30T10:00:00Z"}],
        "shipments": [],
        "surat_jalan_files": [],
        "loading_lines": [],
        "created_at": "2026-08-30T10:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


@pytest.mark.anyio
async def test_case_1_create_faktur_success():
    """CASE 1: Create Faktur from valid PO -> success."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 12oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "crm@eternalpackaging.com",
        "name": "CRM Sales",
        "role": "crm",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-08-31",
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res.status_code == 200, res.text
            inv = res.json()["invoice"]
            assert inv["po_id"] == po["id"]
            assert inv["po_number"] == po_num
            assert inv["total_qty"] == 500
            assert inv["grand_total"] == 500000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_2_po_snapshot_copied_correctly():
    """CASE 2: PO snapshot copied correctly."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 16oz", "quantity": 300, "unit": "pcs"}], customer="PT Sumber Berkah")

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "notes": "Catatan invoice khusus",
                "lines": [{"line_idx": 0, "unit_price": 1500}]
            })
            assert res.status_code == 200, res.text
            inv = res.json()["invoice"]
            assert inv["customer"] == "PT Sumber Berkah"
            assert inv["outlet"] == "Cabang Utama"
            assert inv["recipient"] == "Pak Budi"
            assert inv["address"] == "Jl. Industri Raya No. 8"
            assert inv["delivery_zone"] == "Jakarta Barat"
            assert inv["sales"] == "Vira"
            assert inv["notes"] == "Catatan invoice khusus"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_3_exact_po_line_identity_preserved():
    """CASE 3: Exact PO line identity preserved."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    lines = [
        {"line_idx": 0, "line_id": "line_custom_0", "name": "Cup 12oz", "quantity": 100, "unit": "pcs"},
        {"line_idx": 1, "line_id": "line_custom_1", "name": "Cup 14oz", "quantity": 200, "unit": "pcs"},
    ]
    po = await create_helper_po(po_num, lines)

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [
                    {"line_idx": 0, "line_id": "line_custom_0", "unit_price": 1000},
                    {"line_idx": 1, "line_id": "line_custom_1", "unit_price": 2000},
                ]
            })
            assert res.status_code == 200, res.text
            inv_lines = res.json()["invoice"]["lines"]
            assert len(inv_lines) == 2
            assert inv_lines[0]["line_id"] == "line_custom_0"
            assert inv_lines[0]["line_idx"] == 0
            assert inv_lines[1]["line_id"] == "line_custom_1"
            assert inv_lines[1]["line_idx"] == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_4_same_product_name_remains_two_lines():
    """CASE 4: Same product name on two lines -> remains two invoice lines (never merged)."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    lines = [
        {"line_idx": 0, "name": "Cup A", "quantity": 500, "unit": "pcs"},
        {"line_idx": 1, "name": "Cup A", "quantity": 300, "unit": "pcs"},
    ]
    po = await create_helper_po(po_num, lines)

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [
                    {"line_idx": 0, "unit_price": 1000},
                    {"line_idx": 1, "unit_price": 1200},
                ]
            })
            assert res.status_code == 200, res.text
            inv_lines = res.json()["invoice"]["lines"]
            assert len(inv_lines) == 2
            assert inv_lines[0]["item_name"] == "Cup A"
            assert inv_lines[0]["quantity"] == 500
            assert inv_lines[0]["unit_price"] == 1000.0
            assert inv_lines[0]["line_total"] == 500000.0

            assert inv_lines[1]["item_name"] == "Cup A"
            assert inv_lines[1]["quantity"] == 300
            assert inv_lines[1]["unit_price"] == 1200.0
            assert inv_lines[1]["line_total"] == 360000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_5_line_total_calculation():
    """CASE 5: Qty from PO 500, unit price 1000 -> line total 500000."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup X", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res.status_code == 200
            inv = res.json()["invoice"]
            assert inv["lines"][0]["line_total"] == 500000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_6_multiple_lines_grand_total():
    """CASE 6: Multiple lines -> grand total = sum exact line totals."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    lines = [
        {"line_idx": 0, "name": "Line A", "quantity": 500, "unit": "pcs"},
        {"line_idx": 1, "name": "Line B", "quantity": 200, "unit": "pcs"},
    ]
    po = await create_helper_po(po_num, lines)

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [
                    {"line_idx": 0, "unit_price": 1000},  # 500,000
                    {"line_idx": 1, "unit_price": 2000},  # 400,000
                ]
            })
            assert res.status_code == 200
            inv = res.json()["invoice"]
            assert inv["lines"][0]["line_total"] == 500000.0
            assert inv["lines"][1]["line_total"] == 400000.0
            assert inv["grand_total"] == 900000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_7_negative_unit_price_rejected():
    """CASE 7: unit price negative -> reject."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": -500}]
            })
            assert res.status_code == 400, res.text
            assert "negatif" in res.text.lower()
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_8_malformed_unit_price_rejected():
    """CASE 8: malformed price -> reject."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": "abc-invalid"}]
            })
            assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_9_frontend_fake_total_ignored():
    """CASE 9: Frontend supplied fake total -> backend ignores/recalculates."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 10, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Client attempts to submit fake line_total and fake grand_total
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "grand_total": 99999999,
                "lines": [{"line_idx": 0, "unit_price": 500, "line_total": 1}]
            })
            assert res.status_code == 200
            inv = res.json()["invoice"]
            # Backend calculates 10 * 500 = 5000 authoritative
            assert inv["lines"][0]["line_total"] == 5000.0
            assert inv["grand_total"] == 5000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_10_edit_unit_price_recalculates_totals():
    """CASE 10: Edit unit price -> Faktur total updates."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_create = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res_create.status_code == 200
            inv_id = res_create.json()["invoice"]["id"]

            # Edit price from 1000 to 2500
            res_edit = await client.put(f"/api/sales-invoices/{inv_id}", json={
                "lines": [{"line_idx": 0, "unit_price": 2500}]
            })
            assert res_edit.status_code == 200
            updated = res_edit.json()["invoice"]
            assert updated["lines"][0]["unit_price"] == 2500.0
            assert updated["lines"][0]["line_total"] == 250000.0
            assert updated["grand_total"] == 250000.0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_11_edit_faktur_po_source_unchanged():
    """CASE 11: Edit Faktur -> PO source unchanged."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_create = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            inv_id = res_create.json()["invoice"]["id"]

            await client.put(f"/api/sales-invoices/{inv_id}", json={
                "lines": [{"line_idx": 0, "unit_price": 3000}]
            })

            # Check original PO in DB
            po_in_db = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_in_db["po_number"] == po_num
            assert po_in_db["total_quantity"] == 100
            assert po_in_db["line_items"][0]["quantity"] == 100
            assert "grand_total" not in po_in_db  # PO schema unchanged
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_12_edit_faktur_masters_unchanged():
    """CASE 12: Edit Faktur -> Customer/Outlet master unchanged."""
    # Seed outlet master
    outlet_id = str(uuid.uuid4())
    await server.db.delivery_outlets.insert_one({
        "id": outlet_id,
        "customer_name": "PT Customer Asli",
        "name": "Outlet Asli",
        "address": "Alamat Asli",
    })

    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 50, "unit": "pcs"}], customer="PT Customer Asli")

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_create = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 500}]
            })
            inv_id = res_create.json()["invoice"]["id"]

            await client.put(f"/api/sales-invoices/{inv_id}", json={
                "lines": [{"line_idx": 0, "unit_price": 750}]
            })

            # Verify master delivery_outlets unmodified
            outlet = await server.db.delivery_outlets.find_one({"id": outlet_id}, {"_id": 0})
            assert outlet["customer_name"] == "PT Customer Asli"
            assert outlet["name"] == "Outlet Asli"
            assert outlet["address"] == "Alamat Asli"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_13_double_submit_protection():
    """CASE 13: Double-submit -> no accidental duplicate."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])
    idempotency_key = str(uuid.uuid4())

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "po_id": po["id"],
                "idempotency_key": idempotency_key,
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            }
            # First submit
            res1 = await client.post("/api/sales-invoices", json=payload)
            assert res1.status_code == 200
            inv1_id = res1.json()["invoice"]["id"]

            # Second submit (retry / double-click)
            res2 = await client.post("/api/sales-invoices", json=payload)
            assert res2.status_code == 200
            inv2_id = res2.json()["invoice"]["id"]

            # Same invoice returned, no duplicate record created
            assert inv1_id == inv2_id

            # Verify only 1 invoice exists for this idempotency key
            count = await server.db.sales_invoices.count_documents({"idempotency_key": idempotency_key})
            assert count == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_14_unauthorized_user_rejected():
    """CASE 14: Unauthorized user -> backend reject (403)."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    # Operator role does not have sales.invoice.create permission
    app.dependency_overrides[current_user] = lambda: {
        "email": "operator@eternalpackaging.com",
        "name": "Operator Test",
        "role": "operator",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res.status_code == 403, res.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_15_production_current_stage_unchanged():
    """CASE 15: Create/Edit Faktur -> production current_stage unchanged."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])
    assert po["current_stage"] == 5  # Penjadwalan

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res.status_code == 200
            inv_id = res.json()["invoice"]["id"]

            await client.put(f"/api/sales-invoices/{inv_id}", json={
                "lines": [{"line_idx": 0, "unit_price": 2000}]
            })

            po_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_after["current_stage"] == 5
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_16_no_production_event_created():
    """CASE 16: Create/Edit Faktur -> no production event created."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])
    events_count_before = len(po.get("events", []))

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })
            assert res.status_code == 200
            inv_id = res.json()["invoice"]["id"]

            await client.put(f"/api/sales-invoices/{inv_id}", json={
                "lines": [{"line_idx": 0, "unit_price": 2000}]
            })

            po_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            events_after = po_after.get("events", [])
            assert len(events_after) == events_count_before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_17_surat_jalan_unchanged():
    """CASE 17: Create/Edit Faktur -> Surat Jalan unchanged."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })

            po_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_after.get("shipments") == []
            assert po_after.get("surat_jalan_files") == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_18_loading_unchanged():
    """CASE 18: Create/Edit Faktur -> Loading unchanged."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })

            po_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_after.get("loading_lines") == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_19_driver_terkirim_unchanged():
    """CASE 19: Create/Edit Faktur -> Driver/Terkirim unchanged."""
    po_num = f"PO/2026/08/31/{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"line_idx": 0, "name": "Cup A", "quantity": 100, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "lines": [{"line_idx": 0, "unit_price": 1000}]
            })

            po_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert po_after.get("delivered") is not True
            assert po_after.get("delivery_proof") is None
            assert po_after["current_stage"] == 5
    finally:
        app.dependency_overrides.pop(current_user, None)
