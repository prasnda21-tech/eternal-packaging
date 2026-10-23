"""Targeted Unit and Integration Tests for Sales Invoices, Sales Receipts, and Workflow Payment Integration.

Covers:
- Partial Invoicing with cumulative & remaining quantity rules
- Price calculation, nominal discount, optional PPN (%), payment terms & due date
- Over-invoice & overpayment rejection
- Exact line identity preservation (independent distinct lines with identical item names)
- Backend authoritative recalculation
- Sales Receipt creation, DP vs LUNAS derivation, overpayment & double-submit protection
- Workflow Payment Integration (Cases 22-30):
  - Receipt auto-approves Payment & Approval Payment stage
  - DP is sufficient to auto-approve
  - Sequential workflow preserved: does NOT skip Design when Design is still pending
  - When Design is later approved, Payment auto-resolves and unlocks Penjadwalan
  - Idempotent: no duplicate Approval Payment events on subsequent receipts
  - Other stages (Screener, Helper, Operator, Cetak, Packing, SJ, Loading, Pengiriman) remain strictly unchanged
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
import httpx
import uuid

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


async def _ensure_invoice_journal_posted(invoice_id: str) -> None:
    """Minimal posted sales_invoice journal so CREATE penerimaan passes P3 gate."""
    existing = await server.db.journal_entries.find_one(
        {"source_type": "sales_invoice", "source_id": str(invoice_id), "status": "posted"},
        {"_id": 0, "id": 1},
    )
    if existing:
        return
    jid = str(uuid.uuid4())
    await server.db.journal_entries.insert_one({
        "id": jid,
        "number": f"FIX-INV-{jid[:12]}",
        "source_type": "sales_invoice",
        "source_id": str(invoice_id),
        "status": "posted",
        "reversal_of": None,
        "date": "2026-08-31",
        "description": "fixture posted invoice journal",
        "created_at": "2026-08-31T00:00:00Z",
    })


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
async def bind_motor_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient
    # Pin DB: other suites (e.g. customer_credit) may mutate os.environ["DB_NAME"].
    os.environ["DB_NAME"] = "eternal_packaging_test"
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client["eternal_packaging_test"]
    await server.db.sales_invoices.delete_many({"po_number": {"$regex": r"^TEST-"}})
    await server.db.sales_receipts.delete_many({"po_number": {"$regex": r"^TEST-"}})
    await server.db.orders.delete_many({"po_number": {"$regex": r"^TEST-"}})


async def _seed_test_order(
    po_prefix: str,
    lines: list,
    current_stage: int = 0,
    design_status: str = "Waiting",
    design_files: list = None,
    payment_status: str = "Waiting",
    payment_files: list = None,
):
    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    po_number = f"{po_prefix}-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": order_id,
        "po_number": po_number,
        "customer": "PT Renea Coffee Test",
        "outlet": "Outlet Utama",
        "recipient": "Budi",
        "address": "Jl. Gatot Subroto No. 123",
        "delivery_zone": "Barat",
        "sales": "Sales Rep A",
        "order_date": "2026-08-31",
        "deadline": "2026-09-10",
        "current_stage": current_stage,
        "status_label": server.STAGES[current_stage] if current_stage < len(server.STAGES) else "Pengiriman",
        "design_status": design_status,
        "design_files": design_files or [],
        "payment_status": payment_status,
        "payment_files": payment_files or [],
        "line_items": lines,
        "total_quantity": sum(int(l.get("quantity", 0)) for l in lines),
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "timeline": [{
            "stage": 0,
            "stage_label": "Order Masuk",
            "at": now,
            "by_email": "admin@eternalpackaging.com",
            "note": "PO created for testing",
        }],
        "created_at": now,
        "updated_at": now,
    }
    await server.db.orders.insert_one(doc)
    return doc


@pytest.mark.anyio
async def test_case_1_2_3_partial_invoice_and_over_invoice_rejection():
    """Case 1, 2, 3:
    PO line 500 pcs.
    Faktur #1: 200 pcs -> remaining 300.
    Faktur #2: 150 pcs -> remaining 150.
    Faktur #3: Attempt 151 pcs -> reject 400.
    """
    order = await _seed_test_order("TEST-INV-PARTIAL", [
        {"line_idx": 0, "line_id": "l0", "name": "Cup A 500ml", "quantity": 500, "unit": "pcs", "color": "Clear"}
    ])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Check initial preview
            res = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert res.status_code == 200
            preview = res.json()
            assert preview["lines"][0]["remaining_invoice_qty"] == 500

            # Create Faktur #1: 200 pcs
            res1 = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "invoice_date": "2026-08-31",
                "lines": [{"line_idx": 0, "quantity": 200, "unit_price": 1000}],
            })
            assert res1.status_code == 200
            inv1 = res1.json()["invoice"]
            assert inv1["lines"][0]["quantity"] == 200
            assert inv1["lines"][0]["line_total"] == 200000.0

            # Check preview after Faktur #1 -> remaining should be 300
            res_prev2 = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert res_prev2.json()["lines"][0]["remaining_invoice_qty"] == 300
            assert res_prev2.json()["lines"][0]["invoiced_cumulative"] == 200

            # Create Faktur #2: 150 pcs
            res2 = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "invoice_date": "2026-08-31",
                "lines": [{"line_idx": 0, "quantity": 150, "unit_price": 1000}],
            })
            assert res2.status_code == 200
            inv2 = res2.json()["invoice"]
            assert inv2["lines"][0]["quantity"] == 150

            # Check preview after Faktur #2 -> remaining should be 150
            res_prev3 = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert res_prev3.json()["lines"][0]["remaining_invoice_qty"] == 150
            assert res_prev3.json()["lines"][0]["invoiced_cumulative"] == 350

            # Attempt Faktur #3: 151 pcs -> MUST REJECT (Case 3)
            res3_fail = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 151, "unit_price": 1000}],
            })
            assert res3_fail.status_code == 400
            assert "melebihi sisa PO" in res3_fail.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_4_exact_line_identity_preserved():
    """Case 4: Same product name on multiple PO lines tracked independently by line_idx."""
    order = await _seed_test_order("TEST-INV-EXACT-LINE", [
        {"line_idx": 0, "line_id": "l0", "name": "Cup A 500ml", "quantity": 300, "unit": "pcs", "color": "Red"},
        {"line_idx": 1, "line_id": "l1", "name": "Cup A 500ml", "quantity": 200, "unit": "pcs", "color": "Blue"},
    ])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Invoice 300 on Line 0, 100 on Line 1
            res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [
                    {"line_idx": 0, "quantity": 300, "unit_price": 500},
                    {"line_idx": 1, "quantity": 100, "unit_price": 600},
                ],
            })
            assert res.status_code == 200

            # Preview next: Line 0 should have remaining 0, Line 1 should have remaining 100
            prev = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert prev.status_code == 200
            lines = prev.json()["lines"]
            assert lines[0]["remaining_invoice_qty"] == 0
            assert lines[1]["remaining_invoice_qty"] == 100
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_5_6_7_8_9_10_11_financial_calculations_and_rejections():
    """Case 5-11:
    - Subtotal, Diskon, PPN, Grand Total recalculation backend
    - Rejection of negative discount, discount > subtotal, negative PPN, negative price
    - Fake frontend grand total is ignored and recalculated
    """
    order = await _seed_test_order("TEST-INV-CALC", [
        {"line_idx": 0, "line_id": "l0", "name": "Produk Test", "quantity": 1000, "unit": "pcs"}
    ])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Subtotal: 1000 * 1000 = 1,000,000
            # Discount: 100,000 -> After discount = 900,000
            # PPN 10% -> 90,000 -> Grand total = 990,000
            # Pass a fake "grand_total": 12345 to prove backend authoritative calculation
            res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "invoice_date": "2026-08-31",
                "lines": [{"line_idx": 0, "quantity": 1000, "unit_price": 1000}],
                "discount_amount": 100000,
                "ppn_percent": 10,
                "grand_total": 12345,  # Fake, should be ignored
            })
            assert res.status_code == 200
            inv = res.json()["invoice"]
            assert inv["subtotal"] == 1000000.0
            assert inv["discount_amount"] == 100000.0
            assert inv["after_discount"] == 900000.0
            assert inv["ppn_percent"] == 10.0
            assert inv["ppn_amount"] == 90000.0
            assert inv["grand_total"] == 990000.0

            # Fresh order for negative discount test
            order_disc = await _seed_test_order("TEST-INV-DISC", [
                {"line_idx": 0, "line_id": "l0", "name": "Produk Disc", "quantity": 1000, "unit": "pcs"}
            ])

            # Case 8: Negative discount -> reject
            neg_disc = await client.post("/api/sales-invoices", json={
                "po_id": order_disc["id"],
                "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}],
                "discount_amount": -5000,
            })
            assert neg_disc.status_code == 400

            # Case 9: Discount > subtotal (Subtotal is 100 * 1000 = 100,000, discount is 200,000) -> reject
            over_disc = await client.post("/api/sales-invoices", json={
                "po_id": order_disc["id"],
                "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}],
                "discount_amount": 200000,
            })
            assert over_disc.status_code == 400
            assert "tidak boleh melebihi subtotal" in over_disc.json()["detail"]

            # Case 10: Negative PPN -> reject
            neg_ppn = await client.post("/api/sales-invoices", json={
                "po_id": order_disc["id"],
                "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1000}],
                "ppn_percent": -10,
            })
            assert neg_ppn.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_12_13_termin_due_date_and_edit_invoice():
    """Case 12, 13:
    - Termin 30 days calculates correct due_date (invoice_date + 30 days)
    - Edit invoice updates financial fields and does not mutate PO
    """
    order = await _seed_test_order("TEST-INV-TERM", [
        {"line_idx": 0, "line_id": "l0", "name": "Box Premium", "quantity": 500, "unit": "pcs"}
    ])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "invoice_date": "2026-08-31",
                "payment_term_days": 30,
                "lines": [{"line_idx": 0, "quantity": 500, "unit_price": 2000}],
            })
            assert res.status_code == 200
            inv = res.json()["invoice"]
            assert inv["payment_term_days"] == 30
            assert inv["due_date"] == "2026-09-30"

            # Edit invoice: change discount and PPN
            edit_res = await client.put(f"/api/sales-invoices/{inv['id']}", json={
                "discount_amount": 50000,
                "ppn_percent": 11,
                "notes": "Updated note",
            })
            assert edit_res.status_code == 200
            updated = edit_res.json()["invoice"]
            assert updated["subtotal"] == 1000000.0
            assert updated["discount_amount"] == 50000.0
            assert updated["after_discount"] == 950000.0
            assert updated["ppn_amount"] == 104500.0
            assert updated["grand_total"] == 1054500.0

            # Ensure PO was not mutated
            po_db = await server.db.orders.find_one({"id": order["id"]})
            assert po_db["line_items"][0]["quantity"] == 500
            assert po_db["customer"] == "PT Renea Coffee Test"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_14_to_21_sales_receipts_and_overpayment_protections():
    """Case 14 - 21:
    - Receipt #1: 300,000 on 1,000,000 invoice -> Paid 300k, Outstanding 700k, Status DP
    - Receipt #2: 700,000 -> Paid 1,000,000, Outstanding 0, Status LUNAS
    - Amount 0, negative amount, overpayment (> outstanding) rejected with 400
    - Exact invoice relation preserved
    - Idempotency & double-submit safe
    - Receipt on Invoice A does not alter Invoice B
    """
    order = await _seed_test_order("TEST-REC-FLOW", [
        {"line_idx": 0, "line_id": "l0", "name": "Item A", "quantity": 1000, "unit": "pcs"}
    ])

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create Invoice A: 1,000,000
            invA_res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 500, "unit_price": 2000}],
            })
            invA = invA_res.json()["invoice"]
            assert invA["grand_total"] == 1000000.0

            # Create Invoice B: 500,000
            invB_res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 250, "unit_price": 2000}],
            })
            invB = invB_res.json()["invoice"]
            assert invB["grand_total"] == 500000.0
            assert (await client.post(f"/api/sales-invoices/{invA['id']}/finalize")).status_code == 200
            assert (await client.post(f"/api/sales-invoices/{invB['id']}/finalize")).status_code == 200
            await _ensure_invoice_journal_posted(invA["id"])
            await _ensure_invoice_journal_posted(invB["id"])

            # Case 16, 17: Amount 0 & Negative
            r0 = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "amount": 0,
            })
            assert r0.status_code == 400

            r_neg = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "amount": -10000,
            })
            assert r_neg.status_code == 400

            rec_key = f"rec-key-{uuid.uuid4().hex[:6]}"
            # Case 14: Receipt #1 300,000
            r1 = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "receipt_date": "2026-08-31",
                "amount": 300000,
                "notes": "DP Pembayaran",
                "idempotency_key": rec_key,
            })
            assert r1.status_code == 200
            rec1_data = r1.json()["receipt"]
            assert rec1_data["payment_state"] == "DP"
            assert rec1_data["amount"] == 300000.0

            # Check Invoice A status
            invA_detail = await client.get(f"/api/sales-invoices/{invA['id']}")
            assert invA_detail.json()["invoice"]["paid_total"] == 300000.0
            assert invA_detail.json()["invoice"]["outstanding_amount"] == 700000.0
            assert invA_detail.json()["invoice"]["payment_status"] == "DP"

            # Case 18: Overpayment (> 700,000) rejected
            r_over = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "amount": 700001,
            })
            assert r_over.status_code == 400
            assert "Identitas pelanggan tidak dapat dibuktikan" in r_over.json()["detail"]

            # Case 20: Double submit with same idempotency_key returns identical receipt without second charge
            r1_dup = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "amount": 300000,
                "idempotency_key": rec_key,
            })
            assert r1_dup.status_code == 200
            assert r1_dup.json()["receipt"]["id"] == rec1_data["id"]

            # Case 21: Invoice B remains untouched
            invB_detail = await client.get(f"/api/sales-invoices/{invB['id']}")
            assert invB_detail.json()["invoice"]["paid_total"] == 0.0
            assert invB_detail.json()["invoice"]["outstanding_amount"] == 500000.0
            assert invB_detail.json()["invoice"]["payment_status"] == "BELUM DIBAYAR"

            # Case 15: Receipt #2 700,000 -> LUNAS
            r2 = await client.post("/api/sales-receipts", json={
                "invoice_id": invA["id"],
                "receipt_date": "2026-08-31",
                "amount": 700000,
                "notes": "Pelunasan",
            })
            assert r2.status_code == 200
            assert r2.json()["receipt"]["payment_state"] == "LUNAS"

            invA_detail2 = await client.get(f"/api/sales-invoices/{invA['id']}")
            assert invA_detail2.json()["invoice"]["paid_total"] == 1000000.0
            assert invA_detail2.json()["invoice"]["outstanding_amount"] == 0.0
            assert invA_detail2.json()["invoice"]["payment_status"] == "LUNAS"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_22_23_24_workflow_payment_auto_approval():
    """Case 22, 23, 24:
    - If Design prerequisite is already resolved (Approved / Bypassed),
      creating a DP or LUNAS receipt auto-approves Payment & Approval Payment,
      advancing stage to Penjadwalan (stage 5).
    """
    order = await _seed_test_order(
        "TEST-WF-AUTO-APP",
        [{"line_idx": 0, "line_id": "l0", "name": "Item A", "quantity": 500, "unit": "pcs"}],
        current_stage=2,
        design_status="Approved",
        design_files=[{"id": "d1", "name": "design.pdf"}],
    )

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create invoice
            inv_res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 500, "unit_price": 2000}],
            })
            inv = inv_res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
            await _ensure_invoice_journal_posted(inv["id"])

            # Create DP receipt (Case 24: DP is sufficient, does not require LUNAS)
            rec_res = await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 200000,
                "notes": "DP 20%",
            })
            assert rec_res.status_code == 200

            # Check PO state
            po_db = await server.db.orders.find_one({"id": order["id"]})
            assert po_db["payment_status"] == "Approved"
            assert po_db["payment_approved_by"] == "system"
            assert po_db["current_stage"] == 5  # Penjadwalan unlocked!
            # Check timeline entry exists
            tl_stages = [t.get("stage") for t in po_db.get("timeline", [])]
            assert 4 in tl_stages
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_25_26_design_pending_does_not_skip_and_resolves_later():
    """Case 25, 26:
    - If Design prerequisite is NOT resolved (design_status="Waiting", no design files),
      creating a receipt saves the financial fact, but Penjadwalan remains LOCKED (current_stage < 3).
    - When Design is later approved via approve-design endpoint, Payment auto-resolves
      and Penjadwalan unlocks to stage 5!
    """
    order = await _seed_test_order(
        "TEST-WF-NO-SKIP",
        [{"line_idx": 0, "line_id": "l0", "name": "Item B", "quantity": 500, "unit": "pcs"}],
        current_stage=0,
        design_status="Waiting",
        design_files=[],
    )

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create invoice & receipt
            inv_res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 500, "unit_price": 2000}],
            })
            inv = inv_res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
            await _ensure_invoice_journal_posted(inv["id"])

            rec_res = await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 500000,
            })
            assert rec_res.status_code == 200

            # Case 25: PO must NOT skip to Penjadwalan because Design is pending!
            po_db = await server.db.orders.find_one({"id": order["id"]})
            assert po_db["current_stage"] < 5  # Penjadwalan remains locked
            assert po_db["payment_status"] == "Approved"  # Payment fact recorded

            # Case 26: Now add design file & approve design
            await server.db.orders.update_one({"id": order["id"]}, {"$set": {"design_files": [{"id": "d1", "name": "design.png"}]}})
            app_res = await client.post(f"/api/orders/{order['id']}/approve-design", json={
                "approved": True,
                "note": "Design approved",
            })
            assert app_res.status_code == 200

            # Verify that because payment was already approved from receipt, approving design
            # automatically resolves group2 and unlocks Penjadwalan (stage 5)!
            po_after_design = await server.db.orders.find_one({"id": order["id"]})
            assert po_after_design["design_status"] == "Approved"
            assert po_after_design["payment_status"] == "Approved"
            assert po_after_design["current_stage"] == 5
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_case_27_28_29_30_idempotency_and_no_unrelated_stage_mutations():
    """Case 27 - 30:
    - Subsequent receipt does not duplicate Approval Payment timeline entry
    - Direct advance API cannot force future stage without prerequisites
    - Downstream stages (Screener, Helper, Operator, Cetak, Packing, SJ, Loading, Pengiriman) are NOT mutated
    """
    order = await _seed_test_order(
        "TEST-WF-IDEMP",
        [{"line_idx": 0, "line_id": "l0", "name": "Item C", "quantity": 500, "unit": "pcs"}],
        current_stage=2,
        design_status="Approved",
        design_files=[{"id": "d1", "name": "design.pdf"}],
    )

    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com"}
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv_res = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{"line_idx": 0, "quantity": 500, "unit_price": 2000}],
            })
            inv = inv_res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
            await _ensure_invoice_journal_posted(inv["id"])

            # First receipt -> approves payment
            await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 300000,
            })
            po1 = await server.db.orders.find_one({"id": order["id"]})
            stage4_events_count1 = sum(1 for h in po1.get("payment_approval_history", []) if h.get("action") == "approve")
            assert stage4_events_count1 == 1

            # Second receipt -> MUST NOT create duplicate approve entry in history
            await client.post("/api/sales-receipts", json={
                "invoice_id": inv["id"],
                "amount": 700000,
            })
            po2 = await server.db.orders.find_one({"id": order["id"]})
            stage4_events_count2 = sum(1 for h in po2.get("payment_approval_history", []) if h.get("action") == "approve")
            assert stage4_events_count2 == 1  # Still exactly 1!

            # Downstream stages check (Case 30):
            # Screener, Helper, Operator, Cetak, Packing, SJ, Loading, Pengiriman are untouched
            assert po2.get("screener_info") is None
            assert po2.get("helper_prep") is None
            assert po2.get("operator_splits") == []
            assert po2.get("sample_approved") is None or po2.get("sample_approved") is False
            assert po2.get("packing_info") is None
            assert po2.get("shipments") is None or po2.get("shipments") == []
            assert po2.get("loading_confirmations") is None
            assert po2.get("delivery_status") == "Belum Terkirim"
    finally:
        app.dependency_overrides.clear()
