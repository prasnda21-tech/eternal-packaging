"""Public Catalog final integration — home/detail/slug/eligibility/privacy."""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app
from services import customer_pricing as cp
from services import customer_pricing_public as public_pricing
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
async def isolated_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    previous_client, previous_db = server.client, server.db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    worker = os.environ.get("PYTEST_XDIST_WORKER", "local")
    db_name = f"eternal_packaging_test_public_catalog_int_{worker}"
    await client.drop_database(db_name)
    server.client = client
    server.db = client[db_name]
    await cp.ensure_indexes(server.db)
    await pc.ensure_indexes(server.db)
    await server.db.app_settings.update_one(
        {"key": pc.SETTINGS_KEY},
        {
            "$set": {
                "key": pc.SETTINGS_KEY,
                "catalog_name": "Eternal Catalog",
                "hero_title": "Kemasan Berkualitas",
                "hero_subtitle": "Harga produk terkini",
                "public_whatsapp_phone": "6281234567890",
                "catalog_enabled": True,
                "admin_secret_note": "INTERNAL_ONLY",
            }
        },
        upsert=True,
    )
    yield
    await client.drop_database(db_name)
    client.close()
    server.client, server.db = previous_client, previous_db


def actor():
    return {
        "id": "pcs-int-employee",
        "email": "pcs-int@test.local",
        "name": "PCS Integration",
        "role": "admin",
        "permissions_override": {
            cp.PERM_VIEW: True,
            cp.PERM_MANAGE: True,
            cp.PERM_PUBLISH: True,
            pc.PERM_VIEW: True,
            pc.PERM_MANAGE: True,
        },
        "is_active": True,
    }


async def seed_master(aid: str, name: str, *, active=True):
    doc = {
        "id": f"master-{aid}",
        "accurate_item_id": aid,
        "item_code": f"CODE-{aid}",
        "item_name": name,
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": active,
    }
    await server.db.master_items.insert_one(doc)
    return doc


def manual_payload(draft, products, *, prices=None):
    prices = prices or {aid: [2500, 2300, 2100] for aid in products}
    return {
        "revision": draft["revision"],
        "group_name": draft["group_name"],
        "pricing_mode": cp.MODE_MANUAL_TIER,
        "tiers": [
            {"min_qty": 1000, "max_qty": 2999},
            {"min_qty": 3000, "max_qty": 4999},
            {"min_qty": 5000, "max_qty": None},
        ],
        "products": [
            {"accurate_item_id": aid, "tier_prices": prices[aid]}
            for aid in products
        ],
    }


async def publish_group(name="Paper Cup", products=None, *, prices=None, labels=None):
    products = products or ("aid-12oz",)
    labels = labels or {}
    user = actor()
    for aid in products:
        await seed_master(aid, labels.get(aid) or aid.replace("aid-", "").replace("-", " "))
    group = await cp.create_group(server.db, {"group_name": name}, user)
    draft = await cp.create_or_get_draft(server.db, group["id"], user)
    draft = await cp.update_draft(
        server.db, draft["id"], manual_payload(draft, products, prices=prices), user
    )
    published = await cp.publish_version(
        server.db, draft["id"], {"revision": draft["revision"]}, user
    )
    return group, published["version"]


def all_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from all_keys(nested)


PRIVATE_KEYS = {
    "hpp",
    "hpp_snapshot",
    "manual_hpp",
    "hpp_source",
    "profit",
    "profit_value",
    "profit_mode",
    "markup",
    "margin",
    "accurate_item_id",
    "master_item_id",
    "created_by",
    "updated_by",
    "published_by",
    "pricing_group_id",
    "current_draft_version_id",
    "current_published_version_id",
    "_id",
    "file_id",
    "storage_key",
    "storage_path",
    "admin_secret_note",
    "revision",
    "notes",
}


@pytest.mark.anyio
async def test_public_global_home_settings_phone_and_disabled():
    home = await public_pricing.get_public_catalog_home(server.db)
    assert home["settings"]["catalog_enabled"] is True
    assert home["settings"]["hero_title"] == "Kemasan Berkualitas"
    assert home["settings"]["public_whatsapp_phone"] == "6281234567890"
    assert "admin_secret_note" not in home["settings"]
    assert PRIVATE_KEYS.isdisjoint(set(all_keys(home["settings"])))

    await server.db.app_settings.update_one(
        {"key": pc.SETTINGS_KEY}, {"$set": {"catalog_enabled": False}}
    )
    disabled = await public_pricing.get_public_catalog_home(server.db)
    assert disabled["message"] == public_pricing.CATALOG_DISABLED
    assert disabled["groups"] == []
    assert disabled["categories"] == []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/public/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Katalog harga sedang tidak tersedia."
    assert "admin_secret_note" not in json.dumps(body)


@pytest.mark.anyio
async def test_enabled_categories_order_and_image_endpoint():
    enabled_a = await pc.create_category(
        server.db, {"name": "Bowl", "display_order": 2, "enabled": True}, actor()
    )
    enabled_b = await pc.create_category(
        server.db, {"name": "Cup", "display_order": 1, "enabled": True}, actor()
    )
    await pc.create_category(
        server.db, {"name": "Hidden", "display_order": 0, "enabled": False}, actor()
    )
    with patch(
        "services.public_catalog_images.obj_storage.put_object",
        return_value={"path": "private/cat.png", "size": len(JPEG_1X1)},
    ):
        await images.upload_category_image(
            server.db,
            category_id=enabled_b["id"],
            filename="cup.jpg",
            content_type="image/jpeg",
            data=JPEG_1X1,
            user=actor(),
        )
    cats = await public_pricing.list_public_categories(server.db)
    assert [c["name"] for c in cats] == ["Cup", "Bowl"]
    assert cats[0]["id"] == enabled_b["id"]
    assert f"/categories/{enabled_b['id']}/image" in cats[0]["image_url"]
    assert enabled_a["id"] == cats[1]["id"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        with patch(
            "services.public_catalog_images.get_category_image",
            return_value=(JPEG_1X1, "image/jpeg", "cup.jpg"),
        ):
            # Route resolves via public pricing → images.get_category_image
            img = await client.get(
                f"/api/public/catalog/categories/{enabled_b['id']}/image"
            )
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")


@pytest.mark.anyio
async def test_group_list_eligibility_public_name_min_price_variant_count():
    visible, version = await publish_group(
        "Internal Cup Name",
        ("aid-12", "aid-16", "aid-hidden"),
        prices={
            "aid-12": [2500, 2300, 2100],
            "aid-16": [2800, 2600, 2400],
            "aid-hidden": [1000, 900, 800],
        },
        labels={"aid-12": "12oz", "aid-16": "16oz", "aid-hidden": "20oz Hidden"},
    )
    cat = await pc.create_category(
        server.db, {"name": "Cup", "display_order": 1, "enabled": True}, actor()
    )
    await pc.upsert_presentation_group(
        server.db,
        visible["id"],
        {
            "public_name": "Cup Inject Premium",
            "slug": "cup-inject",
            "category_id": cat["id"],
            "display_order": 3,
            "visible": True,
        },
        actor(),
    )
    hidden_aid = "aid-hidden"
    await pc.upsert_presentation_variant(
        server.db,
        visible["id"],
        hidden_aid,
        {"visible": False, "size": "20oz"},
        actor(),
    )
    await pc.upsert_presentation_variant(
        server.db,
        visible["id"],
        "aid-12",
        {"visible": True, "size": "12oz", "color": "Bening"},
        actor(),
    )
    await pc.upsert_presentation_variant(
        server.db,
        visible["id"],
        "aid-16",
        {"visible": True, "size": "16oz", "color": "Hitam"},
        actor(),
    )

    hidden_group, _ = await publish_group("Hidden Overlay", ("aid-hidden-group",))
    await pc.upsert_presentation_group(
        server.db,
        hidden_group["id"],
        {"visible": False, "public_name": "Should Hide"},
        actor(),
    )
    lazy_group, _ = await publish_group("Lazy Visible", ("aid-lazy",))
    # no overlay → lazy default visible

    disabled_group, _ = await publish_group("Disabled Pricing", ("aid-disabled",))
    await cp.patch_group(server.db, disabled_group["id"], {"enabled": False}, actor())

    groups = await public_pricing.list_public_groups(server.db)
    names = [g["name"] for g in groups]
    assert "Cup Inject Premium" in names
    assert "Lazy Visible" in names
    assert "Should Hide" not in names
    assert "Disabled Pricing" not in names
    assert "Internal Cup Name" not in names

    cup = next(g for g in groups if g["public_id"] == visible["public_id"])
    assert cup["slug"] == "cup-inject"
    assert cup["category"]["name"] == "Cup"
    assert cup["min_unit_price"] == 2100
    assert cup["variant_count"] == 2
    assert all(v["display_name"] for v in cup["variants"])
    assert PRIVATE_KEYS.isdisjoint(set(all_keys(cup)))
    serialized = json.dumps(cup)
    assert visible["id"] not in serialized
    assert version["id"] not in serialized
    assert "accurate_item_id" not in serialized
    assert "pricing_group_id" not in serialized
    assert "20oz Hidden" not in serialized


@pytest.mark.anyio
async def test_detail_structured_gallery_slug_and_legacy_fallback():
    group, version = await publish_group(
        "Bowl Family",
        ("aid-b1", "aid-b2"),
        labels={"aid-b1": "Bowl 500", "aid-b2": "Bowl 750"},
    )
    await pc.upsert_presentation_group(
        server.db,
        group["id"],
        {
            "public_name": "Bowl Premium",
            "slug": "bowl-premium",
            "meta_title": "Bowl Meta",
            "meta_description": "Bowl desc",
            "visible": True,
        },
        actor(),
    )
    await pc.upsert_presentation_variant(
        server.db,
        group["id"],
        "aid-b1",
        {"size": "500ml", "color": "Putih", "finishing": "Glossy"},
        actor(),
    )
    await pc.upsert_presentation_variant(
        server.db,
        group["id"],
        "aid-b2",
        {"size": "750ml", "color": "Hitam", "finishing": "Matte"},
        actor(),
    )
    legacy_file = str(uuid.uuid4())
    await server.db[cp.GROUPS].update_one(
        {"id": group["id"]},
        {
            "$set": {
                "product_images.aid-b2": {
                    "file_id": legacy_file,
                    "content_type": "image/jpeg",
                    "filename": "legacy.jpg",
                }
            }
        },
    )
    img_id = str(uuid.uuid4())
    await server.db[pc.CATALOG_IMAGES].insert_one(
        {
            "id": img_id,
            "pricing_group_id": group["id"],
            "accurate_item_id": "aid-b1",
            "role": "FRONT",
            "display_order": 2,
            "is_default": True,
            "enabled": True,
            "file_id": str(uuid.uuid4()),
            "content_type": "image/jpeg",
            "filename": "front.jpg",
        }
    )
    await server.db[pc.CATALOG_IMAGES].insert_one(
        {
            "id": str(uuid.uuid4()),
            "pricing_group_id": group["id"],
            "accurate_item_id": "aid-b1",
            "role": "TOP",
            "display_order": 1,
            "is_default": False,
            "enabled": False,
            "file_id": str(uuid.uuid4()),
            "content_type": "image/jpeg",
            "filename": "disabled.jpg",
        }
    )
    await server.db[pc.CATALOG_IMAGES].insert_one(
        {
            "id": str(uuid.uuid4()),
            "pricing_group_id": group["id"],
            "accurate_item_id": "aid-b1",
            "role": "SIDE",
            "display_order": 3,
            "is_default": False,
            "enabled": True,
            "file_id": str(uuid.uuid4()),
            "content_type": "image/jpeg",
            "filename": "side.jpg",
        }
    )

    detail = await public_pricing.get_public_group(server.db, group["public_id"])
    assert detail["name"] == "Bowl Premium"
    assert detail["meta_title"] == "Bowl Meta"
    assert detail["contact"]["public_whatsapp_phone"] == "6281234567890"
    assert detail["variants"][0]["minimum_quantity"] == 1000
    assert detail["variants"][0]["size"] == "500ml"
    v1 = next(v for v in detail["variants"] if "500ml" in (v.get("display_name") or "") or v.get("size") == "500ml")
    assert [img["role"] for img in v1["images"]] == ["FRONT", "SIDE"]
    assert v1["images"][0]["is_default"] is True
    assert v1["image_url"].endswith(f"/gallery/{img_id}/image")
    v2 = next(v for v in detail["variants"] if v.get("size") == "750ml")
    assert v2["image_url"].endswith(f"/variants/{v2['public_variant_id']}/image")
    assert PRIVATE_KEYS.isdisjoint(set(all_keys(detail)))
    blob = json.dumps(detail)
    assert group["id"] not in blob
    assert "accurate_item_id" not in blob
    assert "pricing_group_id" not in blob
    assert legacy_file not in blob

    by_slug = await public_pricing.get_public_group_by_slug(server.db, "bowl-premium")
    assert by_slug["public_id"] == group["public_id"]

    with pytest.raises(HTTPException) as missing:
        await public_pricing.get_public_group_by_slug(server.db, "missing-slug")
    assert missing.value.status_code == 404

    await pc.upsert_presentation_group(
        server.db,
        group["id"],
        {"visible": False, "slug": "bowl-premium"},
        actor(),
    )
    with pytest.raises(HTTPException) as hidden:
        await public_pricing.get_public_group_by_slug(server.db, "bowl-premium")
    assert hidden.value.status_code == 404


@pytest.mark.anyio
async def test_http_routes_home_detail_slug_calculate_privacy():
    group, version = await publish_group(
        "Route Cup", ("aid-route",), labels={"aid-route": "Route 12oz"}
    )
    await pc.upsert_presentation_group(
        server.db,
        group["id"],
        {"slug": "route-cup", "public_name": "Route Cup Public"},
        actor(),
    )
    variant_id = version["products"][0]["public_variant_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        home = await client.get("/api/public/catalog")
        by_id = await client.get(
            f"/api/public/customer-pricing/groups/{group['public_id']}"
        )
        by_slug = await client.get(
            "/api/public/customer-pricing/groups/by-slug/route-cup"
        )
        calc = await client.post(
            "/api/public/customer-pricing/calculate",
            json={
                "group_public_id": group["public_id"],
                "public_variant_id": variant_id,
                "quantity": 5000,
            },
        )
        unknown = await client.get(
            "/api/public/customer-pricing/groups/by-slug/does-not-exist"
        )
    assert home.status_code == 200
    assert by_id.status_code == 200
    assert by_slug.status_code == 200
    assert calc.status_code == 200
    assert unknown.status_code == 404
    assert by_id.json()["group"]["name"] == "Route Cup Public"
    assert by_slug.json()["group"]["public_id"] == group["public_id"]
    assert calc.json()["unit_price"] == 2100
    assert calc.json()["total"] == 5000 * 2100
    for response in (home, by_id, by_slug, calc):
        body = response.json()
        assert PRIVATE_KEYS.isdisjoint(set(all_keys(body)))
        text = json.dumps(body)
        assert group["id"] not in text
        assert "accurate_item_id" not in text
        assert "pricing_group_id" not in text
        assert "INTERNAL_ONLY" not in text
