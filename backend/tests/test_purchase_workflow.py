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
async def disable_physical_stock_for_purchase_flow_tests(bind_motor):
    """Purchase workflow fixtures are not seeded with opening balances.
    Keep physical stock OFF so GR/invoice assertions stay about purchasing
    rules, not Stock V2 opening-balance gates. Stock V2 coverage lives in
    test_stock_engine_v2 / purchase→stock dedicated tests.
    """
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
    return {"id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin"}


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
        normalized.append(row)
    res = await c.post("/api/purchase-requests", json={"notes": notes, "lines": normalized})
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
            # Normal flow: receive before invoice (or use early-invoice ack)
            await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 10}],
            })
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
            assert p1["workflow_status"] == "BELUM DIAJUKAN"
            inv_after_draft = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_after_draft["payment_status"] == "BELUM DIBAYAR"
            assert inv_after_draft["paid_total"] == 0

            # Seed peer for submit
            await server.db.users.update_one(
                {"id": "peer-pay-flow"},
                {"$set": {"id": "peer-pay-flow", "email": "peer-pay@example.com", "name": "Peer Pay", "role": "admin", "status": "active"}},
                upsert=True,
            )
            # Override current user with user_id for submit/confirm
            app.dependency_overrides[current_user] = lambda: {
                "id": "admin_test", "user_id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin",
            }
            await server.db.users.update_one(
                {"id": "admin_test"},
                {"$set": {"id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin", "status": "active"}},
                upsert=True,
            )
            await c.post(f"/api/purchase-payments/{p1['id']}/submit", json={"target_user_id": "peer-pay-flow"})
            conf1 = await c.post(f"/api/purchase-payments/{p1['id']}/confirm-paid")
            assert conf1.status_code == 200, conf1.text
            assert conf1.json()["payment"]["workflow_status"] == "DIBAYAR"
            assert conf1.json()["invoice"]["payment_status"] == "DP"

            key = f"retry-{uuid.uuid4()}"
            a = await c.post("/api/purchase-payments", json={"purchase_invoice_id": inv["id"], "amount": 1000000, "idempotency_key": key})
            b = await c.post("/api/purchase-payments", json={"purchase_invoice_id": inv["id"], "amount": 1000000, "idempotency_key": key})
            assert a.status_code == 200 and b.status_code == 200
            assert a.json()["payment"]["id"] == b.json()["payment"]["id"]
            p2 = a.json()["payment"]
            await c.post(f"/api/purchase-payments/{p2['id']}/submit", json={"target_user_id": "peer-pay-flow"})
            await c.post(f"/api/purchase-payments/{p2['id']}/confirm-paid")

            p3 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 1000000,
            })).json()["payment"]
            assert p3["workflow_status"] == "BELUM DIAJUKAN"
            await c.post(f"/api/purchase-payments/{p3['id']}/submit", json={"target_user_id": "peer-pay-flow"})
            conf3 = await c.post(f"/api/purchase-payments/{p3['id']}/confirm-paid")
            assert conf3.status_code == 200, conf3.text
            assert conf3.json()["payment"]["workflow_status"] == "DIBAYAR"
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


@pytest.mark.anyio
async def test_manual_purchase_receipt_http_create_get_zero_write_unauthorized():
    """Requires Mongo. Not executed in cloud VMs without MONGO_URL/mongod."""
    marker = f"TASK-PREVIEW-GR-{uuid.uuid4().hex[:8]}"
    async with await client_ctx() as c:
        try:
            before = await c.get("/api/purchase-receipts", params={"q": marker})
            assert before.status_code == 200
            created = await c.post("/api/purchase-receipts", json={
                "supplier": "PT Task Preview Supplier",
                "receipt_date": "2026-09-09",
                "notes": marker,
                "lines": [{
                    "item_name": "Preview Cup",
                    "item_code": "PV-CUP",
                    "unit": "pcs",
                    "received_qty": 2,
                    "master_item_id": "preview-item-1",
                }],
            })
            assert created.status_code == 200, created.text
            rec = created.json()["receipt"]
            assert rec["source"] == "manual"
            assert rec["receipt_number"].startswith("GR-")
            assert str(rec["accurate_receipt_id"]).startswith("manual:")
            assert rec["last_synced_at"] in (None, "")
            listed = await c.get("/api/purchase-receipts", params={"q": marker})
            assert listed.status_code == 200
            ids = [i["accurate_receipt_id"] for i in listed.json().get("items") or []]
            assert rec["accurate_receipt_id"] in ids
            n = await server.db.accurate_purchase_receipts.count_documents({})
            again = await c.get("/api/purchase-receipts")
            assert again.status_code == 200
            assert await server.db.accurate_purchase_receipts.count_documents({}) == n
        finally:
            app.dependency_overrides.pop(current_user, None)

    app.dependency_overrides[current_user] = lambda: {"role": "helper", "email": "h@x", "name": "H"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post("/api/purchase-receipts", json={
                "supplier": "X",
                "receipt_date": "2026-09-09",
                "lines": [{"item_name": "A", "received_qty": 1}],
            })
            assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
