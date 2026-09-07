"""Urgent toggle follows urgent.manage — not a hardcoded role whitelist."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions


PREFIX = "TEST-URGENT-RBAC"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_motor_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await server.db.orders.delete_many({"po_number": {"$regex": f"^{PREFIX}"}})
    yield
    await server.db.orders.delete_many({"po_number": {"$regex": f"^{PREFIX}"}})


def _user(role, **extra):
    return {
        "email": extra.get("email", f"{role}-urgent@test.local"),
        "name": extra.get("name", role),
        "role": role,
        "permissions_override": extra.get("permissions_override") or {},
    }


async def _seed_order():
    now = datetime.now(timezone.utc).isoformat()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": f"{PREFIX}-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Urgent RBAC",
        "current_stage": 2,
        "priority": "normal",
        "cancelled": False,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "line_items": [],
    }
    await server.db.orders.insert_one(dict(doc))
    return oid


def test_default_role_matrix_urgent_manage():
    defaults = ROLE_DEFAULT_PERMISSIONS
    assert "urgent.manage" in defaults["admin"]
    assert "urgent.manage" in defaults["head_production"]
    assert "urgent.manage" in defaults["head_operations"]
    assert "urgent.manage" not in defaults["crm"]
    assert "urgent.manage" not in defaults["operator"]
    assert "urgent.manage" not in defaults["designer"]
    assert "urgent.manage" not in defaults["screener"]
    assert "urgent.manage" not in defaults["helper"]
    assert "urgent.manage" not in defaults["driver"]
    crm_granted = effective_permissions("crm", {"urgent.manage": True})
    assert "urgent.manage" in crm_granted


def test_endpoint_uses_permission_not_role_whitelist():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index('@api_router.post("/orders/{order_id}/urgent")')
    end = src.index("class BufferStockPayload")
    chunk = src[start:end]
    assert 'await _require_perm(user, "urgent.manage")' in chunk
    assert 'require("admin", "head_production")' not in chunk


@pytest.mark.anyio
async def test_authorized_roles_can_set_urgent_and_operator_cannot(bind_motor_and_clean):
    oid = await _seed_order()
    cases = [
        (_user("admin"), 200),
        (_user("head_production"), 200),
        (_user("head_operations"), 200),
        (_user("crm", permissions_override={"urgent.manage": True}), 200),
        (_user("crm"), 403),
        (_user("operator"), 403),
    ]
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for user, expected in cases:
                app.dependency_overrides[current_user] = lambda u=user: u
                before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
                stage_before = before.get("current_stage")
                r = await client.post(f"/api/orders/{oid}/urgent", json={"urgent": True})
                assert r.status_code == expected, (user["role"], r.status_code, r.text)
                after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
                assert after.get("current_stage") == stage_before
                if expected == 200:
                    assert after.get("priority") == "urgent"
                    await server.db.orders.update_one(
                        {"id": oid}, {"$set": {"priority": "normal", "urgency_approved_by": None}}
                    )
                else:
                    assert after.get("priority") == "normal"
    finally:
        app.dependency_overrides.pop(current_user, None)
