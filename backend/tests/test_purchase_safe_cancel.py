"""Safe cancel / soft-delete for purchasing: PR, PO, Invoice, Payment.

UAT matrix A–J plus GR soft-cancel (K–N).
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]



@pytest.fixture(autouse=True)
async def disable_physical_stock_for_purchase_flow_tests(bind_motor):
    """Purchase flow tests isolate cancel/payment/docs; stock V2 has its own suite."""
    from services.physical_stock_settings import SETTINGS_KEY
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "enabled": False, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "sales_delivery"}},
        upsert=True,
    )


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    db = server.db
    for name in ("purchase_requests", "purchase_orders", "purchase_goods_receipts", "purchase_invoices", "purchase_payments"):
        try:
            await db[name].drop_index("idempotency_key_1")
        except Exception:
            pass
        await db[name].create_index("id", unique=True)
    await db.purchase_requests.create_index("request_number", unique=True)
    await db.purchase_orders.create_index("po_number", unique=True)
    await db.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await db.purchase_invoices.create_index("invoice_number", unique=True)
    await db.purchase_payments.create_index("payment_number", unique=True)
    for name in ("purchase_requests", "purchase_orders", "purchase_goods_receipts", "purchase_invoices", "purchase_payments"):
        await db[name].create_index(
            "idempotency_key", unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )


def admin():
    return {
        "id": "admin_test",
        "user_id": "admin_test",
        "email": "admin@example.com",
        "name": "Admin",
        "role": "admin",
    }


async def client_ctx():
    app.dependency_overrides[current_user] = admin
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def make_request(c, lines, notes=""):
    normalized = []
    for i, ln in enumerate(lines):
        row = dict(ln)
        if not str(row.get("master_item_id") or "").strip():
            row["master_item_id"] = str(row.get("accurate_item_id") or f"mi-{uuid.uuid4().hex[:8]}")
        if not str(row.get("item_code") or "").strip():
            row["item_code"] = f"CODE-{i + 1}"
        if not str(row.get("item_name") or "").strip():
            row["item_name"] = f"Item-{i + 1}"
        normalized.append(row)
    res = await c.post("/api/purchase-requests", json={"notes": notes, "lines": normalized})
    assert res.status_code == 200, res.text
    return res.json()["request"]


async def make_direct_po(c, qty=10, unit_price=1000, supplier="PT Direct"):
    mid = f"mi-{uuid.uuid4().hex[:8]}"
    res = await c.post("/api/purchase-orders", json={
        "supplier": supplier,
        "lines": [{
            "master_item_id": mid,
            "item_code": "DIR-1",
            "item_name": "Direct Item",
            "unit": "pcs",
            "ordered_qty": qty,
            "unit_price": unit_price,
        }],
        "idempotency_key": f"po-{uuid.uuid4()}",
    })
    assert res.status_code == 200, res.text
    return res.json()["order"]


@pytest.mark.anyio
async def test_a_pr_empty_cancel_leaves_active_list():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "A-Item", "requested_qty": 5}])
            assert req.get("can_cancel") is True
            cancel = await c.post(f"/api/purchase-requests/{req['id']}/cancel", json={"reason": "salah buat"})
            assert cancel.status_code == 200, cancel.text
            body = cancel.json()["request"]
            assert body["cancelled"] is True
            assert body["status"] == "DIBATALKAN"
            listed = (await c.get("/api/purchase-requests")).json()["requests"]
            assert all(r["id"] != req["id"] for r in listed)
            # idempotent second cancel
            again = await c.post(f"/api/purchase-requests/{req['id']}/cancel", json={"reason": "lagi"})
            assert again.status_code == 200
            assert again.json()["request"]["cancelled"] is True
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_b_pr_with_active_po_cancel_blocked():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "B-Item", "requested_qty": 100}])
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "PT B",
                "lines": [{"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 40, "unit_price": 1000}],
            })).json()["order"]
            assert po["id"]
            blocked = await c.post(f"/api/purchase-requests/{req['id']}/cancel", json={"reason": "coba"})
            assert blocked.status_code == 409
            assert "sudah digunakan pada Pesanan Pembelian" in blocked.json()["detail"]
            refreshed = (await c.get(f"/api/purchase-requests/{req['id']}")).json()["request"]
            assert refreshed.get("can_cancel") is False
            assert refreshed.get("cancelled") is not True
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_c_po_from_pr_cancel_recalculates_ordered():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "C-Item", "requested_qty": 100}])
            rid = req["lines"][0]["line_id"]
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "PT C",
                "lines": [{"request_line_id": rid, "ordered_qty": 40, "unit_price": 500}],
            })).json()["order"]
            before = (await c.get(f"/api/purchase-requests/{req['id']}")).json()["request"]
            assert before["lines"][0]["cumulative_ordered_qty"] == 40
            assert before["lines"][0]["remaining_order_qty"] == 60
            assert before["status"] == "SEBAGIAN DIPESAN"
            cancel = await c.post(f"/api/purchase-orders/{po['id']}/cancel", json={"reason": "batal po"})
            assert cancel.status_code == 200, cancel.text
            after = (await c.get(f"/api/purchase-requests/{req['id']}")).json()["request"]
            assert after["lines"][0]["cumulative_ordered_qty"] == 0
            assert after["lines"][0]["remaining_order_qty"] == 100
            assert after["status"] == "TERBUKA"
            listed = (await c.get("/api/purchase-orders")).json()["orders"]
            assert all(o["id"] != po["id"] for o in listed)
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_d_two_pos_cancel_one_recalc():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "D-Item", "requested_qty": 100}])
            rid = req["lines"][0]["line_id"]
            po40 = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "PT D1",
                "lines": [{"request_line_id": rid, "ordered_qty": 40, "unit_price": 500}],
            })).json()["order"]
            await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "PT D2",
                "lines": [{"request_line_id": rid, "ordered_qty": 20, "unit_price": 500}],
            })
            cancel = await c.post(f"/api/purchase-orders/{po40['id']}/cancel", json={"reason": "batal 40"})
            assert cancel.status_code == 200, cancel.text
            after = (await c.get(f"/api/purchase-requests/{req['id']}")).json()["request"]
            assert after["lines"][0]["cumulative_ordered_qty"] == 20
            assert after["lines"][0]["remaining_order_qty"] == 80
            assert after["status"] == "SEBAGIAN DIPESAN"
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_e_direct_po_cancel():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c)
            assert po.get("can_cancel") is True
            cancel = await c.post(f"/api/purchase-orders/{po['id']}/cancel", json={"reason": "direct salah"})
            assert cancel.status_code == 200, cancel.text
            assert cancel.json()["order"]["cancelled"] is True
            listed = (await c.get("/api/purchase-orders")).json()["orders"]
            assert all(o["id"] != po["id"] for o in listed)
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_f_po_with_receipt_cancel_blocked():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=10, unit_price=1000)
            lid = po["lines"][0]["line_id"]
            gr = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 3}],
            })
            assert gr.status_code == 200, gr.text
            blocked = await c.post(f"/api/purchase-orders/{po['id']}/cancel", json={"reason": "coba"})
            assert blocked.status_code == 409
            assert "sudah pernah diterima" in blocked.json()["detail"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_g_po_with_invoice_cancel_blocked():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=10, unit_price=1000)
            lid = po["lines"][0]["line_id"]
            inv = await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 2}],
            })
            assert inv.status_code == 200, inv.text
            blocked = await c.post(f"/api/purchase-orders/{po['id']}/cancel", json={"reason": "coba"})
            assert blocked.status_code == 409
            assert "sudah memiliki faktur" in blocked.json()["detail"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_h_unpaid_invoice_cancel_recalc_po_invoice_status():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=10, unit_price=1000)
            lid = po["lines"][0]["line_id"]
            inv = (await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 10}],
            })).json()["invoice"]
            assert inv["payment_status"] == "BELUM DIBAYAR"
            po_mid = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_mid["invoice_status"] == "DIFAKTUR"
            assert po_mid["receiving_status"] == "BELUM DITERIMA"
            cancel = await c.post(f"/api/purchase-invoices/{inv['id']}/cancel", json={"reason": "faktur salah"})
            assert cancel.status_code == 200, cancel.text
            po_after = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_after["invoice_status"] == "BELUM DIFAKTUR"
            assert po_after["receiving_status"] == "BELUM DITERIMA"
            # eligible for new invoice
            again = await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 5}],
            })
            assert again.status_code == 200, again.text
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_i_paid_invoice_cancel_blocked():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=1, unit_price=500000)
            lid = po["lines"][0]["line_id"]
            inv = (await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
            })).json()["invoice"]
            pay = await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 500000,
            })
            assert pay.status_code == 200, pay.text
            blocked = await c.post(f"/api/purchase-invoices/{inv['id']}/cancel", json={"reason": "coba"})
            assert blocked.status_code == 409
            assert "sudah memiliki pembayaran" in blocked.json()["detail"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_j_payment_cancel_recalculates_invoice():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=1, unit_price=500000)
            lid = po["lines"][0]["line_id"]
            inv = (await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
            })).json()["invoice"]
            p175 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 175000,
            })).json()["payment"]
            p325 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 325000,
            })).json()["payment"]
            # Drafts must not settle invoice
            draft_inv = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert draft_inv["payment_status"] == "BELUM DIBAYAR"
            assert draft_inv["paid_total"] == 0

            await server.db.users.update_one(
                {"id": "peer-cancel"},
                {"$set": {"id": "peer-cancel", "email": "peer-cancel@example.com", "name": "Peer", "role": "admin", "status": "active"}},
                upsert=True,
            )
            await server.db.users.update_one(
                {"id": "admin_test"},
                {"$set": {"id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin", "status": "active"}},
                upsert=True,
            )
            app.dependency_overrides[current_user] = lambda: {
                "id": "admin_test", "user_id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin",
            }
            for pid in (p175["id"], p325["id"]):
                assert (await c.post(f"/api/purchase-payments/{pid}/submit", json={"target_user_id": "peer-cancel"})).status_code == 200
                assert (await c.post(f"/api/purchase-payments/{pid}/confirm-paid")).status_code == 200

            full = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert full["payment_status"] == "LUNAS"
            assert full["paid_total"] == 500000
            assert full["outstanding_amount"] == 0

            # Paid cannot be cancelled
            blocked = await c.post(f"/api/purchase-payments/{p325['id']}/cancel", json={})
            assert blocked.status_code == 409
            mid = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert mid["payment_status"] == "LUNAS"
            assert mid["paid_total"] == 500000
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_cancel_reason_optional_defaults_internal():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "R", "requested_qty": 1}])
            ok = await c.post(f"/api/purchase-requests/{req['id']}/cancel", json={})
            assert ok.status_code == 200, ok.text
            body = ok.json()["request"]
            assert body["cancelled"] is True
            assert body.get("cancel_reason") == "user_cancelled"
            assert (body.get("cancelled_by") or {}).get("name")
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_k_gr_cancel_recalculates_po_received_qty_no_stock_double():
    """Soft-cancel GR restores PO receivable qty; no hard delete; no stock reverse when none posted."""
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=10, unit_price=1000)
            lid = po["lines"][0]["line_id"]
            gr_res = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 4}],
                "idempotency_key": f"gr-{uuid.uuid4()}",
            })
            assert gr_res.status_code == 200, gr_res.text
            gr = gr_res.json()["receipt"]
            assert gr.get("cancelled") is not True
            assert gr.get("can_cancel") is True

            po_mid = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_mid["lines"][0]["cumulative_received_qty"] == 4
            assert po_mid["lines"][0]["remaining_receivable_qty"] == 6
            assert po_mid["receiving_status"] == "SEBAGIAN DITERIMA"

            missing_reason = await c.post(
                f"/api/purchase-goods-receipts/{gr['id']}/cancel", json={}
            )
            assert missing_reason.status_code == 400
            assert "alasan" in str(missing_reason.json()["detail"]).lower()

            mov_before = await server.db.stock_movements.count_documents({
                "reference_type": "goods_receipt", "reference_id": gr["id"],
            })

            cancel = await c.post(f"/api/purchase-goods-receipts/{gr['id']}/cancel", json={"reason": "salah input"})
            assert cancel.status_code == 200, cancel.text
            body = cancel.json()["receipt"]
            assert body["cancelled"] is True
            assert body.get("can_cancel") is False

            raw = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
            assert raw is not None and raw.get("cancelled") is True

            listed = (await c.get("/api/purchase-goods-receipts")).json()["receipts"]
            assert all(r["id"] != gr["id"] for r in listed)

            po_after = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_after["lines"][0]["cumulative_received_qty"] == 0
            assert po_after["lines"][0]["remaining_receivable_qty"] == 10
            assert po_after["receiving_status"] == "BELUM DITERIMA"

            mov_after = await server.db.stock_movements.count_documents({
                "reference_type": "goods_receipt", "reference_id": gr["id"],
            })
            assert mov_after == mov_before == 0

            again = await c.post(f"/api/purchase-goods-receipts/{gr['id']}/cancel", json={})
            assert again.status_code == 200
            assert again.json()["receipt"]["cancelled"] is True
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_l_gr_cancel_blocked_when_stock_reversal_is_unsafe():
    async with await client_ctx() as c:
        gr_id = None
        try:
            po = await make_direct_po(c, qty=5, unit_price=500)
            lid = po["lines"][0]["line_id"]
            gr = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 2}],
                "idempotency_key": f"gr-stock-{uuid.uuid4()}",
            })).json()["receipt"]
            gr_id = gr["id"]

            await server.db.stock_movements.insert_one({
                "id": str(uuid.uuid4()),
                "reference_type": "goods_receipt",
                "reference_id": gr["id"],
                "master_item_id": po["lines"][0]["master_item_id"],
                "delta": 2,
                "base_qty": 2,
                "movement_type": "PENERIMAAN",
            })

            blocked = await c.post(f"/api/purchase-goods-receipts/{gr['id']}/cancel", json={"reason": "coba"})
            assert blocked.status_code == 409, blocked.text
            detail = str(blocked.json()["detail"]).lower()
            assert "stok" in detail or "pergerakan" in detail

            still = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
            assert still.get("cancelled") is not True

            got = (await c.get(f"/api/purchase-goods-receipts/{gr['id']}")).json()["receipt"]
            assert got.get("can_cancel") is False
            reason = (got.get("cancel_block_reason") or "").lower()
            assert "stok" in reason or "pergerakan" in reason
        finally:
            if gr_id:
                await server.db.stock_movements.delete_many({"reference_id": gr_id})
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_gr_stock_cancel_appends_one_idempotent_reversal():
    from services.stock_ledger import MOVEMENT_PENERIMAAN, MOVEMENT_REVERSAL_PENERIMAAN

    async with await client_ctx() as c:
        po_id = gr_id = mid = None
        try:
            await server.db.app_settings.update_one(
                {"key": "physical_stock"},
                {"$set": {
                    "key": "physical_stock",
                    "enabled": True,
                    "stock_in_trigger": "goods_receipt",
                    "stock_out_trigger": "sales_delivery",
                }},
                upsert=True,
            )
            po = await make_direct_po(c, qty=10, unit_price=500)
            po_id = po["id"]
            mid = po["lines"][0]["master_item_id"]
            await server.db.master_items.insert_one({
                "id": mid,
                "item_code": po["lines"][0]["item_code"],
                "item_name": po["lines"][0]["item_name"],
                "unit": "pcs",
            })
            await server.db.product_stock_state.insert_one({
                "_id": mid,
                "master_item_id": mid,
                "base_uom": "PCS",
                "initialized": True,
                "status": "INITIALIZED",
                "total_qty": 10,
                "unprinted_qty": 7,
                "printed_qty": 3,
                "version": 1,
                "pending_event": None,
            })

            lid = po["lines"][0]["line_id"]
            gr_res = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po_id,
                "lines": [{"purchase_order_line_id": lid, "received_qty": 4}],
                "idempotency_key": f"gr-reverse-{uuid.uuid4()}",
            })
            assert gr_res.status_code == 200, gr_res.text
            gr = gr_res.json()["receipt"]
            gr_id = gr["id"]

            after_receipt = await server.db.product_stock_state.find_one({"master_item_id": mid})
            assert after_receipt["total_qty"] == 14
            assert after_receipt["unprinted_qty"] == 11
            assert after_receipt["printed_qty"] == 3

            cancel = await c.post(
                f"/api/purchase-goods-receipts/{gr_id}/cancel",
                json={"reason": "Qty penerimaan salah"},
            )
            assert cancel.status_code == 200, cancel.text
            cancelled = cancel.json()["receipt"]
            assert cancelled["cancelled"] is True
            assert cancelled["status"] == "CANCELLED"
            assert cancelled["cancel_reason"] == "Qty penerimaan salah"
            assert cancelled.get("cancelled_by", {}).get("email") == "admin@example.com"
            assert cancelled.get("cancelled_at")
            assert cancelled.get("reversal_movement_id")

            after_cancel = await server.db.product_stock_state.find_one({"master_item_id": mid})
            assert after_cancel["total_qty"] == 10
            assert after_cancel["unprinted_qty"] == 7
            assert after_cancel["printed_qty"] == 3

            movements = await server.db.stock_movements.find({
                "$or": [
                    {"reference_type": "goods_receipt", "reference_id": gr_id},
                    {"goods_receipt_id": gr_id},
                ]
            }, {"_id": 0}).to_list(None)
            original = [m for m in movements if m.get("movement_type") == MOVEMENT_PENERIMAAN]
            reversals = [m for m in movements if m.get("movement_type") == MOVEMENT_REVERSAL_PENERIMAAN]
            assert len(original) == 1
            assert len(reversals) == 1
            assert reversals[0]["transaction_qty"] == -4
            assert reversals[0]["delta"] == -4
            assert reversals[0]["reversal_of_movement_id"] == original[0]["id"]
            assert reversals[0]["goods_receipt_id"] == gr_id
            assert reversals[0]["cancel_reason"] == "Qty penerimaan salah"

            retry = await c.post(f"/api/purchase-goods-receipts/{gr_id}/cancel", json={})
            assert retry.status_code == 200, retry.text
            final_state = await server.db.product_stock_state.find_one({"master_item_id": mid})
            assert final_state["total_qty"] == 10
            assert await server.db.stock_movements.count_documents({
                "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
                "goods_receipt_id": gr_id,
            }) == 1
        finally:
            if gr_id:
                await server.db.stock_movements.delete_many({
                    "$or": [
                        {"reference_id": gr_id},
                        {"goods_receipt_id": gr_id},
                    ]
                })
                await server.db.purchase_goods_receipts.delete_many({"id": gr_id})
            if mid:
                await server.db.product_stock_state.delete_many({"master_item_id": mid})
                await server.db.master_items.delete_many({"id": mid})
            if po_id:
                await server.db.purchase_orders.delete_many({"id": po_id})
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_m_gr_cancel_blocked_when_active_invoice_exists():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=8, unit_price=1000)
            lid = po["lines"][0]["line_id"]
            gr = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 5}],
                "idempotency_key": f"gr-inv-{uuid.uuid4()}",
            })).json()["receipt"]

            inv = await c.post("/api/purchase-invoices", json={
                "invoice_source": "receipt",
                "receipt_id": gr["id"],
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 5}],
                "idempotency_key": f"inv-from-gr-{uuid.uuid4()}",
            })
            assert inv.status_code == 200, inv.text

            po_mid = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_mid["lines"][0]["cumulative_received_qty"] == 5

            blocked = await c.post(f"/api/purchase-goods-receipts/{gr['id']}/cancel", json={})
            assert blocked.status_code == 409
            assert "faktur" in blocked.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_n_invoice_from_receipt_preview_and_create_preserves_received_qty():
    async with await client_ctx() as c:
        try:
            po = await make_direct_po(c, qty=6, unit_price=2500, supplier="PT Receipt Inv")
            lid = po["lines"][0]["line_id"]
            gr = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 6}],
                "idempotency_key": f"gr-prev-{uuid.uuid4()}",
            })).json()["receipt"]

            prev = await c.get(f"/api/purchase-invoices/from-receipt/{gr['id']}/preview")
            assert prev.status_code == 200, prev.text
            body = prev.json()
            assert body.get("receipt_id") == gr["id"]
            assert body.get("purchase_order_id") == po["id"]
            assert body.get("supplier") == "PT Receipt Inv"
            assert len(body.get("lines") or []) >= 1
            assert float(body["lines"][0].get("invoice_qty") or 0) == 6

            before = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert before["lines"][0]["cumulative_received_qty"] == 6

            inv = await c.post("/api/purchase-invoices", json={
                "invoice_source": "receipt",
                "receipt_id": gr["id"],
                "idempotency_key": f"inv-auto-{uuid.uuid4()}",
            })
            assert inv.status_code == 200, inv.text
            invoice = inv.json()["invoice"]
            assert invoice.get("receipt_id") == gr["id"]
            assert float(invoice["lines"][0].get("invoice_qty") or 0) == 6

            after = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert after["lines"][0]["cumulative_received_qty"] == 6
            assert after["receiving_status"] == before["receiving_status"]
        finally:
            app.dependency_overrides.pop(current_user, None)
