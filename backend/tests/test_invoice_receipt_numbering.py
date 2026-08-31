"""Targeted Unit and Integration Tests for Sales Invoice & Receipt Numbering.

Covers exact numbering derivation rules:
- PO number transformed into INV for Invoice (INV/2026/08/20/000019, INV-2425EC)
- Subsequent invoices for same exact PO numbered -2, -3 (INV/2026/08/20/000019-2, INV-2425EC-2, etc.)
- PO number transformed into PAY for Receipt (PAY/2026/08/20/000019, PAY-2425EC)
- Subsequent receipts for same exact PO numbered -2, -3 (PAY/2026/08/20/000019-2, PAY-2425EC-2, etc.)
- Separation between internal UUID (invoice.id) and user-facing business number (invoice.invoice_number)
- Separation between internal UUID (receipt.id) and user-facing business number (receipt.receipt_number)
- Frontend fake numbers rejected / ignored (backend authoritative)
- Immutability on edit
- Double-submit & duplicate protection
- Preserves exact invoice_id and po_id relational links
- Read endpoints do not persist business-number mutations
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
import httpx
import uuid
import re

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.sales_invoices import (
    derive_base_invoice_number,
    derive_invoice_number,
    create_sales_invoice,
    update_sales_invoice,
    list_sales_invoices,
    get_sales_invoice_detail,
)
from services.sales_receipts import (
    derive_base_receipt_number,
    derive_receipt_number,
    create_sales_receipt,
    list_sales_receipts,
    get_sales_receipt_detail,
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


async def _seed_test_order(po_number: str, lines: list):
    order_id = str(uuid.uuid4())
    doc = {
        "id": order_id,
        "po_number": po_number,
        "customer": "PT Sumber Rejeki",
        "outlet": "Pusat",
        "recipient": "Bpk Budi",
        "address": "Jl. Industri No 12",
        "order_date": "2026-08-31",
        "cancelled": False,
        "line_items": lines,
        "items": lines,
        "timeline": [],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


TEST_USER = {
    "id": "usr-admin-1",
    "name": "Admin User",
    "email": "admin@example.com",
    "role": "admin",
}


# ==============================================================================
# FAKTUR NUMBERING UNIT & INTEGRATION TESTS (CASES 1 - 7)
# ==============================================================================

@pytest.mark.anyio
async def test_case_1_first_invoice_number():
    """CASE 1: PO: PO/2026/08/20/000019 -> first invoice: INV/2026/08/20/000019"""
    assert derive_invoice_number("PO/2026/08/20/000019", 1) == "INV/2026/08/20/000019"
    assert derive_invoice_number("PO/2026/08/31/000125", 1) == "INV/2026/08/31/000125"

    po_num = "PO/2026/08/20/000019"
    order = await _seed_test_order(f"{po_num}-{uuid.uuid4().hex[:4]}", [
        {"line_idx": 0, "line_id": "L1", "item_name": "Dus Box", "quantity": 1000, "unit": "pcs", "price": 1500}
    ])

    payload = {
        "po_id": order["id"],
        "invoice_date": "2026-08-31",
        "lines": [{"line_idx": 0, "quantity": 300, "unit_price": 1500}],
    }
    inv = await create_sales_invoice(server.db, payload, TEST_USER)
    expected_num = derive_invoice_number(order["po_number"], 1)
    assert inv["invoice_number"] == expected_num
    assert inv["invoice_index"] == 1
    # Check internal ID is UUID
    assert re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', inv["id"])
    assert inv["id"] != inv["invoice_number"]


@pytest.mark.anyio
async def test_case_2_second_invoice_same_po():
    """CASE 2: second invoice same PO -> INV/2026/08/20/000019-2"""
    assert derive_invoice_number("PO/2026/08/20/000019", 2) == "INV/2026/08/20/000019-2"

    po_num = f"PO/2026/08/20/000019-{uuid.uuid4().hex[:4]}"
    order = await _seed_test_order(po_num, [
        {"line_idx": 0, "line_id": "L1", "item_name": "Dus Box", "quantity": 1000, "unit": "pcs", "price": 1500}
    ])

    # Invoice 1
    inv1 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 300, "unit_price": 1500}],
    }, TEST_USER)
    assert inv1["invoice_number"] == derive_invoice_number(po_num, 1)

    # Invoice 2
    inv2 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 400, "unit_price": 1500}],
    }, TEST_USER)
    assert inv2["invoice_number"] == derive_invoice_number(po_num, 2)
    assert inv2["invoice_index"] == 2


@pytest.mark.anyio
async def test_case_3_dash_format_po_number():
    """CASE 3: PO: PO-2425EC -> first invoice: INV-2425EC, second: INV-2425EC-2"""
    assert derive_invoice_number("PO-2425EC", 1) == "INV-2425EC"
    assert derive_invoice_number("PO-2425EC", 2) == "INV-2425EC-2"

    po_num = f"PO-2425EC-{uuid.uuid4().hex[:4]}"
    order = await _seed_test_order(po_num, [
        {"line_idx": 0, "line_id": "L1", "item_name": "Dus Box", "quantity": 1000, "unit": "pcs", "price": 1500}
    ])

    inv1 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 300, "unit_price": 1500}],
    }, TEST_USER)
    inv2 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 400, "unit_price": 1500}],
    }, TEST_USER)

    assert inv1["invoice_number"] == derive_invoice_number(po_num, 1)
    assert inv2["invoice_number"] == derive_invoice_number(po_num, 2)


@pytest.mark.anyio
async def test_case_4_different_po_number():
    """CASE 4: different PO: PO/2026/08/31/000126 -> INV/2026/08/31/000126"""
    po_num1 = f"PO/2026/08/31/NUM1-{uuid.uuid4().hex[:6]}"
    po_num2 = f"PO/2026/08/31/NUM2-{uuid.uuid4().hex[:6]}"
    order1 = await _seed_test_order(po_num1, [{"line_idx": 0, "item_name": "A", "quantity": 100, "unit": "pcs"}])
    order2 = await _seed_test_order(po_num2, [{"line_idx": 0, "item_name": "B", "quantity": 200, "unit": "pcs"}])

    inv1 = await create_sales_invoice(server.db, {"po_id": order1["id"], "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}]}, TEST_USER)
    inv2 = await create_sales_invoice(server.db, {"po_id": order2["id"], "lines": [{"line_idx": 0, "quantity": 200, "unit_price": 2000}]}, TEST_USER)

    assert inv1["invoice_number"] == derive_invoice_number(po_num1, 1)
    assert inv2["invoice_number"] == derive_invoice_number(po_num2, 1)


@pytest.mark.anyio
async def test_case_5_frontend_fake_invoice_number_ignored():
    """CASE 5: frontend sends fake invoice number -> ignored, backend derived number used"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "A", "quantity": 100, "unit": "pcs"}])

    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "invoice_number": "FAKED-NUMBER-9999",
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}],
    }, TEST_USER)

    assert inv["invoice_number"] == derive_invoice_number(po_num, 1)
    assert inv["invoice_number"] != "FAKED-NUMBER-9999"


@pytest.mark.anyio
async def test_case_6_edit_invoice_number_immutable():
    """CASE 6: edit invoice -> number unchanged and immutable"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "A", "quantity": 100, "unit": "pcs"}])

    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}],
    }, TEST_USER)

    # Edit attempt sending different invoice_number
    updated = await update_sales_invoice(server.db, inv["id"], {
        "invoice_number": "CHANGED-NUMBER",
        "discount_amount": 5000,
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1200}],
    }, TEST_USER)

    assert updated["invoice_number"] == derive_invoice_number(po_num, 1)
    assert updated["discount_amount"] == 5000


@pytest.mark.anyio
async def test_case_7_double_submit_no_duplicate_invoice_number():
    """CASE 7: double submit with idempotency key -> no duplicate invoice number"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "A", "quantity": 100, "unit": "pcs"}])

    key = f"idem-inv-submit-{uuid.uuid4().hex}"
    inv1 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "idempotency_key": key,
        "lines": [{"line_idx": 0, "quantity": 50, "unit_price": 1000}],
    }, TEST_USER)

    inv2 = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "idempotency_key": key,
        "lines": [{"line_idx": 0, "quantity": 50, "unit_price": 1000}],
    }, TEST_USER)

    assert inv1["id"] == inv2["id"]
    assert inv1["invoice_number"] == inv2["invoice_number"] == derive_invoice_number(po_num, 1)
    
    # Verify count in DB is exactly 1
    count = await server.db.sales_invoices.count_documents({"po_id": order["id"]})
    assert count == 1


# ==============================================================================
# PENERIMAAN NUMBERING UNIT & INTEGRATION TESTS (CASES 8 - 15)
# ==============================================================================

@pytest.mark.anyio
async def test_case_8_first_receipt_number():
    """CASE 8: PO: PO/2026/08/20/000019 -> first receipt: PAY/2026/08/20/000019"""
    assert derive_receipt_number("PO/2026/08/20/000019", 1) == "PAY/2026/08/20/000019"
    assert derive_receipt_number("PO/2026/08/31/000125", 1) == "PAY/2026/08/31/000125"

    po_num = f"PO/2026/08/20/000019-{uuid.uuid4().hex[:4]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 10000}],
    }, TEST_USER)

    rec = await create_sales_receipt(server.db, {
        "invoice_id": inv["id"],
        "amount": 300000,
    }, TEST_USER)

    assert rec["receipt_number"] == derive_receipt_number(po_num, 1)
    assert rec["receipt_index"] == 1
    # Check internal ID is UUID
    assert re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', rec["id"])
    assert rec["id"] != rec["receipt_number"]


@pytest.mark.anyio
async def test_case_9_second_receipt_same_po():
    """CASE 9: second receipt same PO -> PAY/2026/08/20/000019-2"""
    assert derive_receipt_number("PO/2026/08/20/000019", 2) == "PAY/2026/08/20/000019-2"

    po_num = f"PO/2026/08/20/000019-{uuid.uuid4().hex[:4]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 10000}],
    }, TEST_USER)

    rec1 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 300000}, TEST_USER)
    rec2 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 400000}, TEST_USER)

    assert rec1["receipt_number"] == derive_receipt_number(po_num, 1)
    assert rec2["receipt_number"] == derive_receipt_number(po_num, 2)
    assert rec2["receipt_index"] == 2


@pytest.mark.anyio
async def test_case_10_dash_format_receipt_number():
    """CASE 10: PO: PO-2425EC -> first receipt: PAY-2425EC, second: PAY-2425EC-2"""
    assert derive_receipt_number("PO-2425EC", 1) == "PAY-2425EC"
    assert derive_receipt_number("PO-2425EC", 2) == "PAY-2425EC-2"

    po_num = f"PO-2425EC-{uuid.uuid4().hex[:4]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 10000}],
    }, TEST_USER)

    rec1 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 300000}, TEST_USER)
    rec2 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 400000}, TEST_USER)

    assert rec1["receipt_number"] == derive_receipt_number(po_num, 1)
    assert rec2["receipt_number"] == derive_receipt_number(po_num, 2)


@pytest.mark.anyio
async def test_case_11_receipt_dp_and_lunas_unique_numbers():
    """CASE 11: receipt #1 DP, receipt #2 LUNAS -> numbers remain unique"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 10000}],
    }, TEST_USER)

    rec_dp = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 500000}, TEST_USER)
    rec_lunas = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "amount": 500000}, TEST_USER)

    assert rec_dp["payment_state"] == "DP"
    assert rec_dp["receipt_number"] == derive_receipt_number(po_num, 1)

    assert rec_lunas["payment_state"] == "LUNAS"
    assert rec_lunas["receipt_number"] == derive_receipt_number(po_num, 2)


@pytest.mark.anyio
async def test_case_12_receipt_relation_remains_exact_invoice_id():
    """CASE 12: receipt relation remains exact invoice_id even across multiple partial invoices"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 200, "unit": "pcs"}])
    inv1 = await create_sales_invoice(server.db, {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 5000}]}, TEST_USER)
    inv2 = await create_sales_invoice(server.db, {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 5000}]}, TEST_USER)

    assert inv1["invoice_number"] == derive_invoice_number(po_num, 1)
    assert inv2["invoice_number"] == derive_invoice_number(po_num, 2)

    rec1 = await create_sales_receipt(server.db, {"invoice_id": inv1["id"], "amount": 500000}, TEST_USER)
    rec2 = await create_sales_receipt(server.db, {"invoice_id": inv2["id"], "amount": 500000}, TEST_USER)

    assert rec1["invoice_id"] == inv1["id"]
    assert rec1["invoice_number"] == derive_invoice_number(po_num, 1)
    assert rec1["receipt_number"] == derive_receipt_number(po_num, 1)

    assert rec2["invoice_id"] == inv2["id"]
    assert rec2["invoice_number"] == derive_invoice_number(po_num, 2)
    assert rec2["receipt_number"] == derive_receipt_number(po_num, 2)


@pytest.mark.anyio
async def test_case_13_frontend_fake_pay_number_ignored():
    """CASE 13: frontend sends fake PAY number -> backend remains authoritative"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 5000}]}, TEST_USER)

    rec = await create_sales_receipt(server.db, {
        "invoice_id": inv["id"],
        "receipt_number": "FAKED-PAY-9999",
        "amount": 200000,
    }, TEST_USER)

    assert rec["receipt_number"] == derive_receipt_number(po_num, 1)
    assert rec["receipt_number"] != "FAKED-PAY-9999"


@pytest.mark.anyio
async def test_case_14_read_endpoints_do_not_mutate_business_numbers():
    """CASE 14: GET list/detail must not persist a healed invoice_number."""
    legacy_id = str(uuid.uuid4())
    po_num = f"PO/2026/08/20/LEGACY-{uuid.uuid4().hex[:4]}"
    legacy_doc = {
        "id": legacy_id,
        "invoice_number": legacy_id,  # historical UUID stored as invoice_number
        "invoice_index": 1,
        "po_id": str(uuid.uuid4()),
        "po_number": po_num,
        "order_date": "2026-08-20",
        "invoice_date": "2026-08-20",
        "payment_term_days": 0,
        "due_date": "2026-08-20",
        "customer": "PT Legacy",
        "lines": [{"line_idx": 0, "quantity": 10, "unit_price": 1000}],
        "grand_total": 10000.0,
        "created_at": "2026-08-20T00:00:00Z",
    }
    await server.db.sales_invoices.insert_one(legacy_doc)

    invoices = await list_sales_invoices(server.db, q=po_num)
    matched = [i for i in invoices if i["id"] == legacy_id]
    assert len(matched) == 1
    assert matched[0]["invoice_number"] == legacy_id

    in_db = await server.db.sales_invoices.find_one({"id": legacy_id})
    assert in_db["invoice_number"] == legacy_id

    detail = await get_sales_invoice_detail(server.db, legacy_id)
    assert detail["invoice_number"] == legacy_id
    in_db_after = await server.db.sales_invoices.find_one({"id": legacy_id})
    assert in_db_after["invoice_number"] == legacy_id


@pytest.mark.anyio
async def test_case_15_double_submit_no_duplicate_pay_number():
    """CASE 15: double-submit receipt -> no duplicate PAY number / payment"""
    po_num = f"PO/2026/08/31/NUM-{uuid.uuid4().hex[:6]}"
    order = await _seed_test_order(po_num, [{"line_idx": 0, "item_name": "Dus", "quantity": 100, "unit": "pcs"}])
    inv = await create_sales_invoice(server.db, {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 5000}]}, TEST_USER)

    key = f"idem-pay-{uuid.uuid4().hex}"
    rec1 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "idempotency_key": key, "amount": 200000}, TEST_USER)
    rec2 = await create_sales_receipt(server.db, {"invoice_id": inv["id"], "idempotency_key": key, "amount": 200000}, TEST_USER)

    assert rec1["id"] == rec2["id"]
    assert rec1["receipt_number"] == rec2["receipt_number"] == derive_receipt_number(po_num, 1)

    count = await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]})
    assert count == 1
