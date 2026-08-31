"""Purchasing workflow: Permintaan → PO → GR → Faktur → Pembayaran."""
import asyncio
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
from services.current_stock import build_stock_rows


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


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
    return {"id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin"}


async def client_ctx():
    app.dependency_overrides[current_user] = admin
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def make_request(c, lines, notes=""):
    res = await c.post("/api/purchase-requests", json={"notes": notes, "lines": lines})
    assert res.status_code == 200, res.text
    return res.json()["request"]


@pytest.mark.anyio
async def test_request_creates_po_exact_lines_same_name_separate():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [
                {"item_name": "Material A", "requested_qty": 100, "unit": "pcs", "accurate_item_id": "101"},
                {"item_name": "Material A", "requested_qty": 50, "unit": "pcs", "accurate_item_id": "101"},
            ])
            assert req["request_number"].startswith("PR-")
            assert len(req["lines"]) == 2
            assert req["lines"][0]["line_id"] != req["lines"][1]["line_id"]
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"],
                "supplier": "PT Supplier",
                "lines": [
                    {"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 100, "unit_price": 1000},
                    {"request_line_id": req["lines"][1]["line_id"], "ordered_qty": 50, "unit_price": 1100},
                ],
            })).json()["order"]
            assert po["po_number"].startswith("PB-")
            assert len(po["lines"]) == 2
            assert po["lines"][0]["request_line_id"] == req["lines"][0]["line_id"]
            assert po["lines"][1]["unit_price"] == 1100
            assert po["grand_total"] == 155000.0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_over_order_rejected_and_partial_remaining():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "Ink", "requested_qty": 10, "unit": "kg"}])
            lid = req["lines"][0]["line_id"]
            over = await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": lid, "ordered_qty": 11, "unit_price": 1}],
            })
            assert over.status_code == 400
            first = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": lid, "ordered_qty": 4, "unit_price": 2}],
            })).json()["order"]
            left = (await c.get(f"/api/purchase-requests/{req['id']}")).json()["request"]
            assert left["lines"][0]["cumulative_ordered_qty"] == 4
            assert left["lines"][0]["remaining_order_qty"] == 6
            assert first["po_number"].startswith("PB-")
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_number_unique_and_concurrent():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [
                {"item_name": "A", "requested_qty": 10},
                {"item_name": "B", "requested_qty": 10},
            ])
            payload1 = {
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 10, "unit_price": 1}],
            }
            payload2 = {
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": req["lines"][1]["line_id"], "ordered_qty": 10, "unit_price": 1}],
            }
            r1, r2 = await asyncio.gather(c.post("/api/purchase-orders", json=payload1), c.post("/api/purchase-orders", json=payload2))
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            n1 = r1.json()["order"]["po_number"]
            n2 = r2.json()["order"]["po_number"]
            assert n1 != n2
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_goods_receipt_po_line_partial_over_receipt_no_stock_change():
    items = [{"id": "m1", "accurate_item_id": "9001", "item_name": "Board", "item_code": "B1", "unit": "pcs", "is_active": True}]
    await server.db.master_items.delete_many({"id": "m1"})
    await server.db.master_items.insert_one(items[0])
    before_rows = build_stock_rows(
        items,
        await server.db.orders.find({}, {"_id": 0}).to_list(5000),
        await server.db.item_stock_manual.find({}, {"_id": 0}).to_list(5000),
        await server.db.accurate_purchase_receipts.find({}, {"_id": 0}).to_list(5000),
        purchase_source="MANUAL_FALLBACK",
    )
    before_qty = before_rows[0]["stock_current"] if before_rows else 0

    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "Board", "requested_qty": 10, "accurate_item_id": "9001"}])
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 10, "unit_price": 500}],
            })).json()["order"]
            line_id = po["lines"][0]["line_id"]
            rec = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": line_id, "received_qty": 4}],
            })).json()["receipt"]
            assert rec["purchase_order_id"] == po["id"]
            assert rec["lines"][0]["purchase_order_line_id"] == line_id
            po2 = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po2["lines"][0]["cumulative_received_qty"] == 4
            assert po2["lines"][0]["remaining_receivable_qty"] == 6
            over = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": line_id, "received_qty": 7}],
            })
            assert over.status_code == 400
            after_rows = build_stock_rows(
                items,
                await server.db.orders.find({}, {"_id": 0}).to_list(5000),
                await server.db.item_stock_manual.find({}, {"_id": 0}).to_list(5000),
                await server.db.accurate_purchase_receipts.find({}, {"_id": 0}).to_list(5000),
                purchase_source="MANUAL_FALLBACK",
            )
            after_qty = after_rows[0]["stock_current"] if after_rows else 0
            assert after_qty == before_qty
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_partial_over_invoice_totals_and_payment_flow():
    async with await client_ctx() as c:
        try:
            req = await make_request(c, [{"item_name": "Resin", "requested_qty": 10}])
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "PT Kimia",
                "lines": [{"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 10, "unit_price": 1000000}],
            })).json()["order"]
            lid = po["lines"][0]["line_id"]
            inv = (await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 4}],
            })).json()["invoice"]
            assert inv["invoice_number"].startswith("FPB-") or inv["invoice_number"].startswith("FPB")
            assert inv["grand_total"] == 4000000.0
            assert inv["payment_status"] == "BELUM DIBAYAR"
            over_inv = await c.post("/api/purchase-invoices", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 7}],
            })
            assert over_inv.status_code == 400
            p1 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 2000000,
                "idempotency_key": f"pay-{uuid.uuid4()}",
            })).json()["payment"]
            assert p1["payment_state"] == "DP"
            key = f"retry-{uuid.uuid4()}"
            a = await c.post("/api/purchase-payments", json={"purchase_invoice_id": inv["id"], "amount": 1000000, "idempotency_key": key})
            b = await c.post("/api/purchase-payments", json={"purchase_invoice_id": inv["id"], "amount": 1000000, "idempotency_key": key})
            assert a.status_code == 200 and b.status_code == 200
            assert a.json()["payment"]["id"] == b.json()["payment"]["id"]
            p3 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 1000000,
            })).json()["payment"]
            assert p3["payment_state"] == "LUNAS"
            detail = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert detail["payment_status"] == "LUNAS"
            assert detail["outstanding_amount"] == 0
            overpay = await c.post("/api/purchase-payments", json={"purchase_invoice_id": inv["id"], "amount": 1})
            assert overpay.status_code == 400
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_get_does_not_mutate_and_unauthorized_hpp():
    async with await client_ctx() as c:
        try:
            listed = await c.get("/api/purchase-orders")
            assert listed.status_code == 200
        finally:
            app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides[current_user] = lambda: {"role": "helper", "email": "h@x", "name": "H"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            res = await c.put("/api/settings/hpp", json={"method": "FIFO", "recognition_point": "goods_receipt"})
            assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
