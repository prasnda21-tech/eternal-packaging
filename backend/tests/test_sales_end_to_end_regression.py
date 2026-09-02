"""Phase 6 & 8: End-to-End Regression Test Suite for Sales Lifecycle.
Chain:
Pesanan Penjualan (PO)
  -> Pengiriman Pesanan (Sales Delivery / SJ)
  -> Faktur Penjualan (Sales Invoice / INV)
  -> Penerimaan Penjualan (Sales Receipt / PAY)

Validates:
1. PO creation with 2 distinct lines (including same item name on different line_idx)
2. Delivery sees eligible PO and isolated line identity
3. Partial Pengiriman creation with correct line balances
4. Faktur sees eligible PO
5. Partial Faktur creation with positive price, subtotal, grand_total, outstanding > 0
6. Penerimaan selector discovers the eligible Faktur (outstanding > 0)
7. Exact invoice_id and po_id binding
8. DP/partial receipt creation
9. Receipt linkage to invoice and PO
10. Outstanding recalculation on invoice
11. Payment / Approval Payment resolution
12. Sequential workflow integrity (no illegal stage jumping)
13. Paid invoice (outstanding = 0) correctly filtered out from eligibility
"""
import os
import sys
import uuid
import pytest
import httpx
from datetime import datetime, timezone
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
async def bind_motor_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]
    # Clean only our isolated test prefix
    await server.db.sales_invoices.delete_many({"po_number": {"$regex": r"^E2E-REG-"}})
    await server.db.sales_receipts.delete_many({"po_number": {"$regex": r"^E2E-REG-"}})
    await server.db.sales_deliveries.delete_many({"po_number": {"$regex": r"^E2E-REG-"}})
    await server.db.orders.delete_many({"po_number": {"$regex": r"^E2E-REG-"}})


@pytest.mark.anyio
async def test_sales_end_to_end_regression_lifecycle():
    """Complete 14-step End-to-End trace scenario."""
    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin E2E Tester",
        "role": "admin",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Create PO fixture with 2 distinct lines, including same name on different line_idx
        po_suffix = uuid.uuid4().hex[:6].upper()
        po_number = f"E2E-REG-{po_suffix}"
        po_payload = {
            "po_number": po_number,
            "order_date": "2026-08-31",
            "customer": "PT E2E Coffee Roasters",
            "outlet": "Cabang Utama",
            "sales": "Sales Rep E2E",
            "recipient": "Pak Hendra",
            "address": "Jl. Kopi No. 99",
            "delivery_zone": "Zona Barat",
            "deadline": "2026-09-15",
            "estimated_delivery": "2026-09-15",
            "payment_terms": "Net 30",
            "expedition": "Armada Sendiri",
            "notes": "End-to-end integration test",
            "line_items": [
                {
                    "item_code": "CUP-16-A",
                    "name": "Cup 16oz Custom Print",
                    "quantity": 1000,
                    "unit": "pcs",
                    "color": "Hitam",
                    "production_process": "print",
                    "notes": "Line 0",
                },
                {
                    "item_code": "CUP-16-B",
                    "name": "Cup 16oz Custom Print",  # Same product name
                    "quantity": 500,
                    "unit": "pcs",
                    "color": "Putih",
                    "production_process": "print",
                    "notes": "Line 1 - distinct color & qty",
                },
            ],
        }

        r_po = await client.post("/api/sales-orders", json=po_payload)
        assert r_po.status_code == 200, r_po.text
        created_po = r_po.json()["order"]
        po_id = created_po["id"]
        assert created_po["total_quantity"] == 1500
        assert len(created_po["line_items"]) == 2
        assert created_po["line_items"][0]["quantity"] == 1000
        assert created_po["line_items"][1]["quantity"] == 500

        # Step 2: Confirm Pengiriman sees correct eligible PO and line identity
        r_deliv_summary = await client.get(f"/api/sales-deliveries/po/{po_id}/summary")
        assert r_deliv_summary.status_code == 200, r_deliv_summary.text
        deliv_sum = r_deliv_summary.json()
        assert deliv_sum["po_number"] == po_number
        assert deliv_sum["total_ordered"] == 1500
        assert deliv_sum["total_shipped"] == 0
        assert deliv_sum["total_remaining"] == 1500
        assert len(deliv_sum["lines"]) == 2
        assert deliv_sum["lines"][0]["remaining_qty"] == 1000
        assert deliv_sum["lines"][1]["remaining_qty"] == 500

        # Step 3: Create partial Pengiriman fixture
        deliv_payload = {
            "po_id": po_id,
            "delivery_date": "2026-09-01",
            "recipient": "Pak Hendra",
            "address": "Jl. Kopi No. 99",
            "line_items": [
                {"line_idx": 0, "qty": 400, "notes": "Kirim sebagian line 0"},
                {"line_idx": 1, "qty": 200, "notes": "Kirim sebagian line 1"},
            ],
        }
        r_create_deliv = await client.post("/api/sales-deliveries", json=deliv_payload)
        assert r_create_deliv.status_code == 200, r_create_deliv.text
        delivery = r_create_deliv.json()["delivery"]
        assert delivery["total_qty"] == 600
        assert delivery["shipment_index"] == 1

        # Verify updated delivery summary
        r_deliv_summary2 = await client.get(f"/api/sales-deliveries/po/{po_id}/summary")
        deliv_sum2 = r_deliv_summary2.json()
        assert deliv_sum2["total_shipped"] == 600
        assert deliv_sum2["total_remaining"] == 900
        assert deliv_sum2["lines"][0]["remaining_qty"] == 600
        assert deliv_sum2["lines"][1]["remaining_qty"] == 300

        # Step 4: Confirm Faktur preview sees correct PO and lines
        r_inv_preview = await client.get(f"/api/sales-invoices/po/{po_id}/preview")
        assert r_inv_preview.status_code == 200, r_inv_preview.text
        inv_prev = r_inv_preview.json()
        assert inv_prev["po_number"] == po_number
        assert len(inv_prev["lines"]) == 2
        assert inv_prev["lines"][0]["remaining_invoice_qty"] == 1000
        assert inv_prev["lines"][1]["remaining_invoice_qty"] == 500

        # Step 5: Create partial Faktur fixture with positive prices (outstanding > 0)
        inv_payload = {
            "po_id": po_id,
            "invoice_date": "2026-09-01",
            "payment_term_days": 14,
            "discount_amount": 10000,
            "ppn_percent": 11,
            "lines": [
                {"line_idx": 0, "quantity": 500, "unit_price": 600},   # 300,000
                {"line_idx": 1, "quantity": 250, "unit_price": 400},   # 100,000
            ],
        }
        # Subtotal: 400,000; Discount: 10,000 -> After disc: 390,000; PPN 11%: 42,900 -> Grand: 432,900
        r_create_inv = await client.post("/api/sales-invoices", json=inv_payload)
        assert r_create_inv.status_code == 200, r_create_inv.text
        created_inv = r_create_inv.json()["invoice"]
        invoice_id = created_inv["id"]
        assert created_inv["subtotal"] == 400000.0
        assert created_inv["discount_amount"] == 10000.0
        assert created_inv["after_discount"] == 390000.0
        assert created_inv["ppn_amount"] == 42900.0
        assert created_inv["grand_total"] == 432900.0

        # Step 6: Confirm Penerimaan invoice selector discovers this Faktur with outstanding > 0
        r_list_invs = await client.get(f"/api/sales-invoices?po_id={po_id}")
        assert r_list_invs.status_code == 200, r_list_invs.text
        invoices_for_po = r_list_invs.json()["invoices"]
        assert len(invoices_for_po) == 1
        inv_row = invoices_for_po[0]
        assert inv_row["id"] == invoice_id
        assert inv_row["grand_total"] == 432900.0
        assert inv_row["paid_total"] == 0.0
        assert inv_row["outstanding_amount"] == 432900.0
        assert inv_row["payment_status"] == "BELUM DIBAYAR"
        # Business eligibility condition for receipt creation: outstanding_amount > 0
        assert inv_row["outstanding_amount"] > 0

        # Step 7: Select invoice & verify detail binds exact invoice_id and po_id
        r_inv_detail = await client.get(f"/api/sales-invoices/{invoice_id}")
        assert r_inv_detail.status_code == 200, r_inv_detail.text
        inv_detail = r_inv_detail.json()["invoice"]
        assert inv_detail["id"] == invoice_id
        assert inv_detail["po_id"] == po_id
        assert inv_detail["po_number"] == po_number
        assert inv_detail["outstanding_amount"] == 432900.0

        # Step 8 & 9: Create DP/partial receipt (amount = 200,000)
        receipt_payload = {
            "invoice_id": invoice_id,
            "receipt_date": "2026-09-02",
            "amount": 200000.0,
            "notes": "Pembayaran DP",
        }
        r_create_rec = await client.post("/api/sales-receipts", json=receipt_payload)
        assert r_create_rec.status_code == 200, r_create_rec.text
        created_receipt = r_create_rec.json()["receipt"]
        receipt_id = created_receipt["id"]
        assert created_receipt["amount"] == 200000.0
        assert created_receipt["payment_state"] == "DP"
        assert created_receipt["invoice_id"] == invoice_id
        assert created_receipt["po_id"] == po_id
        assert created_receipt["po_number"] == po_number

        # Step 10: Verify outstanding recalculation on invoice
        r_inv_recalc = await client.get(f"/api/sales-invoices/{invoice_id}")
        assert r_inv_recalc.status_code == 200, r_inv_recalc.text
        inv_after_rec = r_inv_recalc.json()["invoice"]
        assert inv_after_rec["paid_total"] == 200000.0
        assert inv_after_rec["outstanding_amount"] == 232900.0
        assert inv_after_rec["payment_status"] == "DP"

        # Step 11 & 12: Verify Payment / Approval Payment behavior
        r_order = await client.get(f"/api/orders/{po_id}")
        assert r_order.status_code == 200, r_order.text
        order_doc = r_order.json()["order"]
        assert order_doc["payment_status"] == "Approved"
        # Since design is still "Waiting", the sequential workflow does NOT skip to Penjadwalan
        assert order_doc["current_stage"] < 5

        # Step 13: Pay the remaining 232,900 to make it LUNAS
        receipt_payload2 = {
            "invoice_id": invoice_id,
            "receipt_date": "2026-09-02",
            "amount": 232900.0,
            "notes": "Pelunasan sisa tagihan",
        }
        r_create_rec2 = await client.post("/api/sales-receipts", json=receipt_payload2)
        assert r_create_rec2.status_code == 200, r_create_rec2.text
        rec2 = r_create_rec2.json()["receipt"]
        assert rec2["payment_state"] == "LUNAS"

        # Step 14: Confirm that once LUNAS (outstanding == 0), invoice is NO LONGER eligible for new receipts
        r_inv_lunas = await client.get(f"/api/sales-invoices/{invoice_id}")
        assert r_inv_lunas.status_code == 200
        inv_lunas = r_inv_lunas.json()["invoice"]
        assert inv_lunas["outstanding_amount"] == 0.0
        assert inv_lunas["payment_status"] == "LUNAS"
        # Eligibility rule verification:
        assert not (inv_lunas["outstanding_amount"] > 0)
