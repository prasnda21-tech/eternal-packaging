"""SJ soft-cancel without stock mutations: operational path (Mongo standalone safe).

Loading stays active; Hapus Loading is a separate manual step. Multi-SJ independent.
Fail-closed on ambiguous / unsupported stock evidence.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import bcrypt
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_test_sj_cancel_operational_no_stock"
assert "eternal_packaging_preview" not in TEST_DB
assert "uat_r2_manual" not in TEST_DB
_PREV_DB_NAME = os.environ.get("DB_NAME")
os.environ["DB_NAME"] = TEST_DB
# Intentionally standalone (no replicaSet): proves operational cancel works on
# MongoDB topologies that cannot open multi-document transactions — same class
# as production CELCI failures (HTTP 503 replica-set required).
os.environ["MONGO_URL"] = "mongodb://127.0.0.1:27017"
os.environ.setdefault("JWT_SECRET", "test-secret-key-sj-cancel-operational-32chars")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")

MONGO_URL = os.environ["MONGO_URL"]

import server  # noqa: E402
from server import app, current_user  # noqa: E402
from quantity_rules import (  # noqa: E402
    MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL,
    qty_loaded_for_line,
    validate_cancel_loading_confirmation,
)
from services.physical_stock import (  # noqa: E402
    LEDGER_COLLECTION,
    MOVEMENT_SALES_DELIVERY,
    SALES_DELIVERY_CONFIRMATIONS,
    PhysicalStockUnavailableError,
    assess_sales_delivery_cancel_stock,
    cancel_sales_delivery_document_operational,
    ensure_indexes as ensure_physical_indexes,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

pytestmark = pytest.mark.anyio

ADMIN_EMAIL = "sj-op-cancel-admin@test.local"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await set_r2_authority_active(database, active=False)
    await database.app_settings.update_one(
        {"key": "warehouse_stock_standalone"},
        {"$set": {"key": "warehouse_stock_standalone", "enabled": False}},
        upsert=True,
    )
    await database.users.insert_one({
        "id": "user-sj-op-admin",
        "email": ADMIN_EMAIL,
        "name": "Admin",
        "role": "admin",
        "status": "active",
        "password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
        "permissions_override": {},
    })
    await database.users.insert_one({
        "id": "user-sj-op-designer",
        "email": "sj-op-designer@test.local",
        "name": "Designer",
        "role": "designer",
        "status": "active",
        "password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
        "permissions_override": {
            "sales.delivery.delete": False,
            "sales.delivery.create": False,
            "po.edit": False,
            "loading.delete": False,
        },
    })
    yield database, {}
    await client.drop_database(TEST_DB)
    client.close()
    if _PREV_DB_NAME:
        os.environ["DB_NAME"] = _PREV_DB_NAME


def _admin():
    async def _u():
        return await server.db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    app.dependency_overrides[current_user] = _u


def _designer():
    async def _u():
        return await server.db.users.find_one({"email": "sj-op-designer@test.local"}, {"_id": 0})
    app.dependency_overrides[current_user] = _u


async def _seed_multi_sj(database, *, with_loading=True):
    po_id = f"po-op-{uuid.uuid4().hex[:8]}"
    sj_a = f"sj-a-{uuid.uuid4().hex[:8]}"
    sj_b = f"sj-b-{uuid.uuid4().hex[:8]}"
    line_id = "L0"
    order = {
        "id": po_id,
        "po_number": "PO/12/10/2026/000006",
        "customer": "CELCI COFFEE",
        "total_quantity": 5800,
        "line_items": [{"name": "Cup", "quantity": 5800, "line_id": line_id, "line_idx": 0}],
        "current_stage": 13,
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "delivery_confirmed",
        },
        "shipments": [
            {
                "id": sj_a,
                "number": "SJ/12/10/2026/000006",
                "sales_delivery_id": sj_a,
                "total_qty": 2900,
                "lines": [{"line_idx": 0, "line_id": line_id, "qty": 2900}],
            },
            {
                "id": sj_b,
                "number": "SJ/12/10/2026/000007",
                "sales_delivery_id": sj_b,
                "total_qty": 2900,
                "lines": [{"line_idx": 0, "line_id": line_id, "qty": 2900}],
            },
        ],
        "surat_jalan_files": [
            {
                "id": sj_a,
                "shipment_id": sj_a,
                "sj_number": "SJ/12/10/2026/000006",
                "source": "SYSTEM_GENERATED",
                "name": "a.pdf",
            },
            {
                "id": sj_b,
                "shipment_id": sj_b,
                "sj_number": "SJ/12/10/2026/000007",
                "source": "SYSTEM_GENERATED",
                "name": "b.pdf",
            },
        ],
        "loading_confirmations": [],
        "delivery_confirmations": [],
        "delivery_proofs": [],
    }
    if with_loading:
        order["loading_confirmations"] = [
            {
                "id": f"lc-{sj_a}",
                "surat_jalan_id": sj_a,
                "shipment_id": sj_a,
                "sj_number": "SJ/12/10/2026/000006",
                "lines": [{"line_idx": 0, "qty": 2900}],
            },
            {
                "id": f"lc-{sj_b}",
                "surat_jalan_id": sj_b,
                "shipment_id": sj_b,
                "sj_number": "SJ/12/10/2026/000007",
                "lines": [{"line_idx": 0, "qty": 2900}],
            },
        ]
    await database.orders.insert_one(order)
    for sj_id, number in ((sj_a, "SJ/12/10/2026/000006"), (sj_b, "SJ/12/10/2026/000007")):
        await database.sales_deliveries.insert_one({
            "id": sj_id,
            "po_id": po_id,
            "sj_number": number,
            "total_qty": 2900,
            "status": "SELESAI",
            "is_cancelled": False,
            "line_items": [{"line_idx": 0, "line_id": line_id, "qty": 2900}],
        })
    return {"po_id": po_id, "sj_a": sj_a, "sj_b": sj_b, "lc_a": f"lc-{sj_a}", "lc_b": f"lc-{sj_b}"}


async def test_assess_operational_only_when_loading_without_stock(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    # Fabrication STATUS_CETAK at PO level must not count as SJ stock mutation.
    await database.stock_movements.insert_one({
        "id": f"mov-{uuid.uuid4().hex[:8]}",
        "event_key": f"status_cetak:{seeded['po_id']}:0",
        "movement_type": "STATUS_CETAK",
        "reference_type": "operator_progress",
        "reference_id": seeded["po_id"],
        "status": "POSTED",
    })
    assessment = await assess_sales_delivery_cancel_stock(database, delivery)
    assert assessment["mode"] == "operational_only"


async def test_1_2_3_4_cancel_loading_stays_manual_hapus_multi_sj(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    ledger_before = await database[LEDGER_COLLECTION].count_documents({})
    pss_before = await database.stock_movements.count_documents({})
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["soft_cancelled"] is True
    assert body.get("stock_engine") == "operational_no_stock"
    assert body.get("stock_mode") == "operational_only"

    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]})
    assert delivery["is_cancelled"] is True
    assert delivery["status"] == "DIBATALKAN"

    order = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    by_ship = {sh["id"]: sh for sh in order["shipments"]}
    assert by_ship[seeded["sj_a"]].get("is_cancelled") is True
    assert by_ship[seeded["sj_b"]].get("is_cancelled") is not True
    loading = {c["id"]: c for c in order["loading_confirmations"]}
    assert loading[seeded["lc_a"]].get("cancelled") is not True
    assert loading[seeded["lc_b"]].get("cancelled") is not True
    assert qty_loaded_for_line(order, 0) == 5800
    assert validate_cancel_loading_confirmation(order, seeded["lc_a"]) is None
    assert validate_cancel_loading_confirmation(order, seeded["lc_b"]) == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL
    assert await database[LEDGER_COLLECTION].count_documents({}) == ledger_before
    assert await database.stock_movements.count_documents({}) == pss_before

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hapus = await client.delete(
            f"/api/orders/{seeded['po_id']}/loading-confirm/{seeded['lc_a']}",
        )
    assert hapus.status_code == 200, hapus.text
    order2 = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    lc_a = next(c for c in order2["loading_confirmations"] if c["id"] == seeded["lc_a"])
    assert lc_a.get("cancelled") is True
    assert lc_a.get("lines")  # history preserved
    assert qty_loaded_for_line(order2, 0) == 2900
    assert validate_cancel_loading_confirmation(order2, seeded["lc_b"]) == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL
    sj_b = await database.sales_deliveries.find_one({"id": seeded["sj_b"]})
    assert sj_b.get("is_cancelled") is not True


async def test_5_active_r2_mutation_not_bypassed(db, monkeypatch):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    conf_id = f"conf-{uuid.uuid4().hex[:8]}"
    await database[SALES_DELIVERY_CONFIRMATIONS].insert_one({
        "id": conf_id,
        "sales_delivery_id": seeded["sj_a"],
        "reversed": False,
        "lines": [{"line_idx": 0, "qty": 10}],
    })
    await database[LEDGER_COLLECTION].insert_one({
        "id": f"led-{uuid.uuid4().hex[:8]}",
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -10,
        "metadata": {
            "sales_delivery_id": seeded["sj_a"],
            "confirmation_id": conf_id,
            "order_id": seeded["po_id"],
        },
        "idempotency_key": f"seed-{conf_id}",
    })
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    assessment = await assess_sales_delivery_cancel_stock(database, delivery)
    assert assessment["mode"] == "requires_stock_reversal"

    # Force transaction unavailability — must NOT fall through to operational cancel.
    async def _no_txn(_client):
        raise PhysicalStockUnavailableError(
            "MongoDB transactions are unavailable. A replica set is required."
        )

    monkeypatch.setattr("services.physical_stock.require_transaction_support", _no_txn)
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 503
    still = await database.sales_deliveries.find_one({"id": seeded["sj_a"]})
    assert still.get("is_cancelled") is not True


async def test_6_ambiguous_po_confirmation_fail_closed(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    orphan = f"orphan-{uuid.uuid4().hex[:8]}"
    await database[SALES_DELIVERY_CONFIRMATIONS].insert_one({
        "id": orphan,
        "reversed": False,
        # no sales_delivery_id → cannot attribute to a specific SJ
        "lines": [{"line_idx": 0, "qty": 5}],
    })
    await database.orders.update_one(
        {"id": seeded["po_id"]},
        {"$set": {"r2_confirmed_delivery_ids": [orphan]}},
    )
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    assessment = await assess_sales_delivery_cancel_stock(database, delivery)
    assert assessment["mode"] == "ambiguous"

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 400
    assert "atribusikan" in res.text.lower() or "fail" in res.text.lower() or "ditolak" in res.text.lower()
    still = await database.sales_deliveries.find_one({"id": seeded["sj_a"]})
    assert still.get("is_cancelled") is not True


async def test_7_idempotent_replay_no_double_stock_and_heals_order(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    first = await cancel_sales_delivery_document_operational(
        database, delivery, actor_id="admin", reason="uji",
    )
    assert first["replayed"] is False
    # Simulate crash: SJ cancelled but shipment flags not yet written.
    await database.orders.update_one(
        {"id": seeded["po_id"]},
        {"$set": {
            "shipments.0.is_cancelled": False,
            "shipments.0.cancelled": False,
            "shipments.0.status": "SELESAI",
            "surat_jalan_files.0.cancelled": False,
            "surat_jalan_files.0.status": "active",
        }},
    )
    ledger_before = await database[LEDGER_COLLECTION].count_documents({})
    second = await cancel_sales_delivery_document_operational(
        database, delivery, actor_id="admin", reason="uji",
    )
    assert second["replayed"] is True
    order = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    assert order["shipments"][0].get("is_cancelled") is True
    assert order["surat_jalan_files"][0].get("cancelled") is True
    assert await database[LEDGER_COLLECTION].count_documents({}) == ledger_before

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        again = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert again.status_code == 200
    assert again.json().get("replayed") is True
    assert await database[LEDGER_COLLECTION].count_documents({}) == ledger_before


async def test_8_mid_cancel_failure_leaves_sj_cancelled_order_healable(db):
    """Simulate crash after SJ write, before order patch — replay must heal."""
    database, _ = db
    seeded = await _seed_multi_sj(database)
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    # Partial write: only sales_deliveries cancelled (order still active).
    await database.sales_deliveries.update_one(
        {"id": seeded["sj_a"]},
        {"$set": {
            "is_cancelled": True,
            "cancelled": True,
            "status": "DIBATALKAN",
            "cancelled_at": "2026-10-20T00:00:00+00:00",
            "cancel_reason": "uji partial",
            "r2_reversal": {
                "reversed_confirmations": [],
                "confirmation_ids": [],
                "engine": "operational_no_stock",
            },
        }},
    )
    order_before = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    assert order_before["shipments"][0].get("is_cancelled") is not True
    assert order_before["loading_confirmations"][0].get("cancelled") is not True

    healed = await cancel_sales_delivery_document_operational(
        database, delivery, actor_id="admin", reason="uji",
    )
    assert healed["replayed"] is True
    order = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    assert order["shipments"][0].get("is_cancelled") is True
    assert order["surat_jalan_files"][0].get("cancelled") is True
    assert order["loading_confirmations"][0].get("cancelled") is not True
    assert order["shipments"][1].get("is_cancelled") is not True


async def test_9_permission_denied(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    _designer()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 403
    still = await database.sales_deliveries.find_one({"id": seeded["sj_a"]})
    assert still.get("is_cancelled") is not True


async def test_10_unsupported_pss_sales_delivery_stock_fail_closed(db):
    database, _ = db
    seeded = await _seed_multi_sj(database)
    await database.stock_movements.insert_one({
        "id": f"pss-{uuid.uuid4().hex[:8]}",
        "event_key": f"stock_out:sales_delivery:{seeded['sj_a']}:L0",
        "movement_type": "PENGIRIMAN",
        "reference_type": "sales_delivery",
        "reference_id": seeded["sj_a"],
        "status": "POSTED",
    })
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_a"]}, {"_id": 0})
    assessment = await assess_sales_delivery_cancel_stock(database, delivery)
    assert assessment["mode"] == "unsupported_stock"
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 400
    still = await database.sales_deliveries.find_one({"id": seeded["sj_a"]})
    assert still.get("is_cancelled") is not True


async def test_operational_path_does_not_require_replica_set(db, monkeypatch):
    """CELCI-class: Loading blocker + no stock → cancel works even if txn unavailable."""
    database, _ = db
    seeded = await _seed_multi_sj(database)

    async def _no_txn(_client):
        raise PhysicalStockUnavailableError(
            "MongoDB transactions are unavailable. A replica set is required."
        )

    monkeypatch.setattr("services.physical_stock.require_transaction_support", _no_txn)
    # Guard: transactional cancel must not be invoked for operational_only.
    boom = AsyncMock(side_effect=AssertionError("cancel_sales_delivery_document must not run"))
    monkeypatch.setattr("services.physical_stock.cancel_sales_delivery_document", boom)

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_a']}")
    assert res.status_code == 200, res.text
    assert res.json().get("stock_engine") == "operational_no_stock"
    boom.assert_not_awaited()
