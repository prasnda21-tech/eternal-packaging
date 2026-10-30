"""Startup top-up must not restore revoked sales-order menu grants."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MONGO_URL"] = "mongodb://127.0.0.1:27017"
os.environ["DB_NAME"] = "eternal_phase_b_sales_order_topup"
os.environ["APP_ENV"] = "production"
os.environ["SEED_DEMO_ORDERS"] = "0"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from motor.motor_asyncio import AsyncIOMotorClient

REVOKED_KEYS = ("sales_order.view", "sales_order.create", "nav.sales.sales_order")
ROLES = ("crm", "head_production", "head_operations", "admin")
OTHER_TOP_UP = {
    "crm": "buffer_stock.manage",
    "head_production": "dashboard.production.view",
    "head_operations": "po.cancel",
    "admin": "po.import.view",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _bind():
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


async def _stored(role):
    doc = await server.db.role_permissions.find_one({"role": role}, {"_id": 0, "permissions": 1})
    return list((doc or {}).get("permissions") or [])


async def _startup():
    assert os.environ["APP_ENV"] == "production"
    await server.seed()


@pytest.mark.anyio
async def test_startup_keeps_manual_sales_order_grants():
    _bind()
    await server.client.drop_database(os.environ["DB_NAME"])
    try:
        for role in ROLES:
            await server.db.role_permissions.insert_one({
                "role": role,
                "permissions": ["test.keep"],
            })
        await server.db.role_permissions.insert_one({
            "role": "designer",
            "permissions": ["customer_approval.view", "test.keep"],
        })

        await _startup()
        for role in ROLES:
            stored = await _stored(role)
            assert "test.keep" in stored
            assert OTHER_TOP_UP[role] in stored, role
            for key in REVOKED_KEYS:
                assert key not in stored, (role, key)
        designer = await _stored("designer")
        assert "customer_approval.manage" in designer
        for key in REVOKED_KEYS:
            assert key not in designer

        for role in ROLES:
            await server.db.role_permissions.update_one(
                {"role": role},
                {"$addToSet": {"permissions": {"$each": list(REVOKED_KEYS)}}},
            )
        await _startup()
        for role in ROLES:
            stored = await _stored(role)
            assert "test.keep" in stored
            assert OTHER_TOP_UP[role] in stored
            for key in REVOKED_KEYS:
                assert key in stored, (role, key)

        for role in ROLES:
            await server.db.role_permissions.update_one(
                {"role": role},
                {"$pull": {"permissions": {"$in": list(REVOKED_KEYS)}}},
            )
        await _startup()
        await _startup()
        for role in ROLES:
            stored = await _stored(role)
            assert "test.keep" in stored
            assert OTHER_TOP_UP[role] in stored, role
            for key in REVOKED_KEYS:
                assert key not in stored, (role, key)

        assert server._ADMIN_IMPLICIT_MANAGEMENT == frozenset({
            "users.manage",
            "permissions.manage",
            "roles.manage",
        })
        assert not set(REVOKED_KEYS) & server._ADMIN_IMPLICIT_MANAGEMENT
    finally:
        await server.client.drop_database(os.environ["DB_NAME"])
        server.client.close()
