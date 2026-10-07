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


def test_local_runtime_detects_preview_mongo():
    assert is_local_runtime(None, "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs") is True
    assert is_local_runtime("preview", "mongodb://127.0.0.1:27017") is True
    assert is_local_runtime("production", "mongodb://127.0.0.1:27017") is False
    assert is_local_runtime(None, "mongodb://prod-mongo.example:27017") is False


def test_production_allows_only_holding_team():
    assert can_access_purchasing_inventory_package(
        {"role": "holding_team"}, "production", "mongodb://prod:27017"
    )
    assert not can_access_purchasing_inventory_package(
        {"role": "admin"}, "production", "mongodb://prod:27017"
    )
    assert not can_access_purchasing_inventory_package(
        {"role": "head_production"}, "production", "mongodb://prod:27017"
    )


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
