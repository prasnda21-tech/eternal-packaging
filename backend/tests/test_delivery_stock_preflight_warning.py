"""Delivery shortage preflight + confirm revalidation (isolated DB)."""
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

TEST_DB = "eternal_packaging_test_delivery_stock_preflight"
assert "eternal_packaging_preview" not in TEST_DB
assert "uat_r2_manual" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("JWT_SECRET", "test-secret-key-delivery-stock-preflight-32")
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
    LEDGER_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_SALES_DELIVERY,
    adjust_physical_stock_to_target,
    confirm_sales_delivery,
    ensure_indexes as ensure_physical_indexes,
    post_movement,
    require_r2_delivery_stock_ready,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from server import current_user  # noqa: E402

pytestmark = pytest.mark.anyio

EMAIL = "preflight-uat@test.local"
PASSWORD = "TestPreflight!23"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def seeded():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await set_r2_authority_active(database, active=True)

    branch_id = str(uuid.uuid4())
    wh_a = str(uuid.uuid4())
    wh_b = str(uuid.uuid4())
    item = str(uuid.uuid4())
    await database.branches.insert_one(
        {"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True},
    )
    await database.warehouses.insert_many([
        {
            "id": wh_a, "branch_id": branch_id, "code": "GUD-A", "name": "Gudang A",
            "active": True, "is_operational": True,
        },
        {
            "id": wh_b, "branch_id": branch_id, "code": "GUD-B", "name": "Gudang B",
            "active": True, "is_operational": True,
        },
    ])
    await database.master_items.insert_one({
        "id": item,
        "item_code": "PF-ADJ-001",
        "item_name": "Preflight Item",
        "unit": "PCS",
        "is_active": True,
        "accurate_item_id": f"LOCAL-{item[:8]}",
    })
    await post_movement(database, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": wh_a,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 11,
        "reference_type": "opening",
        "reference_id": "pf-open-a",
        "idempotency_key": "pf-open-a",
    }, actor_id="seed")
    await post_movement(database, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": wh_b,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": 5,
        "reference_type": "opening",
        "reference_id": "pf-open-b",
        "idempotency_key": "pf-open-b",
    }, actor_id="seed")

    await database.users.insert_one({
        "id": "user-preflight",
        "email": EMAIL,
        "name": "Preflight Admin",
        "role": "admin",
        "status": "active",
        "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        "permissions_override": {},
    })

    po_id = f"po-pf-{uuid.uuid4().hex[:8]}"
    sj_id = f"sj-pf-{uuid.uuid4().hex[:8]}"
    line_id = f"{po_id}_line_0"
    await database.orders.insert_one({
        "id": po_id,
        "po_number": "PO/PF/OVER15",
        "customer": "PF Customer",
        "branch_id": branch_id,
        "cancelled": False,
        "current_stage": 13,
        "updated_at": "2026-10-13T18:00:00+00:00",
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "line_items": [{
            "line_id": line_id,
            "master_item_id": item,
            "item_code": "PF-ADJ-001",
            "name": "Preflight Item",
            "quantity": 15,
            "unit": "PCS",
            "production_process": "non_print",
        }],
        "packing_info": {"qty_packed": 0, "lines": []},
        "shipments": [{
            "id": sj_id,
            "number": "SJ/PF/OVER15",
            "sales_delivery_id": sj_id,
            "total_qty": 15,
            "lines": [{
                "line_idx": 0,
                "line_id": line_id,
                "qty": 15,
                "name": "Preflight Item",
                "unit": "PCS",
            }],
        }],
        "surat_jalan_files": [{
            "id": sj_id,
            "shipment_id": sj_id,
            "sj_number": "SJ/PF/OVER15",
            "name": "SJ/PF/OVER15.pdf",
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": 15}],
        }],
        "loading_confirmations": [{
            "id": str(uuid.uuid4()),
            "surat_jalan_id": sj_id,
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": 15, "name": "Preflight Item"}],
            "confirmed_at": "2026-10-13T18:00:00+00:00",
        }],
        "delivery_proofs": [
            {"id": "p1", "shipment_id": sj_id, "storage_path": "x/p1.jpg", "original_filename": "a.jpg"},
            {"id": "p2", "shipment_id": sj_id, "storage_path": "x/p2.jpg", "original_filename": "b.jpg"},
        ],
        "delivery_recipient_signature": {
            "id": "sig-r",
            "recipient_name": "Penerima PF",
            "signature_storage_path": "sig.png",
            "recipient_signed_at": "2026-10-13T18:01:00+00:00",
            "frozen": False,
            "shipment_id": sj_id,
        },
        "delivery_driver_signature": {
            "id": "sig-d",
            "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "2026-10-13T18:01:30+00:00",
            "frozen": False,
            "shipment_id": sj_id,
        },
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
    })
    await database.sales_deliveries.insert_one({
        "id": sj_id,
        "po_id": po_id,
        "sj_number": "SJ/PF/OVER15",
        "total_qty": 15,
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{
            "line_idx": 0,
            "line_id": line_id,
            "qty": 15,
            "master_item_id": item,
            "name": "Preflight Item",
        }],
    })

    fx = {
        "branch_id": branch_id, "wh_a": wh_a, "wh_b": wh_b, "item": item,
        "po_id": po_id, "sj_id": sj_id,
    }
    yield database, fx
    await client.drop_database(TEST_DB)
    client.close()


async def test_preflight_shortage_11_vs_15(seeded):
    db, fx = seeded
    ledger_before = await db[LEDGER_COLLECTION].count_documents({})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        res = await client.post(
            f"/api/orders/{fx['po_id']}/delivery-stock-preflight",
            headers=headers,
            json={"shipment_id": fx["sj_id"], "warehouse_id": fx["wh_a"]},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["r2_authority_active"] is True
        assert body["blocks_delivery"] is False
        assert body["has_shortage_warning"] is True
        warn = body["stock_warnings"][0]
        assert warn["stok_tercatat"] == 11
        assert warn["kebutuhan"] == 15
        assert warn["kekurangan"] == 4
        assert warn["projected_book"] == -4
        assert warn["blocks_delivery"] is False
        assert warn["warehouse_code"] == "GUD-A"
    assert await db[LEDGER_COLLECTION].count_documents({}) == ledger_before


async def test_preflight_no_warning_when_sufficient(seeded):
    db, fx = seeded
    await adjust_physical_stock_to_target(
        db,
        fx["item"],
        {
            "branch_id": fx["branch_id"],
            "warehouse_id": fx["wh_a"],
            "target_physical_qty": 20,
            "reason": "Top-up preflight sufficient",
            "idempotency_key": "pf-topup-20",
        },
        actor_id="seed",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        res = await client.post(
            f"/api/orders/{fx['po_id']}/delivery-stock-preflight",
            headers=headers,
            json={"shipment_id": fx["sj_id"], "warehouse_id": fx["wh_a"]},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["has_shortage_warning"] is False
        assert all(int(w.get("kekurangan") or 0) == 0 for w in body["stock_warnings"])


async def test_preflight_r2_off_compatible(seeded):
    db, fx = seeded
    await set_r2_authority_active(db, active=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        res = await client.post(
            f"/api/orders/{fx['po_id']}/delivery-stock-preflight",
            headers=headers,
            json={"shipment_id": fx["sj_id"], "warehouse_id": fx["wh_a"]},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["r2_authority_active"] is False
        assert body["stock_warnings"] == []


async def test_shortage_does_not_block_and_replay_is_idempotent(seeded):
    """Service-level confirm: shortage warns, posts once, replay does not duplicate."""
    db, fx = seeded
    order = await db.orders.find_one({"id": fx["po_id"]}, {"_id": 0})
    _br, _wh, warnings = await require_r2_delivery_stock_ready(
        db,
        order,
        warehouse_id=fx["wh_a"],
        branch_id=fx["branch_id"],
        lines=[{"master_item_id": fx["item"], "qty": 15, "line_idx": 0, "line_id": f"{fx['po_id']}_line_0"}],
    )
    assert warnings
    assert warnings[0]["kekurangan"] == 4
    assert warnings[0]["blocks_delivery"] is False

    conf_id = f"conf-{uuid.uuid4().hex[:8]}"
    delivered = await confirm_sales_delivery(
        db,
        order_id=fx["po_id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 15, "master_item_id": fx["item"], "line_id": f"{fx['po_id']}_line_0"}],
        actor_id="seed",
        warehouse_id=fx["wh_a"],
        branch_id=fx["branch_id"],
    )
    assert delivered["replayed"] is False
    assert delivered["movements"][0]["qty_delta"] == -15
    assert delivered["movements"][0]["balance_after"] == -4
    assert (delivered.get("stock_warnings") or [{}])[0].get("kekurangan") == 4

    replay = await confirm_sales_delivery(
        db,
        order_id=fx["po_id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 15, "master_item_id": fx["item"], "line_id": f"{fx['po_id']}_line_0"}],
        actor_id="seed",
        warehouse_id=fx["wh_a"],
        branch_id=fx["branch_id"],
    )
    assert replay["replayed"] is True
    sales = await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": fx["wh_a"],
        "master_item_id": fx["item"],
    })
    assert sales == 1
    bal_b = await db.physical_stock_balances.find_one({
        "master_item_id": fx["item"], "warehouse_id": fx["wh_b"],
    })
    assert float(bal_b["physical_qty"]) == 5


async def test_confirm_delivered_http_returns_stock_warnings(seeded):
    """HTTP confirm path surfaces shortage warnings without blocking."""
    db, fx = seeded
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-preflight",
        "email": EMAIL,
        "name": "Preflight Admin",
        "role": "admin",
        "permissions_override": {},
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            action_id = str(uuid.uuid4())
            conf = await client.post(
                f"/api/orders/{fx['po_id']}/confirm-delivered",
                json={
                    "action_id": action_id,
                    "shipment_id": fx["sj_id"],
                    "warehouse_id": fx["wh_a"],
                    "recipient_name": "Penerima PF",
                },
            )
            assert conf.status_code == 200, conf.text
            body = conf.json()
            assert body.get("has_shortage_warning") is True
            warns = body.get("stock_warnings") or []
            assert warns and warns[0]["kekurangan"] == 4
            assert body["order"]["delivery_status"] == "Terkirim"
            sales = await db[LEDGER_COLLECTION].count_documents({
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "warehouse_id": fx["wh_a"],
            })
            assert sales == 1
            replay = await client.post(
                f"/api/orders/{fx['po_id']}/confirm-delivered",
                json={
                    "action_id": action_id,
                    "shipment_id": fx["sj_id"],
                    "warehouse_id": fx["wh_a"],
                    "recipient_name": "Penerima PF",
                },
            )
            assert replay.status_code == 200, replay.text
            assert await db[LEDGER_COLLECTION].count_documents({
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "warehouse_id": fx["wh_a"],
            }) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
