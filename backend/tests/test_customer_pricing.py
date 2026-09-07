"""Customer Pricing group tests, isolated from authoritative HPP collections."""
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
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG
from server import app, current_user
from services import customer_pricing as cp
from services.hpp_valuation import METHOD_AVG, METHOD_FIFO


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
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await db.customer_pricing_products.delete_many({})
    await db.master_items.delete_many({"accurate_item_id": {"$regex": "^cp-test-"}})
    await db.app_settings.delete_many({"key": "customer_pricing"})
    await cp.ensure_indexes(db)
    yield
    await db.customer_pricing_groups.delete_many({})
    await db.customer_pricing_versions.delete_many({})
    await db.customer_pricing_products.delete_many({})
    await db.master_items.delete_many({"accurate_item_id": {"$regex": "^cp-test-"}})
    await db.app_settings.delete_many({"key": "customer_pricing"})


def user(*permissions, role="staff"):
    return {
        "id": str(uuid.uuid4()),
        "email": "cp@test.local",
        "name": "CP Tester",
        "role": role,
        "permissions_override": {permission: True for permission in permissions},
        "is_active": True,
    }


async def seed_master(aid: str, name: str, *, active=True, unit="pcs"):
    doc = {
        "id": str(uuid.uuid4()),
        "accurate_item_id": aid,
        "item_code": f"CODE-{aid}",
        "item_name": name,
        "item_type": "INVENTORY",
        "unit": unit,
        "is_active": active,
    }
    await server.db.master_items.insert_one(doc)
    return doc


async def create_group_with_draft(name="Paper Cup"):
    actor = user(cp.PERM_VIEW, cp.PERM_MANAGE, cp.PERM_PUBLISH, role="admin")
    group = await cp.create_group(server.db, {"group_name": name}, actor)
    draft = await cp.create_or_get_draft(server.db, group["id"], actor)
    return actor, group, draft


def formula_payload(draft, aids, *, source=cp.HPP_MANUAL, manual_values=None,
                    profit_mode=cp.PROFIT_PERCENT, profits=(40, 25)):
    manual_values = manual_values or {}
    return {
        "revision": draft["revision"],
        "group_name": draft["group_name"],
        "pricing_mode": cp.MODE_HPP_FORMULA,
        "hpp_source": source,
        "profit_mode": profit_mode,
        "tiers": [
            {"min_qty": 1, "max_qty": 99, "profit_value": profits[0]},
            {"min_qty": 100, "max_qty": None, "profit_value": profits[1]},
        ],
        "products": [
            {"accurate_item_id": aid, "manual_hpp": manual_values.get(aid)}
            for aid in aids
        ],
    }


def manual_price_payload(draft, products, *, prices):
    return {
        "revision": draft["revision"],
        "group_name": draft["group_name"],
        "pricing_mode": cp.MODE_MANUAL_TIER,
        "tiers": [
            {"min_qty": 1, "max_qty": 99},
            {"min_qty": 100, "max_qty": None},
        ],
        "products": [
            {"accurate_item_id": aid, "tier_prices": prices[aid]}
            for aid in products
        ],
    }


def hpp_settings(method, version="mock-hpp-v1"):
    return {"method": method, "active_version": version}


def acquisitions(unit_values):
    return {
        "acquisitions": [
            {
                "remaining_qty": 10,
                "unit_hpp": value,
                "recognized_at": f"2026-01-{index + 1:02d}",
                "source_id": f"po-{index}",
                "source_line_id": f"line-{index}",
            }
            for index, value in enumerate(unit_values)
        ]
    }


@pytest.mark.anyio
async def test_permissions_unchanged():
    keys = {permission for _module, permission, _label in PERMISSION_CATALOG}
    assert {cp.PERM_VIEW, cp.PERM_MANAGE, cp.PERM_PUBLISH} <= keys
    assert cp.PERM_VIEW in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_group_contains_multiple_exact_same_name_products():
    await seed_master("cp-test-a", "Same Name")
    await seed_master("cp-test-b", "Same Name")
    actor, group, draft = await create_group_with_draft()
    saved = await cp.update_draft(
        server.db,
        draft["id"],
        formula_payload(
            draft,
            ["cp-test-a", "cp-test-b"],
            manual_values={"cp-test-a": 100, "cp-test-b": 200},
        ),
        actor,
    )
    assert [product["accurate_item_id"] for product in saved["products"]] == [
        "cp-test-a", "cp-test-b"
    ]
    assert saved["products"][0]["master_item_id"] != saved["products"][1]["master_item_id"]
    listed = await cp.list_groups(server.db)
    assert listed[0]["summary_version"]["products"][1]["display_name"] == "Same Name"
    assert group["id"] == listed[0]["id"]


@pytest.mark.anyio
async def test_duplicate_exact_product_in_group_rejected():
    await seed_master("cp-test-dup", "Duplicate")
    actor, _group, draft = await create_group_with_draft()
    with pytest.raises(HTTPException) as error:
        await cp.update_draft(
            server.db,
            draft["id"],
            formula_payload(
                draft,
                ["cp-test-dup", "cp-test-dup"],
                manual_values={"cp-test-dup": 100},
            ),
            actor,
        )
    assert error.value.status_code == 400


@pytest.mark.anyio
async def test_product_cannot_belong_to_two_active_groups():
    await seed_master("cp-test-one-group", "Reserved")
    actor, _group1, draft1 = await create_group_with_draft("Group One")
    await cp.update_draft(
        server.db,
        draft1["id"],
        formula_payload(
            draft1, ["cp-test-one-group"], manual_values={"cp-test-one-group": 100}
        ),
        actor,
    )
    _actor2, _group2, draft2 = await create_group_with_draft("Group Two")
    with pytest.raises(HTTPException) as error:
        await cp.update_draft(
            server.db,
            draft2["id"],
            formula_payload(
                draft2, ["cp-test-one-group"], manual_values={"cp-test-one-group": 120}
            ),
            actor,
        )
    assert error.value.status_code == 409


@pytest.mark.anyio
async def test_manual_hpp_per_product_and_shared_percent_independent_prices():
    aids = ["cp-test-12", "cp-test-14", "cp-test-16"]
    for aid in aids:
        await seed_master(aid, aid)
    actor, _group, draft = await create_group_with_draft()
    saved = await cp.update_draft(
        server.db,
        draft["id"],
        formula_payload(
            draft,
            aids,
            manual_values={
                "cp-test-12": 300,
                "cp-test-14": 350,
                "cp-test-16": 400,
            },
        ),
        actor,
    )
    assert [product["manual_hpp"] for product in saved["products"]] == [300, 350, 400]
    assert [product["tier_prices"][0] for product in saved["products"]] == [420, 490, 560]
    assert saved["tiers"][0]["profit_value"] == 40
    assert saved["tiers"][1]["profit_value"] == 25


@pytest.mark.anyio
async def test_shared_fixed_profit_applies_to_each_product_hpp():
    for aid in ("cp-test-fixed-a", "cp-test-fixed-b"):
        await seed_master(aid, aid)
    actor, _group, draft = await create_group_with_draft()
    saved = await cp.update_draft(
        server.db,
        draft["id"],
        formula_payload(
            draft,
            ["cp-test-fixed-a", "cp-test-fixed-b"],
            manual_values={"cp-test-fixed-a": 100, "cp-test-fixed-b": 250},
            profit_mode=cp.PROFIT_FIXED,
            profits=(50, 20),
        ),
        actor,
    )
    assert saved["products"][0]["tier_prices"] == [150, 120]
    assert saved["products"][1]["tier_prices"] == [300, 270]


@pytest.mark.anyio
async def test_auto_hpp_resolves_each_product_independently():
    for aid in ("cp-test-auto-a", "cp-test-auto-b"):
        await seed_master(aid, aid)
    actor, _group, draft = await create_group_with_draft()
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        formula_payload(
            draft, ["cp-test-auto-a", "cp-test-auto-b"], source=cp.HPP_AUTO
        ),
        actor,
    )

    async def boundary(_db, *, item_identity_key):
        return acquisitions([100] if item_identity_key == "a:cp-test-auto-a" else [200])

    with patch("services.customer_pricing.get_hpp_settings", new_callable=AsyncMock) as settings_mock, \
         patch("services.customer_pricing.query_cost_boundary", side_effect=boundary) as boundary_mock, \
         patch("services.hpp_valuation.recalculate_hpp", new_callable=AsyncMock) as recalculate:
        settings_mock.return_value = hpp_settings(METHOD_AVG)
        refreshed = await cp.refresh_auto_hpp(server.db, draft["id"], actor)
    assert [product["hpp_snapshot"] for product in refreshed["products"]] == [100, 200]
    assert [product["tier_prices"][0] for product in refreshed["products"]] == [140, 280]
    assert boundary_mock.await_count == 2
    recalculate.assert_not_called()


@pytest.mark.anyio
async def test_one_ambiguous_fifo_product_blocks_whole_group_publish():
    for aid in ("cp-test-ready", "cp-test-ambiguous"):
        await seed_master(aid, aid)
    actor, _group, draft = await create_group_with_draft()
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        formula_payload(
            draft, ["cp-test-ready", "cp-test-ambiguous"], source=cp.HPP_AUTO
        ),
        actor,
    )

    async def boundary(_db, *, item_identity_key):
        return acquisitions([100] if item_identity_key == "a:cp-test-ready" else [100, 150])

    with patch("services.customer_pricing.get_hpp_settings", new_callable=AsyncMock) as settings_mock, \
         patch("services.customer_pricing.query_cost_boundary", side_effect=boundary):
        settings_mock.return_value = hpp_settings(METHOD_FIFO)
        draft = await cp.refresh_auto_hpp(server.db, draft["id"], actor)
    assert draft["products"][0]["hpp_status"] == cp.HPP_OK
    assert draft["products"][1]["hpp_status"] == cp.HPP_AMBIGUOUS
    with pytest.raises(HTTPException) as error:
        await cp.publish_version(
            server.db, draft["id"], {"revision": draft["revision"]}, actor
        )
    assert error.value.status_code == 400
    assert (await cp.get_version(server.db, draft["id"]))["status"] == cp.STATUS_DRAFT


@pytest.mark.anyio
async def test_avg_and_fifo_snapshot_rules_remain_independent():
    with patch("services.customer_pricing.get_hpp_settings", new_callable=AsyncMock) as settings_mock, \
         patch("services.customer_pricing.query_cost_boundary", new_callable=AsyncMock) as boundary:
        settings_mock.return_value = hpp_settings(METHOD_AVG)
        boundary.return_value = acquisitions([100, 140])
        avg = await cp.resolve_auto_hpp(server.db, "cp-test-avg")
        assert avg["hpp_snapshot"] == 140

        settings_mock.return_value = hpp_settings(METHOD_FIFO)
        boundary.return_value = acquisitions([250])
        fifo = await cp.resolve_auto_hpp(server.db, "cp-test-fifo")
        assert fifo["hpp_snapshot"] == 250

        boundary.return_value = acquisitions([200, 200])
        same = await cp.resolve_auto_hpp(server.db, "cp-test-fifo-same")
        assert same["hpp_snapshot"] == 200


@pytest.mark.anyio
async def test_manual_tier_price_matrix_and_per_product_descending_validation():
    for aid in ("cp-test-matrix-a", "cp-test-matrix-b"):
        await seed_master(aid, aid)
    actor, _group, draft = await create_group_with_draft()
    saved = await cp.update_draft(
        server.db,
        draft["id"],
        manual_price_payload(
            draft,
            ["cp-test-matrix-a", "cp-test-matrix-b"],
            prices={
                "cp-test-matrix-a": [2500, 2100],
                "cp-test-matrix-b": [2800, 2400],
            },
        ),
        actor,
    )
    assert saved["products"][0]["tier_prices"] == [2500, 2100]
    assert saved["products"][1]["tier_prices"] == [2800, 2400]

    invalid = manual_price_payload(
        saved,
        ["cp-test-matrix-a", "cp-test-matrix-b"],
        prices={
            "cp-test-matrix-a": [2500, 2600],
            "cp-test-matrix-b": [2800, 2400],
        },
    )
    saved = await cp.update_draft(server.db, saved["id"], invalid, actor)
    preview = await cp.preview_version(server.db, saved["id"])
    assert not preview["valid"]
    assert any("cp-test-matrix-a" in error and "lebih mahal" in error for error in preview["errors"])
    with pytest.raises(HTTPException):
        await cp.publish_version(
            server.db, saved["id"], {"revision": saved["revision"]}, actor
        )


@pytest.mark.anyio
async def test_draft_can_add_remove_variant_and_published_snapshot_is_immutable():
    for aid in ("cp-test-history-a", "cp-test-history-b", "cp-test-history-c"):
        await seed_master(aid, aid)
    actor, group, draft = await create_group_with_draft()
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        manual_price_payload(
            draft,
            ["cp-test-history-a", "cp-test-history-b"],
            prices={
                "cp-test-history-a": [200, 180],
                "cp-test-history-b": [220, 190],
            },
        ),
        actor,
    )
    published = await cp.publish_version(
        server.db, draft["id"], {"revision": draft["revision"]}, actor
    )
    published_id = published["version"]["id"]
    with pytest.raises(HTTPException) as immutable:
        await cp.update_draft(
            server.db,
            published_id,
            {"revision": published["version"]["revision"], "products": []},
            actor,
        )
    assert immutable.value.status_code == 409

    draft2 = await cp.create_or_get_draft(server.db, group["id"], actor)
    draft2 = await cp.update_draft(
        server.db,
        draft2["id"],
        manual_price_payload(
            draft2,
            ["cp-test-history-b", "cp-test-history-c"],
            prices={
                "cp-test-history-b": [230, 200],
                "cp-test-history-c": [260, 220],
            },
        ),
        actor,
    )
    assert [product["accurate_item_id"] for product in draft2["products"]] == [
        "cp-test-history-b", "cp-test-history-c"
    ]
    frozen = await cp.get_version(server.db, published_id)
    assert [product["accurate_item_id"] for product in frozen["products"]] == [
        "cp-test-history-a", "cp-test-history-b"
    ]
    assert frozen["products"][0]["tier_prices"] == [200, 180]


@pytest.mark.anyio
async def test_inactive_product_blocks_group_publish():
    await seed_master("cp-test-inactive", "Inactive", active=False)
    actor, _group, draft = await create_group_with_draft()
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        manual_price_payload(
            draft,
            ["cp-test-inactive"],
            prices={"cp-test-inactive": [100, 90]},
        ),
        actor,
    )
    with pytest.raises(HTTPException) as error:
        await cp.publish_version(
            server.db, draft["id"], {"revision": draft["revision"]}, actor
        )
    assert error.value.status_code == 400


@pytest.mark.anyio
async def test_stale_revision_conflict():
    await seed_master("cp-test-revision", "Revision")
    actor, _group, draft = await create_group_with_draft()
    payload = manual_price_payload(
        draft, ["cp-test-revision"], prices={"cp-test-revision": [100, 90]}
    )
    await cp.update_draft(server.db, draft["id"], payload, actor)
    with pytest.raises(HTTPException) as error:
        await cp.update_draft(server.db, draft["id"], payload, actor)
    assert error.value.status_code == 409


@pytest.mark.anyio
async def test_group_http_rbac_manage_cannot_publish():
    await seed_master("cp-test-http", "HTTP")
    publisher = user(cp.PERM_VIEW, cp.PERM_MANAGE, cp.PERM_PUBLISH)
    manager = user(cp.PERM_VIEW, cp.PERM_MANAGE)
    viewer = user(cp.PERM_VIEW)
    nobody = user()
    group = await cp.create_group(server.db, {"group_name": "HTTP Group"}, publisher)
    draft = await cp.create_or_get_draft(server.db, group["id"], publisher)
    draft = await cp.update_draft(
        server.db,
        draft["id"],
        manual_price_payload(
            draft, ["cp-test-http"], prices={"cp-test-http": [500, 400]}
        ),
        publisher,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        app.dependency_overrides[current_user] = lambda: nobody
        assert (await client.get("/api/customer-pricing/groups")).status_code == 403
        app.dependency_overrides[current_user] = lambda: viewer
        assert (await client.get("/api/customer-pricing/groups")).status_code == 200
        app.dependency_overrides[current_user] = lambda: manager
        response = await client.post(
            f"/api/customer-pricing/versions/{draft['id']}/publish",
            json={"revision": draft["revision"]},
        )
        assert response.status_code == 403
        app.dependency_overrides[current_user] = lambda: publisher
        response = await client.post(
            f"/api/customer-pricing/versions/{draft['id']}/publish",
            json={"revision": draft["revision"]},
        )
        assert response.status_code == 200
        assert response.json()["version"]["status"] == cp.STATUS_PUBLISHED
    app.dependency_overrides.pop(current_user, None)


async def _membership_cache(group_id: str):
    row = await server.db.customer_pricing_groups.find_one(
        {"id": group_id}, {"_id": 0, "active_accurate_item_ids": 1}
    )
    return list((row or {}).get("active_accurate_item_ids") or [])


@pytest.mark.anyio
async def test_membership_index_excludes_empty_arrays():
    info = await server.db.customer_pricing_groups.index_information()
    spec = info[cp.ACTIVE_MEMBERSHIP_INDEX]
    assert spec["unique"] is True
    assert spec["key"] == [("active_accurate_item_ids", 1)]
    partial = dict(spec["partialFilterExpression"])
    assert partial["enabled"] is True
    exists = partial["active_accurate_item_ids.0"]
    assert dict(exists) == {"$exists": True}


@pytest.mark.anyio
async def test_ensure_indexes_keeps_old_membership_index_on_populated_collection():
    actor = user(cp.PERM_VIEW, cp.PERM_MANAGE, role="admin")
    group = await cp.create_group(server.db, {"group_name": "Populated Old Index"}, actor)
    groups = server.db.customer_pricing_groups
    await groups.drop_index(cp.ACTIVE_MEMBERSHIP_INDEX)
    await groups.create_index(
        "active_accurate_item_ids",
        unique=True,
        partialFilterExpression={"enabled": True},
        name=cp.ACTIVE_MEMBERSHIP_INDEX,
    )
    assert await groups.count_documents({}) >= 1
    installed = dict(
        (await groups.index_information())[cp.ACTIVE_MEMBERSHIP_INDEX]["partialFilterExpression"]
    )
    assert installed == {"enabled": True}
    assert "active_accurate_item_ids.0" not in installed

    await cp.ensure_indexes(server.db)

    leftover = (await groups.index_information())[cp.ACTIVE_MEMBERSHIP_INDEX]
    leftover_partial = dict(leftover["partialFilterExpression"])
    assert leftover["unique"] is True
    assert leftover["key"] == [("active_accurate_item_ids", 1)]
    assert leftover_partial == {"enabled": True}
    assert "active_accurate_item_ids.0" not in leftover_partial
    assert await groups.find_one({"id": group["id"]}, {"_id": 0, "id": 1}) == {"id": group["id"]}
    assert await _membership_cache(group["id"]) == []


@pytest.mark.anyio
async def test_multiple_enabled_empty_groups_allowed():
    actor = user(cp.PERM_VIEW, cp.PERM_MANAGE, cp.PERM_PUBLISH, role="admin")
    first = await cp.create_group(server.db, {"group_name": "Empty One"}, actor)
    second = await cp.create_group(server.db, {"group_name": "Empty Two"}, actor)
    assert await _membership_cache(first["id"]) == []
    assert await _membership_cache(second["id"]) == []


@pytest.mark.anyio
async def test_empty_draft_clears_cache_and_releases_product():
    await seed_master("cp-test-release-x", "Released")
    actor, group_a, draft_a = await create_group_with_draft("Group A Occupied")
    saved = await cp.update_draft(
        server.db,
        draft_a["id"],
        manual_price_payload(
            draft_a,
            ["cp-test-release-x"],
            prices={"cp-test-release-x": [100, 90]},
        ),
        actor,
    )
    assert await _membership_cache(group_a["id"]) == ["cp-test-release-x"]
    await cp.create_group(server.db, {"group_name": "Sibling Empty"}, actor)

    emptied = await cp.update_draft(
        server.db,
        saved["id"],
        {
            "revision": saved["revision"],
            "group_name": saved["group_name"],
            "pricing_mode": cp.MODE_MANUAL_TIER,
            "tiers": [
                {"min_qty": 1, "max_qty": 99},
                {"min_qty": 100, "max_qty": None},
            ],
            "products": [],
        },
        actor,
    )
    assert emptied["revision"] == saved["revision"] + 1
    assert emptied["products"] == []
    assert await _membership_cache(group_a["id"]) == []

    _actor_b, _group_b, draft_b = await create_group_with_draft("Group B Claim")
    claimed = await cp.update_draft(
        server.db,
        draft_b["id"],
        manual_price_payload(
            draft_b,
            ["cp-test-release-x"],
            prices={"cp-test-release-x": [110, 95]},
        ),
        actor,
    )
    assert [product["accurate_item_id"] for product in claimed["products"]] == [
        "cp-test-release-x"
    ]


@pytest.mark.anyio
async def test_published_membership_still_reserves_after_draft_removal():
    await seed_master("cp-test-published-keep", "Keep Published")
    actor, group_a, draft_a = await create_group_with_draft("Published Owner")
    draft_a = await cp.update_draft(
        server.db,
        draft_a["id"],
        manual_price_payload(
            draft_a,
            ["cp-test-published-keep"],
            prices={"cp-test-published-keep": [200, 180]},
        ),
        actor,
    )
    await cp.publish_version(
        server.db, draft_a["id"], {"revision": draft_a["revision"]}, actor
    )
    draft_b = await cp.create_or_get_draft(server.db, group_a["id"], actor)
    emptied = await cp.update_draft(
        server.db,
        draft_b["id"],
        {
            "revision": draft_b["revision"],
            "group_name": draft_b["group_name"],
            "pricing_mode": cp.MODE_MANUAL_TIER,
            "tiers": [
                {"min_qty": 1, "max_qty": 99},
                {"min_qty": 100, "max_qty": None},
            ],
            "products": [],
        },
        actor,
    )
    assert emptied["products"] == []
    assert await _membership_cache(group_a["id"]) == ["cp-test-published-keep"]

    _actor2, _group_b, other_draft = await create_group_with_draft("Other Claim")
    with pytest.raises(HTTPException) as error:
        await cp.update_draft(
            server.db,
            other_draft["id"],
            manual_price_payload(
                other_draft,
                ["cp-test-published-keep"],
                prices={"cp-test-published-keep": [210, 190]},
            ),
            actor,
        )
    assert error.value.status_code == 409
    assert "sudah digunakan kelompok Published Owner" in str(error.value.detail)


@pytest.mark.anyio
async def test_disabled_group_does_not_block_membership():
    await seed_master("cp-test-disabled-x", "Disabled Owner")
    actor, group_a, draft_a = await create_group_with_draft("Soon Disabled")
    await cp.update_draft(
        server.db,
        draft_a["id"],
        manual_price_payload(
            draft_a,
            ["cp-test-disabled-x"],
            prices={"cp-test-disabled-x": [100, 90]},
        ),
        actor,
    )
    await cp.patch_group(server.db, group_a["id"], {"enabled": False}, actor)
    _actor2, _group_b, draft_b = await create_group_with_draft("Takes Disabled Product")
    claimed = await cp.update_draft(
        server.db,
        draft_b["id"],
        manual_price_payload(
            draft_b,
            ["cp-test-disabled-x"],
            prices={"cp-test-disabled-x": [120, 100]},
        ),
        actor,
    )
    assert [product["accurate_item_id"] for product in claimed["products"]] == [
        "cp-test-disabled-x"
    ]
