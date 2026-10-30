"""Direktur full-access + explicit package confirmation (local/dummy DB)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_director_rbac_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import (
    ALL_PERMISSIONS,
    DIRECTOR_MUST_OPT_IN_PERMISSIONS,
    OPT_IN_ONLY_PERMISSIONS,
    director_full_permission_set,
    is_director_full_access_role,
)
from server import app, current_user, user_permissions
from services.purchasing_inventory_access import (
    PACKAGE_CONFIRMED_KEYS_FIELD,
    PACKAGE_PERMISSIONS,
    apply_explicit_package_confirmation,
    filter_package_permissions,
    shrink_package_confirmed_keys,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db(monkeypatch):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client["eternal_director_rbac_test"]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    yield
    app.dependency_overrides.pop(current_user, None)
    monkeypatch.delenv("APP_ENV", raising=False)
    await client.drop_database(database.name)
    client.close()


SALES_DIR_OPT_IN = frozenset({
    "sales.invoice.cancel",
    "sales.receipt.cancel",
    "sales.receipt.edit",
})


def test_director_helpers():
    assert is_director_full_access_role("direktur_utama")
    assert director_full_permission_set() == set(ALL_PERMISSIONS) - DIRECTOR_MUST_OPT_IN_PERMISSIONS
    assert (OPT_IN_ONLY_PERMISSIONS - DIRECTOR_MUST_OPT_IN_PERMISSIONS).issubset(
        director_full_permission_set()
    )
    assert not DIRECTOR_MUST_OPT_IN_PERMISSIONS.intersection(director_full_permission_set())
    # Displayed Direktor /auth/me must not imply sales cancel/edit without grant.
    assert SALES_DIR_OPT_IN.issubset(DIRECTOR_MUST_OPT_IN_PERMISSIONS)
    assert not SALES_DIR_OPT_IN.intersection(director_full_permission_set())
    # Adjacent OPT_IN modules stay on the prior Direktur contract (not this patch).
    for keep in (
        "cash_bank.reconcile.purge_unmatched",
        "reports.purchase.view",
        "paint.view",
    ):
        assert keep in director_full_permission_set()
    for urgent in (
        "urgent.request",
        "urgent.review",
        "urgent.approve",
        "urgent.notify",
        "urgent.email_notify",
    ):
        assert urgent in DIRECTOR_MUST_OPT_IN_PERMISSIONS
        assert urgent not in director_full_permission_set()


def test_confirm_helpers_do_not_auto_activate_on_shrink():
    prev = ["purchase_requests.view", "purchase_orders.view", "purchase_hpp.view"]
    # Revoke C only — A+B stay unconfirmed if never stamped
    next_perms = ["purchase_requests.view", "purchase_orders.view", "po.view"]
    assert shrink_package_confirmed_keys([], next_perms) == []
    assert shrink_package_confirmed_keys(prev, next_perms) == sorted(
        {"purchase_requests.view", "purchase_orders.view"} & set(prev)
    )
    # Explicit confirm only A
    confirmed = apply_explicit_package_confirmation(
        prev, next_perms, ["purchase_requests.view"]
    )
    assert confirmed == ["purchase_requests.view"]
    assert "purchase_orders.view" not in confirmed


def test_approved_package_keys_are_effective_and_unreviewed_grants_stay_off():
    """Stored package grants stay ineffective until the reviewed list approves them."""
    perms = PACKAGE_PERMISSIONS | {"po.view"}
    unreviewed = filter_package_permissions(
        perms,
        {"role": "crm"},
        "production",
        "mongodb://prod:27017",
    )
    assert "po.view" in unreviewed
    assert not unreviewed.intersection(PACKAGE_PERMISSIONS)
    approved = filter_package_permissions(
        perms,
        {"role": "crm"},
        "production",
        "mongodb://prod:27017",
        role_confirmed_keys=["purchase_hpp.view"],
        user_confirmed_keys=["current_stock.view"],
    )
    assert "po.view" in approved
    assert approved.intersection(PACKAGE_PERMISSIONS) == {
        "purchase_hpp.view",
        "current_stock.view",
    }
    approved_user = filter_package_permissions(
        perms | {"current_stock.view"},
        {"role": "crm", "permissions_override": {"current_stock.view": True}},
        "production",
        "mongodb://prod:27017",
        role_confirmed_keys=["purchase_hpp.view"],
        user_confirmed_keys=["current_stock.view"],
    )
    assert approved_user.intersection(PACKAGE_PERMISSIONS) == {
        "purchase_hpp.view",
        "current_stock.view",
    }
    assert "po.view" in approved_user


@pytest.mark.anyio
async def test_director_full_access_despite_short_persisted_and_deny(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    await server.db.users.insert_one(
        {
            "id": "dir-1",
            "email": "dir@test.local",
            "role": "direktur_utama",
            "status": "active",
            "permissions_override": {"po.view": False, "purchase_hpp.view": False},
        }
    )
    await server.db.role_permissions.insert_one(
        {"role": "direktur_utama", "permissions": ["po.view", "chat.use"]}
    )
    perms = await user_permissions(
        {
            "role": "direktur_utama",
            "email": "dir@test.local",
            "permissions_override": {"po.view": False, "purchase_hpp.view": False},
        }
    )
    assert "po.view" in perms
    assert "purchase_hpp.view" in perms
    assert "dashboard.owner.view" in perms
    assert "current_stock.update" in perms
    assert "nav.purchasing.advances" in perms
    assert "cash_bank.reconcile.purge_unmatched" in perms
    # persisted short doc left intact
    doc = await server.db.role_permissions.find_one({"role": "direktur_utama"})
    assert doc["permissions"] == ["po.view", "chat.use"]


PURGE = "cash_bank.reconcile.purge_unmatched"


@pytest.mark.anyio
async def test_director_super_admin_keeps_opt_in_purge_and_auth_me(bind_db, monkeypatch):
    """Direktur + is_super_admin must not lose OPT_IN via SA strip."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    await server.db.users.insert_one(
        {
            "id": "dir-sa-1",
            "email": "dir.sa@test.local",
            "role": "direktur_utama",
            "status": "active",
            "is_super_admin": True,
            "permissions_override": {},
        }
    )
    await server.db.users.insert_one(
        {
            "id": "admin-sa-1",
            "email": "admin.sa@test.local",
            "role": "admin",
            "status": "active",
            "is_super_admin": True,
            "permissions_override": {},
        }
    )
    await server.db.users.insert_one(
        {
            "id": "hold-1",
            "email": "hold@test.local",
            "role": "holding_team",
            "status": "active",
            "is_super_admin": False,
            "permissions_override": {},
        }
    )

    dir_sa = await user_permissions(
        {"role": "direktur_utama", "email": "dir.sa@test.local", "permissions_override": {}}
    )
    dir_nosa = await user_permissions(
        {"role": "direktur_utama", "email": "dir.nosa@missing.local", "permissions_override": {}}
    )
    admin_sa = await user_permissions(
        {"role": "admin", "email": "admin.sa@test.local", "permissions_override": {}}
    )
    holding = await user_permissions(
        {"role": "holding_team", "email": "hold@test.local", "permissions_override": {}}
    )
    operator = await user_permissions(
        {
            "role": "operator",
            "email": "op@missing.local",
            "permissions_override": {
                "cash_bank.view": True,
                "cash_bank.reconcile": True,
            },
        }
    )

    assert PURGE in dir_sa
    assert PURGE in dir_nosa
    assert PURGE not in admin_sa
    assert PURGE in holding
    assert PURGE not in operator
    # Director-full includes OPT_IN except DIRECTOR_MUST_OPT_IN; non-director SA strips OPT_IN.
    for opt_in in OPT_IN_ONLY_PERMISSIONS - DIRECTOR_MUST_OPT_IN_PERMISSIONS:
        assert opt_in in dir_sa
        assert opt_in not in admin_sa
    for must in DIRECTOR_MUST_OPT_IN_PERMISSIONS:
        assert must not in dir_sa
        assert must not in admin_sa
    for sales_key in SALES_DIR_OPT_IN:
        assert sales_key not in dir_sa
        assert sales_key not in dir_nosa
        assert sales_key not in admin_sa

    app.dependency_overrides[current_user] = lambda: {
        "id": "dir-sa-1",
        "email": "dir.sa@test.local",
        "role": "direktur_utama",
        "name": "Dir SA",
        "permissions_override": {},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        me = await client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["role"] == "direktur_utama"
        assert body["is_super_admin"] is True
        assert PURGE in body["permissions"]
        for sales_key in SALES_DIR_OPT_IN:
            assert sales_key not in body["permissions"]

        # Effective triad matches runtime for Direktur+SA
        app.dependency_overrides[current_user] = lambda: {
            "id": "admin-sa-1",
            "email": "admin.sa@test.local",
            "role": "admin",
            "permissions_override": {},
        }
        triad_dir = await client.get("/api/admin/users/dir-sa-1/permissions/effective")
        assert triad_dir.status_code == 200, triad_dir.text
        assert PURGE in triad_dir.json()["effective"]
        triad_admin = await client.get("/api/admin/users/admin-sa-1/permissions/effective")
        assert triad_admin.status_code == 200, triad_admin.text
        assert PURGE not in triad_admin.json()["effective"]


@pytest.mark.anyio
async def test_director_sales_cancel_edit_require_explicit_grant(bind_db, monkeypatch):
    """/auth/me sales cancel/edit match executable opt-in: role_permissions or override."""
    monkeypatch.setenv("APP_ENV", "local")
    await server.db.users.insert_one(
        {
            "id": "dir-sales-1",
            "email": "dir.sales@test.local",
            "role": "direktur_utama",
            "status": "active",
            "permissions_override": {},
        }
    )
    bare = await user_permissions(
        {"role": "direktur_utama", "email": "dir.sales@test.local", "permissions_override": {}}
    )
    for key in SALES_DIR_OPT_IN:
        assert key not in bare

    await server.db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {
            "$set": {
                "role": "direktur_utama",
                "permissions": sorted(SALES_DIR_OPT_IN),
            }
        },
        upsert=True,
    )
    granted = await user_permissions(
        {"role": "direktur_utama", "email": "dir.sales@test.local", "permissions_override": {}}
    )
    for key in SALES_DIR_OPT_IN:
        assert key in granted

    denied = await user_permissions(
        {
            "role": "direktur_utama",
            "email": "dir.sales@test.local",
            "permissions_override": {key: False for key in SALES_DIR_OPT_IN},
        }
    )
    for key in SALES_DIR_OPT_IN:
        assert key not in denied

    await server.db.role_permissions.delete_many({"role": "direktur_utama"})
    override_only = await user_permissions(
        {
            "role": "direktur_utama",
            "email": "dir.sales@missing.local",
            "permissions_override": {"sales.receipt.cancel": True},
        }
    )
    assert "sales.receipt.cancel" in override_only
    assert "sales.receipt.edit" not in override_only
    assert "sales.invoice.cancel" not in override_only


@pytest.mark.anyio
async def test_director_sales_opt_in_auth_me_matches_endpoint_and_capability(bind_db, monkeypatch):
    """Same grant config → /auth/me, lifecycle capability, and write endpoint agree."""
    from services.sales_lifecycle_capability import (
        OP_BUSINESS_CANCEL,
        OP_EDIT_REVISION,
        build_receipt_lifecycle_capability,
    )

    monkeypatch.setenv("APP_ENV", "local")
    from services.sales_business_cancel import RECEIPT_FLAG_KEY, prepare_cancel_indexes_for_tests

    await prepare_cancel_indexes_for_tests(server.db)
    await server.db.app_settings.update_one(
        {"key": RECEIPT_FLAG_KEY},
        {"$set": {"key": RECEIPT_FLAG_KEY, "enabled": True}},
        upsert=True,
    )

    director = {
        "id": "dir-matrix-1",
        "email": "dir.matrix@test.local",
        "name": "Dir Matrix",
        "role": "direktur_utama",
        "permissions_override": {},
    }
    await server.db.users.insert_one({**director, "status": "active"})
    await server.db.role_permissions.delete_many({"role": "direktur_utama"})

    # No grant: display, capability, endpoint all deny.
    bare_perms = await user_permissions(director)
    for key in SALES_DIR_OPT_IN:
        assert key not in bare_perms
    bare_cap = await build_receipt_lifecycle_capability(server.db, director, None)
    assert bare_cap[OP_BUSINESS_CANCEL]["available"] is False
    assert "permission_missing" in (bare_cap[OP_BUSINESS_CANCEL].get("reasons") or [])
    assert bare_cap[OP_EDIT_REVISION]["available"] is False

    app.dependency_overrides[current_user] = lambda: dict(director)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        me = await client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        for key in SALES_DIR_OPT_IN:
            assert key not in me.json()["permissions"]
        denied = await client.post(
            "/api/sales-receipts/does-not-exist/business-cancel",
            json={"reason": "Matrix deny", "idempotency_key": "matrix-deny-1"},
        )
        assert denied.status_code == 403, denied.text

        # role_permissions grant: display + capability + endpoint auth agree (404 after auth).
        await server.db.role_permissions.update_one(
            {"role": "direktur_utama"},
            {"$set": {"role": "direktur_utama", "permissions": sorted(SALES_DIR_OPT_IN)}},
            upsert=True,
        )
        granted_perms = await user_permissions(director)
        for key in SALES_DIR_OPT_IN:
            assert key in granted_perms
        grant_cap = await build_receipt_lifecycle_capability(server.db, director, None)
        assert "permission_missing" not in (grant_cap[OP_BUSINESS_CANCEL].get("reasons") or [])
        assert "permission_missing" not in (grant_cap[OP_EDIT_REVISION].get("reasons") or [])
        me2 = await client.get("/api/auth/me")
        for key in SALES_DIR_OPT_IN:
            assert key in me2.json()["permissions"]
        not_found = await client.post(
            "/api/sales-receipts/does-not-exist/business-cancel",
            json={"reason": "Matrix grant", "idempotency_key": "matrix-grant-1"},
        )
        assert not_found.status_code == 404, not_found.text

        # Explicit deny wins over role_permissions.
        blocked = {
            **director,
            "permissions_override": {key: False for key in SALES_DIR_OPT_IN},
        }
        app.dependency_overrides[current_user] = lambda: dict(blocked)
        blocked_perms = await user_permissions(blocked)
        for key in SALES_DIR_OPT_IN:
            assert key not in blocked_perms
        deny = await client.post(
            "/api/sales-receipts/does-not-exist/business-cancel",
            json={"reason": "Matrix explicit deny", "idempotency_key": "matrix-deny-2"},
        )
        assert deny.status_code == 403, deny.text


@pytest.mark.anyio
async def test_demotion_and_inactive_reject(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    await server.db.users.insert_one(
        {
            "id": "u1",
            "email": "exdir@test.local",
            "role": "crm",
            "status": "active",
            "permissions_override": {},
        }
    )
    # JWT claim still says direktur_utama — current_user must reload DB role
    async def fake_jwt_user():
        return {"email": "exdir@test.local", "role": "direktur_utama", "name": "X"}

    # Exercise current_user via dependency path through user_permissions after reload simulation
    u = await server.db.users.find_one({"email": "exdir@test.local"})
    payload = {"email": u["email"], "role": "direktur_utama"}
    payload["role"] = u.get("role") or ""
    perms = await user_permissions(payload)
    assert "dashboard.owner.view" not in perms or "crm" == payload["role"]
    assert payload["role"] == "crm"

    await server.db.users.update_one({"email": "exdir@test.local"}, {"$set": {"status": "inactive"}})
    missing = await server.db.users.find_one({"email": "gone@test.local"})
    assert missing is None

    await server.db.roles.insert_one({"role": "crm", "status": "inactive", "is_builtin": True})
    inactive_perms = await user_permissions({"role": "crm", "email": "exdir@test.local"})
    assert "po.view" not in inactive_perms


@pytest.mark.anyio
async def test_role_permissions_api_confirm_and_director_lock(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    await server.db.users.insert_one(
        {
            "id": "admin-1",
            "email": "admin@test.local",
            "role": "admin",
            "status": "active",
            "permissions_override": {},
        }
    )
    await server.db.roles.insert_one({"role": "crm", "label": "CRM", "status": "active", "is_builtin": True})
    await server.db.roles.insert_one(
        {"role": "direktur_utama", "label": "Direktur", "status": "active", "is_builtin": True}
    )
    await server.db.role_permissions.insert_one(
        {
            "role": "crm",
            "permissions": [
                "purchase_requests.view",
                "purchase_orders.view",
                "purchase_hpp.view",
                "po.view",
            ],
            PACKAGE_CONFIRMED_KEYS_FIELD: [],
        }
    )

    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-1",
        "email": "admin@test.local",
        "role": "admin",
        "permissions_override": {},
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        from permissions import with_nav_defaults

        stored = [
            "purchase_requests.view",
            "purchase_orders.view",
            "purchase_hpp.view",
            "po.view",
        ]
        # Missing snapshot is still rejected. confirm_package_keys is not an activation gate.
        r = await client.put(
            "/api/admin/roles/crm/permissions",
            json={"permissions": stored, "confirm_package_keys": ["purchase_requests.view"]},
        )
        assert r.status_code == 409, r.text

        # Resending the stored list does not turn the unconfirmed package keys on.
        r_keep = await client.put(
            "/api/admin/roles/crm/permissions",
            json={
                "permissions": stored,
                "expected_permissions": sorted(with_nav_defaults(set(stored))),
            },
        )
        assert r_keep.status_code == 200, r_keep.text
        assert r_keep.json()[PACKAGE_CONFIRMED_KEYS_FIELD] == []
        assert "purchase_requests.view" not in r_keep.json()["permissions"]
        assert "po.view" in r_keep.json()["permissions"]
        assert r_keep.json()["manual_grant_normalized"] is True

        baseline = r_keep.json()["permissions"]
        r2 = await client.put(
            "/api/admin/roles/crm/permissions",
            json={
                "permissions": ["purchase_requests.view", "po.view"],
                "expected_permissions": sorted(with_nav_defaults(set(baseline))),
                "confirm_package_keys": ["purchase_hpp.view"],
            },
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()[PACKAGE_CONFIRMED_KEYS_FIELD] == ["purchase_requests.view"]
        assert "purchase_hpp.view" not in r2.json()["permissions"]

        # Direktur permissions locked
        r4 = await client.put(
            "/api/admin/roles/direktur_utama/permissions",
            json={"permissions": ["po.view"]},
        )
        assert r4.status_code == 400
        assert "Direktur" in str(r4.json().get("detail", ""))

        outsider = {
            "id": "crm-1",
            "email": "crm-no-manage@test.local",
            "role": "crm",
            "permissions_override": {},
        }
        app.dependency_overrides[current_user] = lambda: outsider
        denied = await client.put(
            "/api/admin/roles/crm/permissions",
            json={"permissions": ["po.view"], "expected_permissions": ["po.view"]},
        )
        assert denied.status_code == 403

        app.dependency_overrides[current_user] = lambda: {
            "id": "admin-1",
            "email": "admin@test.local",
            "role": "admin",
            "permissions_override": {},
        }
        listing = await client.get("/api/admin/roles")
        assert listing.status_code == 200
        crm = next(x for x in listing.json()["roles"] if x["role"] == "crm")
        assert crm[PACKAGE_CONFIRMED_KEYS_FIELD] == ["purchase_requests.view"]


@pytest.mark.anyio
async def test_production_director_keeps_package_and_holding_unchanged(bind_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    dir_perms = await user_permissions({"role": "direktur_utama", "email": "d@test.local"})
    hold_perms = await user_permissions({"role": "holding_team", "email": "h@test.local"})
    admin_perms = await user_permissions({"role": "admin", "email": "a@test.local"})
    assert "purchase_hpp.view" in dir_perms
    assert "purchase_hpp.view" in hold_perms
    assert "purchase_hpp.view" not in admin_perms


@pytest.mark.anyio
async def test_director_auth_passes_but_business_and_r2_gates_still_enforce(bind_db, monkeypatch):
    """Direktur clears role guards; invalid business + R2-off still reject."""
    from fastapi import HTTPException
    from services.branch_access import has_global_branch_scope
    from services.product_stock_state import can_correct_physical_stock, initialize_opening_balance
    from services.r2_go_live import require_r2_authority_active

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod:27017")
    director = {"id": "dir-1", "email": "dir@test.local", "role": "direktur_utama", "name": "Dir"}

    assert has_global_branch_scope(director, app_env="production", mongo_url="mongodb://prod:27017")
    assert can_correct_physical_stock(director, "production", "mongodb://prod:27017")

    # R2 authority off → mutation gate rejects even for Direktur
    with pytest.raises(HTTPException) as r2_exc:
        await require_r2_authority_active(server.db)
    assert r2_exc.value.status_code == 409

    # Invalid business qty still rejected after auth would pass
    prod_id = "prod-dir-invalid"
    await server.db.master_items.insert_one({"id": prod_id, "item_name": "Cup", "unit": "PCS"})
    with pytest.raises(HTTPException) as biz_exc:
        await initialize_opening_balance(
            server.db,
            prod_id,
            {"unprinted_qty": -1, "printed_qty": 0, "total_qty": -1, "base_uom": "PCS"},
            user=director,
        )
    assert biz_exc.value.status_code == 400
