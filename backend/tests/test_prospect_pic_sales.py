"""Outlet Prospek Owner / PIC Sales allowlist — isolated test DB only."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_sa_pic_test"
assert TEST_DB != "eternal_packaging"
assert "test" in TEST_DB

os.environ["MONGO_URL"] = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ["SALES_ACTIVITY_EVALUATOR_ENABLED"] = "0"

import httpx
import server
from server import app, current_user
from services.sales_activity.constants import (
    COL_EVENTS,
    COL_OWNERSHIP,
    COL_OUTLETS,
    COL_POOL,
    COL_ACTIVITIES,
    COL_BATCHES,
    COL_CONFLICTS,
    COL_VISIT_PHOTOS,
    KIND_PROSPECT,
)
from services.sales_activity.indexes import ensure_indexes

SALES_PERMS = {
    "sales.activity.view": True,
    "sales.activity.create": True,
    "sales.outlet.create": True,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def isolated_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = TEST_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[TEST_DB]
    assert server.db.name == TEST_DB
    await ensure_indexes(server.db)
    for name in (COL_OUTLETS, COL_ACTIVITIES, COL_OWNERSHIP, COL_EVENTS, COL_POOL, COL_BATCHES, COL_CONFLICTS, COL_VISIT_PHOTOS):
        await server.db[name].delete_many({})
    await server.db.app_settings.delete_many({"key": {"$in": ["sales_activity", "sales_activity_evaluator_lock"]}})
    await server.db.users.delete_many({})
    yield


def _user(uid, email, name, role="crm", perms=None, admin=False, status="active"):
    return {
        "id": uid,
        "user_id": uid,
        "email": email,
        "name": name,
        "role": "admin" if admin else role,
        "permissions_override": perms or {},
        "status": status,
    }


async def _seed_user(user):
    await server.db.users.insert_one({
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "status": user.get("status") or "active",
        "permissions_override": user.get("permissions_override") or {},
        "password_hash": "x",
    })


async def _client_for(user):
    await _seed_user(user)
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _close(client):
    await client.aclose()
    app.dependency_overrides.pop(current_user, None)


async def _set_pics(client, ids):
    settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
    settings["prospect_pic_user_ids"] = list(ids)
    res = await client.put("/api/sales-activity/settings", json=settings)
    assert res.status_code == 200, res.text
    return res.json()["settings"]


@pytest.mark.anyio
async def test_settings_store_prospect_pic_user_ids():
    admin = _user("u-adm", "adm@pic-test.local", "Admin", admin=True)
    a = _user("u-a", "a@pic-test.local", "PIC A")
    b = _user("u-b", "b@pic-test.local", "PIC B")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    try:
        settings = await _set_pics(client, ["u-a", "u-b"])
        assert settings["prospect_pic_user_ids"] == ["u-a", "u-b"]
        got = (await client.get("/api/sales-activity/settings")).json()["settings"]
        assert got["prospect_pic_user_ids"] == ["u-a", "u-b"]
        pics = (await client.get("/api/sales-activity/prospect-pic-users")).json()["users"]
        assert [u["id"] for u in pics] == ["u-a", "u-b"]
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_inactive_user_cannot_become_eligible_or_assigned():
    admin = _user("u-adm2", "adm2@pic-test.local", "Admin", admin=True)
    active = _user("u-act", "act@pic-test.local", "Aktif")
    inactive = _user("u-inact", "inact@pic-test.local", "Nonaktif", status="inactive")
    await _seed_user(active)
    await _seed_user(inactive)
    client = await _client_for(admin)
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["prospect_pic_user_ids"] = ["u-act", "u-inact"]
        bad = await client.put("/api/sales-activity/settings", json=settings)
        assert bad.status_code == 400
        await _set_pics(client, ["u-act"])
        await server.db.users.update_one({"id": "u-act"}, {"$set": {"status": "inactive"}})
        res = await client.post("/api/sales-activity/outlets", json={"name": "Inactive PIC", "owner_id": "u-act"})
        assert res.status_code == 400
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_create_accepts_eligible_rejects_non_eligible_and_arbitrary():
    admin = _user("u-adm3", "adm3@pic-test.local", "Admin", admin=True)
    a = _user("u-a3", "a3@pic-test.local", "PIC A")
    b = _user("u-b3", "b3@pic-test.local", "PIC B")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    try:
        await _set_pics(client, ["u-a3"])
        ok = await client.post(
            "/api/sales-activity/outlets",
            json={"name": "Prospek OK", "owner_id": "u-a3", "owner_name": "Fake Name Spoof"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["ownership"]["current_owner_id"] == "u-a3"
        assert body["ownership"]["current_owner_name"] == "PIC A"
        bad = await client.post("/api/sales-activity/outlets", json={"name": "Prospek Bad", "owner_id": "u-b3"})
        assert bad.status_code == 400
        arb = await client.post("/api/sales-activity/outlets", json={"name": "Prospek Arb", "owner_id": "u-does-not-exist"})
        assert arb.status_code == 400
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_logged_in_user_not_auto_accepted_unless_eligible():
    sales = _user("u-sales4", "sales4@pic-test.local", "Sales", perms=SALES_PERMS)
    pic = _user("u-pic4", "pic4@pic-test.local", "Eligible PIC")
    await _seed_user(pic)
    admin = _user("u-adm4", "adm4@pic-test.local", "Admin", admin=True)
    admin_client = await _client_for(admin)
    try:
        await _set_pics(admin_client, ["u-pic4"])
    finally:
        await _close(admin_client)

    client = await _client_for(sales)
    try:
        # Not eligible — must not silently assign self
        res = await client.post("/api/sales-activity/outlets", json={"name": "No Self"})
        assert res.status_code == 400
        # Eligible PIC may be selected by sales create
        ok = await client.post("/api/sales-activity/outlets", json={"name": "With PIC", "owner_id": "u-pic4"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["ownership"]["current_owner_id"] == "u-pic4"
        assert ok.json()["ownership"]["current_owner_name"] == "Eligible PIC"
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_eligible_logged_in_user_may_be_selected():
    sales = _user("u-sales5", "sales5@pic-test.local", "Sales Eligible", perms=SALES_PERMS)
    other = _user("u-other5", "other5@pic-test.local", "Other PIC")
    await _seed_user(sales)
    await _seed_user(other)
    admin = _user("u-adm5", "adm5@pic-test.local", "Admin", admin=True)
    admin_client = await _client_for(admin)
    try:
        await _set_pics(admin_client, ["u-sales5", "u-other5"])
    finally:
        await _close(admin_client)

    client = await _client_for(sales)
    try:
        res = await client.post("/api/sales-activity/outlets", json={"name": "Self OK"})
        assert res.status_code == 200, res.text
        assert res.json()["ownership"]["current_owner_id"] == "u-sales5"
        pics = (await client.get("/api/sales-activity/prospect-pic-users")).json()["users"]
        assert {u["id"] for u in pics} == {"u-sales5", "u-other5"}
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_edit_owner_eligible_ok_non_eligible_rejected_history_preserved():
    admin = _user("u-adm6", "adm6@pic-test.local", "Admin", admin=True)
    a = _user("u-a6", "a6@pic-test.local", "PIC A")
    b = _user("u-b6", "b6@pic-test.local", "PIC B")
    c = _user("u-c6", "c6@pic-test.local", "PIC C")
    for u in (a, b, c):
        await _seed_user(u)
    client = await _client_for(admin)
    try:
        await _set_pics(client, ["u-a6", "u-b6", "u-c6"])
        created = await client.post("/api/sales-activity/outlets", json={"name": "Edit PIC", "owner_id": "u-a6"})
        oid = created.json()["outlet"]["id"]
        assert created.json()["ownership"]["current_owner_name"] == "PIC A"

        ok = await client.post(
            f"/api/sales-activity/outlets/{oid}/reassign",
            json={"owner_id": "u-b6", "reason": "Ganti PIC B"},
        )
        assert ok.status_code == 200, ok.text
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-b6"
        assert snap["current_owner_name"] == "PIC B"

        # Remove C from allowlist — historical owner B must remain readable; change to C rejected
        await _set_pics(client, ["u-a6", "u-b6"])
        bad = await client.post(
            f"/api/sales-activity/outlets/{oid}/reassign",
            json={"owner_id": "u-c6", "reason": "C tidak eligible"},
        )
        assert bad.status_code == 400
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap2["current_owner_id"] == "u-b6"
        assert snap2["current_owner_name"] == "PIC B"

        # Removing B from settings does not rewrite historical prospect
        await _set_pics(client, ["u-a6"])
        detail = await client.get(f"/api/sales-activity/outlets/{oid}")
        assert detail.status_code == 200
        assert detail.json()["ownership"]["current_owner_id"] == "u-b6"
        assert detail.json()["ownership"]["current_owner_name"] == "PIC B"
        assert detail.json()["ownership"]["customer_kind"] == KIND_PROSPECT
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_empty_allowlist_blocks_create_and_get_settings_pure():
    admin = _user("u-adm7", "adm7@pic-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    try:
        before = (await client.get("/api/sales-activity/settings")).json()["settings"]
        assert before.get("prospect_pic_user_ids") == []
        res = await client.post("/api/sales-activity/outlets", json={"name": "Empty", "owner_id": "u-adm7"})
        assert res.status_code == 400
        assert "Belum ada PIC Sales Prospek" in (res.json().get("detail") or "")
        after = (await client.get("/api/sales-activity/settings")).json()["settings"]
        assert after == before
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_rbac_settings_manage_required_for_pic_config():
    sales = _user("u-sales8", "sales8@pic-test.local", "Sales", perms=SALES_PERMS)
    client = await _client_for(sales)
    try:
        bad = await client.put("/api/sales-activity/settings", json={"prospect_pic_user_ids": ["u-sales8"]})
        assert bad.status_code == 403
    finally:
        await _close(client)
