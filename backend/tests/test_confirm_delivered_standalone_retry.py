"""Confirm-delivered HTTP crash/retry under warehouse_stock_standalone (R2 OFF).

Isolated standalone mongod only. Rebinds server.db; never touches preview/prod.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import bcrypt
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_test_confirm_delivered_standalone_retry"
assert "eternal_packaging_preview" not in TEST_DB
STANDALONE_URL = os.environ.get("STANDALONE_MONGO_URL", "mongodb://127.0.0.1:27018")
os.environ["DB_NAME"] = TEST_DB
os.environ["MONGO_URL"] = STANDALONE_URL
os.environ.setdefault("JWT_SECRET", "test-secret-key-confirm-delivered-standalone-32")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server  # noqa: E402
from server import app, current_user  # noqa: E402
from services.physical_stock import (  # noqa: E402
    LEDGER_COLLECTION,
    ensure_indexes as ensure_physical_indexes,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    post_opening_balance_standalone,
    set_warehouse_stock_standalone_enabled,
)

pytestmark = pytest.mark.anyio

EMAIL = "confirm-sa@test.local"
PASSWORD = "TestConfirmSa!23"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def seeded():
    client = AsyncIOMotorClient(STANDALONE_URL)
    hello = await client.admin.command("hello")
    assert not hello.get("setName"), (
        f"Must run on standalone mongod; got setName={hello.get('setName')!r}"
    )
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)

    branch_id = str(uuid.uuid4())
    wh_a = str(uuid.uuid4())
    item = str(uuid.uuid4())
    await database.branches.insert_one(
        {"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True},
    )
    await database.warehouses.insert_one({
        "id": wh_a,
        "branch_id": branch_id,
        "code": "MAIN",
        "name": "Gudang Utama",
        "active": True,
        "is_operational": True,
        "is_default": True,
    })
    await database.master_items.insert_one({
        "id": item,
        "item_code": "SA-DEL-001",
        "item_name": "Standalone Delivery Item",
        "unit": "PCS",
        "is_active": True,
        "item_type": "INVENTORY",
    })
    await post_opening_balance_standalone(
        database,
        item,
        {
            "branch_id": branch_id,
            "warehouse_id": wh_a,
            "physical_qty": 100,
            "idempotency_key": f"open-sa-del-{item[:8]}",
        },
        actor_id="seed",
    )
    await database.users.insert_one({
        "id": "user-confirm-sa",
        "email": EMAIL,
        "name": "Confirm SA Admin",
        "role": "admin",
        "status": "active",
        "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        "permissions_override": {},
    })

    po_id = f"po-sa-{uuid.uuid4().hex[:8]}"
    sj_id = f"sj-sa-{uuid.uuid4().hex[:8]}"
    line_id = f"{po_id}_line_0"
    action_id = f"act-sa-{uuid.uuid4().hex[:8]}"
    conf_id = f"conf-sa-{uuid.uuid4().hex[:8]}"
    # Business confirm already persisted; warehouse ledger intentionally missing
    # (crash after order write, before standalone stock posting).
    await database.orders.insert_one({
        "id": po_id,
        "po_number": "PO/SA/DEL",
        "customer": "SA Customer",
        "branch_id": branch_id,
        "cancelled": False,
        "current_stage": 14,
        "updated_at": "2026-10-13T18:00:00+00:00",
        "delivery_status": "Terkirim",
        "qty_delivered": 25,
        "delivered_at": "2026-10-13T18:05:00+00:00",
        "line_items": [{
            "line_id": line_id,
            "master_item_id": item,
            "item_code": "SA-DEL-001",
            "name": "Standalone Delivery Item",
            "quantity": 25,
            "unit": "PCS",
            "production_process": "non_print",
        }],
        "packing_info": {
            "qty_packed": 25,
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": 25}],
        },
        "shipments": [{
            "id": sj_id,
            "number": "SJ/SA/DEL",
            "sales_delivery_id": sj_id,
            "total_qty": 25,
            "delivered": True,
            "delivered_at": "2026-10-13T18:05:00+00:00",
            "delivered_qty": 25,
            "lines": [{
                "line_idx": 0,
                "line_id": line_id,
                "qty": 25,
                "name": "Standalone Delivery Item",
                "unit": "PCS",
            }],
        }],
        "surat_jalan_files": [{
            "id": sj_id,
            "shipment_id": sj_id,
            "sj_number": "SJ/SA/DEL",
            "name": "SJ/SA/DEL.pdf",
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": 25}],
        }],
        "loading_confirmations": [{
            "id": str(uuid.uuid4()),
            "surat_jalan_id": sj_id,
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": 25, "name": "Standalone Delivery Item"}],
            "confirmed_at": "2026-10-13T18:00:00+00:00",
        }],
        "delivery_proofs": [
            {"id": "p1", "shipment_id": sj_id, "storage_path": "x/p1.jpg", "original_filename": "a.jpg"},
            {"id": "p2", "shipment_id": sj_id, "storage_path": "x/p2.jpg", "original_filename": "b.jpg"},
        ],
        "delivery_recipient_signature": {
            "id": "sig-r",
            "recipient_name": "Penerima SA",
            "signature_storage_path": "sig.png",
            "recipient_signed_at": "2026-10-13T18:01:00+00:00",
            "frozen": True,
            "shipment_id": sj_id,
        },
        "delivery_driver_signature": {
            "id": "sig-d",
            "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "2026-10-13T18:01:30+00:00",
            "frozen": True,
            "shipment_id": sj_id,
        },
        "delivery_confirmations": [{
            "id": conf_id,
            "action_id": action_id,
            "shipment_id": sj_id,
            "qty": 25,
            "items": [{"line_idx": 0, "line_id": line_id, "qty": 25}],
            "confirmed_at": "2026-10-13T18:05:00+00:00",
        }],
        "delivery_confirm_action_ids": [action_id],
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "delivery_confirmed",
        },
    })
    await database.sales_deliveries.insert_one({
        "id": sj_id,
        "po_id": po_id,
        "order_id": po_id,
        "sj_number": "SJ/SA/DEL",
        "total_qty": 25,
        "status": "TERKIRIM",
        "is_cancelled": False,
        "line_items": [{
            "line_idx": 0,
            "line_id": line_id,
            "qty": 25,
            "master_item_id": item,
            "name": "Standalone Delivery Item",
        }],
    })

    fx = {
        "branch_id": branch_id,
        "wh_a": wh_a,
        "item": item,
        "po_id": po_id,
        "sj_id": sj_id,
        "action_id": action_id,
    }
    try:
        yield database, fx
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _qty(db, item_id, warehouse_id) -> int:
    bal = await db.physical_stock_balances.find_one(
        {"master_item_id": item_id, "warehouse_id": warehouse_id},
        {"_id": 0, "physical_qty": 1},
    )
    return int((bal or {}).get("physical_qty") or 0)


async def test_confirm_delivered_http_retries_missing_standalone_stock(seeded):
    """Order already TERKIRIM + action recorded; retry recovers missing ledger once."""
    db, fx = seeded
    assert await _qty(db, fx["item"], fx["wh_a"]) == 100
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": "SALES_DELIVERY",
        "warehouse_id": fx["wh_a"],
        "master_item_id": fx["item"],
    }) == 0

    app.dependency_overrides[current_user] = lambda: {
        "id": "user-confirm-sa",
        "email": EMAIL,
        "name": "Confirm SA Admin",
        "role": "admin",
        "permissions_override": {},
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            retry = await client.post(
                f"/api/orders/{fx['po_id']}/confirm-delivered",
                json={
                    "action_id": fx["action_id"],
                    "shipment_id": fx["sj_id"],
                    "warehouse_id": fx["wh_a"],
                    "recipient_name": "Penerima SA",
                },
            )
            assert retry.status_code == 200, retry.text
            body = retry.json()
            order = body.get("order") or body
            assert order.get("delivery_status") == "Terkirim"
            assert int(order.get("qty_delivered") or 0) == 25
            assert fx["action_id"] in list(order.get("delivery_confirm_action_ids") or [])
            assert len(order.get("delivery_confirmations") or []) == 1

            assert await _qty(db, fx["item"], fx["wh_a"]) == 75
            assert await db[LEDGER_COLLECTION].count_documents({
                "movement_type": "SALES_DELIVERY",
                "warehouse_id": fx["wh_a"],
                "master_item_id": fx["item"],
            }) == 1

            # Same action/idempotency key again — no double debit, status unchanged.
            again = await client.post(
                f"/api/orders/{fx['po_id']}/confirm-delivered",
                json={
                    "action_id": fx["action_id"],
                    "shipment_id": fx["sj_id"],
                    "warehouse_id": fx["wh_a"],
                    "recipient_name": "Penerima SA",
                },
            )
            assert again.status_code == 200, again.text
            again_order = (again.json().get("order") or again.json())
            assert again_order.get("delivery_status") == "Terkirim"
            assert int(again_order.get("qty_delivered") or 0) == 25
            assert len(again_order.get("delivery_confirmations") or []) == 1
            assert await _qty(db, fx["item"], fx["wh_a"]) == 75
            assert await db[LEDGER_COLLECTION].count_documents({
                "movement_type": "SALES_DELIVERY",
                "warehouse_id": fx["wh_a"],
                "master_item_id": fx["item"],
            }) == 1
            # No PSS dual-write under standalone.
            assert await db.stock_movements.count_documents({
                "movement_type": "PENGIRIMAN",
                "master_item_id": fx["item"],
            }) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
