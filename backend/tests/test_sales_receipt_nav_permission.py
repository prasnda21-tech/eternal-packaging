"""Registry + Settings sidebar save for nav.sales.sales_receipt."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from permissions import ALL_PERMISSIONS, NAV_ALWAYS_VISIBLE, ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user

ROLE = "crm"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {"email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


@pytest.mark.anyio
async def test_catalog_and_sidebar_save_accept_sales_receipt_nav():
    app.dependency_overrides[current_user] = _admin
    before = await server.db.role_permissions.find_one({"role": ROLE}, {"_id": 0})
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog = await client.get("/api/admin/permissions/catalog")
            assert catalog.status_code == 200, catalog.text
            body = catalog.json()
            assert "nav.sales.sales_receipt" in body["all_keys"]
            nav_keys = [m["key"] for m in body["nav_menu"]]
            assert "nav.sales.sales_receipt" in nav_keys
            for key in (
                "nav.sales",
                "nav.sales.sales_order",
                "nav.sales.delivery_order",
                "nav.sales.sales_invoice",
                "nav.sales.import_po",
            ):
                assert key in body["all_keys"]
                assert key in nav_keys

            matrix = [
                "nav.sales",
                "nav.sales.sales_order",
                "nav.sales.sales_invoice",
                "nav.sales.sales_receipt",
                "nav.configured",
            ]
            saved = await client.put(f"/api/admin/roles/{ROLE}/permissions", json={"permissions": matrix})
            assert saved.status_code == 200, saved.text
            assert "nav.sales.sales_receipt" in saved.json()["permissions"]

            unknown = await client.put(
                f"/api/admin/roles/{ROLE}/permissions",
                json={"permissions": matrix + ["totally.unknown.permission"]},
            )
            assert unknown.status_code == 400
            detail = str(unknown.json()["detail"])
            assert "Permission tidak dikenal" in detail
            assert "totally.unknown.permission" in detail
            assert detail == "Permission tidak dikenal: ['totally.unknown.permission']"
    finally:
        if before:
            await server.db.role_permissions.replace_one({"role": ROLE}, before, upsert=True)
        else:
            await server.db.role_permissions.delete_many({"role": ROLE})
        app.dependency_overrides.pop(current_user, None)


def test_unknown_permission_still_rejected_offline():
    assert "nav.sales.sales_receipt" in ALL_PERMISSIONS
    assert "totally.unknown.permission" not in ALL_PERMISSIONS


def test_role_defaults_unchanged_for_sales_receipt_nav():
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        derived = with_nav_defaults(set(perms))
        assert "nav.sales.sales_receipt" not in perms
        assert "nav.sales.sales_receipt" not in derived
        assert "nav.sales.sales_receipt" not in NAV_ALWAYS_VISIBLE
