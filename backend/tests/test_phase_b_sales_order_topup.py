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
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from services.manual_grant_cutover import apply_cutover, load_targets
from services.purchasing_inventory_access import MANUAL_GRANT_NORMALIZED_FIELD

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


NORMALIZED_DB = "eternal_startup_normalized_topup"
CLEANED = ("purchase_hpp.view", "current_stock.view", "purchase_receipts.view", "master_items.view")
MANUAL_KEYS = ("buffer_stock.manage", "chat.use", "current_stock.view")


def _bind_normalized():
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[NORMALIZED_DB]


async def _role_doc(role):
    return await server.db.role_permissions.find_one(
        {"role": role},
        {"_id": 0, "permissions": 1, "updated_by": 1, MANUAL_GRANT_NORMALIZED_FIELD: 1},
    )


@pytest.mark.anyio
async def test_normalized_role_keeps_cutover_grant_across_startup():
    _bind_normalized()
    await server.client.drop_database(NORMALIZED_DB)
    try:
        await server.db.role_permissions.insert_many([
            {"role": "crm", "permissions": ["po.view", "purchase_hpp.view", "current_stock.view"]},
            {"role": "head_production", "permissions": [
                "po.view", "current_stock.view", "purchase_receipts.view", "dashboard.production.view",
            ]},
            {"role": "admin", "permissions": [
                "po.view", "current_stock.view", "master_items.view", "po.import.view",
            ]},
            {"role": "holding_team", "permissions": ["po.view", "purchase_orders.view", "purchase_hpp.view"]},
            {"role": "direktur_utama", "permissions": ["po.view", "purchase_hpp.view"]},
        ])
        _roles, _users, plans = await load_targets(server.db)
        await apply_cutover(server.db, plans, stamp="2026-10-30T12:00:00Z")

        frozen = {}
        for role in ("crm", "head_production", "admin", "holding_team", "direktur_utama"):
            doc = await _role_doc(role)
            assert doc[MANUAL_GRANT_NORMALIZED_FIELD] is True
            frozen[role] = list(doc["permissions"])
            for key in CLEANED:
                if role in ("holding_team", "direktur_utama"):
                    continue
                assert key not in doc["permissions"], (role, key)
        assert "purchase_orders.view" in frozen["holding_team"]
        assert "purchase_hpp.view" in frozen["direktur_utama"]
        assert "purchase_hpp.view" in frozen["holding_team"]

        await server.db.role_permissions.insert_one({
            "role": "designer",
            "permissions": ["customer_approval.view", "test.keep"],
        })

        await _startup()
        await _startup()

        for role, before in frozen.items():
            doc = await _role_doc(role)
            assert doc["permissions"] == before, role
            assert doc[MANUAL_GRANT_NORMALIZED_FIELD] is True
            assert doc.get("updated_by") not in {
                "system.migration.phaseB",
                "system.migration.chat_v1",
            }
        assert await server.db.role_permissions.find_one({"role": "screener"}) is None

        designer = await _role_doc("designer")
        assert designer.get(MANUAL_GRANT_NORMALIZED_FIELD) is not True
        for key in ("test.keep", "customer_approval.view", "customer_approval.manage", "chat.use", "nav.chat"):
            assert key in designer["permissions"], key

        crm_eff = await server.user_permissions({"role": "crm", "permissions_override": {}})
        assert "po.view" in crm_eff
        for key in ("purchase_hpp.view", "current_stock.view", "buffer_stock.manage", "chat.use"):
            assert key not in crm_eff, key
        holding_eff = await server.user_permissions({"role": "holding_team", "permissions_override": {}})
        assert "purchase_orders.view" in holding_eff
        assert "purchase_hpp.view" in holding_eff
        director_eff = await server.user_permissions({"role": "direktur_utama", "permissions_override": {}})
        assert "purchase_hpp.view" in director_eff

        await server._require_perm({"role": "admin"}, "po.view")
        with pytest.raises(HTTPException) as denied:
            await server._require_perm({"role": "admin"}, "current_stock.view")
        assert denied.value.status_code == 403
        await server.db.users.insert_one({
            "id": "sa-proof",
            "email": "sa-proof@example.test",
            "role": "admin",
            "status": "active",
            "is_super_admin": True,
            "permissions_override": {},
        })
        sa_eff = await server.user_permissions({
            "email": "sa-proof@example.test",
            "role": "admin",
            "permissions_override": {},
        })
        assert "po.view" in sa_eff
        assert "current_stock.view" not in sa_eff
        assert server._ADMIN_IMPLICIT_MANAGEMENT == frozenset({
            "users.manage", "permissions.manage", "roles.manage",
        })
        assert "org.branches.manage" not in server._ADMIN_IMPLICIT_MANAGEMENT

        await server.db.role_permissions.update_one(
            {"role": "crm"},
            {"$addToSet": {"permissions": {"$each": list(MANUAL_KEYS)}}},
        )
        await _startup()
        granted = await _role_doc("crm")
        for key in MANUAL_KEYS:
            assert key in granted["permissions"], key
        assert granted[MANUAL_GRANT_NORMALIZED_FIELD] is True
        granted_eff = await server.user_permissions({"role": "crm", "permissions_override": {}})
        for key in MANUAL_KEYS:
            assert key in granted_eff, key

        await server.db.role_permissions.update_one(
            {"role": "crm"},
            {"$pull": {"permissions": {"$in": list(MANUAL_KEYS)}}},
        )
        await _startup()
        await _startup()
        revoked = await _role_doc("crm")
        revoked_eff = await server.user_permissions({"role": "crm", "permissions_override": {}})
        for key in MANUAL_KEYS:
            assert key not in revoked["permissions"], key
            assert key not in revoked_eff, key
        assert "po.view" in revoked["permissions"]
        assert revoked[MANUAL_GRANT_NORMALIZED_FIELD] is True
        assert "purchase_hpp.view" not in revoked_eff
    finally:
        await server.client.drop_database(NORMALIZED_DB)
        server.client.close()
