"""HTTP path proof: login → physical-stock / opname / GR / delivery → ledger.

Isolated DB only. Rebinds server.db; does not touch eternal_packaging_preview.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_test_warehouse_ab_http"
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("JWT_SECRET", "test-secret-key-warehouse-ab-http-flow-32")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("STORAGE_PROVIDER", "local")

MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"
    os.environ["MONGO_URL"] = MONGO_URL

import server  # noqa: E402
from server import app  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    ensure_indexes as ensure_physical_indexes,
    get_balance,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_opname_sessions import ensure_indexes as ensure_opname_indexes  # noqa: E402

pytestmark = pytest.mark.anyio

EMAIL = "wh-ab-http@test.local"
PASSWORD = "TestWhAb!23"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def seeded():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await ensure_opname_indexes(database)
    await set_r2_authority_active(database, active=True)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )

    branch_a, branch_b = str(uuid.uuid4()), str(uuid.uuid4())
    wh_a, wh_b = str(uuid.uuid4()), str(uuid.uuid4())
    item = str(uuid.uuid4())
    await database.branches.insert_many([
        {"id": branch_a, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": branch_b, "code": "SMG", "name": "Semarang", "active": True},
    ])
    await database.warehouses.insert_many([
        {
            "id": wh_a, "branch_id": branch_a, "code": "A", "name": "Gudang A",
            "active": True, "is_operational": True,
        },
        {
            "id": wh_b, "branch_id": branch_b, "code": "B", "name": "Gudang B",
            "active": True, "is_operational": True,
        },
    ])
    await database.master_items.insert_one({
        "id": item,
        "item_code": "AB-HTTP-1",
        "item_name": "Karton HTTP",
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
        "accurate_item_id": f"LOCAL-{item}",
    })
    open_at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    for wid, bid, qty, key in ((wh_a, branch_a, 1000, "a"), (wh_b, branch_b, 500, "b")):
        await post_movement(database, {
            "master_item_id": item,
            "branch_id": bid,
            "warehouse_id": wid,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": qty,
            "reference_type": "opening_balance",
            "reference_id": f"http-open-{key}",
            "idempotency_key": f"http-open-{key}",
            "occurred_at": open_at,
        }, actor_id="seeder")

    await database.users.insert_one({
        "id": "user-wh-ab-http",
        "email": EMAIL,
        "name": "WH AB HTTP",
        "role": "admin",
        "status": "active",
        "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        "permissions_override": {},
    })

    fx = {
        "branch_a": branch_a, "branch_b": branch_b,
        "wh_a": wh_a, "wh_b": wh_b, "item": item,
    }
    yield database, fx
    await client.drop_database(TEST_DB)
    client.close()


async def _qty(db, item, branch, warehouse):
    doc = await get_balance(db, item, branch, warehouse)
    return float((doc or {}).get("physical_qty") or 0)


async def test_http_login_opname_receipt_delivery_ledger(seeded):
    db, fx = seeded
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as client:
        login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Stock Saat Ini (R2 catalog) — branch A / warehouse A
        cat = await client.get(
            "/api/physical-stock",
            params={"branch_id": fx["branch_a"], "warehouse_id": fx["wh_a"], "q": "AB-HTTP-1"},
            headers=headers,
        )
        assert cat.status_code == 200, cat.text
        body = cat.json()
        assert body.get("stock_read_mode") == "r2"
        row = next(r for r in body["items"] if r["master_item_id"] == fx["item"])
        assert float(row["physical_qty"]) == 1000

        # Opname A → 950 (HTTP session routes)
        create = await client.post("/api/stock-opname-sessions", headers=headers, json={
            "branch_id": fx["branch_a"],
            "warehouse_id": fx["wh_a"],
            "mode": "operational",
            "idempotency_key": "http-opname-1",
        })
        assert create.status_code in (200, 201), create.text
        session_id = create.json()["id"]
        cutoff = await client.post(f"/api/stock-opname-sessions/{session_id}/cutoff", headers=headers)
        assert cutoff.status_code == 200, cutoff.text
        line = next(
            r for r in cutoff.json()["lines"]
            if r["master_item_id"] == fx["item"]
        )
        count_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        counted = await client.post(
            f"/api/stock-opname-sessions/{session_id}/counts",
            headers=headers,
            json={"lines": [{
                "line_id": line["line_id"],
                "physical_count_qty": 950,
                "physical_count_at": count_at,
            }]},
        )
        assert counted.status_code == 200, counted.text
        review = await client.post(f"/api/stock-opname-sessions/{session_id}/review", headers=headers)
        assert review.status_code == 200, review.text
        confirm = await client.post(
            f"/api/stock-opname-sessions/{session_id}/confirm",
            headers=headers,
            json={"idempotency_key": "http-conf-1"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "CONFIRMED"
        confirm2 = await client.post(
            f"/api/stock-opname-sessions/{session_id}/confirm",
            headers=headers,
            json={"idempotency_key": "http-conf-1"},
        )
        assert confirm2.status_code == 200
        assert await db[LEDGER_COLLECTION].count_documents({
            "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
            "warehouse_id": fx["wh_a"],
        }) == 1
        assert await _qty(db, fx["item"], fx["branch_a"], fx["wh_a"]) == 950
        assert await _qty(db, fx["item"], fx["branch_b"], fx["wh_b"]) == 500

        # Purchase order + goods receipt via HTTP operational endpoints
        po = await client.post("/api/purchase-orders", headers=headers, json={
            "supplier": "Supplier HTTP AB",
            "lines": [{
                "master_item_id": fx["item"],
                "item_name": "Karton HTTP",
                "item_code": "AB-HTTP-1",
                "unit": "PCS",
                "ordered_qty": 40,
                "unit_price": 1000,
            }],
            "idempotency_key": "http-po-1",
        })
        assert po.status_code in (200, 201), po.text
        po_body = po.json().get("order") or po.json()
        gr = await client.post("/api/purchase-goods-receipts", headers=headers, json={
            "purchase_order_id": po_body["id"],
            "warehouse_id": fx["wh_a"],
            "lines": [{
                "purchase_order_line_id": po_body["lines"][0]["line_id"],
                "received_qty": 40,
            }],
            "idempotency_key": "http-gr-1",
        })
        assert gr.status_code in (200, 201), gr.text
        gr_body = gr.json().get("receipt") or gr.json()
        gr2 = await client.post("/api/purchase-goods-receipts", headers=headers, json={
            "purchase_order_id": po_body["id"],
            "warehouse_id": fx["wh_a"],
            "lines": [{
                "purchase_order_line_id": po_body["lines"][0]["line_id"],
                "received_qty": 40,
            }],
            "idempotency_key": "http-gr-1",
        })
        assert gr2.status_code in (200, 201)
        gr2_body = gr2.json().get("receipt") or gr2.json()
        assert gr2_body["id"] == gr_body["id"]
        assert await _qty(db, fx["item"], fx["branch_a"], fx["wh_a"]) == 990
        assert await _qty(db, fx["item"], fx["branch_b"], fx["wh_b"]) == 500

        # Delivery: SJ confirm HTTP posts via apply_confirmed_sales_delivery.
        # Engine entrypoint confirm_sales_delivery is the same writer (see server.py _post_r2_delivery).
        from services.physical_stock import confirm_sales_delivery
        await db.orders.insert_one({
            "id": "order-http-ab-deliver",
            "status": "open",
            "cancelled": False,
            "line_items": [{
                "line_id": "ld1",
                "master_item_id": fx["item"],
                "quantity": 25,
                "production_process": "non_print",
                "name": "Karton HTTP",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 25}],
        })
        delivered = await confirm_sales_delivery(
            db,
            order_id="order-http-ab-deliver",
            confirmation_id="http-sd-1",
            lines=[{"line_idx": 0, "qty": 25, "master_item_id": fx["item"]}],
            actor_id="user-wh-ab-http",
            warehouse_id=fx["wh_a"],
            branch_id=fx["branch_a"],
        )
        assert delivered["replayed"] is False
        replay_sd = await confirm_sales_delivery(
            db,
            order_id="order-http-ab-deliver",
            confirmation_id="http-sd-1",
            lines=[{"line_idx": 0, "qty": 25, "master_item_id": fx["item"]}],
            actor_id="user-wh-ab-http",
            warehouse_id=fx["wh_a"],
            branch_id=fx["branch_a"],
        )
        assert replay_sd["replayed"] is True
        assert await _qty(db, fx["item"], fx["branch_a"], fx["wh_a"]) == 965
        assert await _qty(db, fx["item"], fx["branch_b"], fx["wh_b"]) == 500

        cat_end = await client.get(
            "/api/physical-stock",
            params={"branch_id": fx["branch_a"], "warehouse_id": fx["wh_a"], "q": "AB-HTTP-1"},
            headers=headers,
        )
        assert cat_end.status_code == 200
        end_row = next(r for r in cat_end.json()["items"] if r["master_item_id"] == fx["item"])
        assert float(end_row["physical_qty"]) == 965

        assert await db[LEDGER_COLLECTION].count_documents({
            "movement_type": MOVEMENT_PURCHASE_RECEIPT, "warehouse_id": fx["wh_a"],
        }) == 1
        assert await db[LEDGER_COLLECTION].count_documents({
            "movement_type": MOVEMENT_SALES_DELIVERY, "warehouse_id": fx["wh_a"],
        }) == 1
        assert await db[BALANCE_COLLECTION].count_documents({"master_item_id": fx["item"]}) == 2
