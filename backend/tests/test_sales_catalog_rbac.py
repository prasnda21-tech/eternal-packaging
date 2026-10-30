"""Sales catalog + any-of package gate + effective permissions consistency."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("DB_NAME", "eternal_sales_catalog_rbac_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user, user_permissions
from services.purchasing_inventory_access import (
    PACKAGE_PERMISSIONS,
    any_of_satisfied_without_package,
    filter_package_permissions,
    split_package_permission_args,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db(monkeypatch):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    await database.master_items.insert_one(
        {
            "id": "mi-1",
            "accurate_item_id": "A1",
            "item_code": "SKU-1",
            "item_name": "Lid PET 99",
            "unit": "pcs",
            "is_active": True,
            "production_process": "print",
            "item_type": "INVENTORY",
        }
    )
    yield
    app.dependency_overrides.pop(current_user, None)
    monkeypatch.delenv("APP_ENV", raising=False)
    await client.drop_database(database.name)
    client.close()


def _user(role: str, perms: list[str] | None = None, **extra):
    doc = {
        "id": f"u-{role}",
        "name": role,
        "email": f"{role}@test.local",
        "role": role,
        "status": "active",
        "permissions_override": {},
        **extra,
    }
    return doc, perms


async def _as(role: str, perms: list[str] | None = None, **extra):
    user, _ = _user(role, perms, **extra)
    if perms is not None:
        await server.db.role_permissions.update_one(
            {"role": role},
            {"$set": {"role": role, "permissions": list(perms)}},
            upsert=True,
        )
    await server.db.users.update_one(
        {"email": user["email"]},
        {"$set": user},
        upsert=True,
    )
    app.dependency_overrides[current_user] = lambda: {
        "id": user["id"],
        "user_id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": role,
        "permissions_override": user.get("permissions_override") or {},
        "_package_confirmed_keys": list(user.get("package_production_confirmed_keys") or []),
    }
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_split_and_any_of_helpers():
    non_pkg, pkg = split_package_permission_args(
        ["po.edit", "master_items.view", "sales_order.create"]
    )
    assert non_pkg == ("po.edit", "sales_order.create")
    assert pkg == ("master_items.view",)
    assert any_of_satisfied_without_package(
        ["master_items.view", "po.edit"], {"po.edit", "po.view"}
    )
    assert not any_of_satisfied_without_package(
        ["master_items.view", "po.edit"], {"po.view"}
    )


@pytest.mark.anyio
async def test_crm_po_edit_sales_catalog_ok(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["po.view", "po.edit"]) as client:
        r = await client.get("/api/sales/catalog/products", params={"q": "Lid"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items and items[0]["item_code"] == "SKU-1"
        assert "production_process" in items[0]
        # Safe catalog — no inventory valuation fields.
        assert "hpp" not in items[0]
        assert "quantity" not in items[0]


@pytest.mark.anyio
async def test_crm_sales_order_create_sales_catalog_ok(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["sales_order.create", "po.view"]) as client:
        r = await client.get("/api/sales/catalog/products", params={"q": "SKU"})
        assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_crm_without_sales_catalog_perms_denied(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["po.view"]) as client:
        r = await client.get("/api/sales/catalog/products", params={"q": "Lid"})
        assert r.status_code == 403, r.text


@pytest.mark.anyio
async def test_crm_admin_master_items_list_denied(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["po.edit", "sales_order.create", "master_items.view"]) as client:
        # Stored master_items.view is stripped from effective; full catalog denied.
        r = await client.get("/api/master-items")
        assert r.status_code == 403, r.text
        assert "master_items.view" in (r.json().get("detail") or "")


@pytest.mark.anyio
async def test_crm_stock_hpp_still_denied(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as(
        "crm",
        ["po.edit", "current_stock.view", "purchase_hpp.view"],
    ) as client:
        stock = await client.get("/api/physical-stock")
        assert stock.status_code == 403, stock.text
        hpp = await client.get("/api/pembelian/hpp")
        assert hpp.status_code == 403, hpp.text


@pytest.mark.anyio
async def test_holding_package_still_ok(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as(
        "holding_team",
        ["master_items.view", "purchase_hpp.view", "current_stock.view", "nav.purchasing.hpp"],
    ) as client:
        picker = await client.get("/api/master-items", params={"picker": True, "q": "Lid"})
        assert picker.status_code == 200, picker.text
        hpp = await client.get("/api/pembelian/hpp")
        assert hpp.status_code == 200, hpp.text


@pytest.mark.anyio
async def test_mixed_any_of_picker_allows_po_edit(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["po.edit"]) as client:
        r = await client.get("/api/master-items", params={"picker": True, "q": "Lid"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"]
        # Sales-safe projection (no sync metadata required).
        assert set(body["items"][0]).issubset(
            {
                "id",
                "accurate_item_id",
                "item_code",
                "item_name",
                "unit",
                "is_active",
                "production_process",
            }
        )


@pytest.mark.anyio
async def test_package_only_any_of_still_hard_gated(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    async with await _as("crm", ["master_items.view"]) as client:
        r = await client.get("/api/master-items")
        assert r.status_code == 403, r.text
        assert "master_items.view" in (r.json().get("detail") or "")


@pytest.mark.anyio
async def test_permissions_effective_matches_auth_me_package_policy(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    await server.db.role_permissions.update_one(
        {"role": "crm"},
        {
            "$set": {
                "role": "crm",
                "permissions": ["po.view", "po.edit", "master_items.view", "purchase_hpp.view"],
                "package_production_confirmed_keys": ["master_items.view", "purchase_hpp.view"],
            }
        },
        upsert=True,
    )
    await server.db.users.update_one(
        {"email": "crm@test.local"},
        {
            "$set": {
                "id": "u-crm",
                "email": "crm@test.local",
                "role": "crm",
                "status": "active",
                "name": "CRM",
                "permissions_override": {},
            }
        },
        upsert=True,
    )
    # Admin viewer for effective endpoint
    await server.db.users.update_one(
        {"email": "admin@test.local"},
        {
            "$set": {
                "id": "u-admin",
                "email": "admin@test.local",
                "role": "admin",
                "status": "active",
                "name": "Admin",
                "permissions_override": {},
            }
        },
        upsert=True,
    )
    await server.db.role_permissions.update_one(
        {"role": "admin"},
        {"$set": {"role": "admin", "permissions": ["users.view", "po.view"]}},
        upsert=True,
    )

    crm_user = {
        "role": "crm",
        "email": "crm@test.local",
        "permissions_override": {},
        "_package_confirmed_keys": ["master_items.view"],
    }
    me_perms = await user_permissions(crm_user)
    assert "po.edit" in me_perms
    assert "master_items.view" in me_perms
    assert "purchase_hpp.view" in me_perms

    app.dependency_overrides[current_user] = lambda: {
        "id": "u-admin",
        "email": "admin@test.local",
        "role": "admin",
        "permissions_override": {},
    }
    # users.view may be stripped for admin in production if only in role doc —
    # use super path: grant via override and local package? Admin production loses
    # package but users.view is not package. Ensure role has users.view.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # admin role perms above may not include users.view in effective if
        # ROLE_DEFAULT for admin is ALL locally but production filters package only.
        # Force override on the dependency user_permissions path via role doc.
        await server.db.role_permissions.update_one(
            {"role": "admin"},
            {"$set": {"permissions": ["users.view", "permissions.manage", "po.view"]}},
        )
        eff = await client.get("/api/admin/users/u-crm/permissions/effective")
        assert eff.status_code == 200, eff.text
        body = eff.json()
        assert body["package_access"] is False
        assert "po.edit" in body["effective"]
        assert "master_items.view" in body["effective"]
        assert "purchase_hpp.view" in body["effective"]
        # Stored role defaults may still list package keys.
        assert "master_items.view" in body["default_from_role"]
        assert set(body["effective"]) == set(me_perms)


def test_confirm_stamp_does_not_create_pseudo_access():
    stored = {"po.edit", "master_items.view", "purchase_hpp.view"}
    unreviewed = filter_package_permissions(
        stored,
        {"role": "crm", "permissions_override": {"master_items.view": True}},
        "production",
        "mongodb://prod:27017",
    )
    assert unreviewed == {"po.edit"}
    approved = filter_package_permissions(
        stored,
        {"role": "crm", "permissions_override": {"master_items.view": True}},
        "production",
        "mongodb://prod:27017",
        role_confirmed_keys=["master_items.view"],
    )
    assert approved == {"po.edit", "master_items.view"}
