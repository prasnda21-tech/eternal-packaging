"""Branch + Warehouse foundation — isolated TEST DB."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_org_locations")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS
from server import app, current_user
from services.org_locations import (
    BRANCH_HAS_WAREHOUSE_MESSAGE,
    BRANCH_IN_USE_MESSAGE,
    BRANCHES_COLLECTION,
    PERM_BRANCH_MANAGE,
    PERM_BRANCH_VIEW,
    PERM_WAREHOUSE_MANAGE,
    PERM_WAREHOUSE_VIEW,
    WAREHOUSE_IN_USE_MESSAGE,
    WAREHOUSES_COLLECTION,
    OrgLocationConflictError,
    OrgLocationValidationError,
    create_branch,
    create_warehouse,
    delete_branch,
    delete_warehouse,
    ensure_indexes,
    get_branch,
    get_warehouse,
    list_warehouses,
    update_branch,
    update_warehouse,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_collections(bind_motor):
    db = server.db
    await ensure_indexes(db)
    await db[BRANCHES_COLLECTION].delete_many({})
    await db[WAREHOUSES_COLLECTION].delete_many({})
    yield
    await db[BRANCHES_COLLECTION].delete_many({})
    await db[WAREHOUSES_COLLECTION].delete_many({})


def _user(role="admin", **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": kw.get("permissions_override") or {},
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_permissions_catalog_contains_org_keys():
    for key in (
        PERM_BRANCH_VIEW,
        PERM_BRANCH_MANAGE,
        PERM_WAREHOUSE_VIEW,
        PERM_WAREHOUSE_MANAGE,
        "nav.inventory.branches_warehouses",
    ):
        assert key in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_create_branch():
    doc = await create_branch(server.db, {"code": "ygy", "name": " Yogyakarta "}, user=_user())
    assert doc["code"] == "YGY"
    assert doc["name"] == "Yogyakarta"
    assert doc["active"] is True
    assert doc["id"]


@pytest.mark.anyio
async def test_duplicate_branch_rejected():
    await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    with pytest.raises(OrgLocationConflictError):
        await create_branch(server.db, {"code": "YGY", "name": "Other"}, user=_user())


@pytest.mark.anyio
async def test_update_branch():
    doc = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    updated = await update_branch(server.db, doc["id"], {"name": "Yogya Baru"}, user=_user())
    assert updated["name"] == "Yogya Baru"
    assert updated["code"] == "YGY"


@pytest.mark.anyio
async def test_deactivate_reactivate_branch():
    doc = await create_branch(server.db, {"code": "SMG", "name": "Semarang"}, user=_user())
    off = await update_branch(server.db, doc["id"], {"active": False}, user=_user())
    assert off["active"] is False
    on = await update_branch(server.db, doc["id"], {"active": True}, user=_user())
    assert on["active"] is True


@pytest.mark.anyio
async def test_create_warehouse():
    branch = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    wh = await create_warehouse(
        server.db,
        {"branch_id": branch["id"], "code": "main", "name": " Gudang Yogyakarta "},
        user=_user(),
    )
    assert wh["code"] == "MAIN"
    assert wh["name"] == "Gudang Yogyakarta"
    assert wh["branch_id"] == branch["id"]
    assert wh["active"] is True


@pytest.mark.anyio
async def test_branch_with_zero_warehouse_valid():
    branch = await create_branch(server.db, {"code": "SMG", "name": "Semarang"}, user=_user())
    items = await list_warehouses(server.db, branch_id=branch["id"])
    assert items == []


@pytest.mark.anyio
async def test_multiple_warehouses_valid():
    branch = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    await create_warehouse(server.db, {"branch_id": branch["id"], "code": "MAIN", "name": "Utama"}, user=_user())
    await create_warehouse(server.db, {"branch_id": branch["id"], "code": "SECOND", "name": "Cadangan"}, user=_user())
    items = await list_warehouses(server.db, branch_id=branch["id"])
    assert len(items) == 2


@pytest.mark.anyio
async def test_duplicate_warehouse_code_same_branch_rejected():
    branch = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    await create_warehouse(server.db, {"branch_id": branch["id"], "code": "MAIN", "name": "A"}, user=_user())
    with pytest.raises(OrgLocationConflictError):
        await create_warehouse(server.db, {"branch_id": branch["id"], "code": "MAIN", "name": "B"}, user=_user())


@pytest.mark.anyio
async def test_same_warehouse_code_different_branch_accepted():
    ygy = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    smg = await create_branch(server.db, {"code": "SMG", "name": "Semarang"}, user=_user())
    a = await create_warehouse(server.db, {"branch_id": ygy["id"], "code": "MAIN", "name": "YGY Main"}, user=_user())
    b = await create_warehouse(server.db, {"branch_id": smg["id"], "code": "MAIN", "name": "SMG Main"}, user=_user())
    assert a["code"] == b["code"] == "MAIN"
    assert a["branch_id"] != b["branch_id"]


@pytest.mark.anyio
async def test_warehouse_requires_valid_branch():
    with pytest.raises(OrgLocationValidationError):
        await create_warehouse(
            server.db,
            {"branch_id": "missing-branch", "code": "MAIN", "name": "X"},
            user=_user(),
        )


@pytest.mark.anyio
async def test_update_warehouse():
    branch = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    wh = await create_warehouse(server.db, {"branch_id": branch["id"], "code": "MAIN", "name": "A"}, user=_user())
    updated = await update_warehouse(server.db, wh["id"], {"name": "Gudang Yogyakarta"}, user=_user())
    assert updated["name"] == "Gudang Yogyakarta"


@pytest.mark.anyio
async def test_deactivate_reactivate_warehouse():
    branch = await create_branch(server.db, {"code": "YGY", "name": "Yogyakarta"}, user=_user())
    wh = await create_warehouse(server.db, {"branch_id": branch["id"], "code": "MAIN", "name": "A"}, user=_user())
    off = await update_warehouse(server.db, wh["id"], {"active": False}, user=_user())
    assert off["active"] is False
    on = await update_warehouse(server.db, wh["id"], {"active": True}, user=_user())
    assert on["active"] is True


@pytest.mark.anyio
async def test_http_branch_warehouse_flow_and_no_auto_warehouse():
    async with await client_for(_user()) as client:
        r = await client.post("/api/branches", json={"code": "YGY", "name": "Yogyakarta"})
        assert r.status_code == 201, r.text
        branch = r.json()
        whs = await client.get("/api/warehouses", params={"branch_id": branch["id"]})
        assert whs.status_code == 200
        assert whs.json()["total"] == 0

        bad = await client.post("/api/branches", json={"code": "YGY", "name": "Dup"})
        assert bad.status_code == 409

        create_wh = await client.post(
            "/api/warehouses",
            json={"branch_id": branch["id"], "code": "MAIN", "name": "Gudang Yogyakarta"},
        )
        assert create_wh.status_code == 201, create_wh.text

        malformed = await client.post("/api/branches", json={"code": "", "name": "X"})
        assert malformed.status_code in (400, 422)


@pytest.mark.anyio
async def test_safe_delete_guards_do_not_cascade():
    """Temporary records only. Referenced rows and children stay in place."""
    db = server.db
    probe = "org_location_delete_probe"
    try:
        empty = await create_branch(db, {"code": "TMP", "name": "Temp Empty"}, user=_user())
        removed = await delete_branch(db, empty["id"], user=_user())
        assert removed["id"] == empty["id"]
        assert await get_branch(db, empty["id"]) is None

        parent = await create_branch(db, {"code": "PAR", "name": "Temp Parent"}, user=_user())
        child = await create_warehouse(
            db, {"branch_id": parent["id"], "code": "TMP", "name": "Temp Gudang"}, user=_user()
        )
        spare = await create_warehouse(
            db, {"branch_id": parent["id"], "code": "KEEP", "name": "Keep Gudang"}, user=_user()
        )

        deleted_wh = await delete_warehouse(db, child["id"], user=_user())
        assert deleted_wh["id"] == child["id"]
        assert await get_warehouse(db, child["id"]) is None
        assert await get_branch(db, parent["id"]) is not None
        assert await get_warehouse(db, spare["id"]) is not None

        with pytest.raises(OrgLocationConflictError, match=BRANCH_HAS_WAREHOUSE_MESSAGE):
            await delete_branch(db, parent["id"], user=_user())
        assert await get_branch(db, parent["id"]) is not None
        assert await get_warehouse(db, spare["id"]) is not None

        await db[probe].insert_one({"id": "ref-wh", "warehouse_id": spare["id"], "kind": "stock"})
        with pytest.raises(OrgLocationConflictError, match=WAREHOUSE_IN_USE_MESSAGE):
            await delete_warehouse(db, spare["id"], user=_user())
        assert await db[probe].count_documents({"warehouse_id": spare["id"]}) == 1
        assert await get_warehouse(db, spare["id"]) is not None

        lone = await create_branch(db, {"code": "REF", "name": "Temp Referenced"}, user=_user())
        await db[probe].insert_one({"id": "ref-br", "branch_id": lone["id"], "kind": "purchase"})
        with pytest.raises(OrgLocationConflictError, match=BRANCH_IN_USE_MESSAGE):
            await delete_branch(db, lone["id"], user=_user())
        assert await get_branch(db, lone["id"]) is not None
        assert await db[probe].count_documents({"branch_id": lone["id"]}) == 1
    finally:
        await db[probe].delete_many({})


@pytest.mark.anyio
async def test_http_safe_delete():
    db = server.db
    probe = "org_location_delete_probe"
    try:
        async with await client_for(_user()) as client:
            branch = (await client.post("/api/branches", json={"code": "TMP", "name": "Temp"})).json()
            wh = (await client.post(
                "/api/warehouses",
                json={"branch_id": branch["id"], "code": "TMP", "name": "Temp Gudang"},
            )).json()

            blocked = await client.delete(f"/api/branches/{branch['id']}")
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == BRANCH_HAS_WAREHOUSE_MESSAGE
            assert await get_warehouse(db, wh["id"]) is not None

            gone = await client.delete(f"/api/warehouses/{wh['id']}")
            assert gone.status_code == 200, gone.text
            assert await get_warehouse(db, wh["id"]) is None
            assert await get_branch(db, branch["id"]) is not None

            await db[probe].insert_one({"id": "http-ref", "branch_id": branch["id"]})
            used = await client.delete(f"/api/branches/{branch['id']}")
            assert used.status_code == 409
            assert used.json()["detail"] == BRANCH_IN_USE_MESSAGE
            assert await get_branch(db, branch["id"]) is not None
            await db[probe].delete_many({})

            ok = await client.delete(f"/api/branches/{branch['id']}")
            assert ok.status_code == 200, ok.text
            assert await get_branch(db, branch["id"]) is None

            other = (await client.post("/api/branches", json={"code": "OTH", "name": "Other"})).json()
            held = (await client.post(
                "/api/warehouses",
                json={"branch_id": other["id"], "code": "HOLD", "name": "Held"},
            )).json()
            await db[probe].insert_one({"id": "http-wh", "warehouse_id": held["id"], "purchase_order_id": "po-1"})
            denied = await client.delete(f"/api/warehouses/{held['id']}")
            assert denied.status_code == 409
            assert denied.json()["detail"] == WAREHOUSE_IN_USE_MESSAGE
            assert await get_warehouse(db, held["id"]) is not None
            assert await db[probe].count_documents({"id": "http-wh"}) == 1
    finally:
        await db[probe].delete_many({})
