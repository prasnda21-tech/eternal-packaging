"""Comprehensive backend tests for Pesanan Penjualan (Manual Sales Order Creation).

Covers:
- CASE A: manual create 1 line cetak -> success
- CASE B: manual create multi-line -> success
- CASE C: line item with same name on separate lines -> exact identity preserved (not merged)
- CASE D: qty 0 / negative -> reject (400)
- CASE E: duplicate No PO -> reject (409)
- CASE F: customer empty / invalid -> reject (400)
- CASE G: manual PO initial workflow matches imported PO (stage 0, Order Masuk, timeline)
- CASE H: manual PO appears in list / Status PO / Order Masuk
- CASE I: RBAC unauthorized -> 403
- CASE J: normalized business payload comparison: manual create vs imported PO
"""
import os
import sys
import uuid
import pytest
from datetime import datetime, timezone
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from server import app, db, current_user, _persist_imported_po, _next_suggested_po_number


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


async def _seed_master(name: str, *, item_code: str = "", item_type: str = "INVENTORY") -> dict:
    import server
    mid = f"mst-{uuid.uuid4().hex[:10]}"
    aid = f"aid-{uuid.uuid4().hex[:10]}"
    doc = {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": item_code or f"SKU-{uuid.uuid4().hex[:6].upper()}",
        "item_name": name,
        "name": name,
        "unit": "pcs",
        "item_type": item_type,
        "is_active": True,
    }
    await server.db.master_items.insert_one(dict(doc))
    return doc


def _line_with_master(master: dict, **extra) -> dict:
    row = {
        "item_code": master.get("item_code"),
        "name": master.get("item_name") or master.get("name"),
        "quantity": 100,
        "unit": "pcs",
        "production_process": "print",
        "master_item_id": master["id"],
        "accurate_item_id": master.get("accurate_item_id"),
    }
    row.update(extra)
    return row


@pytest.mark.anyio
async def test_case_a_manual_create_single_line_print():
    """CASE A: manual create 1 line cetak -> sukses."""
    master = await _seed_master("Paper Cup 12oz Motif", item_code="PC-12")
    po_num = f"PO-TEST-A-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "order_date": "2026-08-30",
        "customer": "PT Sumber Rasa Jaya",
        "outlet": "Pusat",
        "sales": "Vira",
        "recipient": "Pak Budi",
        "address": "Jl. Industri No. 10",
        "delivery_zone": "Jakarta Barat",
        "deadline": "2026-09-02",
        "estimated_delivery": "2026-09-02",
        "payment_terms": "Net 30",
        "expedition": "Internal fleet",
        "notes": "Jangan ditumpuk lebih dari 5 box",
        "line_items": [
            _line_with_master(
                master,
                quantity=5000,
                color="2 Warna",
                production_process="print",
                notes="Warna navy dan kuning",
            )
        ],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 200, res.text
            data = res.json()
            order = data["order"]
            assert order["po_number"] == po_num
            assert order["customer"] == "PT Sumber Rasa Jaya"
            assert order["total_quantity"] == 5000
            assert order["item_count"] == 1
            assert len(order["line_items"]) == 1
            assert order["line_items"][0]["production_process"] == "print"
            assert order["line_items"][0]["quantity"] == 5000
            assert order["current_stage"] == 0
            assert order["status_label"] == "Order Masuk"
            assert order["import_source"] == "sales_order_manual"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_b_manual_create_multiline():
    """CASE B: manual create multi-line -> sukses."""
    m1 = await _seed_master("Paper Bowl 800ml", item_code="BW-01")
    m2 = await _seed_master("Tutup Bowl 800ml Bening", item_code="LD-01")
    m3 = await _seed_master("Sendok Plastik Hitam", item_code="SP-01")
    po_num = f"PO-TEST-B-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "CV Maju Bersama",
        "outlet": "Cabang Bandung",
        "sales": "Rina",
        "line_items": [
            _line_with_master(m1, quantity=3000, color="1 Warna", production_process="print"),
            _line_with_master(m2, quantity=3000, production_process="non_print"),
            _line_with_master(m3, quantity=1500, production_process="non_print"),
        ],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "crm@eternalpackaging.com",
        "name": "CRM Sales",
        "role": "crm",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 200, res.text
            order = res.json()["order"]
            assert order["po_number"] == po_num
            assert order["total_quantity"] == 7500
            assert order["item_count"] == 3
            assert len(order["line_items"]) == 3
            assert order["line_items"][0]["production_process"] == "print"
            assert order["line_items"][1]["production_process"] == "non_print"
            assert order["line_items"][2]["production_process"] == "non_print"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_c_exact_line_identity_no_merge():
    """CASE C: line item nama sama tapi baris berbeda -> tidak dimerge."""
    m1 = await _seed_master("Cup 8oz", item_code="PC-08")
    m2 = await _seed_master("Cup 8oz", item_code="PC-08")
    po_num = f"PO-TEST-C-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "Kopi Kenangan Senja",
        "line_items": [
            _line_with_master(
                m1, quantity=1000, color="Putih Polos", production_process="non_print", notes="Batch Pagi"
            ),
            _line_with_master(
                m2, quantity=2000, color="Hitam Matte", production_process="print", notes="Batch Sore"
            ),
        ],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 200
            order = res.json()["order"]
            assert len(order["line_items"]) == 2
            assert order["line_items"][0]["quantity"] == 1000
            assert order["line_items"][0]["notes"] == "Batch Pagi"
            assert order["line_items"][1]["quantity"] == 2000
            assert order["line_items"][1]["notes"] == "Batch Sore"
            assert order["total_quantity"] == 3000
            assert order["item_count"] == 2
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_d_reject_zero_or_negative_qty():
    """CASE D: qty 0 / negative -> reject (400)."""
    payload_zero = {
        "customer": "PT Retail Sukses",
        "line_items": [
            {"name": "Box Nasi", "quantity": 0, "production_process": "print"}
        ],
    }
    payload_neg = {
        "customer": "PT Retail Sukses",
        "line_items": [
            {"name": "Box Nasi", "quantity": -50, "production_process": "print"}
        ],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/sales-orders", json=payload_zero)
            assert r1.status_code == 400
            assert "Quantity harus > 0" in r1.text

            r2 = await client.post("/api/sales-orders", json=payload_neg)
            assert r2.status_code == 400
            assert "Quantity harus > 0" in r2.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_e_duplicate_po_number_rejected():
    """CASE E: duplicate No PO -> reject (409)."""
    master = await _seed_master("Item A")
    po_num = f"PO-TEST-DUP-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "PT Pelanggan Tetap",
        "line_items": [_line_with_master(master, quantity=100)],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/sales-orders", json=payload)
            assert r1.status_code == 200

            r2 = await client.post("/api/sales-orders", json=payload)
            assert r2.status_code == 409
            assert "sudah pernah digunakan" in r2.text or "sudah terdaftar" in r2.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_f_invalid_customer_or_empty_lines():
    """CASE F: customer invalid / kosong -> reject (400)."""
    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Empty customer
            r1 = await client.post("/api/sales-orders", json={"customer": "  ", "line_items": [{"name": "Item", "quantity": 10}]})
            assert r1.status_code == 400
            assert "Customer wajib diisi" in r1.text

            # Empty lines
            r2 = await client.post("/api/sales-orders", json={"customer": "PT Ada", "line_items": []})
            assert r2.status_code == 400

            # Line item without name
            r3 = await client.post("/api/sales-orders", json={"customer": "PT Ada", "line_items": [{"name": "", "quantity": 10}]})
            assert r3.status_code == 400
            assert "Nama barang/jasa wajib diisi" in r3.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_g_initial_workflow_matches_import():
    """CASE G: manual PO initial workflow matches imported PO (stage 0, timeline, initial status)."""
    master = await _seed_master("Barang X")
    po_num = f"PO-TEST-G-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "PT Workflow Test",
        "line_items": [_line_with_master(master, quantity=500, production_process="print")],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "crm@eternalpackaging.com",
        "name": "CRM User",
        "role": "crm",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 200
            order = res.json()["order"]
            assert order["current_stage"] == 0
            assert order["status_label"] == "Order Masuk"
            assert order["packing_status"] == "Belum"
            assert order["delivery_status"] == "Belum Terkirim"
            assert order["payment_status"] == "Waiting"
            assert order["design_status"] == "Waiting"
            assert isinstance(order["timeline"], list)
            assert len(order["timeline"]) == 1
            assert order["timeline"][0]["stage"] == 0
            assert order["timeline"][0]["stage_label"] == "Order Masuk"
            assert order["timeline"][0]["by_email"] == "crm@eternalpackaging.com"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_h_appears_in_list_and_status_po():
    """CASE H: manual PO appears in list / Status PO."""
    master = await _seed_master("Barang H")
    po_num = f"PO-TEST-H-{uuid.uuid4().hex[:6].upper()}"
    cust_name = f"Cust H {uuid.uuid4().hex[:4]}"
    payload = {
        "po_number": po_num,
        "customer": cust_name,
        "outlet": "Cabang H",
        "line_items": [_line_with_master(master, quantity=1200)],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r_create = await client.post("/api/sales-orders", json=payload)
            assert r_create.status_code == 200
            order_id = r_create.json()["order"]["id"]

            r_list = await client.get(f"/api/sales-orders?q={po_num}")
            assert r_list.status_code == 200
            orders = r_list.json()["orders"]
            assert any(o["id"] == order_id for o in orders)

            # Query status-po
            r_spo = await client.get("/api/dashboard/status-po")
            assert r_spo.status_code == 200
            rows = r_spo.json()["rows"]
            assert any(r["id"] == order_id for r in rows)

            # Query orders (Order Masuk)
            r_ord = await client.get("/api/orders?status=all")
            assert r_ord.status_code == 200
            ord_list = r_ord.json()["orders"]
            assert any(o["id"] == order_id for o in ord_list)

            # Get order detail
            r_detail = await client.get(f"/api/orders/{order_id}")
            assert r_detail.status_code == 200
            assert r_detail.json()["order"]["po_number"] == po_num
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_i_rbac_unauthorized_rejected():
    """CASE I: RBAC unauthorized -> 403."""
    payload = {
        "customer": "PT Unauthorized Attempt",
        "line_items": [{"name": "Barang", "quantity": 100}],
    }

    app.dependency_overrides[current_user] = lambda: {
        "email": "driver@eternalpackaging.com",
        "name": "Driver User",
        "role": "driver",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_j_normalized_payload_equivalence():
    """CASE J: normalized business payload comparison: manual create vs imported PO."""
    m1 = await _seed_master("Produk Satu", item_code="ITEM-1")
    m2 = await _seed_master("Produk Dua", item_code="ITEM-2")
    po_num_man = f"PO-COMP-M-{uuid.uuid4().hex[:6].upper()}"
    payload_man = {
        "po_number": po_num_man,
        "order_date": "2026-08-30",
        "customer": "PT Perbandingan Identik",
        "outlet": "Cabang Utama",
        "sales": "Sales Satu",
        "recipient": "Pak Doni",
        "address": "Kawasan Industri MM2100",
        "deadline": "2026-09-03",
        "estimated_delivery": "2026-09-03",
        "payment_terms": "Net 14",
        "expedition": "Internal fleet",
        "notes": "Prioritas standar",
        "line_items": [
            _line_with_master(m1, quantity=1000, color="Merah", production_process="print"),
            _line_with_master(m2, quantity=500, production_process="non_print"),
        ],
    }

    preview_imp = {
        "po_number": f"PO-COMP-I-{uuid.uuid4().hex[:6].upper()}",
        "order_date": "2026-08-30",
        "customer": "PT Perbandingan Identik",
        "outlet": "Cabang Utama",
        "sales": "Sales Satu",
        "recipient": "Pak Doni",
        "address": "Kawasan Industri MM2100",
        "deadline": "2026-09-03",
        "estimated_delivery": "2026-09-03",
        "payment_terms": "Net 14",
        "expedition": "Internal fleet",
        "notes": "Prioritas standar",
        "line_items": [
            {"item_code": "ITEM-1", "name": "Produk Satu", "quantity": 1000, "unit": "pcs", "color": "Merah", "production_process": "print"},
            {"item_code": "ITEM-2", "name": "Produk Dua", "quantity": 500, "unit": "pcs", "production_process": "non_print"},
        ],
        "total_quantity": 1500,
        "item_count": 2,
    }

    test_user = {"email": "admin@eternalpackaging.com", "name": "Admin Tester", "role": "admin"}

    imported_doc = await _persist_imported_po(preview_imp, source="accurate", user=test_user)

    app.dependency_overrides[current_user] = lambda: test_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload_man)
            assert res.status_code == 200
            manual_doc = res.json()["order"]
    finally:
        app.dependency_overrides.pop(current_user, None)

    invariant_keys = [
        "customer", "outlet", "recipient", "address", "sales", "payment_terms",
        "payment_status", "design_status", "expedition", "total_quantity", "item_count",
        "current_stage", "status_label", "packing_status", "delivery_status", "notes"
    ]
    for k in invariant_keys:
        assert manual_doc[k] == imported_doc[k], f"Mismatch for field '{k}': manual={manual_doc[k]} vs import={imported_doc[k]}"

    assert len(manual_doc["line_items"]) == len(imported_doc["line_items"])
    for li_m, li_i in zip(manual_doc["line_items"], imported_doc["line_items"]):
        assert li_m["name"] == li_i["name"]
        assert li_m["quantity"] == li_i["quantity"]
        assert li_m["production_process"] == li_i["production_process"]
        assert li_m["unit"] == li_i["unit"]
