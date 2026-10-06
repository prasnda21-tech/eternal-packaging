"""Payment draft / submit / confirm settlement lifecycle (TEST A–J)."""
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
    for name in ("purchase_orders", "purchase_invoices", "purchase_payments"):
        await db[name].create_index("id", unique=True)
    await db.purchase_orders.create_index("po_number", unique=True)
    await db.purchase_invoices.create_index("invoice_number", unique=True)
    await db.purchase_payments.create_index("payment_number", unique=True)
    for name in ("purchase_orders", "purchase_invoices", "purchase_payments"):
        try:
            await db[name].drop_index("idempotency_key_1")
        except Exception:
            pass
        await db[name].create_index(
            "idempotency_key", unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )


ACTOR = {
    "id": "pay-actor",
    "user_id": "pay-actor",
    "email": "pay-actor@example.com",
    "name": "Pay Actor",
    "role": "admin",
}
PEER = {
    "id": "pay-peer",
    "email": "pay-peer@example.com",
    "name": "Pay Peer",
    "role": "holding_team",
    "status": "active",
}


async def seed_users():
    for u in (
        {**ACTOR, "status": "active"},
        PEER,
    ):
        await server.db.users.update_one({"id": u["id"]}, {"$set": u}, upsert=True)


async def client_ctx():
    app.dependency_overrides[current_user] = lambda: ACTOR
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def make_invoice(c, total=500000):
    mid = f"mi-{uuid.uuid4().hex[:8]}"
    qty = 1
    unit = total
    po = (await c.post("/api/purchase-orders", json={
        "supplier": "PT Pay Life",
        "lines": [{
            "master_item_id": mid,
            "item_code": "PL1",
            "item_name": "Pay Item",
            "unit": "pcs",
            "ordered_qty": qty,
            "unit_price": unit,
        }],
        "idempotency_key": f"po-{uuid.uuid4()}",
    })).json()["order"]
    lid = po["lines"][0]["line_id"]
    inv = (await c.post("/api/purchase-invoices", json={
        "purchase_order_id": po["id"],
        "invoice_source": "purchase_order",
        "precedes_goods_receipt": True,
        "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
        "idempotency_key": f"inv-{uuid.uuid4()}",
    })).json()["invoice"]
    return inv


async def submit(c, payment_id):
    return await c.post(f"/api/purchase-payments/{payment_id}/submit", json={"target_user_id": PEER["id"]})


async def confirm(c, payment_id):
    return await c.post(f"/api/purchase-payments/{payment_id}/confirm-paid")


@pytest.mark.anyio
async def test_a_to_j_payment_lifecycle():
    await seed_users()
    async with await client_ctx() as c:
        try:
            # A — draft full
            inv = await make_invoice(c, 500000)
            pay = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"], "amount": 500000,
                "idempotency_key": f"a-{uuid.uuid4()}",
            })).json()["payment"]
            assert pay["workflow_status"] == "BELUM DIAJUKAN"
            assert pay["settlement_status"] == "UNPAID"
            inv_a = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_a["paid_total"] == 0
            assert inv_a["outstanding_amount"] == 500000
            assert inv_a["payment_status"] == "BELUM DIBAYAR"

            # B — submit
            sub = await submit(c, pay["id"])
            assert sub.status_code == 200, sub.text
            p_b = sub.json()["payment"]
            assert p_b["workflow_status"] == "DIAJUKAN"
            inv_b = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_b["paid_total"] == 0
            assert inv_b["payment_status"] == "BELUM DIBAYAR"

            # C — confirm paid
            conf = await confirm(c, pay["id"])
            assert conf.status_code == 200, conf.text
            p_c = conf.json()["payment"]
            assert p_c["workflow_status"] == "DIBAYAR"
            assert p_c["settlement_status"] == "PAID"
            assert p_c.get("paid_by_user_id") == ACTOR["id"]
            inv_c = conf.json()["invoice"]
            assert inv_c["paid_total"] == 500000
            assert inv_c["outstanding_amount"] == 0
            assert inv_c["payment_status"] == "LUNAS"

            # J — double confirm
            again = await confirm(c, pay["id"])
            assert again.status_code == 200
            assert again.json()["reused"] is True
            inv_j = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_j["paid_total"] == 500000

            # I — cancel paid blocked
            blocked = await c.post(f"/api/purchase-payments/{pay['id']}/cancel", json={})
            assert blocked.status_code == 409

            # D — partial confirmed
            inv2 = await make_invoice(c, 500000)
            p175 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv2["id"], "amount": 175000,
            })).json()["payment"]
            assert (await submit(c, p175["id"])).status_code == 200
            assert (await confirm(c, p175["id"])).status_code == 200
            inv_d = (await c.get(f"/api/purchase-invoices/{inv2['id']}")).json()["invoice"]
            assert inv_d["paid_total"] == 175000
            assert inv_d["outstanding_amount"] == 325000
            assert inv_d["payment_status"] == "DP"

            # E — second payment
            p325 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv2["id"], "amount": 325000,
            })).json()["payment"]
            assert (await submit(c, p325["id"])).status_code == 200
            assert (await confirm(c, p325["id"])).status_code == 200
            inv_e = (await c.get(f"/api/purchase-invoices/{inv2['id']}")).json()["invoice"]
            assert inv_e["paid_total"] == 500000
            assert inv_e["outstanding_amount"] == 0
            assert inv_e["payment_status"] == "LUNAS"

            # F — overpay
            over = await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv2["id"], "amount": 1,
            })
            assert over.status_code == 400

            # G — cancel draft
            inv3 = await make_invoice(c, 100000)
            draft = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv3["id"], "amount": 100000,
            })).json()["payment"]
            cancel_g = await c.post(f"/api/purchase-payments/{draft['id']}/cancel", json={})
            assert cancel_g.status_code == 200, cancel_g.text
            inv_g = (await c.get(f"/api/purchase-invoices/{inv3['id']}")).json()["invoice"]
            assert inv_g["paid_total"] == 0
            assert inv_g["payment_status"] == "BELUM DIBAYAR"

            # H — cancel submitted unpaid
            inv4 = await make_invoice(c, 200000)
            draft2 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv4["id"], "amount": 200000,
            })).json()["payment"]
            assert (await submit(c, draft2["id"])).status_code == 200
            cancel_h = await c.post(f"/api/purchase-payments/{draft2['id']}/cancel", json={})
            assert cancel_h.status_code == 200, cancel_h.text
            inv_h = (await c.get(f"/api/purchase-invoices/{inv4['id']}")).json()["invoice"]
            assert inv_h["paid_total"] == 0
            assert inv_h["payment_status"] == "BELUM DIBAYAR"

            # F variant: submitted overpay on confirm
            inv5 = await make_invoice(c, 100)
            p_ok = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv5["id"], "amount": 100,
            })).json()["payment"]
            assert (await submit(c, p_ok["id"])).status_code == 200
            assert (await confirm(c, p_ok["id"])).status_code == 200
            # craft a second submitted payment that would overpay if confirmed —
            # create is rejected when outstanding=0, so inject via DB then confirm
            rogue_id = str(uuid.uuid4())
            await server.db.purchase_payments.insert_one({
                "id": rogue_id,
                "payment_number": f"PPB-ROGUE-{uuid.uuid4().hex[:6]}",
                "purchase_invoice_id": inv5["id"],
                "amount": 1,
                "submission_status": "SUBMITTED",
                "settlement_status": "UNPAID",
                "cancelled": False,
            })
            bad_conf = await confirm(c, rogue_id)
            assert bad_conf.status_code == 400
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_ajukan_requires_successful_chat_and_is_idempotent(monkeypatch):
    """DIAJUKAN only after chat succeeds; retry with same idempotency key reuses chat."""
    async with await client_ctx() as c:
        try:
            await seed_users()
            inv = await make_invoice(c, total=100000)
            key = f"ajukan-chat-{uuid.uuid4().hex[:8]}"

            async def boom(*args, **kwargs):
                raise RuntimeError("chat down")

            monkeypatch.setattr("services.purchase_payments._send_submission_chat", boom)
            fail = await c.post("/api/purchase-payments/ajukan", json={
                "purchase_invoice_id": inv["id"],
                "amount": 100000,
                "target_user_id": PEER["id"],
                "idempotency_key": key,
            })
            assert fail.status_code == 502, fail.text
            docs = await server.db.purchase_payments.find({"idempotency_key": key}, {"_id": 0}).to_list(5)
            for d in docs:
                assert d.get("submission_status") != "SUBMITTED"
                assert (d.get("workflow_status") or d.get("status")) != "DIAJUKAN"

            async def ok_chat(*args, **kwargs):
                return "room-1", "msg-1", "2026-09-12T10:00:00+00:00"

            monkeypatch.setattr("services.purchase_payments._send_submission_chat", ok_chat)
            ok = await c.post("/api/purchase-payments/ajukan", json={
                "purchase_invoice_id": inv["id"],
                "amount": 100000,
                "target_user_id": PEER["id"],
                "idempotency_key": key,
            })
            assert ok.status_code == 200, ok.text
            body = ok.json()
            pay = body.get("payment") or {}
            assert pay.get("workflow_status") == "DIAJUKAN" or pay.get("status") == "DIAJUKAN"
            assert body.get("chat_room_id") == "room-1"
            assert body.get("message_id") == "msg-1"

            again = await c.post("/api/purchase-payments/ajukan", json={
                "purchase_invoice_id": inv["id"],
                "amount": 100000,
                "target_user_id": PEER["id"],
                "idempotency_key": key,
            })
            assert again.status_code == 200, again.text
            assert again.json().get("reused") is True
            assert again.json().get("chat_room_id") == "room-1"
        finally:
            app.dependency_overrides.pop(current_user, None)
