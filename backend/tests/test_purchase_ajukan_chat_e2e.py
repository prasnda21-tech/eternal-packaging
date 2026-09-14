"""E2E: Ajukan Pembayaran → existing personal chat (reuse, idempotent, openable)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Do NOT overwrite DB_NAME — share eternal_packaging_test via setdefault like other purchase tests.
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.chat_constants import COLL_MESSAGES, COLL_ROOMS, ROOM_DM
from services.chat_indexes import ensure_indexes as ensure_chat_indexes
from services.chat_rooms import get_or_create_dm


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
        {
            "$set": {
                "key": SETTINGS_KEY,
                "enabled": False,
                "stock_in_trigger": "goods_receipt",
                "stock_out_trigger": "sales_delivery",
            }
        },
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
        try:
            await db[name].create_index(
                "idempotency_key",
                unique=True,
                partialFilterExpression={"idempotency_key": {"$type": "string"}},
                name="idempotency_key_partial_unique",
            )
        except Exception:
            pass


def _fresh_users():
    suffix = uuid.uuid4().hex[:8]
    actor = {
        "id": f"ajukan-actor-{suffix}",
        "user_id": f"ajukan-actor-{suffix}",
        "email": f"ajukan-actor-{suffix}@example.com",
        "name": "Pengaju Utama",
        "role": "admin",
        "status": "active",
        "is_super_admin": True,
    }
    peer = {
        "id": f"ajukan-peer-{suffix}",
        "email": f"ajukan-peer-{suffix}@example.com",
        "name": "Finance Target",
        "role": "holding_team",
        "status": "active",
    }
    return actor, peer


async def seed_users(actor, peer):
    for u in (actor, peer):
        await server.db.users.update_one({"id": u["id"]}, {"$set": u}, upsert=True)


async def client_as(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def make_invoice(c, total=150000):
    mid = f"mi-{uuid.uuid4().hex[:8]}"
    po = (
        await c.post(
            "/api/purchase-orders",
            json={
                "supplier": "PT Chat Supplier",
                "lines": [
                    {
                        "master_item_id": mid,
                        "item_code": "CH1",
                        "item_name": "Chat Item",
                        "unit": "pcs",
                        "ordered_qty": 1,
                        "unit_price": total,
                    }
                ],
                "idempotency_key": f"po-{uuid.uuid4()}",
            },
        )
    ).json()["order"]
    lid = po["lines"][0]["line_id"]
    inv = (
        await c.post(
            "/api/purchase-invoices",
            json={
                "purchase_order_id": po["id"],
                "invoice_source": "purchase_order",
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
                "idempotency_key": f"inv-{uuid.uuid4()}",
            },
        )
    ).json()["invoice"]
    return inv


@pytest.mark.anyio
async def test_ajukan_reuses_personal_chat_message_and_is_idempotent():
    actor, peer = _fresh_users()
    await seed_users(actor, peer)
    await ensure_chat_indexes(server.db)

    # Existing personal chat for THIS pair must be reused (ignore other DMs in shared DB).
    existing = await get_or_create_dm(server.db, actor, peer["id"])
    existing_room_id = existing["id"]
    assert await server.db[COLL_ROOMS].count_documents({"type": ROOM_DM, "id": existing_room_id}) == 1

    key = f"ajukan-e2e-{uuid.uuid4().hex}"
    async with await client_as(actor) as c:
        try:
            inv = await make_invoice(c, total=150000)
            body = {
                "purchase_invoice_id": inv["id"],
                "amount": 150000,
                "target_user_id": peer["id"],
                "notes": "Mohon diproses",
                "idempotency_key": key,
            }
            r1 = await c.post("/api/purchase-payments/ajukan", json=body)
            assert r1.status_code == 200, r1.text
            data1 = r1.json()
            pay = data1["payment"]
            assert (pay.get("workflow_status") or pay.get("status")) == "DIAJUKAN"
            assert data1.get("chat_room_id") == existing_room_id
            assert pay.get("submission_chat_room_id") == existing_room_id
            assert data1.get("reused") is False

            msgs = await server.db[COLL_MESSAGES].find(
                {"room_id": existing_room_id}, {"_id": 0}
            ).to_list(20)
            assert len(msgs) == 1
            content = msgs[0].get("content") or ""
            assert inv.get("invoice_number") in content or "No Faktur:" in content
            assert "PT Chat Supplier" in content or "Supplier:" in content
            assert "150.000" in content or "150000" in content or "Nominal:" in content
            assert "Pengaju Utama" in content or "Diajukan oleh:" in content
            assert "Tanggal Pengajuan:" in content
            assert "pembayaran-pembelian" in content
            assert pay["id"] in content

            # Retry same key → no duplicate payment/room/message
            r2 = await c.post("/api/purchase-payments/ajukan", json=body)
            assert r2.status_code == 200, r2.text
            data2 = r2.json()
            assert data2.get("reused") is True
            assert data2.get("chat_room_id") == existing_room_id
            assert data2["payment"]["id"] == pay["id"]
            assert await server.db[COLL_ROOMS].count_documents({"id": existing_room_id}) == 1
            assert await server.db[COLL_MESSAGES].count_documents({"room_id": existing_room_id}) == 1
            assert await server.db.purchase_payments.count_documents({"idempotency_key": key}) == 1

            # Buka Chat path: room + messages reachable via existing Chat HTTP
            room_http = await c.get(f"/api/chat/rooms/{existing_room_id}")
            assert room_http.status_code == 200, room_http.text
            listed = await c.get("/api/chat/rooms")
            assert listed.status_code == 200, listed.text
            listed_body = listed.json()
            rooms = listed_body.get("rooms") if isinstance(listed_body, dict) else listed_body
            assert any((r.get("id") or r.get("room_id")) == existing_room_id for r in rooms)

            msgs_http = await c.get(f"/api/chat/rooms/{existing_room_id}/messages")
            assert msgs_http.status_code == 200, msgs_http.text
            payload = msgs_http.json()
            http_msgs = payload.get("messages") if isinstance(payload, dict) else payload
            assert any("Tanggal Pengajuan:" in (m.get("content") or "") for m in http_msgs)
        finally:
            app.dependency_overrides.pop(current_user, None)
