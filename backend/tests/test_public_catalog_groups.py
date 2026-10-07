"""Public Catalog Settings Phase B — group presentation overlay."""
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services import public_catalog as pc


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
    await db[pc.CATALOG_GROUPS].delete_many({})
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await db.app_settings.delete_many({"key": pc.SETTINGS_KEY})
    await pc.ensure_indexes(db)
    yield
    await db[pc.CATEGORIES].delete_many({})
    await db[pc.CATALOG_GROUPS].delete_many({})
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})


def user(*permissions, role="staff"):
    return {
        "id": str(uuid.uuid4()),
        "email": "pcs-b@test.local",
        "name": "PCS B Tester",
        "role": role,
        "permissions_override": {permission: True for permission in permissions},
        "is_active": True,
    }


async def seed_pricing_group(name="Cup Inject", *, enabled=True, description="Desc"):
    doc = {
        "id": str(uuid.uuid4()),
        "public_id": str(uuid.uuid4()),
        "group_name": name,
        "public_description": description,
        "catalog_image": None,
        "enabled": enabled,
        "current_draft_version_id": None,
        "current_published_version_id": None,
        "updated_at": "2026-01-01T00:00:00Z",
    }
    await server.db.customer_pricing_groups.insert_one(doc)
    return doc


@pytest.mark.anyio
async def test_list_lazy_defaults_no_get_side_effect():
    group = await seed_pricing_group("Paper Cup")
    before = await server.db[pc.CATALOG_GROUPS].count_documents({})
    rows = await pc.list_presentation_groups(server.db)
    assert len(rows) == 1
    row = rows[0]
    assert row["pricing_group_id"] == group["id"]
    assert row["catalog"]["configured"] is False
    assert row["catalog"]["public_name_display"] == "Paper Cup"
    assert row["catalog"]["slug"] == ""
    assert row["catalog"]["category_id"] is None
    assert row["catalog"]["visible"] is True
    assert row["catalog"]["display_order"] is None
    assert await server.db[pc.CATALOG_GROUPS].count_documents({}) == before


@pytest.mark.anyio
async def test_upsert_edit_slug_collision_category_and_isolation():
    actor = user(pc.PERM_VIEW, pc.PERM_MANAGE)
    g1 = await seed_pricing_group("Cup Inject", enabled=True)
    g2 = await seed_pricing_group("Cup Inject Alt", enabled=False)
    category = await pc.create_category(
        server.db, {"name": "Cup PET", "slug": "cup-pet"}, actor
    )
    disabled = await pc.create_category(
        server.db, {"name": "Archived", "slug": "archived", "enabled": False}, actor
    )
    before_versions = await server.db.customer_pricing_versions.count_documents({})
    pricing_before = await server.db.customer_pricing_groups.find_one(
        {"id": g1["id"]}, {"_id": 0}
    )

    first = await pc.upsert_presentation_group(
        server.db,
        g1["id"],
        {
            "category_id": category["id"],
            "public_name": "Cup Inject Premium",
            "slug": "cup-inject",
            "display_order": 2,
            "visible": True,
            "meta_title": "Title",
            "meta_description": "Desc SEO",
        },
        actor,
    )
    assert first["catalog"]["configured"] is True
    assert first["catalog"]["slug"] == "cup-inject"
    assert first["catalog"]["public_name"] == "Cup Inject Premium"
    assert first["catalog"]["category_name"] == "Cup PET"

    second = await pc.upsert_presentation_group(
        server.db,
        g2["id"],
        {"slug": "cup-inject", "display_order": 2, "visible": False},
        actor,
    )
    assert second["catalog"]["slug"] == "cup-inject-2"
    assert second["pricing_enabled"] is False
    assert second["catalog"]["visible"] is False

    with_disabled = await pc.upsert_presentation_group(
        server.db,
        g1["id"],
        {"category_id": disabled["id"], "visible": False},
        actor,
    )
    assert with_disabled["catalog"]["category_id"] == disabled["id"]
    assert with_disabled["catalog"]["category_enabled"] is False
    assert with_disabled["pricing_enabled"] is True

    null_category = await pc.upsert_presentation_group(
        server.db, g1["id"], {"category_id": None}, actor
    )
    assert null_category["catalog"]["category_id"] is None

    pricing_after = await server.db.customer_pricing_groups.find_one(
        {"id": g1["id"]}, {"_id": 0}
    )
    assert pricing_after["enabled"] == pricing_before["enabled"]
    assert pricing_after["group_name"] == pricing_before["group_name"]
    assert pricing_after["public_description"] == pricing_before["public_description"]
    assert await server.db.customer_pricing_versions.count_documents({}) == before_versions

    listed = await pc.list_presentation_groups(server.db)
    assert {row["pricing_group_id"] for row in listed} == {g1["id"], g2["id"]}
    assert [row["catalog"]["display_order"] for row in listed] == [2, 2]
    names = [row["catalog"]["public_name_display"] for row in listed]
    assert names == sorted(names, key=str.casefold)


@pytest.mark.anyio
async def test_http_permissions_and_public_unchanged():
    manager = user(pc.PERM_MANAGE, pc.PERM_VIEW)
    viewer = user(pc.PERM_VIEW)
    outsider = user("users.view")
    group = await seed_pricing_group("Box Kraft")
    transport = httpx.ASGITransport(app=app)

    app.dependency_overrides[current_user] = lambda: viewer
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/settings/public-catalog/groups")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        denied = await client.put(
            f"/api/settings/public-catalog/groups/{group['id']}",
            json={"slug": "box-kraft"},
        )
        assert denied.status_code == 403

    app.dependency_overrides[current_user] = lambda: manager
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put(
            f"/api/settings/public-catalog/groups/{group['id']}",
            json={
                "public_name": "Box Kraft Premium",
                "slug": "box-kraft",
                "display_order": 1,
                "visible": True,
                "meta_title": "MT",
                "meta_description": "MD",
            },
        )
        assert saved.status_code == 200
        body = saved.json()["group"]
        assert body["catalog"]["slug"] == "box-kraft"
        assert body["catalog"]["meta_title"] == "MT"

        got = await client.get(f"/api/settings/public-catalog/groups/{group['id']}")
        assert got.status_code == 200
        assert got.json()["group"]["catalog"]["configured"] is True

        public_list = await client.get("/api/public/customer-pricing/groups")
        assert public_list.status_code == 200

    app.dependency_overrides[current_user] = lambda: outsider
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied_view = await client.get("/api/settings/public-catalog/groups")
        assert denied_view.status_code == 403
    app.dependency_overrides.pop(current_user, None)
