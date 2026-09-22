"""Dedicated Nilai Persediaan permission enforcement."""
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_inventory_valuation_permission_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client["eternal_inventory_valuation_permission_test"]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    yield
    app.dependency_overrides.pop(current_user, None)
    await client.drop_database(database.name)
    client.close()


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
@pytest.mark.parametrize("role", ["admin", "holding_team"])
async def test_allowed_roles_can_view_and_export(role):
    listing = await _get_as(role, "/api/inventory-valuation")
    assert listing.status_code == 200, listing.text
    exported = await _get_as(role, "/api/inventory-valuation/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "role",
    [
        "head_production",
        "head_operations",
        "operator",
        "helper",
        "screener",
        "designer",
        "driver",
    ],
)
async def test_production_roles_are_forbidden_from_direct_api(role):
    listing = await _get_as(role, "/api/inventory-valuation")
    assert listing.status_code == 403
    exported = await _get_as(role, "/api/inventory-valuation/export")
    assert exported.status_code == 403
