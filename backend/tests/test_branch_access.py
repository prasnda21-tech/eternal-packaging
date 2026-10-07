"""Branch / warehouse access authority tests. Isolated DB only."""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")

from services.branch_access import (  # noqa: E402
    SCOPE_ALL,
    SCOPE_ASSIGNED,
    SCOPE_YGY_FALLBACK,
    require_branch_access,
    require_warehouse_access,
    resolve_branch_access,
    resolve_stock_catalog_branch_id,
)
from services.org_locations import ensure_indexes  # noqa: E402
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_opname_sessions import create_stock_opname_session  # noqa: E402

TEST_DB = "eternal_packaging_test_branch_access"
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod.example.com:27017")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"].replace("prod.example.com", "127.0.0.1"))
    # Force production-like access checks via explicit app_env/mongo_url args in helpers.
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    ygy = {
        "id": "br-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
    }
    smg = {
        "id": "br-smg", "code": "SMG", "name": "Semarang", "active": True,
    }
    await database.branches.insert_many([ygy, smg])
    await database.warehouses.insert_many([
        {
            "id": "wh-ygy", "branch_id": "br-ygy", "code": "YGY-UTAMA",
            "name": "Gudang Utama Yogyakarta", "active": True, "is_operational": True,
        },
        {
            "id": "wh-smg", "branch_id": "br-smg", "code": "SMG-UTAMA",
            "name": "Gudang Utama Semarang", "active": True, "is_operational": True,
        },
    ])
    yield database
    await client.drop_database(TEST_DB)
    client.close()


PROD = {"app_env": "production", "mongo_url": "mongodb://prod.example.com:27017"}
LOCAL = {"app_env": "local", "mongo_url": "mongodb://127.0.0.1:27017"}


async def test_local_admin_all_branches(db):
    admin = {"id": "a1", "email": "admin@test", "role": "admin", "is_super_admin": True}
    access = await resolve_branch_access(db, admin, **LOCAL)
    assert access["scope"] == SCOPE_ALL
    await require_branch_access(db, admin, "br-ygy", **LOCAL)
    await require_branch_access(db, admin, "br-smg", **LOCAL)


async def test_holding_team_all_branches_production(db):
    holding = {"id": "h1", "email": "holding@test", "role": "holding_team"}
    access = await resolve_branch_access(db, holding, **PROD)
    assert access["scope"] == SCOPE_ALL
    await require_branch_access(db, holding, "br-ygy", **PROD)
    await require_branch_access(db, holding, "br-smg", **PROD)


async def test_ygy_user_denied_smg(db):
    user = {"id": "u1", "email": "ygy@test", "role": "sales", "branch_ids": ["br-ygy"]}
    access = await resolve_branch_access(db, user, **PROD)
    assert access["scope"] == SCOPE_ASSIGNED
    await require_branch_access(db, user, "br-ygy", **PROD)
    with pytest.raises(HTTPException) as exc:
        await require_branch_access(db, user, "br-smg", **PROD)
    assert exc.value.status_code == 403


async def test_smg_user_denied_ygy(db):
    user = {"id": "u2", "email": "smg@test", "role": "sales", "branch_ids": ["br-smg"]}
    await require_branch_access(db, user, "br-smg", **PROD)
    with pytest.raises(HTTPException) as exc:
        await require_branch_access(db, user, "br-ygy", **PROD)
    assert exc.value.status_code == 403


async def test_unassigned_uses_explicit_ygy_fallback(db):
    user = {"id": "u3", "email": "ops@test", "role": "operator"}
    access = await resolve_branch_access(db, user, **PROD)
    assert access["scope"] == SCOPE_YGY_FALLBACK
    assert access["ygy_fallback_applied"] is True
    await require_branch_access(db, user, "br-ygy", **PROD)
    with pytest.raises(HTTPException):
        await require_branch_access(db, user, "br-smg", **PROD)


async def test_foreign_warehouse_denied(db):
    user = {"id": "u1", "email": "ygy@test", "role": "sales", "branch_ids": ["br-ygy"]}
    await require_warehouse_access(db, user, "wh-ygy", **PROD)
    with pytest.raises(HTTPException) as exc:
        await require_warehouse_access(db, user, "wh-smg", **PROD)
    assert exc.value.status_code == 403


async def test_stock_opname_cross_branch_denied(db):
    user = {"id": "u1", "email": "ygy@test", "role": "sales", "branch_ids": ["br-ygy"]}
    with pytest.raises(HTTPException) as exc:
        await create_stock_opname_session(
            db,
            {"branch_id": "br-smg", "warehouse_id": "wh-smg"},
            user,
        )
    assert exc.value.status_code == 403


async def test_stock_catalog_default_ygy_and_branch_switch(db):
    holding = {"id": "h1", "email": "holding@test", "role": "holding_team"}
    resolved = await resolve_stock_catalog_branch_id(db, holding, None, **PROD)
    assert resolved["branch"]["code"] == "YGY"
    assert resolved["defaulted"] is True
    switched = await resolve_stock_catalog_branch_id(db, holding, "br-smg", **PROD)
    assert switched["branch"]["id"] == "br-smg"
    ygy_user = {"id": "u1", "role": "sales", "branch_ids": ["br-ygy"]}
    with pytest.raises(HTTPException):
        await resolve_stock_catalog_branch_id(db, ygy_user, "br-smg", **PROD)


async def test_holding_global_does_not_bypass_unrelated_permission_concept(db):
    """Branch ALL scope is independent of unrelated capability grants."""
    from services.purchasing_inventory_access import HOLDING_TEAM_PACKAGE_GRANTS

    holding = {"id": "h1", "email": "holding@test", "role": "holding_team"}
    access = await resolve_branch_access(db, holding, **PROD)
    assert access["scope"] == SCOPE_ALL
    # Package grants do not include arbitrary non-package keys like users.manage
    assert "users.manage" not in HOLDING_TEAM_PACKAGE_GRANTS


async def test_direct_api_cross_branch_tamper_denied(db):
    user = {"id": "u1", "role": "sales", "branch_ids": ["br-ygy"]}
    with pytest.raises(HTTPException) as exc:
        await require_branch_access(db, user, "br-smg", **PROD)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc2:
        await require_warehouse_access(
            db, user, "wh-smg", expected_branch_id="br-ygy", **PROD,
        )
    # branch/warehouse pair mismatch → 400; pure foreign warehouse → 403
    assert exc2.value.status_code == 400
    with pytest.raises(HTTPException) as exc3:
        await require_warehouse_access(db, user, "wh-smg", **PROD)
    assert exc3.value.status_code == 403
