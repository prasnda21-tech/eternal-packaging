"""Production HOLDING_TEAM lock for Purchasing/Inventory/HPP package."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("DB_NAME", "eternal_pi_access_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user, user_permissions
from services.purchasing_inventory_access import (
    PACKAGE_PERMISSIONS,
    can_access_purchasing_inventory_package,
    filter_package_permissions,
    is_local_runtime,
)


def test_local_runtime_fail_closed():
    """Local only with explicit APP_ENV; loopback Mongo alone is not local."""
    assert is_local_runtime("preview", "mongodb://127.0.0.1:27017") is True
    assert is_local_runtime("local", "mongodb://127.0.0.1:27017") is True
    assert is_local_runtime("development", "mongodb://127.0.0.1:27017") is True
    assert is_local_runtime("test", "mongodb://127.0.0.1:27017") is True
    assert is_local_runtime("production", "mongodb://127.0.0.1:27017") is False
    assert is_local_runtime(None, "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs") is False
    assert is_local_runtime("", "mongodb://127.0.0.1:27017") is False
    assert is_local_runtime("staging", "mongodb://127.0.0.1:27017") is False
    assert is_local_runtime(None, "mongodb://prod-mongo.example:27017") is False


def test_production_allows_holding_and_director():
    assert can_access_purchasing_inventory_package(
        {"role": "holding_team"}, "production", "mongodb://prod:27017"
    )
    assert can_access_purchasing_inventory_package(
        {"role": "direktur_utama"}, "production", "mongodb://prod:27017"
    )
    assert not can_access_purchasing_inventory_package(
        {"role": "admin"}, "production", "mongodb://prod:27017"
    )
    assert not can_access_purchasing_inventory_package(
        {"role": "head_production"}, "production", "mongodb://prod:27017"
    )


def test_unset_app_env_loopback_denies_admin_package():
    """Unset APP_ENV + loopback Mongo must not grant admin package access."""
    admin = {"role": "admin"}
    assert not can_access_purchasing_inventory_package(
        admin, None, "mongodb://127.0.0.1:27017"
    )
    filtered = filter_package_permissions(
        PACKAGE_PERMISSIONS | {"po.view", "chat.use", "nav.settings"},
        admin,
        None,
        "mongodb://127.0.0.1:27017",
    )
    assert "po.view" in filtered
    assert "chat.use" in filtered
    assert "nav.settings" in filtered
    assert not filtered.intersection(PACKAGE_PERMISSIONS)


def test_filter_strips_package_keys_on_production():
    filtered = filter_package_permissions(
        PACKAGE_PERMISSIONS | {"po.view", "chat.use"},
        {"role": "head_operations"},
        "production",
        "mongodb://prod:27017",
    )
    assert "po.view" in filtered
    assert "chat.use" in filtered
    assert not filtered.intersection(PACKAGE_PERMISSIONS)


def test_filter_preserves_unrelated_module_keys_for_admin_when_locked():
    """Sales/SPK/Production/Delivery/Payroll/Settings keys stay when package stripped."""
    unrelated = {
        "po.view",
        "spk.view_all",
        "dashboard.production.view",
        "delivery.update",
        "payroll.view",
        "nav.settings",
        "nav.sales",
    }
    filtered = filter_package_permissions(
        unrelated | PACKAGE_PERMISSIONS,
        {"role": "admin"},
        "production",
        "mongodb://127.0.0.1:27017",
    )
    assert filtered == unrelated


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db(monkeypatch):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client["eternal_pi_access_test"]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    yield
    app.dependency_overrides.pop(current_user, None)
    monkeypatch.delenv("APP_ENV", raising=False)
    await client.drop_database(database.name)
    client.close()


@pytest.mark.anyio
async def test_local_admin_keeps_package_perms(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    perms = await user_permissions({"role": "admin", "email": "admin@eternalpackaging.com"})
    assert "purchase_hpp.view" in perms
    assert "current_stock.view" in perms
    assert "org.branches.view" in perms


@pytest.mark.anyio
async def test_production_head_production_loses_package_perms(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    perms = await user_permissions({"role": "head_production", "email": "hop@test.local"})
    assert "purchase_hpp.view" not in perms
    assert "current_stock.view" not in perms
    assert "nav.purchasing" not in perms


@pytest.mark.anyio
async def test_production_normalized_crm_grant_is_effective_without_old_stamp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    await server.db.role_permissions.insert_one({
        "role": "crm",
        "permissions": ["schedule.view", "purchase_orders.view", "purchase_hpp.view"],
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    legacy = await user_permissions({"role": "crm", "email": "crm-legacy@test.local"})
    assert "purchase_orders.view" in legacy
    assert "purchase_hpp.view" not in legacy
    assert "schedule.view" in legacy

    await server.db.role_permissions.update_one(
        {"role": "crm"},
        {"$set": {
            "permissions": ["schedule.view", "purchase_orders.view"],
            "package_production_confirmed_keys": [],
            "manual_grant_normalized": True,
        }},
    )
    normalized = await user_permissions({"role": "crm", "email": "crm-norm@test.local"})
    assert "purchase_orders.view" in normalized
    assert "purchase_hpp.view" not in normalized
    assert "schedule.view" in normalized

    hop = await user_permissions({"role": "head_production", "email": "hop-norm@test.local"})
    assert "purchase_hpp.view" not in hop
    assert "current_stock.view" not in hop


@pytest.mark.anyio
async def test_production_holding_team_retains_package_perms(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    perms = await user_permissions({"role": "holding_team", "email": "holding@test.local"})
    assert "purchase_hpp.view" in perms
    assert "current_stock.view" in perms
    assert "org.branches.view" in perms


async def _get_as(role: str, path: str):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "name": role,
        "email": f"{role}@test.local",
        "role": role,
        "permissions_override": {},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_production_api_denies_head_production_hpp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    listing = await _get_as("head_production", "/api/pembelian/hpp")
    assert listing.status_code == 403, listing.text


@pytest.mark.anyio
async def test_production_api_allows_holding_hpp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    listing = await _get_as("holding_team", "/api/pembelian/hpp")
    assert listing.status_code == 200, listing.text


@pytest.mark.anyio
async def test_local_api_allows_admin_hpp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    listing = await _get_as("admin", "/api/pembelian/hpp")
    assert listing.status_code == 200, listing.text


@pytest.mark.anyio
async def test_preview_api_allows_admin_hpp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "preview")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    listing = await _get_as("admin", "/api/pembelian/hpp")
    assert listing.status_code == 200, listing.text


@pytest.mark.anyio
async def test_unset_app_env_api_denies_admin_hpp(bind_db, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    listing = await _get_as("admin", "/api/pembelian/hpp")
    assert listing.status_code == 403, listing.text


@pytest.mark.anyio
async def test_production_api_denies_admin_hpp(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017")
    listing = await _get_as("admin", "/api/pembelian/hpp")
    assert listing.status_code == 403, listing.text


@pytest.mark.anyio
async def test_production_holding_missing_org_view_denied_branches(bind_db, monkeypatch):
    """HOLDING_TEAM still needs granted org.branches.view — package role alone is not enough."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod-mongo.example:27017")
    await server.db.role_permissions.insert_one(
        {
            "role": "holding_team",
            "permissions": [
                "purchase_hpp.view",
                "nav.purchasing.hpp",
                "current_stock.view",
                "nav.inventory.current_stock",
            ],
        }
    )
    listing = await _get_as("holding_team", "/api/branches")
    assert listing.status_code == 403, listing.text
