"""HTTP + sync: one-PO legacy line_id repair (ELEMENT Accurate import shape)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402

ELEMENT_MID = "9bfd5938-6868-4d1b-bf9b-6212225134a2"
ELEMENT_ACC_LINE = "103136"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]


def db():
    return server_mod.db


def _admin():
    return {
        "id": "u-admin",
        "email": "admin@eternalpackaging.com",
        "role": "admin",
        "name": "Admin",
    }


def _as(user):
    app.dependency_overrides[current_user] = lambda: user


def _clear_user():
    app.dependency_overrides.pop(current_user, None)


async def _seed_legacy_order(**extra):
    oid = f"ord-legacy-{uuid.uuid4().hex[:8]}"
    po = f"PO-LEGACY-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": oid,
        "po_number": po,
        "customer": "ELEMENT Fixture",
        "outlet": "O",
        "recipient": "R",
        "address": "A",
        "payment_terms": "NET",
        "sales": "S",
        "created_by_user": "x",
        "expedition": "",
        "delivery_zone": "",
        "seller": "S",
        "order_date": "2026-10-14",
        "estimated_delivery": "2026-10-17",
        "current_stage": 11,
        "status_label": "Packing",
        "cancelled": False,
        "updated_at": "2026-10-14T00:00:00+00:00",
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "operator_splits": [
            {"line_idx": 0, "quantity": 10000, "qty_completed": 10000},
        ],
        "helper_prep": {
            "qty_prepared": 10000,
            "items": [{"line_idx": 0, "qty_prepared": 10000}],
        },
        "packing_info": {},
        "line_items": [{
            "name": "Add On Warna Sablon HITAM",
            "quantity": 10000,
            "unit": "pcs",
            "production_process": "print",
            "master_item_id": ELEMENT_MID,
            "item_type": "SERVICE",
            "accurate_line_id": ELEMENT_ACC_LINE,
            "accurate_item_id": "acc-item-hitam",
        }],
        **extra,
    }
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


async def _cleanup(order):
    await db().orders.delete_many({"id": order["id"]})
    await db().audit_logs.delete_many({"target_email": f"po:{order['po_number']}"})


async def _post_repair(order_id, body):
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        return await client.post(
            f"/api/orders/{order_id}/line-items/legacy-line-identity-repair",
            json=body,
        )


@pytest.mark.anyio
async def test_http_requires_explicit_confirm():
    order = await _seed_legacy_order()
    _as(_admin())
    try:
        res = await _post_repair(order["id"], {
            "accurate_line_id": ELEMENT_ACC_LINE,
            "master_item_id": ELEMENT_MID,
            "physical_fulfillment": True,
            "confirm": False,
        })
        assert res.status_code == 400
        assert "confirm" in res.text.lower() or "Konfirmasi" in res.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert not fresh["line_items"][0].get("line_id")
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_http_repair_mints_audits_and_idempotent():
    order = await _seed_legacy_order()
    _as(_admin())
    try:
        res = await _post_repair(order["id"], {
            "accurate_line_id": ELEMENT_ACC_LINE,
            "master_item_id": ELEMENT_MID,
            "physical_fulfillment": True,
            "confirm": True,
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["changed"] is True
        lid = body["after"]["line_id"]
        assert lid.startswith("ln-")
        assert lid != ELEMENT_ACC_LINE
        assert body["after"]["physical_fulfillment"] is True
        assert body["before"]["line_id"] is None
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["line_id"] == lid
        assert fresh["line_items"][0]["physical_fulfillment"] is True
        assert fresh["line_items"][0]["quantity"] == 10000
        assert fresh["line_items"][0]["item_type"] == "SERVICE"
        assert fresh["line_items"][0]["accurate_line_id"] == ELEMENT_ACC_LINE
        assert fresh["operator_splits"][0]["qty_completed"] == 10000
        count = await db().audit_logs.count_documents({
            "action": "production.line.legacy_identity.repair",
            "target_email": f"po:{order['po_number']}",
        })
        assert count == 1

        again = await _post_repair(order["id"], {
            "accurate_line_id": ELEMENT_ACC_LINE,
            "master_item_id": ELEMENT_MID,
            "physical_fulfillment": True,
            "confirm": True,
        })
        assert again.status_code == 200, again.text
        assert again.json()["changed"] is False
        assert again.json()["after"]["line_id"] == lid
        count2 = await db().audit_logs.count_documents({
            "action": "production.line.legacy_identity.repair",
            "target_email": f"po:{order['po_number']}",
        })
        assert count2 == 1  # no extra audit on no-op
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_http_stale_updated_at_conflicts(monkeypatch):
    order = await _seed_legacy_order()
    real = server_mod._load_order_for_mutation

    async def stale(order_id):
        loaded = await real(order_id)
        loaded["updated_at"] = "1999-01-01T00:00:00+00:00"
        return loaded

    monkeypatch.setattr(server_mod, "_load_order_for_mutation", stale)
    _as(_admin())
    try:
        res = await _post_repair(order["id"], {
            "accurate_line_id": ELEMENT_ACC_LINE,
            "master_item_id": ELEMENT_MID,
            "confirm": True,
        })
        assert res.status_code == 409
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert not fresh["line_items"][0].get("line_id")
        assert fresh["updated_at"] == "2026-10-14T00:00:00+00:00"
        count = await db().audit_logs.count_documents({
            "action": "production.line.legacy_identity.repair",
            "target_email": f"po:{order['po_number']}",
        })
        assert count == 0
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_accurate_sync_preserves_repaired_line(monkeypatch):
    from services.accurate import sync_sales_orders

    order = await _seed_legacy_order()
    # Simulate prior repair
    await db().orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "line_items.0.line_id": "ln-synced-keep",
            "line_items.0.physical_fulfillment": True,
            "updated_at": "2026-10-14T01:00:00+00:00",
        }},
    )

    class FakeClient:
        is_live = False

        async def fetch_sales_orders(self):
            return [{
                "po_number": order["po_number"],
                "customer": "ELEMENT Fixture",
                "outlet": "O",
                "recipient": "R",
                "address": "A",
                "payment_terms": "NET",
                "sales": "S",
                "created_by_user": "x",
                "expedition": "",
                "delivery_zone": "",
                "seller": "S",
                "line_items": [{
                    "name": "Add On Warna Sablon HITAM",
                    "quantity": 10000,
                    "accurate_line_id": ELEMENT_ACC_LINE,
                    "accurate_item_id": "acc-item-hitam",
                    "master_item_id": ELEMENT_MID,
                    "item_type": "SERVICE",
                }],
            }]

    async def passthrough_lines(db_arg, client, lines, require_master=True):
        return list(lines)

    async def noop_stock(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "services.master_items.resolve_po_line_items", passthrough_lines,
    )
    monkeypatch.setattr(
        "services.stock_engine_v2.validate_sales_order_availability", noop_stock,
    )
    monkeypatch.setattr(
        "services.physical_stock_engine.attach_order_stock_snapshot", noop_stock,
    )

    _as(_admin())
    try:
        result = await sync_sales_orders(db(), FakeClient())
        assert result["updated"] >= 1
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["line_id"] == "ln-synced-keep"
        assert fresh["line_items"][0]["physical_fulfillment"] is True
        assert fresh["line_items"][0]["accurate_line_id"] == ELEMENT_ACC_LINE
        assert fresh["line_items"][0]["quantity"] == 10000
        # Sibling-safe: production_process preserved from existing
        assert fresh["line_items"][0].get("production_process") == "print"
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_rbac_denies_operator_without_perm():
    order = await _seed_legacy_order()
    _as({
        "id": "u-op",
        "email": "operator@eternalpackaging.com",
        "role": "operator",
        "name": "Op",
        "permissions_override": {},
    })
    try:
        res = await _post_repair(order["id"], {
            "accurate_line_id": ELEMENT_ACC_LINE,
            "master_item_id": ELEMENT_MID,
            "confirm": True,
        })
        assert res.status_code in (403, 401)
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert not fresh["line_items"][0].get("line_id")
    finally:
        _clear_user()
        await _cleanup(order)
