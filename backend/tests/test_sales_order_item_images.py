"""Sales order product photos come from Customer Pricing ids, not names."""
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _user(role):
    return {"email": f"{role}@eternalpackaging.com", "name": role, "role": role}


@pytest.mark.anyio
async def test_item_image_uses_accurate_id_and_hides_pricing():
    import server
    from services.customer_pricing import GROUPS

    marker = uuid.uuid4().hex[:8]
    aid = f"aid-{marker}"
    other = f"other-{marker}"
    named_like = f"name-{marker}"
    group_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    master_id = str(uuid.uuid4())
    await server.db[GROUPS].insert_one({
        "id": group_id,
        "group_name": f"Cup {marker}",
        "updated_at": "2026-09-25T00:00:00+00:00",
        "active_accurate_item_ids": [aid],
        "catalog_image": {"file_id": "cover-should-not-leak", "content_type": "image/png"},
        "product_images": {
            aid: {"file_id": file_id, "content_type": "image/png"},
        },
        "products": [{
            "accurate_item_id": aid,
            "display_name": f"Cup {marker}",
            "hpp_snapshot": 999,
            "tiers": [{"min_qty": 1, "price": 700}],
        }],
    })
    await server.db[GROUPS].insert_one({
        "id": str(uuid.uuid4()),
        "group_name": f"Cup {marker}",
        "active_accurate_item_ids": [named_like],
        "catalog_image": {"file_id": "same-name-cover"},
        "product_images": {},
    })
    await server.db.master_items.insert_one({
        "id": master_id,
        "accurate_item_id": aid,
        "item_name": f"Cup {marker}",
        "is_active": True,
    })
    await server.db.files.insert_one({
        "id": file_id,
        "kind": "customer_pricing_product_image",
        "group_id": group_id,
        "accurate_item_id": aid,
        "storage_path": f"test/{file_id}.png",
        "content_type": "image/png",
        "original_filename": "cup.png",
        "is_deleted": False,
    })

    app.dependency_overrides[current_user] = lambda: _user("crm")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders/item-images", params={
                "accurate_item_ids": f"{aid},{other}",
                "master_item_ids": master_id,
            })
            assert listed.status_code == 200
            body = listed.json()
            assert set(body.keys()) == {"images"}
            assert set(body["images"].keys()) == {aid}
            record = body["images"][aid]
            assert set(record.keys()) == {"accurate_item_id", "url"}
            assert record["accurate_item_id"] == aid
            assert file_id in record["url"]
            blob = body["images"]
            assert "hpp" not in str(blob).lower()
            assert "tier" not in str(blob).lower()
            assert "700" not in str(blob)
            assert "cover-should-not-leak" not in str(blob)
            assert other not in body["images"]
            assert named_like not in body["images"]

            with patch(
                "services.customer_pricing_catalog.obj_storage.get_object",
                return_value=(b"png-bytes", "image/png"),
            ):
                image = await client.get(f"/api/sales-orders/item-images/{aid}")
            assert image.status_code == 200
            assert image.headers["content-type"].startswith("image/png")
            assert image.content == b"png-bytes"
            assert "hpp" not in image.headers.get("content-type", "")

            missing = await client.get(f"/api/sales-orders/item-images/{named_like}")
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db[GROUPS].delete_many({"group_name": f"Cup {marker}"})
        await server.db.master_items.delete_many({"id": master_id})
        await server.db.files.delete_many({"id": file_id})


@pytest.mark.anyio
async def test_item_image_requires_sales_order_permission():
    app.dependency_overrides[current_user] = lambda: _user("holding_team")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/sales-orders/item-images", params={"accurate_item_ids": "x"})
            assert denied.status_code == 403
            denied_file = await client.get("/api/sales-orders/item-images/aid-hidden")
            assert denied_file.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
