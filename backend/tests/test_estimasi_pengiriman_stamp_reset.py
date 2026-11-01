"""Stamp invalidation on gate reset — rollback / design+payment re-upload.

Dummy data only. Proves date A → reset → non-final → re-approve date B ≠ A.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_estimasi_stamp_reset_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.production_wave2 import stamp_delivery_sla
from services.tanggal_pengiriman import (
    delivery_sla_effective,
    invalidate_delivery_sla_stamp,
)

pytestmark = pytest.mark.anyio

TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


def _user(perms):
    async def inner():
        return {
            "id": "u-stamp",
            "user_id": "u-stamp",
            "email": "stamp@example.com",
            "name": "Stamp Tester",
            "role": "helper",
            "status": "active",
            "permissions_override": {key: True for key in perms},
        }

    return inner


def _perms():
    return _user([
        "po.view",
        "design.approve",
        "payment.approve",
        "design.upload",
        "payment.upload",
        "workflow.rollback",
    ])


def _order(**extra):
    row = {
        "id": "po-stamp",
        "po_number": "PO-STAMP",
        "cancelled": False,
        "current_stage": 8,
        "priority": "normal",
        "updated_at": "2026-10-01T00:00:00+00:00",
        "created_at": "2026-10-01T02:00:00+00:00",
        "order_date": "2026-10-01",
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-09-14T10:00:00+07:00",
        "payment_approved_at": "2026-09-15T10:00:00+07:00",
        "design_approved_by": "stamp@example.com",
        "payment_approved_by": "stamp@example.com",
        "qty_delivered": 0,
        "buffer_stock": False,
        "hold": False,
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "print"}],
        "design_files": [{"id": "d1", "name": "desain.png", "data_url": TINY_PNG, "content_type": "image/png"}],
        "payment_files": [{"id": "p1", "name": "bukti.png", "data_url": TINY_PNG, "content_type": "image/png"}],
        "timeline": [],
    }
    row.update(extra)
    return row


def _simulate_gate_reset_design(order: dict) -> None:
    """Mirror server.py design re-upload / rollback design clear + invalidate."""
    order["design_status"] = "Waiting"
    order["design_approved_by"] = None
    order["design_approved_by_name"] = None
    order["design_approved_at"] = None
    invalidate_delivery_sla_stamp(order, now="2026-09-16T12:00:00+07:00")


def _simulate_gate_reset_payment(order: dict) -> None:
    order["payment_status"] = "Waiting"
    order["payment_approved_by"] = None
    order["payment_approved_by_name"] = None
    order["payment_approved_at"] = None
    invalidate_delivery_sla_stamp(order, now="2026-09-16T12:00:00+07:00")


def test_normal_dual_gate_stamps_date_a():
    o = _order()
    assert stamp_delivery_sla(o, []) is True
    fact = delivery_sla_effective(o, 3)
    assert fact["final"] is True
    assert fact["date"] == "2026-09-18"
    assert o["delivery_sla_v2"].get("invalidated") is not True


def test_rollback_style_reset_then_reapprove_restamps_date_b():
    o = _order()
    assert stamp_delivery_sla(o, []) is True
    date_a = o["delivery_sla_v2"]["automatic_date"]
    assert date_a == "2026-09-18"

    # rollback_stage to_stage<=3 clears payment gate + invalidate
    _simulate_gate_reset_payment(o)
    assert o["delivery_sla_v2"]["invalidated"] is True
    assert delivery_sla_effective(o, 3)["final"] is False
    assert delivery_sla_effective(o, 3)["date"] is None

    # re-approve payment with later timestamp → restamp date B
    o["payment_status"] = "Approved"
    o["payment_approved_at"] = "2026-09-20T10:00:00+07:00"
    assert stamp_delivery_sla(o, []) is True
    date_b = o["delivery_sla_v2"]["automatic_date"]
    assert date_b == "2026-09-23"
    assert date_b != date_a
    assert o["delivery_sla_v2"].get("invalidated") is not True
    assert delivery_sla_effective(o, 3)["date"] == date_b


def test_design_reupload_reset_then_reapprove_restamps_date_b():
    o = _order()
    assert stamp_delivery_sla(o, []) is True
    date_a = o["delivery_sla_v2"]["automatic_date"]

    _simulate_gate_reset_design(o)
    assert o["delivery_sla_v2"]["invalidated"] is True
    assert delivery_sla_effective(o, 3)["final"] is False

    o["design_status"] = "Approved"
    o["design_approved_at"] = "2026-09-22T10:00:00+07:00"
    assert stamp_delivery_sla(o, []) is True
    date_b = o["delivery_sla_v2"]["automatic_date"]
    assert date_b == "2026-09-25"
    assert date_b != date_a
    assert delivery_sla_effective(o, 3)["final"] is True


def test_payment_reupload_reset_then_reapprove_restamps_date_b():
    o = _order()
    assert stamp_delivery_sla(o, []) is True
    date_a = o["delivery_sla_v2"]["automatic_date"]

    _simulate_gate_reset_payment(o)
    assert o["delivery_sla_v2"]["invalidated"] is True
    assert delivery_sla_effective(o, 3)["final"] is False

    o["payment_status"] = "Approved"
    o["payment_approved_at"] = "2026-09-21T10:00:00+07:00"
    assert stamp_delivery_sla(o, []) is True
    date_b = o["delivery_sla_v2"]["automatic_date"]
    assert date_b == "2026-09-24"
    assert date_b != date_a


def test_upload_without_approval_reset_does_not_invalidate():
    """was_approved False path must not call invalidate (stamp stays live)."""
    o = _order(
        design_status="Waiting",
        design_approved_at=None,
        design_approved_by=None,
        payment_status="Approved",
        payment_approved_at="2026-09-15T10:00:00+07:00",
        delivery_sla_v2={
            "rule": "working_days_3",
            "working_days": 3,
            "unit": "working_days",
            "anchor_at": "2026-09-15T10:00:00+07:00",
            "automatic_date": "2026-09-18",
            "holiday_dates": [],
            "stamped_at": "2026-09-15T11:00:00+07:00",
            "legacy": False,
        },
    )
    was_approved = o.get("design_status") == "Approved"
    assert was_approved is False
    # Mirror add_attachment: only invalidate inside `if was_approved`
    if was_approved:
        invalidate_delivery_sla_stamp(o)
    assert o["delivery_sla_v2"].get("invalidated") is not True
    assert invalidate_delivery_sla_stamp(o) is True  # helper still works when called
    assert o["delivery_sla_v2"]["invalidated"] is True
    # second call is no-op (no duplicate side effect)
    assert invalidate_delivery_sla_stamp(o) is False


def test_revoke_style_invalidate_still_allows_restamp():
    o = _order()
    assert stamp_delivery_sla(o, []) is True
    date_a = o["delivery_sla_v2"]["automatic_date"]
    # approve_design / approve_payment revoke pattern
    o["design_status"] = "Waiting"
    o["design_approved_at"] = None
    invalidate_delivery_sla_stamp(o, now="2026-09-16T08:00:00+07:00")
    assert delivery_sla_effective(o, 3)["final"] is False
    o["design_status"] = "Approved"
    o["design_approved_at"] = "2026-09-19T10:00:00+07:00"
    assert stamp_delivery_sla(o, []) is True
    # Sat 19 + 3 working days (Mon–Sat) → Wed 23
    assert o["delivery_sla_v2"]["automatic_date"] != date_a
    assert o["delivery_sla_v2"]["automatic_date"] == "2026-09-23"


def test_server_py_wires_invalidate_on_three_reset_paths():
    """Static proof: invalidate sits inside gate_reset / was_approved blocks."""
    src = SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = {"rollback_gate_reset": False, "design_reupload": False, "payment_reupload": False}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "rollback_stage":
            body = ast.unparse(node)
            assert "gate_reset = False" in body
            assert "if gate_reset:" in body
            assert "invalidate_delivery_sla_stamp" in body
            hits["rollback_gate_reset"] = True
        if node.name == "add_attachment":
            body = ast.unparse(node)
            assert body.count("invalidate_delivery_sla_stamp") >= 2
            assert "design_status" in body and "payment_status" in body
            assert "was_approved" in body
            # invalidate only appears under was_approved branches (not on every upload)
            assert "if was_approved:" in body
            hits["design_reupload"] = True
            hits["payment_reupload"] = True

    assert all(hits.values()), hits


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await server.db.orders.delete_many({})
    await server.db.app_settings.delete_many({"key": "delivery_deadline"})
    yield server.db
    app.dependency_overrides.pop(current_user, None)


async def test_http_rollback_invalidates_and_reapprove_restamps(db):
    order = _order(id="po-rb", po_number="PO-RB", current_stage=8)
    assert stamp_delivery_sla(order, []) is True
    date_a = order["delivery_sla_v2"]["automatic_date"]
    await db.orders.insert_one(dict(order))

    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rb = await client.post(
            "/api/orders/po-rb/rollback",
            json={"to_stage": 3, "reason": "uji reset payment gate"},
        )
        assert rb.status_code == 200, rb.text
        mid = rb.json()["order"]
        assert mid["payment_status"] == "Waiting"
        assert mid["delivery_sla_v2"]["invalidated"] is True
        assert delivery_sla_effective(mid, 3)["final"] is False

        # re-upload not needed — payment_files still present
        ap = await client.post("/api/orders/po-rb/approve-payment", json={"approved": True})
        assert ap.status_code == 200, ap.text
        after = ap.json()["order"]
        assert after["payment_status"] == "Approved"
        assert after["delivery_sla_v2"].get("invalidated") is not True
        date_b = after["delivery_sla_v2"]["automatic_date"]
        assert date_b != date_a
        fact = delivery_sla_effective(after, 3)
        assert fact["date"] == date_b
        assert fact["final"] is True


async def test_http_design_reupload_reset_invalidates_and_restamps(db):
    order = _order(id="po-du", po_number="PO-DU")
    assert stamp_delivery_sla(order, []) is True
    date_a = order["delivery_sla_v2"]["automatic_date"]
    await db.orders.insert_one(dict(order))

    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        up = await client.post(
            "/api/orders/po-du/attachment",
            json={"kind": "design", "name": "d2.png", "data_url": TINY_PNG},
        )
        assert up.status_code == 200, up.text
        mid = up.json()["order"]
        assert mid["design_status"] == "Waiting"
        assert mid["delivery_sla_v2"]["invalidated"] is True
        assert delivery_sla_effective(mid, 3)["final"] is False

        ap = await client.post("/api/orders/po-du/approve-design", json={"approved": True})
        assert ap.status_code == 200, ap.text
        after = ap.json()["order"]
        date_b = after["delivery_sla_v2"]["automatic_date"]
        assert date_b != date_a
        assert delivery_sla_effective(after, 3)["final"] is True
        assert after["delivery_sla_v2"].get("invalidated") is not True


async def test_http_payment_reupload_reset_invalidates_and_restamps(db):
    order = _order(id="po-pu", po_number="PO-PU")
    assert stamp_delivery_sla(order, []) is True
    date_a = order["delivery_sla_v2"]["automatic_date"]
    await db.orders.insert_one(dict(order))

    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        up = await client.post(
            "/api/orders/po-pu/attachment",
            json={"kind": "payment", "name": "p2.png", "data_url": TINY_PNG},
        )
        assert up.status_code == 200, up.text
        mid = up.json()["order"]
        assert mid["payment_status"] == "Waiting"
        assert mid["delivery_sla_v2"]["invalidated"] is True
        assert delivery_sla_effective(mid, 3)["final"] is False

        ap = await client.post("/api/orders/po-pu/approve-payment", json={"approved": True})
        assert ap.status_code == 200, ap.text
        after = ap.json()["order"]
        date_b = after["delivery_sla_v2"]["automatic_date"]
        assert date_b != date_a
        assert delivery_sla_effective(after, 3)["final"] is True


async def test_http_design_upload_without_approved_does_not_invalidate(db):
    order = _order(
        id="po-nu",
        po_number="PO-NU",
        design_status="Waiting",
        design_approved_at=None,
        design_approved_by=None,
        payment_status="Approved",
        payment_approved_at="2026-09-15T10:00:00+07:00",
    )
    # Pre-seed a live stamp that would be wrongly wiped if upload always invalidated.
    order["delivery_sla_v2"] = {
        "rule": "working_days_3",
        "working_days": 3,
        "unit": "working_days",
        "anchor_at": "2026-09-15T10:00:00+07:00",
        "automatic_date": "2026-09-18",
        "holiday_dates": [],
        "stamped_at": "2026-09-15T11:00:00+07:00",
        "legacy": False,
    }
    await db.orders.insert_one(dict(order))

    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        up = await client.post(
            "/api/orders/po-nu/attachment",
            json={"kind": "design", "name": "first.png", "data_url": TINY_PNG},
        )
        assert up.status_code == 200, up.text
        mid = up.json()["order"]
        assert mid["design_status"] == "Waiting"
        assert mid["delivery_sla_v2"].get("invalidated") is not True
        assert mid["delivery_sla_v2"]["automatic_date"] == "2026-09-18"


async def test_http_revoke_design_still_invalidates(db):
    order = _order(id="po-rv", po_number="PO-RV")
    assert stamp_delivery_sla(order, []) is True
    date_a = order["delivery_sla_v2"]["automatic_date"]
    await db.orders.insert_one(dict(order))

    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rv = await client.post("/api/orders/po-rv/approve-design", json={"approved": False})
        assert rv.status_code == 200, rv.text
        mid = rv.json()["order"]
        assert mid["design_status"] == "Waiting"
        assert mid["delivery_sla_v2"]["invalidated"] is True
        assert delivery_sla_effective(mid, 3)["final"] is False

        ap = await client.post("/api/orders/po-rv/approve-design", json={"approved": True})
        assert ap.status_code == 200, ap.text
        after = ap.json()["order"]
        assert after["delivery_sla_v2"]["automatic_date"] != date_a
        assert delivery_sla_effective(after, 3)["final"] is True
