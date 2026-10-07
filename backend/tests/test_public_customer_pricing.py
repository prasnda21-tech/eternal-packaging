"""Phase 2 public Customer Pricing tests on an isolated Mongo database."""
import copy
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from services import customer_pricing_catalog as catalog
from services import customer_pricing_public as public_pricing


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def isolated_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    previous_client, previous_db = server.client, server.db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    worker = os.environ.get("PYTEST_XDIST_WORKER", "local")
    db_name = f"eternal_packaging_test_public_pricing_{worker}"
    await client.drop_database(db_name)
    server.client = client
    server.db = client[db_name]
    await cp.ensure_indexes(server.db)
    from services import public_catalog as pc

    await pc.ensure_indexes(server.db)
    await server.db.app_settings.update_one(
        {"key": pc.SETTINGS_KEY},
        {
            "$set": {
                "key": pc.SETTINGS_KEY,
                "catalog_name": "Eternal Catalog",
                "hero_title": "Kemasan Berkualitas",
                "hero_subtitle": "Harga produk terkini",
                "public_whatsapp_phone": "",
                "catalog_enabled": True,
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
        "id": "public-pricing-test-employee",
        "email": "public-pricing@test.local",
        "name": "Public Pricing Tester",
        "role": "admin",
        "permissions_override": {
            cp.PERM_VIEW: True,
            cp.PERM_MANAGE: True,
            cp.PERM_PUBLISH: True,
        },
        "is_active": True,
    }


async def seed_master(aid: str, name: str, *, active=True, unit="pcs"):
    doc = {
        "id": f"master-{aid}",
        "accurate_item_id": aid,
        "item_code": f"CODE-{aid}",
        "item_name": name,
        "item_type": "INVENTORY",
        "unit": unit,
        "is_active": active,
    }
    await server.db.master_items.insert_one(doc)
    return doc


def manual_payload(draft, products, *, tiers=None, prices=None):
    tiers = tiers or [
        {"min_qty": 1000, "max_qty": 2999},
        {"min_qty": 3000, "max_qty": 4999},
        {"min_qty": 5000, "max_qty": None},
    ]
    prices = prices or {
        aid: [2500, 2300, 2100] for aid in products
    }
    return {
        "revision": draft["revision"],
        "group_name": draft["group_name"],
        "pricing_mode": cp.MODE_MANUAL_TIER,
        "tiers": tiers,
        "products": [
            {"accurate_item_id": aid, "tier_prices": prices[aid]}
            for aid in products
        ],
    }


async def publish_manual(name="Paper Cup", products=("public-test-12oz",)):
    user = actor()
    for aid in products:
        await seed_master(aid, aid.replace("public-test-", ""))
    group = await cp.create_group(server.db, {"group_name": name}, user)
    draft = await cp.create_or_get_draft(server.db, group["id"], user)
    draft = await cp.update_draft(
        server.db, draft["id"], manual_payload(draft, products), user
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


class WriteGuardCollection:
    WRITE_METHODS = {
        "bulk_write",
        "create_index",
        "delete_many",
        "delete_one",
        "drop",
        "drop_index",
        "find_one_and_delete",
        "find_one_and_replace",
        "find_one_and_update",
        "insert_many",
        "insert_one",
        "replace_one",
        "update_many",
        "update_one",
    }

    def __init__(self, collection, writes):
        self._collection = collection
        self._writes = writes

    def __getattr__(self, name):
        if name in self.WRITE_METHODS:
            async def blocked_write(*args, **kwargs):
                self._writes.append((self._collection.name, name))
                raise AssertionError(
                    f"Public pricing attempted DB write: {self._collection.name}.{name}"
                )

            return blocked_write
        return getattr(self._collection, name)


class WriteGuardDatabase:
    def __init__(self, database):
        self._database = database
        self.writes = []

    def __getitem__(self, name):
        return WriteGuardCollection(self._database[name], self.writes)

    def __getattr__(self, name):
        return WriteGuardCollection(getattr(self._database, name), self.writes)


@pytest.mark.anyio
async def test_public_visibility_is_enabled_current_published_membership_only():
    visible_group, visible_version = await publish_manual()

    disabled_group, _disabled_version = await publish_manual(
        "Disabled Secret", ("public-test-disabled",)
    )
    await cp.patch_group(
        server.db, disabled_group["id"], {"enabled": False}, actor()
    )

    draft_group = await cp.create_group(
        server.db, {"group_name": "Draft Only Secret"}, actor()
    )
    await cp.create_or_get_draft(server.db, draft_group["id"], actor())

    groups = await public_pricing.list_public_groups(server.db)
    assert [group["name"] for group in groups] == ["Paper Cup"]
    assert groups[0]["public_id"] == visible_group["public_id"]
    assert groups[0]["variants"][0]["public_variant_id"] == (
        visible_version["products"][0]["public_variant_id"]
    )

    await seed_master("public-test-new", "16oz")
    draft2 = await cp.create_or_get_draft(server.db, visible_group["id"], actor())
    draft2 = await cp.update_draft(
        server.db,
        draft2["id"],
        manual_payload(draft2, ("public-test-new",)),
        actor(),
    )
    current = await cp.publish_version(
        server.db, draft2["id"], {"revision": draft2["revision"]}, actor()
    )
    detail = await public_pricing.get_public_group(
        server.db, visible_group["public_id"]
    )
    assert [variant["display_name"] for variant in detail["variants"]] == ["16oz"]
    assert detail["variants"][0]["public_variant_id"] == (
        current["version"]["products"][0]["public_variant_id"]
    )
    with pytest.raises(HTTPException) as old_variant:
        await public_pricing.calculate_public_price(
            server.db,
            group_public_id=visible_group["public_id"],
            public_variant_id=visible_version["products"][0]["public_variant_id"],
            quantity=5000,
        )
    assert old_variant.value.status_code == 404


@pytest.mark.anyio
async def test_public_json_serializer_excludes_every_internal_pricing_field_and_id():
    group, version = await publish_manual()
    await cp.patch_group(
        server.db,
        group["id"],
        {"public_description": "Kemasan minuman untuk kebutuhan usaha."},
        actor(),
    )
    with patch(
        "services.customer_pricing_catalog.obj_storage.put_object",
        return_value={
            "path": "eternal-packaging/private/customer-pricing-object.png",
            "size": 68,
        },
    ):
        await catalog.upload_catalog_image(
            server.db,
            group_id=group["id"],
            filename="../../catalog cup.png",
            content_type="application/octet-stream",
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 60,
            user=actor(),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        catalog_response = await client.get("/api/public/customer-pricing/groups")
        detail_response = await client.get(
            f"/api/public/customer-pricing/groups/{group['public_id']}"
        )
        calculate_response = await client.post(
            "/api/public/customer-pricing/calculate",
            json={
                "group_public_id": group["public_id"],
                "public_variant_id": version["products"][0]["public_variant_id"],
                "quantity": 5000,
            },
        )
    assert catalog_response.status_code == 200
    assert detail_response.status_code == 200
    assert calculate_response.status_code == 200
    assert catalog_response.json()["groups"][0]["public_description"].startswith(
        "Kemasan minuman"
    )
    assert catalog_response.json()["groups"][0]["image_url"] == (
        f"/api/public/customer-pricing/groups/{group['public_id']}/image"
    )
    # Public-safe category/image `id` keys are intentional exceptions.
    forbidden = {
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
        "notes",
    }
    for response in (catalog_response, detail_response, calculate_response):
        body = response.json()
        assert forbidden.isdisjoint(set(all_keys(body)))
        serialized = json.dumps(body)
        for internal_value in (
            group["id"],
            version["id"],
            version["products"][0]["accurate_item_id"],
            version["products"][0]["master_item_id"],
            actor()["id"],
            "eternal-packaging/private/customer-pricing-object.png",
        ):
            assert internal_value not in serialized


@pytest.mark.anyio
async def test_calculator_boundaries_moq_validation_cap_and_authoritative_total():
    group, version = await publish_manual()
    variant_id = version["products"][0]["public_variant_id"]
    cases = (
        (1000, 2500, 1000, 2999),
        (2999, 2500, 1000, 2999),
        (3000, 2300, 3000, 4999),
        (4999, 2300, 3000, 4999),
        (5000, 2100, 5000, None),
        (10_000_000, 2100, 5000, None),
    )
    for quantity, price, minimum, maximum in cases:
        result = await public_pricing.calculate_public_price(
            server.db,
            group_public_id=group["public_id"],
            public_variant_id=variant_id,
            quantity=quantity,
        )
        assert result["unit_price"] == price
        assert result["total"] == quantity * price
        assert result["selected_tier"] == {
            "min_qty": minimum,
            "max_qty": maximum,
        }
    with pytest.raises(HTTPException) as moq:
        await public_pricing.calculate_public_price(
            server.db,
            group_public_id=group["public_id"],
            public_variant_id=variant_id,
            quantity=999,
        )
    assert moq.value.status_code == 422
    assert "1.000 pcs" in moq.value.detail
    for invalid in (0, -1, 1.5, True, 10_000_001):
        with pytest.raises(HTTPException) as error:
            await public_pricing.calculate_public_price(
                server.db,
                group_public_id=group["public_id"],
                public_variant_id=variant_id,
                quantity=invalid,
            )
        assert error.value.status_code == 400


@pytest.mark.anyio
async def test_formula_and_manual_prices_are_frozen_and_no_hpp_lookup_occurs():
    user = actor()
    await seed_master("public-test-formula", "Formula 12oz")
    formula_group = await cp.create_group(
        server.db, {"group_name": "Formula Group"}, user
    )
    draft = await cp.create_or_get_draft(server.db, formula_group["id"], user)
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        {
            "revision": draft["revision"],
            "group_name": draft["group_name"],
            "pricing_mode": cp.MODE_HPP_FORMULA,
            "hpp_source": cp.HPP_AUTO,
            "profit_mode": cp.PROFIT_FIXED,
            "tiers": [
                {"min_qty": 1000, "max_qty": 4999, "profit_value": 50},
                {"min_qty": 5000, "max_qty": None, "profit_value": 20},
            ],
            "products": [{"accurate_item_id": "public-test-formula"}],
        },
        user,
    )
    with patch(
        "services.customer_pricing.get_hpp_settings",
        new=AsyncMock(return_value={"method": "AVG", "active_version": "frozen-v1"}),
    ), patch(
        "services.customer_pricing.query_cost_boundary",
        new=AsyncMock(
            return_value={
                "acquisitions": [
                    {
                        "remaining_qty": 10,
                        "unit_hpp": 100,
                        "recognized_at": "2026-01-01",
                        "source_id": "source",
                        "source_line_id": "line",
                    }
                ]
            }
        ),
    ):
        draft = await cp.refresh_auto_hpp(server.db, draft["id"], user)
    formula_version = (
        await cp.publish_version(
            server.db, draft["id"], {"revision": draft["revision"]}, user
        )
    )["version"]
    manual_group, manual_version = await publish_manual(
        "Manual Group", ("public-test-manual",)
    )
    await server.db.hpp_cost_layers.insert_one(
        {"id": "sentinel", "unit_hpp": 999999}
    )

    with patch(
        "services.customer_pricing.get_hpp_settings",
        new=AsyncMock(side_effect=AssertionError("live HPP settings called")),
    ) as get_settings, patch(
        "services.customer_pricing.query_cost_boundary",
        new=AsyncMock(side_effect=AssertionError("live HPP boundary called")),
    ) as query_boundary, patch(
        "services.hpp_valuation.recalculate_hpp",
        new=AsyncMock(side_effect=AssertionError("HPP recalculation called")),
    ) as recalculate:
        formula_result = await public_pricing.calculate_public_price(
            server.db,
            group_public_id=formula_group["public_id"],
            public_variant_id=formula_version["products"][0]["public_variant_id"],
            quantity=5000,
        )
        manual_result = await public_pricing.calculate_public_price(
            server.db,
            group_public_id=manual_group["public_id"],
            public_variant_id=manual_version["products"][0]["public_variant_id"],
            quantity=5000,
        )
    assert formula_result["unit_price"] == 120
    assert manual_result["unit_price"] == 2100
    get_settings.assert_not_awaited()
    query_boundary.assert_not_awaited()
    recalculate.assert_not_awaited()
    assert await server.db.hpp_cost_layers.find_one(
        {"id": "sentinel"}, {"_id": 0}
    ) == {"id": "sentinel", "unit_hpp": 999999}


@pytest.mark.anyio
async def test_public_groups_detail_and_calculate_are_pure_reads():
    group, version = await publish_manual()
    with patch(
        "services.customer_pricing_catalog.obj_storage.put_object",
        return_value={
            "path": "eternal-packaging/customer-pricing/test/catalog.png",
            "size": 68,
        },
    ):
        await catalog.upload_catalog_image(
            server.db,
            group_id=group["id"],
            filename="catalog.png",
            content_type="image/png",
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 60,
            user=actor(),
        )
    real_db = server.db
    published_before = copy.deepcopy(
        await real_db[cp.VERSIONS].find_one({"id": version["id"]})
    )
    group_before = copy.deepcopy(
        await real_db[cp.GROUPS].find_one({"id": group["id"]})
    )
    guard = WriteGuardDatabase(real_db)
    server.db = guard
    request = {
        "group_public_id": group["public_id"],
        "public_variant_id": version["products"][0]["public_variant_id"],
        "quantity": 5000,
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            groups_response = await client.get(
                "/api/public/customer-pricing/groups"
            )
            assert groups_response.status_code == 200
            assert guard.writes == []
            assert await real_db[cp.VERSIONS].find_one(
                {"id": version["id"]}
            ) == published_before

            detail_response = await client.get(
                f"/api/public/customer-pricing/groups/{group['public_id']}"
            )
            assert detail_response.status_code == 200
            assert guard.writes == []
            assert await real_db[cp.VERSIONS].find_one(
                {"id": version["id"]}
            ) == published_before

            with patch(
                "services.customer_pricing_catalog.obj_storage.get_object",
                return_value=(
                    b"\x89PNG\r\n\x1a\n" + b"\x00" * 60,
                    "image/png",
                ),
            ):
                image_response = await client.get(
                    f"/api/public/customer-pricing/groups/{group['public_id']}/image"
                )
            assert image_response.status_code == 200
            assert image_response.headers["content-type"].startswith("image/png")
            assert image_response.headers["x-content-type-options"] == "nosniff"
            assert "public" in image_response.headers["cache-control"]
            assert guard.writes == []
            assert await real_db[cp.VERSIONS].find_one(
                {"id": version["id"]}
            ) == published_before

            calculate_response = await client.post(
                "/api/public/customer-pricing/calculate", json=request
            )
            assert calculate_response.status_code == 200
            assert guard.writes == []
            assert await real_db[cp.VERSIONS].find_one(
                {"id": version["id"]}
            ) == published_before
    finally:
        server.db = real_db
    assert await real_db[cp.GROUPS].find_one({"id": group["id"]}) == group_before
    assert await real_db[cp.VERSIONS].find_one(
        {"id": version["id"]}
    ) == published_before


@pytest.mark.anyio
async def test_catalog_image_manage_permission_validation_and_snapshot_immutability():
    group, version = await publish_manual()
    version_before = copy.deepcopy(
        await server.db[cp.VERSIONS].find_one({"id": version["id"]})
    )
    manager = actor()
    viewer = {
        **actor(),
        "role": "test-viewer",
        "permissions_override": {cp.PERM_VIEW: True},
    }
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
    storage_path = (
        f"{catalog.obj_storage.APP_NAME}/customer-pricing/"
        f"{group['id']}/server-generated.png"
    )

    app.dependency_overrides[server.current_user] = lambda: manager
    try:
        with patch(
            "services.customer_pricing_catalog.obj_storage.put_object",
            return_value={"path": storage_path, "size": len(png)},
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                uploaded = await client.post(
                    f"/api/customer-pricing/groups/{group['id']}/catalog-image",
                    files={"file": ("../cup premium.png", png, "image/png")},
                )
        assert uploaded.status_code == 200
        image_meta = uploaded.json()["group"]["catalog_image"]
        assert image_meta["content_type"] == "image/png"
        assert image_meta["original_filename"] == "cup_premium.png"
        assert "storage_path" not in json.dumps(uploaded.json())

        app.dependency_overrides[server.current_user] = lambda: viewer
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/catalog-image",
                files={"file": ("cup.png", png, "image/png")},
            )
        assert denied.status_code == 403

        app.dependency_overrides[server.current_user] = lambda: manager
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            invalid = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/catalog-image",
                files={"file": ("payload.exe", b"MZ\x90\x00not-an-image", "image/png")},
            )
            oversized = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/catalog-image",
                files={
                    "file": (
                        "large.png",
                        png + b"\x00" * catalog.MAX_CATALOG_IMAGE_BYTES,
                        "image/png",
                    )
                },
            )
        assert invalid.status_code == 400
        assert oversized.status_code == 400
        assert await server.db[cp.VERSIONS].find_one(
            {"id": version["id"]}
        ) == version_before

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            deleted = await client.delete(
                f"/api/customer-pricing/groups/{group['id']}/catalog-image"
            )
        assert deleted.status_code == 200
        assert deleted.json()["group"]["catalog_image"] is None
        assert await server.db[cp.VERSIONS].find_one(
            {"id": version["id"]}
        ) == version_before
    finally:
        app.dependency_overrides.pop(server.current_user, None)


@pytest.mark.anyio
async def test_public_catalog_image_missing_or_ineligible_returns_safe_404():
    group, _version = await publish_manual()
    disabled, _disabled_version = await publish_manual(
        "Hidden Image Group", ("public-test-hidden-image",)
    )
    await cp.patch_group(server.db, disabled["id"], {"enabled": False}, actor())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get(
            f"/api/public/customer-pricing/groups/{group['public_id']}/image"
        )
        hidden = await client.get(
            f"/api/public/customer-pricing/groups/{disabled['public_id']}/image"
        )
    assert missing.status_code == 404
    assert hidden.status_code == 404
    assert "storage_path" not in missing.text
    assert "storage_path" not in hidden.text


@pytest.mark.anyio
async def test_calculate_http_is_anonymous_strict_no_store_and_has_zero_side_effects():
    group, version = await publish_manual()
    sensitive_collections = (
        "quotations",
        "orders",
        "purchase_orders",
        "sales_activities",
        "item_stock_manual",
        "production_records",
        "hpp_cost_layers",
    )
    for collection in sensitive_collections:
        await server.db[collection].insert_one(
            {"id": f"sentinel-{collection}", "value": collection}
        )
    before = {
        collection: await server.db[collection].find_one(
            {"id": f"sentinel-{collection}"}, {"_id": 0}
        )
        for collection in sensitive_collections
    }
    request = {
        "group_public_id": group["public_id"],
        "public_variant_id": version["products"][0]["public_variant_id"],
        "quantity": 5000,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/public/customer-pricing/calculate", json=request
        )
        internal = await client.get("/api/customer-pricing/groups")
        supplied_price = await client.post(
            "/api/public/customer-pricing/calculate",
            json={**request, "unit_price": 1, "total": 1, "hpp": 1, "profit": 1},
        )
        non_integer = await client.post(
            "/api/public/customer-pricing/calculate",
            json={**request, "quantity": 5000.5},
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["unit_price"] == 2100
    assert response.json()["total"] == 10_500_000
    assert internal.status_code in {401, 403}
    assert supplied_price.status_code == 422
    assert non_integer.status_code == 422
    after = {
        collection: await server.db[collection].find_one(
            {"id": f"sentinel-{collection}"}, {"_id": 0}
        )
        for collection in sensitive_collections
    }
    assert after == before


@pytest.mark.anyio
async def test_public_variant_id_is_opaque_stable_and_legacy_publish_is_never_mutated():
    group, version = await publish_manual()
    first_id = version["products"][0]["public_variant_id"]
    assert first_id != version["products"][0]["accurate_item_id"]
    uuid.UUID(first_id)
    cloned = await cp.create_or_get_draft(server.db, group["id"], actor())
    assert cloned["products"][0]["public_variant_id"] == first_id

    legacy_group, legacy_version = await publish_manual(
        "Legacy Group", ("public-test-legacy",)
    )
    await server.db[cp.VERSIONS].update_one(
        {"id": legacy_version["id"]},
        {"$unset": {"products.0.public_variant_id": ""}},
    )
    immutable_legacy = copy.deepcopy(
        await server.db[cp.VERSIONS].find_one({"id": legacy_version["id"]})
    )

    groups = await public_pricing.list_public_groups(server.db)
    assert legacy_group["public_id"] not in {
        listed["public_id"] for listed in groups
    }
    with pytest.raises(HTTPException) as unavailable:
        await public_pricing.get_public_group(
            server.db, legacy_group["public_id"]
        )
    assert unavailable.value.status_code == 404
    assert await server.db[cp.VERSIONS].find_one(
        {"id": legacy_version["id"]}
    ) == immutable_legacy

    legacy_draft = await cp.create_or_get_draft(
        server.db, legacy_group["id"], actor()
    )
    assert "public_variant_id" not in legacy_draft["products"][0]
    legacy_draft = await cp.update_draft(
        server.db,
        legacy_draft["id"],
        manual_payload(legacy_draft, ("public-test-legacy",)),
        actor(),
    )
    new_public_variant_id = legacy_draft["products"][0]["public_variant_id"]
    uuid.UUID(new_public_variant_id)
    assert await server.db[cp.VERSIONS].find_one(
        {"id": legacy_version["id"]}
    ) == immutable_legacy

    republished = await cp.publish_version(
        server.db,
        legacy_draft["id"],
        {"revision": legacy_draft["revision"]},
        actor(),
    )
    assert (
        republished["version"]["products"][0]["public_variant_id"]
        == new_public_variant_id
    )
    assert await server.db[cp.VERSIONS].find_one(
        {"id": legacy_version["id"]}
    ) == immutable_legacy
    detail = await public_pricing.get_public_group(
        server.db, legacy_group["public_id"]
    )
    assert detail["variants"][0]["public_variant_id"] == new_public_variant_id


@pytest.mark.anyio
async def test_product_photo_lifecycle_isolation_and_public_delivery():
    manager = actor()
    viewer = {
        **actor(),
        "role": "test-viewer",
        "permissions_override": {cp.PERM_VIEW: True},
    }
    png1 = b"\x89PNG\r\n\x1a\n" + b"\x01" * 60
    png2 = b"\x89PNG\r\n\x1a\n" + b"\x02" * 60
    cover_png = b"\x89PNG\r\n\x1a\n" + b"\x09" * 60

    aid1 = "public-test-prod-1"
    aid2 = "public-test-prod-2"
    # Both products have the EXACT same name to verify stable identity isolation
    await seed_master(aid1, "Kardus Custom")
    await seed_master(aid2, "Kardus Custom")

    group = await cp.create_group(server.db, {"group_name": "Box Group"}, manager)
    draft = await cp.create_or_get_draft(server.db, group["id"], manager)
    draft = await cp.update_draft(
        server.db, draft["id"], manual_payload(draft, (aid1, aid2)), manager
    )
    published = await cp.publish_version(
        server.db, draft["id"], {"revision": draft["revision"]}, manager
    )
    version = published["version"]
    version_before = copy.deepcopy(
        await server.db[cp.VERSIONS].find_one({"id": version["id"]})
    )

    # Attach group cover image
    storage_cover = f"{catalog.obj_storage.APP_NAME}/customer-pricing/{group['id']}/cover.png"
    with patch(
        "services.customer_pricing_catalog.obj_storage.put_object",
        return_value={"path": storage_cover, "size": len(cover_png)},
    ):
        await catalog.upload_catalog_image(
            server.db,
            group_id=group["id"],
            filename="cover.png",
            content_type="image/png",
            data=cover_png,
            user=manager,
        )

    v1_id = version["products"][0]["public_variant_id"]
    v2_id = version["products"][1]["public_variant_id"]

    storage_p1 = f"{catalog.obj_storage.APP_NAME}/customer-pricing/{group['id']}/products/{aid1}/p1.png"
    storage_p1_v2 = f"{catalog.obj_storage.APP_NAME}/customer-pricing/{group['id']}/products/{aid1}/p1-v2.png"

    app.dependency_overrides[server.current_user] = lambda: viewer
    try:
        # 1. Unauthorized upload rejected
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image",
                files={"file": ("p1.png", png1, "image/png")},
            )
        assert denied.status_code == 403

        # 2. Validation: invalid mime & oversized rejected
        app.dependency_overrides[server.current_user] = lambda: manager
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            invalid = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image",
                files={"file": ("virus.exe", b"MZ\x90not-img", "image/png")},
            )
            oversized = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image",
                files={
                    "file": (
                        "big.png",
                        png1 + b"\x00" * catalog.MAX_CATALOG_IMAGE_BYTES,
                        "image/png",
                    )
                },
            )
            mismatched = await client.post(
                f"/api/customer-pricing/groups/{group['id']}/products/non-existent-aid/image",
                files={"file": ("p1.png", png1, "image/png")},
            )
        assert invalid.status_code == 400
        assert oversized.status_code == 400
        assert mismatched.status_code == 404

        # 3. Successful upload attaches to exact product identity (aid1)
        with patch(
            "services.customer_pricing_catalog.obj_storage.put_object",
            return_value={"path": storage_p1, "size": len(png1)},
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                upload_res = await client.post(
                    f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image",
                    files={"file": ("p1.png", png1, "image/png")},
                )
        assert upload_res.status_code == 200
        res_group = upload_res.json()["group"]
        assert aid1 in res_group["product_images"]
        assert aid2 not in res_group["product_images"]  # Same-name product 2 has NO photo
        assert res_group["product_images"][aid1]["content_type"] == "image/png"
        assert res_group["catalog_image"] is not None  # Group cover intact!

        # Published version immutability check
        assert await server.db[cp.VERSIONS].find_one({"id": version["id"]}) == version_before

        # Internal GET product image
        with patch(
            "services.customer_pricing_catalog.obj_storage.get_object",
            return_value=(png1, "image/png"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                get_p1 = await client.get(
                    f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image"
                )
                get_p2 = await client.get(
                    f"/api/customer-pricing/groups/{group['id']}/products/{aid2}/image"
                )
        assert get_p1.status_code == 200
        assert get_p1.content == png1
        assert get_p2.status_code == 404

        # 4. Public catalog listing & detail verification
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pub_groups = await client.get("/api/public/customer-pricing/groups")
            pub_detail = await client.get(f"/api/public/customer-pricing/groups/{group['public_id']}")
        assert pub_groups.status_code == 200
        assert pub_detail.status_code == 200

        group_summary = next(g for g in pub_groups.json()["groups"] if g["public_id"] == group["public_id"])
        # Group cover present on group
        assert group_summary["image_url"] == f"/api/public/customer-pricing/groups/{group['public_id']}/image"
        # Variant 1 has photo URL
        v1_summary = next(v for v in group_summary["variants"] if v["public_variant_id"] == v1_id)
        assert v1_summary["image_url"] == f"/api/public/customer-pricing/groups/{group['public_id']}/variants/{v1_id}/image"
        # Variant 2 has NO photo -> image_url is None (DILARANG fallback to group cover!)
        v2_summary = next(v for v in group_summary["variants"] if v["public_variant_id"] == v2_id)
        assert v2_summary["image_url"] is None

        # Detail response
        detail_data = pub_detail.json()["group"]
        assert detail_data["image_url"] == f"/api/public/customer-pricing/groups/{group['public_id']}/image"
        v1_detail = next(v for v in detail_data["variants"] if v["public_variant_id"] == v1_id)
        v2_detail = next(v for v in detail_data["variants"] if v["public_variant_id"] == v2_id)
        assert v1_detail["image_url"] == f"/api/public/customer-pricing/groups/{group['public_id']}/variants/{v1_id}/image"
        assert v2_detail["image_url"] is None

        # Public variant image retrieval
        with patch(
            "services.customer_pricing_catalog.obj_storage.get_object",
            return_value=(png1, "image/png"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                pub_v1_img = await client.get(
                    f"/api/public/customer-pricing/groups/{group['public_id']}/variants/{v1_id}/image"
                )
                pub_v2_img = await client.get(
                    f"/api/public/customer-pricing/groups/{group['public_id']}/variants/{v2_id}/image"
                )
        assert pub_v1_img.status_code == 200
        assert pub_v1_img.content == png1
        assert "public, max-age=300" in pub_v1_img.headers["Cache-Control"]
        assert pub_v2_img.status_code == 404

        # 5. Replace photo on product 1
        with patch(
            "services.customer_pricing_catalog.obj_storage.put_object",
            return_value={"path": storage_p1_v2, "size": len(png2)},
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                replace_res = await client.post(
                    f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image",
                    files={"file": ("p1-new.png", png2, "image/png")},
                )
        assert replace_res.status_code == 200
        assert aid1 in replace_res.json()["group"]["product_images"]
        assert aid2 not in replace_res.json()["group"]["product_images"]
        assert await server.db[cp.VERSIONS].find_one({"id": version["id"]}) == version_before

        # 6. Delete photo clears ONLY exact product photo
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            del_res = await client.delete(
                f"/api/customer-pricing/groups/{group['id']}/products/{aid1}/image"
            )
        assert del_res.status_code == 200
        assert aid1 not in del_res.json()["group"]["product_images"]
        # Group cover still intact!
        assert del_res.json()["group"]["catalog_image"] is not None
        # Version snapshot still untouched!
        assert await server.db[cp.VERSIONS].find_one({"id": version["id"]}) == version_before

        # After delete, public variant 1 returns 404
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pub_after_del = await client.get(
                f"/api/public/customer-pricing/groups/{group['public_id']}/variants/{v1_id}/image"
            )
        assert pub_after_del.status_code == 404
    finally:
        app.dependency_overrides.pop(server.current_user, None)
