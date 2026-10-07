"""Public Catalog Settings Phase C — variant metadata + gallery."""
import io
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
from server import app, current_user
from services import public_catalog as pc
from services import public_catalog_images as images


JPEG_1X1 = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
    b"\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04"
    b"\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q"
    b"\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82"
    b"\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghij"
    b"stuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97"
    b"\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5"
    b"\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3"
    b"\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9"
    b"\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\x1f\xff\xd9"
)


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
    await db[pc.CATALOG_VARIANTS].delete_many({})
    await db[pc.CATALOG_IMAGES].delete_many({})
    await db[pc.CATALOG_GROUPS].delete_many({})
    await db[pc.CATEGORIES].delete_many({})
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await db.master_items.delete_many({})
    await db.files.delete_many({"kind": images.VARIANT_IMAGE_KIND})
    await pc.ensure_indexes(db)
    yield
    await db[pc.CATALOG_VARIANTS].delete_many({})
    await db[pc.CATALOG_IMAGES].delete_many({})
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await db.files.delete_many({"kind": images.VARIANT_IMAGE_KIND})


def user(*permissions, role="staff"):
    return {
        "id": str(uuid.uuid4()),
        "email": "pcs-c@test.local",
        "name": "PCS C Tester",
        "role": role,
        "permissions_override": {permission: True for permission in permissions},
        "is_active": True,
    }


async def seed_group_with_products(
    *,
    names=None,
    enabled=True,
    product_images=None,
    catalog_image=None,
):
    names = names or [("Cup 12oz", "CUP-12", "pcs"), ("Cup 16oz", "CUP-16", "pcs")]
    group_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    products = []
    active_ids = []
    for name, code, unit in names:
        aid = str(uuid.uuid4())
        active_ids.append(aid)
        products.append(
            {
                "accurate_item_id": aid,
                "public_variant_id": str(uuid.uuid4()),
                "display_name": name,
                "item_code": code,
                "unit": unit,
                "is_active": True,
            }
        )
        await server.db.master_items.insert_one(
            {
                "id": str(uuid.uuid4()),
                "accurate_item_id": aid,
                "item_name": name,
                "item_code": code,
                "unit": unit,
                "is_active": True,
            }
        )
    await server.db.customer_pricing_versions.insert_one(
        {
            "id": version_id,
            "group_id": group_id,
            "status": "published",
            "products": products,
        }
    )
    group = {
        "id": group_id,
        "public_id": str(uuid.uuid4()),
        "group_name": "Cup Inject",
        "enabled": enabled,
        "active_accurate_item_ids": active_ids,
        "current_published_version_id": version_id,
        "current_draft_version_id": None,
        "product_images": product_images or {},
        "catalog_image": catalog_image,
        "public_description": "desc",
    }
    await server.db.customer_pricing_groups.insert_one(group)
    return group, products


@pytest.mark.anyio
async def test_variant_lazy_list_no_get_side_effect_and_identity():
    group, products = await seed_group_with_products()
    before = await server.db[pc.CATALOG_VARIANTS].count_documents({})
    rows = await pc.list_presentation_variants(server.db, group["id"])
    assert len(rows) == 2
    assert before == await server.db[pc.CATALOG_VARIANTS].count_documents({})
    for row in rows:
        assert row["catalog"]["configured"] is False
        assert row["catalog"]["size"] == ""
        assert row["catalog"]["color"] == ""
        assert row["catalog"]["finishing"] == ""
        assert row["catalog"]["display_order"] is None
        assert row["catalog"]["visible"] is True
        assert "display_name" not in (
            await server.db[pc.CATALOG_VARIANTS].find_one({}) or {}
        )
    assert {row["accurate_item_id"] for row in rows} == {
        p["accurate_item_id"] for p in products
    }
    assert all(row["display_name"] for row in rows)


@pytest.mark.anyio
async def test_variant_upsert_optional_attrs_order_visibility_isolation():
    actor = user(pc.PERM_VIEW, pc.PERM_MANAGE)
    group, products = await seed_group_with_products(
        names=[
            ("Zeta Long Name Product", "Z-1", "pcs"),
            ("Alpha Short", "A-1", "pcs"),
        ],
        enabled=True,
    )
    a = products[0]
    b = products[1]
    before_versions = await server.db.customer_pricing_versions.count_documents({})
    pricing_before = await server.db.customer_pricing_groups.find_one(
        {"id": group["id"]}, {"_id": 0}
    )

    first = await pc.upsert_presentation_variant(
        server.db,
        group["id"],
        a["accurate_item_id"],
        {
            "size": "12 oz",
            "color": "",
            "finishing": "Frosted",
            "display_order": 2,
            "visible": False,
        },
        actor,
    )
    assert first["catalog"]["configured"] is True
    assert first["catalog"]["size"] == "12 oz"
    assert first["catalog"]["color"] == ""
    assert first["catalog"]["finishing"] == "Frosted"
    assert first["catalog"]["visible"] is False
    assert first["is_active"] is True

    second = await pc.upsert_presentation_variant(
        server.db,
        group["id"],
        b["accurate_item_id"],
        {"display_order": 2, "visible": True, "color": "Hitam"},
        actor,
    )
    assert second["catalog"]["color"] == "Hitam"

    listed = await pc.list_presentation_variants(server.db, group["id"])
    assert [row["catalog"]["display_order"] for row in listed] == [2, 2]
    # stable tie-break by display_name then accurate_item_id
    names = [row["display_name"] for row in listed]
    assert names == sorted(names, key=str.casefold)

    stored = await server.db[pc.CATALOG_VARIANTS].find_one(
        {
            "pricing_group_id": group["id"],
            "accurate_item_id": a["accurate_item_id"],
        },
        {"_id": 0},
    )
    assert "display_name" not in stored
    assert "item_code" not in stored
    assert stored["accurate_item_id"] == a["accurate_item_id"]

    with pytest.raises(Exception):
        await pc.upsert_presentation_variant(
            server.db, group["id"], str(uuid.uuid4()), {"size": "x"}, actor
        )

    pricing_after = await server.db.customer_pricing_groups.find_one(
        {"id": group["id"]}, {"_id": 0}
    )
    assert pricing_after["enabled"] == pricing_before["enabled"]
    assert pricing_after["active_accurate_item_ids"] == pricing_before[
        "active_accurate_item_ids"
    ]
    assert await server.db.customer_pricing_versions.count_documents({}) == before_versions


@pytest.mark.anyio
async def test_gallery_upload_default_fallback_delete_rbac_public_unchanged():
    actor = user(pc.PERM_MANAGE, pc.PERM_VIEW)
    viewer = user(pc.PERM_VIEW)
    outsider = user("users.view")
    legacy_file = str(uuid.uuid4())
    cover_file = str(uuid.uuid4())
    group, products = await seed_group_with_products(
        product_images={
            # filled after seed
        },
        catalog_image={"file_id": cover_file, "content_type": "image/jpeg"},
    )
    aid = products[0]["accurate_item_id"]
    await server.db.customer_pricing_groups.update_one(
        {"id": group["id"]},
        {
            "$set": {
                f"product_images.{aid}": {
                    "file_id": legacy_file,
                    "content_type": "image/jpeg",
                }
            }
        },
    )

    # fallback before gallery
    resolved = await pc.resolve_variant_image(server.db, group["id"], aid)
    assert resolved["source"] == "legacy_variant"

    stored_objects = {}

    def fake_put(path, data, content_type):
        stored_objects[path] = data
        return {"path": path, "size": len(data)}

    def fake_get(path):
        return stored_objects[path], "image/jpeg"

    with patch("services.storage.put_object", side_effect=fake_put), patch(
        "services.storage.get_object", side_effect=fake_get
    ):
        img1 = await images.upload_variant_gallery_image(
            server.db,
            pricing_group_id=group["id"],
            accurate_item_id=aid,
            filename="a.jpg",
            content_type="image/jpeg",
            data=JPEG_1X1,
            user=actor,
            role="FRONT",
            display_order=2,
        )
        assert img1["is_default"] is True  # first image auto-default
        assert img1["role"] == "FRONT"

        img2 = await images.upload_variant_gallery_image(
            server.db,
            pricing_group_id=group["id"],
            accurate_item_id=aid,
            filename="b.jpg",
            content_type="image/jpeg",
            data=JPEG_1X1,
            user=actor,
            role="SIDE",
            display_order=1,
            is_default=True,
        )
        assert img2["is_default"] is True
        listed = await pc.list_gallery_images(server.db, group["id"], aid)
        assert len(listed) == 2
        assert [row["display_order"] for row in listed] == [1, 2]
        defaults = [row for row in listed if row["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["id"] == img2["id"]

        with pytest.raises(Exception):
            await images.upload_variant_gallery_image(
                server.db,
                pricing_group_id=group["id"],
                accurate_item_id=aid,
                filename="bad.txt",
                content_type="text/plain",
                data=b"not-an-image",
                user=actor,
            )

        with pytest.raises(Exception):
            await images.upload_variant_gallery_image(
                server.db,
                pricing_group_id=group["id"],
                accurate_item_id=aid,
                filename="big.jpg",
                content_type="image/jpeg",
                data=b"\xff\xd8\xff" + (b"0" * (5 * 1024 * 1024 + 10)),
                user=actor,
            )

        await pc.update_gallery_image(
            server.db,
            group["id"],
            aid,
            img2["id"],
            {"enabled": False, "role": "DETAIL"},
            actor,
        )
        resolved = await pc.resolve_variant_image(server.db, group["id"], aid)
        assert resolved["source"] == "gallery_default" or resolved["source"] == "gallery_first_enabled"
        # disabled default → next enabled
        assert resolved["image"]["id"] == img1["id"]

        await images.remove_variant_gallery_image(
            server.db,
            pricing_group_id=group["id"],
            accurate_item_id=aid,
            image_id=img1["id"],
            user=actor,
        )
        await images.remove_variant_gallery_image(
            server.db,
            pricing_group_id=group["id"],
            accurate_item_id=aid,
            image_id=img2["id"],
            user=actor,
        )
        assert await server.db[pc.CATALOG_IMAGES].count_documents({}) == 0
        group_after = await server.db.customer_pricing_groups.find_one(
            {"id": group["id"]}, {"_id": 0}
        )
        assert group_after["product_images"][aid]["file_id"] == legacy_file
        resolved = await pc.resolve_variant_image(server.db, group["id"], aid)
        assert resolved["source"] == "legacy_variant"

    transport = httpx.ASGITransport(app=app)
    app.dependency_overrides[current_user] = lambda: outsider
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get(
            f"/api/settings/public-catalog/groups/{group['id']}/variants"
        )
        assert denied.status_code == 403

    app.dependency_overrides[current_user] = lambda: viewer
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(
            f"/api/settings/public-catalog/groups/{group['id']}/variants"
        )
        assert listed.status_code == 200
        assert listed.json()["count"] == 2
        write_denied = await client.put(
            f"/api/settings/public-catalog/groups/{group['id']}/variants/{aid}",
            json={"size": "x"},
        )
        assert write_denied.status_code == 403

    app.dependency_overrides[current_user] = lambda: actor
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put(
            f"/api/settings/public-catalog/groups/{group['id']}/variants/{aid}",
            json={"size": "12 oz", "visible": True},
        )
        assert saved.status_code == 200
        public_groups = await client.get("/api/public/customer-pricing/groups")
        # public endpoint may 200 with empty or existing; must not 500 and route still present
        assert public_groups.status_code in (200, 401, 403)

    # pricing writes frozen
    assert await server.db.customer_pricing_versions.count_documents({}) == 1
