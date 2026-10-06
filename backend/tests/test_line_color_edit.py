"""Focused tests: edit orders.line_items[].color without qty or product changes."""
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


def _operator():
    return {
        "id": "u-operator-nocolor",
        "email": "operator-nocolor@eternalpackaging.com",
        "role": "operator",
        "name": "Operator",
        "permissions_override": {},
    }


def _line(**extra):
    base = {
        "name": "Cup 12 oz",
        "item_code": "CUP-12",
        "quantity": 1000,
        "unit": "pcs",
        "color": "HITAM",
        "production_process": "print",
        "notes": "SABLON WARNA HITAM",
        "unit_price": 1250,
        "line_id": "A",
    }
    base.update(extra)
    return base


async def _seed_order(**extra):
    oid = f"ord-color-{uuid.uuid4().hex[:8]}"
    po = f"PO-COLOR-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": oid,
        "po_number": po,
        "customer": "Fixture Customer",
        "current_stage": 0,
        "status_label": "Order Masuk",
        "notes": "SABLON WARNA HITAM",
        "sample_files": [],
        "sample_approved": False,
        "shipments": [],
        "operator_splits": [],
        "helper_prep": {"snapshot": "keep"},
        "loading_confirmations": [],
        "delivery_confirmations": [],
        "packing_info": {},
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "cancelled": False,
        "updated_at": "2026-10-06T00:00:00+00:00",
        "line_items": extra.pop("line_items", [_line(), _line(quantity=500, color="PUTIH", line_id="B")]),
        **extra,
    }
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


async def _cleanup(order):
    await db().orders.delete_many({"id": order["id"]})
    await db().sales_invoices.delete_many({"po_id": order["id"]})
    await db().sales_deliveries.delete_many({"po_id": order["id"]})
    await db().audit_logs.delete_many({"target_email": f"po:{order['po_number']}"})


def _as(user):
    app.dependency_overrides[current_user] = lambda: user


def _clear_user():
    app.dependency_overrides.pop(current_user, None)


async def _patch(order_id, line_idx, body):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.patch(f"/api/sales-orders/{order_id}/lines/{line_idx}/color", json=body)


@pytest.mark.anyio
async def test_valid_color_edit_changes_only_targeted_line():
    order = await _seed_order()
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["line_idx"] == 1
        assert body["before_color"] == "PUTIH"
        assert body["after_color"] == "MERAH"
        assert body["order"]["line_items"][0]["color"] == "HITAM"
        assert body["order"]["line_items"][1]["color"] == "MERAH"
        assert body["line"]["color"] == "MERAH"
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["quantity"] == 1000
        assert fresh["line_items"][1]["quantity"] == 500
        assert fresh["line_items"][0]["production_process"] == "print"
        assert fresh["line_items"][1]["production_process"] == "print"
        assert fresh["line_items"][0]["notes"] == "SABLON WARNA HITAM"
        assert fresh["line_items"][1]["notes"] == "SABLON WARNA HITAM"
        assert fresh["line_items"][1]["unit_price"] == 1250
        assert fresh["line_items"][1]["item_code"] == "CUP-12"
        assert fresh["line_items"][1]["name"] == "Cup 12 oz"
        assert fresh["notes"] == "SABLON WARNA HITAM"
        assert fresh["helper_prep"] == {"snapshot": "keep"}
        assert fresh["operator_splits"] == []
        assert fresh["shipments"] == []
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_trim_preserves_case_comma_and_slash():
    order = await _seed_order()
    _as(_admin())
    try:
        trimmed = await _patch(order["id"], 1, {"color": "  MERAH  ", "line_id": "B"})
        assert trimmed.status_code == 200, trimmed.text
        assert trimmed.json()["after_color"] == "MERAH"
        mixed = await _patch(order["id"], 1, {"color": "Merah / hitam / krem", "line_id": "B"})
        assert mixed.status_code == 200, mixed.text
        assert mixed.json()["after_color"] == "Merah / hitam / krem"
        comma = await _patch(order["id"], 0, {"color": "PUTIH, HIJAU, PINK", "line_id": "A"})
        assert comma.status_code == 200, comma.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
        assert fresh["line_items"][1]["color"] == "Merah / hitam / krem"
        assert fresh["line_items"][0]["quantity"] == 1000
        assert fresh["line_items"][1]["quantity"] == 500
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_empty_string_becomes_null_and_identical_value_is_noop():
    order = await _seed_order()
    _as(_admin())
    try:
        cleared = await _patch(order["id"], 1, {"color": "   ", "line_id": "B"})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["before_color"] == "PUTIH"
        assert cleared.json()["after_color"] is None
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] is None
        stamp = fresh["updated_at"]
        audits_before = await db().audit_logs.count_documents({
            "action": "sales_order.line_color.update",
            "target_email": f"po:{order['po_number']}",
        })
        noop = await _patch(order["id"], 1, {"color": "", "line_id": "B"})
        assert noop.status_code == 200, noop.text
        assert noop.json()["before_color"] is None
        assert noop.json()["after_color"] is None
        again = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert again["updated_at"] == stamp
        assert again["line_items"][0]["color"] == "HITAM"
        audits_after = await db().audit_logs.count_documents({
            "action": "sales_order.line_color.update",
            "target_email": f"po:{order['po_number']}",
        })
        assert audits_after == audits_before
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_duplicate_item_code_edits_the_addressed_row_only():
    order = await _seed_order()
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "2 Warna", "line_id": "B"})
        assert res.status_code == 200, res.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["item_code"] == fresh["line_items"][1]["item_code"]
        assert fresh["line_items"][0]["color"] == "HITAM"
        assert fresh["line_items"][1]["color"] == "2 Warna"
        assert fresh["line_items"][0]["line_id"] == "A"
        assert fresh["line_items"][1]["line_id"] == "B"
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_line_id_and_accurate_line_id_identity():
    order = await _seed_order(line_items=[
        _line(color="HITAM", line_id="A"),
        {
            "name": "Cup 12 oz",
            "item_code": "CUP-12",
            "quantity": 500,
            "unit": "pcs",
            "color": "PUTIH",
            "production_process": "print",
            "notes": "SABLON WARNA HITAM",
            "unit_price": 900,
            "accurate_line_id": "ACC-B",
        },
    ])
    _as(_admin())
    try:
        ok = await _patch(order["id"], 0, {"color": "NAVY", "line_id": "A"})
        assert ok.status_code == 200, ok.text
        acc = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "ACC-B"})
        assert acc.status_code == 200, acc.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][0]["color"] == "NAVY"
        assert fresh["line_items"][1]["color"] == "MERAH"
        wrong = await _patch(order["id"], 1, {"color": "BIRU", "line_id": "A"})
        assert wrong.status_code == 409
        assert "Identitas baris" in wrong.text
        stayed = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert stayed["line_items"][1]["color"] == "MERAH"
        assert stayed["line_items"][0]["color"] == "NAVY"
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_missing_line_and_missing_order():
    order = await _seed_order()
    _as(_admin())
    try:
        missing_line = await _patch(order["id"], 9, {"color": "MERAH", "line_id": "B"})
        assert missing_line.status_code == 400
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] == "PUTIH"
        missing_order = await _patch("ord-color-missing", 0, {"color": "MERAH", "line_id": "A"})
        assert missing_order.status_code == 404
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_without_po_edit_is_forbidden_and_cancelled_is_rejected():
    order = await _seed_order()
    _as(_operator())
    try:
        denied = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert denied.status_code == 403
    finally:
        _clear_user()
    await db().orders.update_one({"id": order["id"]}, {"$set": {"cancelled": True}})
    _as(_admin())
    try:
        cancelled = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert cancelled.status_code == 409
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] == "PUTIH"
        assert fresh["notes"] == "SABLON WARNA HITAM"
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_color_edit_allowed_after_sample_and_schedule_progress():
    order = await _seed_order(
        sample_files=[{"id": "sample-1", "name": "proof.pdf"}],
        sample_approved=True,
        operator_splits=[{"line_idx": 1, "qty_completed": 40, "operator_id": "op-1"}],
        helper_prep={"line_idx": 1, "color": "PUTIH", "ready": True},
        current_stage=8,
    )
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert res.status_code == 200, res.text
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] == "MERAH"
        assert fresh["line_items"][1]["quantity"] == 500
        assert fresh["sample_files"] == [{"id": "sample-1", "name": "proof.pdf"}]
        assert fresh["sample_approved"] is True
        assert fresh["operator_splits"] == [{"line_idx": 1, "qty_completed": 40, "operator_id": "op-1"}]
        assert fresh["helper_prep"] == {"line_idx": 1, "color": "PUTIH", "ready": True}
        assert fresh["current_stage"] == 8
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_invoice_and_delivery_snapshots_and_other_collections_stay():
    order = await _seed_order()
    invoice = {
        "id": f"inv-{order['id']}",
        "po_id": order["id"],
        "lines": [{"line_id": "B", "color": "PUTIH", "quantity": 500, "item_code": "CUP-12"}],
    }
    delivery = {
        "id": f"sj-{order['id']}",
        "po_id": order["id"],
        "line_items": [{"line_id": "B", "color": "PUTIH", "quantity": 500}],
    }
    await db().sales_invoices.delete_many({"id": invoice["id"]})
    await db().sales_deliveries.delete_many({"id": delivery["id"]})
    await db().sales_invoices.insert_one(dict(invoice))
    await db().sales_deliveries.insert_one(dict(delivery))
    inv_count = await db().sales_invoices.count_documents({"po_id": order["id"]})
    sj_count = await db().sales_deliveries.count_documents({"po_id": order["id"]})
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert res.status_code == 200, res.text
        stored_inv = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        stored_sj = await db().sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
        assert stored_inv["lines"][0]["color"] == "PUTIH"
        assert stored_sj["line_items"][0]["color"] == "PUTIH"
        assert await db().sales_invoices.count_documents({"po_id": order["id"]}) == inv_count
        assert await db().sales_deliveries.count_documents({"po_id": order["id"]}) == sj_count
        assert res.json()["order"]["line_items"][1]["color"] == "MERAH"
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_audit_records_before_after_without_whole_order():
    order = await _seed_order()
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert res.status_code == 200, res.text
        rows = await db().audit_logs.find(
            {"action": "sales_order.line_color.update", "target_email": f"po:{order['po_number']}"},
            {"_id": 0},
        ).to_list(10)
        assert len(rows) == 1
        doc = rows[0]
        assert doc["actor_email"] == "admin@eternalpackaging.com"
        assert doc["before"]["before_color"] == "PUTIH"
        assert doc["after"]["after_color"] == "MERAH"
        assert doc["before"]["line_idx"] == 1
        assert doc["before"]["line_id"] == "B"
        assert doc["before"]["item_code"] == "CUP-12"
        assert doc["before"]["order_id"] == order["id"]
        assert doc["before"]["po_number"] == order["po_number"]
        assert doc["after"]["actor"] == "admin@eternalpackaging.com"
        blob = str(doc["before"]) + str(doc["after"])
        assert "line_items" not in doc["before"]
        assert "line_items" not in doc["after"]
        assert "SABLON WARNA HITAM" not in blob
        assert "password" not in blob
        assert "customer" not in doc["before"]
        assert "helper_prep" not in doc["before"]
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_stale_updated_at_conflicts_without_writing_color(monkeypatch):
    order = await _seed_order()
    real = server_mod._load_order_for_mutation

    async def stale(order_id):
        loaded = await real(order_id)
        loaded["updated_at"] = "1999-01-01T00:00:00+00:00"
        return loaded

    monkeypatch.setattr(server_mod, "_load_order_for_mutation", stale)
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {"color": "MERAH", "line_id": "B"})
        assert res.status_code == 409
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] == "PUTIH"
        assert fresh["updated_at"] == "2026-10-06T00:00:00+00:00"
        count = await db().audit_logs.count_documents({
            "action": "sales_order.line_color.update",
            "target_email": f"po:{order['po_number']}",
        })
        assert count == 0
    finally:
        _clear_user()
        await _cleanup(order)


@pytest.mark.anyio
async def test_payload_rejects_quantity_and_product_fields():
    order = await _seed_order()
    _as(_admin())
    try:
        res = await _patch(order["id"], 1, {
            "color": "MERAH",
            "line_id": "B",
            "quantity": 1,
            "master_item_id": "m-1",
            "notes": "changed",
            "production_process": "non_print",
            "unit_price": 1,
        })
        assert res.status_code == 422
        fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert fresh["line_items"][1]["color"] == "PUTIH"
        assert fresh["line_items"][1]["quantity"] == 500
        assert fresh["line_items"][1]["notes"] == "SABLON WARNA HITAM"
    finally:
        _clear_user()
        await _cleanup(order)
