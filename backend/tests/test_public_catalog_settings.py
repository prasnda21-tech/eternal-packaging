"""Public Catalog Settings Phase A — global settings + categories."""
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG
from server import app, current_user
from services import public_catalog as pc
from services.customer_pricing_catalog import MAX_CATALOG_IMAGE_BYTES


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_and_index(bind_motor):
    db = server.db
    await db[pc.CATEGORIES].delete_many({})
    await db.app_settings.delete_many({"key": pc.SETTINGS_KEY})
    await db.app_settings.delete_many({"key": "customer_pricing"})
    await db.files.delete_many({"kind": "public_catalog_category_image"})
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await pc.ensure_indexes(db)
    yield
    await db[pc.CATEGORIES].delete_many({})
    await db.app_settings.delete_many({"key": pc.SETTINGS_KEY})
    await db.files.delete_many({"kind": "public_catalog_category_image"})


def user(*permissions, role="staff"):
    return {
        "id": str(uuid.uuid4()),
        "email": "pcs@test.local",
        "name": "PCS Tester",
        "role": role,
        "permissions_override": {permission: True for permission in permissions},
        "is_active": True,
    }


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


@pytest.mark.anyio
async def test_permissions_in_catalog():
    keys = {permission for _module, permission, _label in PERMISSION_CATALOG}
    assert {pc.PERM_VIEW, pc.PERM_MANAGE} <= keys
    assert "settings.public_catalog.publish" not in keys
    assert pc.PERM_VIEW in ALL_PERMISSIONS
    assert pc.PERM_MANAGE in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_slugify_helper():
    assert pc.slugify("Cup PET!") == "cup-pet"
    assert pc.slugify("  Cup---PET  ") == "cup-pet"
    assert pc.slugify("Kertas 120µ") == "kertas-120"


@pytest.mark.anyio
async def test_view_and_manage_global_settings_and_whatsapp_separation():
    await server.db.app_settings.insert_one(
        {
            "key": "customer_pricing",
            "admin_phone": "0811111111",
            "default_quotation_validity_days": 7,
        }
    )
    viewer = user(pc.PERM_VIEW)
    manager = user(pc.PERM_MANAGE)
    outsider = user("users.view")

    app.dependency_overrides[current_user] = lambda: viewer
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        viewed = await client.get("/api/settings/public-catalog")
        assert viewed.status_code == 200
        body = viewed.json()["settings"]
        assert body["catalog_name"] == ""
        assert body["public_whatsapp_phone"] == ""
        assert body["catalog_enabled"] is False
        assert "admin_phone" not in body

        denied_write = await client.put(
            "/api/settings/public-catalog",
            json={"catalog_name": "X", "catalog_enabled": True},
        )
        assert denied_write.status_code == 403

    app.dependency_overrides[current_user] = lambda: manager
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put(
            "/api/settings/public-catalog",
            json={
                "catalog_name": "Eternal Katalog",
                "hero_title": "Harga Retail",
                "hero_subtitle": "Cup & kemasan",
                "public_whatsapp_phone": "+62 812-3456-7890",
                "catalog_enabled": True,
            },
        )
        assert saved.status_code == 200
        settings = saved.json()["settings"]
        assert settings["catalog_name"] == "Eternal Katalog"
        assert settings["public_whatsapp_phone"] == "+62 812-3456-7890"
        assert settings["catalog_enabled"] is True

        reloaded = await client.get("/api/settings/public-catalog")
        assert reloaded.json()["settings"]["catalog_enabled"] is True

    pricing = await server.db.app_settings.find_one({"key": "customer_pricing"}, {"_id": 0})
    assert pricing["admin_phone"] == "0811111111"
    catalog = await server.db.app_settings.find_one({"key": pc.SETTINGS_KEY}, {"_id": 0})
    assert catalog["public_whatsapp_phone"] == "+62 812-3456-7890"
    assert catalog.get("admin_phone") is None

    app.dependency_overrides[current_user] = lambda: outsider
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/settings/public-catalog")
        assert denied.status_code == 403
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_category_crud_order_slug_collision_soft_disable():
    actor = user(pc.PERM_VIEW, pc.PERM_MANAGE)
    first = await pc.create_category(
        server.db,
        {"name": "Cup PET", "slug": "cup-pet", "display_order": 2},
        actor,
    )
    second = await pc.create_category(
        server.db,
        {"name": "Paper Cup", "slug": "cup-pet", "display_order": 2},
        actor,
    )
    third = await pc.create_category(
        server.db,
        {"name": "Box", "display_order": 1, "enabled": True},
        actor,
    )
    assert first["slug"] == "cup-pet"
    assert second["slug"] == "cup-pet-2"
    assert first["id"] != second["id"]

    listed = await pc.list_categories(server.db)
    assert [row["name"] for row in listed] == ["Box", "Cup PET", "Paper Cup"]

    disabled = await pc.update_category(
        server.db, third["id"], {"enabled": False, "name": "Box Large"}, actor
    )
    assert disabled["enabled"] is False
    assert disabled["name"] == "Box Large"
    still = await pc.list_categories(server.db)
    assert any(row["id"] == third["id"] and row["enabled"] is False for row in still)


@pytest.mark.anyio
async def test_category_http_and_image_validation_no_pricing_mutation():
    manager = user(pc.PERM_MANAGE, pc.PERM_VIEW)
    viewer = user(pc.PERM_VIEW)
    await server.db.customer_pricing_groups.insert_one(
        {"id": "cp-group-freeze", "group_name": "Freeze", "public_id": "freeze"}
    )
    before_groups = await server.db.customer_pricing_groups.count_documents({})
    before_versions = await server.db.customer_pricing_versions.count_documents({})

    app.dependency_overrides[current_user] = lambda: manager
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/public-catalog/categories",
            json={"name": "Cup PET", "display_order": 5},
        )
        assert created.status_code == 200
        category = created.json()["category"]
        assert category["slug"] == "cup-pet"
        category_id = category["id"]

        patched = await client.patch(
            f"/api/public-catalog/categories/{category_id}",
            json={"display_order": 1, "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["category"]["enabled"] is False
        assert patched.json()["category"]["display_order"] == 1

        storage_path = (
            f"eternal-packaging/public-catalog/categories/{category_id}/img.png"
        )
        with patch(
            "services.public_catalog_images.obj_storage.put_object",
            return_value={"path": storage_path, "size": len(PNG)},
        ):
            uploaded = await client.post(
                f"/api/public-catalog/categories/{category_id}/image",
                files={"file": ("cup.png", PNG, "image/png")},
            )
        assert uploaded.status_code == 200
        image = uploaded.json()["category"]["image"]
        assert image["content_type"] == "image/png"
        assert image["file_id"]
        assert "base64" not in str(image).lower()

        with patch(
            "services.public_catalog_images.obj_storage.put_object",
            return_value={"path": storage_path, "size": len(PNG)},
        ):
            bad_mime = await client.post(
                f"/api/public-catalog/categories/{category_id}/image",
                files={"file": ("payload.exe", b"MZ\x90\x00not-an-image", "image/png")},
            )
            too_big = await client.post(
                f"/api/public-catalog/categories/{category_id}/image",
                files={
                    "file": (
                        "large.png",
                        PNG + b"\x00" * MAX_CATALOG_IMAGE_BYTES,
                        "image/png",
                    )
                },
            )
        assert bad_mime.status_code == 400
        assert too_big.status_code == 400

        with patch(
            "services.public_catalog_images.obj_storage.get_object",
            return_value=(PNG, "image/png"),
        ):
            fetched = await client.get(
                f"/api/public-catalog/categories/{category_id}/image"
            )
        assert fetched.status_code == 200
        assert fetched.headers["content-type"].startswith("image/png")

        deleted = await client.delete(
            f"/api/public-catalog/categories/{category_id}/image"
        )
        assert deleted.status_code == 200
        assert deleted.json()["category"]["image"] is None

        listed = await client.get("/api/public-catalog/categories")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["categories"][0]["enabled"] is False

    app.dependency_overrides[current_user] = lambda: viewer
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        readable = await client.get("/api/public-catalog/categories")
        assert readable.status_code == 200
        denied = await client.post(
            "/api/public-catalog/categories",
            json={"name": "Nope"},
        )
        assert denied.status_code == 403

    assert await server.db.customer_pricing_groups.count_documents({}) == before_groups
    assert await server.db.customer_pricing_versions.count_documents({}) == before_versions
    freeze = await server.db.customer_pricing_groups.find_one(
        {"id": "cp-group-freeze"}, {"_id": 0}
    )
    assert freeze["group_name"] == "Freeze"
    app.dependency_overrides.pop(current_user, None)
