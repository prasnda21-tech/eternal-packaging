"""Urgent PO approval workflow — Phase A functional tests (isolated DB)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_urgent_po_phase_a_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import (
    DIRECTOR_MUST_OPT_IN_PERMISSIONS,
    OPT_IN_ONLY_PERMISSIONS,
    ROLE_DEFAULT_PERMISSIONS,
    director_full_permission_set,
    effective_permissions,
)  # DIRECTOR_MUST_OPT_IN used in catalog assertions
from server import app, current_user, user_permissions
from services import urgent_requests as urg


PREFIX = "TEST-URG-A"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    await urg.ensure_indexes(database)
    yield database
    app.dependency_overrides.pop(current_user, None)
    await client.drop_database(database.name)
    client.close()


def _user(role, **extra):
    return {
        "id": extra.get("id", str(uuid.uuid4())),
        "email": extra.get("email", f"{role}-{uuid.uuid4().hex[:6]}@test.local"),
        "name": extra.get("name", role),
        "role": role,
        "permissions_override": extra.get("permissions_override") or {},
    }


async def _seed_order(db, *, priority="normal", cancelled=False):
    now = datetime.now(timezone.utc).isoformat()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": f"{PREFIX}-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Urgent Phase A",
        "current_stage": 2,
        "priority": priority,
        "urgency_approved_by": "legacy@eternal.local" if priority == "urgent" else None,
        "cancelled": cancelled,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "line_items": [],
    }
    await db.orders.insert_one(dict(doc))
    return doc


def test_permissions_catalog_and_defaults():
    for key in (
        "urgent.request",
        "urgent.review",
        "urgent.approve",
        "urgent.notify",
        "urgent.email_notify",
    ):
        assert key in OPT_IN_ONLY_PERMISSIONS
        for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
            assert key not in perms, role
    assert "urgent.approve" in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    assert "urgent.email_notify" in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    full = director_full_permission_set()
    for key in (
        "urgent.request",
        "urgent.review",
        "urgent.approve",
        "urgent.notify",
        "urgent.email_notify",
    ):
        assert key in DIRECTOR_MUST_OPT_IN_PERMISSIONS
        assert key not in full
    assert "urgent.manage" not in OPT_IN_ONLY_PERMISSIONS


@pytest.mark.anyio
async def test_director_and_sa_need_explicit_approve(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    dir_user = _user("direktur_utama", email="dir@test.local", id="dir-1")
    await server.db.users.insert_one({**dir_user, "status": "active", "is_super_admin": False})
    perms = await user_permissions(dir_user)
    assert "urgent.request" not in perms
    assert "urgent.review" not in perms
    assert "urgent.approve" not in perms
    assert "urgent.notify" not in perms
    assert "urgent.email_notify" not in perms

    dir_granted = _user(
        "direktur_utama",
        email="dir2@test.local",
        id="dir-2",
        permissions_override={"urgent.approve": True},
    )
    await server.db.users.insert_one({**dir_granted, "status": "active"})
    perms2 = await user_permissions(dir_granted)
    assert "urgent.approve" in perms2

    sa = _user("admin", email="sa@test.local", id="sa-1")
    await server.db.users.insert_one({**sa, "status": "active", "is_super_admin": True})
    # SA path strips OPT_IN unless override
    perms_sa = await user_permissions(sa)
    assert "urgent.approve" not in perms_sa


@pytest.mark.anyio
async def test_legacy_endpoint_blocked(bind_db):
    order = await _seed_order(bind_db)
    user = _user("admin", permissions_override={"urgent.manage": True, "urgent.request": True})
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/orders/{order['id']}/urgent", json={"urgent": True})
        assert r.status_code == 410, r.text
    after = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert after.get("priority") == "normal"


@pytest.mark.anyio
async def test_request_approve_reject_and_permissions(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", id="req-1", permissions_override={"urgent.request": True})
    await bind_db.users.insert_one({**requester, "status": "active"})
    approver = _user(
        "crm",
        id="apr-1",
        email="apr@test.local",
        permissions_override={"urgent.approve": True, "urgent.notify": True},
    )
    await bind_db.users.insert_one({**approver, "status": "active"})
    stranger = _user("crm", id="str-1", permissions_override={})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No permission
        app.dependency_overrides[current_user] = lambda: stranger
        r = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={"reason": "Butuh cepat", "target_completion_date": "2026-10-28"},
        )
        assert r.status_code == 403

        # Submit
        app.dependency_overrides[current_user] = lambda: requester
        r = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={
                "reason": "Customer outlet baru",
                "target_completion_date": "2026-10-28",
                "notes": "opsional",
                "idempotency_key": "idem-1",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["request"]["status"] == "pending"
        assert body["order"]["priority"] == "normal"
        rid = body["request"]["id"]

        # Duplicate pending
        r2 = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={"reason": "lagi", "target_completion_date": "2026-10-29"},
        )
        assert r2.status_code == 409

        # Idempotent retry
        r3 = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={
                "reason": "Customer outlet baru",
                "target_completion_date": "2026-10-28",
                "idempotency_key": "idem-1",
            },
        )
        assert r3.status_code == 200
        assert r3.json()["request"]["id"] == rid

        # Stranger cannot read
        app.dependency_overrides[current_user] = lambda: stranger
        r = await client.get(f"/api/urgent-requests/{rid}")
        assert r.status_code == 403

        # Owner can read
        app.dependency_overrides[current_user] = lambda: requester
        r = await client.get(f"/api/urgent-requests/{rid}")
        assert r.status_code == 200

        # Reject without reason
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "rejected", "decision_notes": ""},
        )
        assert r.status_code == 400

        # Approve
        r = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["request"]["status"] == "approved"
        assert r.json()["order"]["priority"] == "urgent"
        assert r.json()["order"]["urgency_approved_by"] == approver["email"]

        # Double decide
        r = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "rejected", "decision_notes": "no"},
        )
        assert r.status_code == 409


@pytest.mark.anyio
async def test_reject_keeps_normal_and_legacy_untouched(bind_db):
    order = await _seed_order(bind_db)
    legacy = await _seed_order(bind_db, priority="urgent")
    legacy_before = await bind_db.orders.find_one({"id": legacy["id"]}, {"_id": 0})

    requester = _user("crm", permissions_override={"urgent.request": True})
    approver = _user("crm", email="a2@test.local", permissions_override={"urgent.approve": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: requester
        r = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={"reason": "Butuh", "target_completion_date": "2026-11-01"},
        )
        rid = r.json()["request"]["id"]
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "rejected", "decision_notes": "Penuh kapasitas"},
        )
        assert r.status_code == 200
        assert r.json()["order"]["priority"] == "normal"
        assert r.json()["request"]["decision_notes"] == "Penuh kapasitas"

    legacy_after = await bind_db.orders.find_one({"id": legacy["id"]}, {"_id": 0})
    assert legacy_after["priority"] == "urgent"
    assert legacy_after.get("urgency_approved_by") == legacy_before.get("urgency_approved_by")
    assert await bind_db[urg.COLLECTION].count_documents({"order_id": legacy["id"]}) == 0


@pytest.mark.anyio
async def test_cancelled_po_and_cancel_pending(bind_db):
    cancelled = await _seed_order(bind_db, cancelled=True)
    order = await _seed_order(bind_db)
    requester = _user("crm", id="c1", permissions_override={"urgent.request": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: requester
        r = await client.post(
            f"/api/orders/{cancelled['id']}/urgent-requests",
            json={"reason": "x", "target_completion_date": "2026-11-01"},
        )
        assert r.status_code == 400

        r = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={"reason": "x", "target_completion_date": "2026-11-01"},
        )
        rid = r.json()["request"]["id"]
        r = await client.post(f"/api/urgent-requests/{rid}/cancel")
        assert r.status_code == 200
        assert r.json()["request"]["status"] == "cancelled"


@pytest.mark.anyio
async def test_concurrent_approvers_one_wins(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    a1 = _user("crm", email="a1@t.local", permissions_override={"urgent.approve": True})
    a2 = _user("crm", email="a2@t.local", permissions_override={"urgent.approve": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: requester
        r = await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            json={"reason": "race", "target_completion_date": "2026-11-02"},
        )
        rid = r.json()["request"]["id"]

        app.dependency_overrides[current_user] = lambda: a1
        r1 = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "approved", "decision_notes": "a1"},
        )
        app.dependency_overrides[current_user] = lambda: a2
        r2 = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "approved", "decision_notes": "a2"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 409
        final = await bind_db[urg.COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert final["status"] == "approved"
        assert final["decided_by_email"] == a1["email"]


@pytest.mark.anyio
async def test_email_notify_does_not_imply_approve():
    eff = effective_permissions("crm", {"urgent.email_notify": True})
    assert "urgent.email_notify" in eff
    assert "urgent.approve" not in eff


def test_old_endpoint_source_blocked():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index('@api_router.post("/orders/{order_id}/urgent")')
    end = src.index("class BufferStockPayload")
    chunk = src[start:end]
    assert "410" in chunk
    assert "urgent-requests" in chunk
    assert 'order["priority"] = "urgent"' not in chunk
