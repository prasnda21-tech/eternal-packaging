"""Audit trail + payment submission (personal chat reuse) for purchasing."""
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
from services.chat_constants import COLL_MESSAGES, COLL_ROOMS


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
    # users indexes already exist in shared test DB; do not recreate


def actor_a():
    return {
        "id": "user-a-audit",
        "user_id": "user-a-audit",
        "email": "a-audit@example.com",
        "name": "Actor A",
        "role": "admin",
    }


def actor_b_doc():
    return {
        "id": "user-b-audit",
        "email": "b-audit@example.com",
        "name": "Actor B Finance",
        "role": "holding_team",
        "status": "active",
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def seed_users():
    db = server.db
    for u in (
        {"id": "user-a-audit", "email": "a-audit@example.com", "name": "Actor A", "role": "admin", "status": "active"},
        actor_b_doc(),
    ):
        await db.users.update_one({"id": u["id"]}, {"$set": u}, upsert=True)


@pytest.mark.anyio
async def test_create_stamps_created_by_from_authenticated_user():
    await seed_users()
    async with await client_for(actor_a()) as c:
        try:
            mid = f"mi-{uuid.uuid4().hex[:8]}"
            pr = await c.post("/api/purchase-requests", json={
                "notes": "audit",
                "lines": [{"master_item_id": mid, "item_code": "A1", "item_name": "Item A", "requested_qty": 2}],
            })
            assert pr.status_code == 200, pr.text
            req = pr.json()["request"]
            assert req["created_by"]["id"] == "user-a-audit"
            assert req["created_by"]["name"] == "Actor A"
            assert req["updated_by"]["id"] == "user-a-audit"

            po = await c.post("/api/purchase-orders", json={
                "supplier": "PT Audit",
                "lines": [{
                    "master_item_id": mid,
                    "item_code": "A1",
                    "item_name": "Item A",
                    "unit": "pcs",
                    "ordered_qty": 2,
                    "unit_price": 1000,
                }],
                "idempotency_key": f"po-{uuid.uuid4()}",
            })
            assert po.status_code == 200, po.text
            order = po.json()["order"]
            assert order["created_by"]["id"] == "user-a-audit"
            lid = order["lines"][0]["line_id"]

            gr = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": order["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 2}],
                "idempotency_key": f"gr-{uuid.uuid4()}",
            })
            assert gr.status_code == 200, gr.text
            receipt = gr.json()["receipt"]
            assert receipt["created_by"]["id"] == "user-a-audit"

            inv = await c.post("/api/purchase-invoices", json={
                "purchase_order_id": order["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 2}],
                "idempotency_key": f"inv-{uuid.uuid4()}",
            })
            assert inv.status_code == 200, inv.text
            invoice = inv.json()["invoice"]
            assert invoice["created_by"]["id"] == "user-a-audit"

            pay = await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": invoice["id"],
                "amount": invoice["outstanding_amount"],
                "idempotency_key": f"pay-{uuid.uuid4()}",
            })
            assert pay.status_code == 200, pay.text
            payment = pay.json()["payment"]
            assert payment["created_by"]["id"] == "user-a-audit"
            assert payment["submission_status"] == "NOT_SUBMITTED"
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_cancel_without_reason_sets_cancelled_by():
    await seed_users()
    async with await client_for(actor_a()) as c:
        try:
            mid = f"mi-{uuid.uuid4().hex[:8]}"
            pr = await c.post("/api/purchase-requests", json={
                "lines": [{"master_item_id": mid, "item_code": "C1", "item_name": "Cancel Me", "requested_qty": 1}],
            })
            req = pr.json()["request"]
            cancel = await c.post(f"/api/purchase-requests/{req['id']}/cancel", json={})
            assert cancel.status_code == 200, cancel.text
            body = cancel.json()["request"]
            assert body["cancelled"] is True
            assert body["cancel_reason"] == "user_cancelled"
            assert body["cancelled_by"]["id"] == "user-a-audit"
            assert body["cancelled_by"]["name"] == "Actor A"
            assert body.get("cancelled_at")
            listed = await c.get("/api/purchase-requests")
            ids = {r["id"] for r in listed.json()["requests"]}
            assert req["id"] not in ids
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_payment_submit_targets_and_personal_chat_once():
    await seed_users()
    db = server.db
    async with await client_for(actor_a()) as c:
        try:
            targets = await c.get("/api/purchase-payments/submit-targets", params={"q": "Finance"})
            assert targets.status_code == 200, targets.text
            users = targets.json()["users"]
            assert any(u["id"] == "user-b-audit" for u in users)
            assert all(u["id"] != "user-a-audit" for u in users)

            mid = f"mi-{uuid.uuid4().hex[:8]}"
            po = await c.post("/api/purchase-orders", json={
                "supplier": "PT Submit",
                "lines": [{
                    "master_item_id": mid,
                    "item_code": "S1",
                    "item_name": "Submit Item",
                    "unit": "pcs",
                    "ordered_qty": 1,
                    "unit_price": 5000,
                }],
                "idempotency_key": f"po-sub-{uuid.uuid4()}",
            })
            order = po.json()["order"]
            lid = order["lines"][0]["line_id"]
            inv = await c.post("/api/purchase-invoices", json={
                "purchase_order_id": order["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
                "idempotency_key": f"inv-sub-{uuid.uuid4()}",
            })
            assert inv.status_code == 200, inv.text
            invoice = inv.json()["invoice"]
            pay = await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": invoice["id"],
                "amount": invoice["outstanding_amount"],
                "idempotency_key": f"pay-sub-{uuid.uuid4()}",
            })
            assert pay.status_code == 200, pay.text
            payment = pay.json()["payment"]
            assert payment["workflow_status"] == "BELUM DIAJUKAN"
            assert payment["settlement_status"] == "UNPAID"
            inv_check = (await c.get(f"/api/purchase-invoices/{invoice['id']}")).json()["invoice"]
            assert inv_check["payment_status"] == "BELUM DIBAYAR"
            assert inv_check["paid_total"] == 0

            sub1 = await c.post(f"/api/purchase-payments/{payment['id']}/submit", json={
                "target_user_id": "user-b-audit",
                "notes": "mohon proses",
            })
            assert sub1.status_code == 200, sub1.text
            body1 = sub1.json()
            assert body1["reused"] is False
            p1 = body1["payment"]
            assert p1["submission_status"] == "SUBMITTED"
            assert p1["submitted_to_user_id"] == "user-b-audit"
            assert p1["submitted_by_user_id"] == "user-a-audit"
            assert p1["payment_state"] == "DIAJUKAN" or p1["workflow_status"] == "DIAJUKAN"
            assert p1["settlement_status"] == "UNPAID"
            inv_after = (await c.get(f"/api/purchase-invoices/{invoice['id']}")).json()["invoice"]
            assert inv_after["paid_total"] == 0
            assert inv_after["payment_status"] == "BELUM DIBAYAR"
            assert body1["chat_room_id"]
            assert body1["message_id"]
            assert await db[COLL_MESSAGES].count_documents({"id": body1["message_id"]}) == 1

            room = await db[COLL_ROOMS].find_one({"id": body1["chat_room_id"]}, {"_id": 0})
            assert room["type"] == "dm"

            sub2 = await c.post(f"/api/purchase-payments/{payment['id']}/submit", json={
                "target_user_id": "user-b-audit",
                "notes": "double click",
            })
            assert sub2.status_code == 200, sub2.text
            body2 = sub2.json()
            assert body2["reused"] is True
            assert body2["message_id"] == body1["message_id"]
            assert await db[COLL_MESSAGES].count_documents({"id": body1["message_id"]}) == 1

            msg = await db[COLL_MESSAGES].find_one({"id": body1["message_id"]}, {"_id": 0})
            assert msg
            assert "Pengajuan Pembayaran" in (msg.get("content") or "")
            assert payment["payment_number"] in (msg.get("content") or "")
        finally:
            app.dependency_overrides.pop(current_user, None)
