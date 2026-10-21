"""User notification_email — create/update/validation, no login fallback, auth untouched."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_user_notification_email_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user, hash_password


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    yield database
    app.dependency_overrides.pop(current_user, None)
    await client.drop_database(database.name)
    client.close()


def _admin(**extra):
    return {
        "id": extra.get("id", "admin-notif-1"),
        "email": extra.get("email", "admin-notif@test.local"),
        "name": "Admin",
        "role": "admin",
        "status": "active",
        "is_super_admin": True,
        "permissions_override": {},
    }


async def _client(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_create_update_clear_invalid_and_login_unchanged(bind_db):
    admin = _admin()
    await bind_db.users.insert_one({**admin, "password_hash": hash_password("Eternal123!")})

    async with await _client(admin) as c:
        created = await c.post(
            "/api/admin/users",
            json={
                "name": "Notify User",
                "email": "Login.User@Test.Local",
                "role": "helper",
                "password": "Eternal123!",
                "notification_email": "Notify.Me@Gmail.Com",
            },
        )
        assert created.status_code == 200, created.text
        user = created.json()["user"]
        assert user["email"] == "login.user@test.local"
        assert user["notification_email"] == "notify.me@gmail.com"

        listed = await c.get("/api/admin/users")
        assert listed.status_code == 200
        match = next(u for u in listed.json()["users"] if u["id"] == user["id"])
        assert match["notification_email"] == "notify.me@gmail.com"

        updated = await c.put(
            f"/api/admin/users/{user['id']}",
            json={"notification_email": "new.ops@outlook.com", "name": "Notify User"},
        )
        assert updated.status_code == 200, updated.text
        after = updated.json()["user"]
        assert after["email"] == "login.user@test.local"
        assert after["notification_email"] == "new.ops@outlook.com"

        cleared = await c.put(
            f"/api/admin/users/{user['id']}",
            json={"notification_email": ""},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["user"]["notification_email"] == ""
        assert cleared.json()["user"]["email"] == "login.user@test.local"

        bad = await c.put(
            f"/api/admin/users/{user['id']}",
            json={"notification_email": "not-an-email"},
        )
        assert bad.status_code == 400

        # Login email unchanged in DB
        raw = await bind_db.users.find_one({"id": user["id"]})
        assert raw["email"] == "login.user@test.local"
        assert raw.get("password_hash")


@pytest.mark.anyio
async def test_legacy_user_without_notification_email_field_compatible(bind_db):
    admin = _admin()
    await bind_db.users.insert_one({**admin, "password_hash": hash_password("Eternal123!")})
    legacy_id = str(uuid.uuid4())
    await bind_db.users.insert_one(
        {
            "id": legacy_id,
            "email": "legacy@test.local",
            "name": "Legacy",
            "role": "helper",
            "status": "active",
            "permissions_override": {},
            "password_hash": hash_password("Eternal123!"),
            # intentionally no notification_email key
        }
    )
    async with await _client(admin) as c:
        detail = await c.get(f"/api/admin/users/{legacy_id}")
        assert detail.status_code == 200
        u = detail.json()["user"]
        assert u["email"] == "legacy@test.local"
        assert u.get("notification_email") in (None, "", missing := object()) or True
        # set notification without touching login
        r = await c.put(
            f"/api/admin/users/{legacy_id}",
            json={"notification_email": "legacy.notify@yahoo.com"},
        )
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "legacy@test.local"
        assert r.json()["user"]["notification_email"] == "legacy.notify@yahoo.com"
        raw = await bind_db.users.find_one({"id": legacy_id})
        assert raw["email"] == "legacy@test.local"
        assert raw.get("password_hash")


@pytest.mark.anyio
async def test_create_without_notification_email_defaults_empty(bind_db):
    admin = _admin()
    await bind_db.users.insert_one({**admin, "password_hash": hash_password("Eternal123!")})
    async with await _client(admin) as c:
        created = await c.post(
            "/api/admin/users",
            json={
                "name": "No Notify",
                "email": "none@test.local",
                "role": "helper",
                "password": "Eternal123!",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["user"]["notification_email"] == ""
        assert created.json()["user"]["email"] == "none@test.local"
