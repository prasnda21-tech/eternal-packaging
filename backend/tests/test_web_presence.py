import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from services.web_presence import Heartbeat, list_presence, presence_row, record_heartbeat, register_web_presence_routes
from services.finance_widget_layout import finance_widget_allowed, SIZES

NOW = datetime(2026, 10, 31, 8, tzinfo=timezone.utc)


def test_heartbeat_uses_authenticated_identity_and_server_time():
    users = SimpleNamespace(update_one=AsyncMock())
    asyncio.run(record_heartbeat(SimpleNamespace(users=users), {"email": "owner@example.test"}, Heartbeat(activity_age_ms=1200), NOW))
    users.update_one.assert_awaited_once_with({"email": "owner@example.test"}, {"$max": {
        "web_presence.seen_at": NOW,
        "web_presence.active_at": NOW - timedelta(milliseconds=1200),
    }})


def test_idle_heartbeat_does_not_move_activity():
    users = SimpleNamespace(update_one=AsyncMock())
    asyncio.run(record_heartbeat(SimpleNamespace(users=users), {"email": "owner@example.test"}, Heartbeat(), NOW))
    assert users.update_one.call_args.args[1] == {"$max": {"web_presence.seen_at": NOW}}


@pytest.mark.parametrize("payload", [{"user_id": "other"}, {"activity_age_ms": -1}, {"activity_age_ms": 90001}, {"activity_age_ms": True}, {"activity_age_ms": "1"}])
def test_reject_identity_and_invalid_activity(payload):
    with pytest.raises(ValidationError):
        Heartbeat(**payload)


@pytest.mark.parametrize("age,online", [(0, True), (89, True), (90, False), (91, False), (-1, False)])
def test_online_boundary_and_no_secret_leak(age, online):
    row = presence_row({"id": "u1", "name": "User", "password_hash": "secret", "email": "private",
                        "web_presence": {"seen_at": NOW - timedelta(seconds=age)}}, NOW)
    assert row["online"] is online
    assert row["last_active_at"] is None
    assert set(row) == {"id", "name", "online", "last_seen_at", "last_active_at"}


def test_never_seen_and_mongo_naive_datetime():
    assert presence_row({"id": "u1"}, NOW)["online"] is False
    assert presence_row({"id": "u1", "web_presence": {"seen_at": NOW.replace(tzinfo=None)}}, NOW)["online"] is True


def test_multi_document_max_updates_do_not_regress():
    class Users:
        def __init__(self): self.values = {}
        async def update_one(self, query, update):
            for key, value in update["$max"].items():
                self.values[key] = max(self.values.get(key, value), value)
    users = Users()
    async def scenario():
        db = SimpleNamespace(users=users)
        await record_heartbeat(db, {"email": "same"}, Heartbeat(activity_age_ms=0), NOW)
        await record_heartbeat(db, {"email": "same"}, Heartbeat(activity_age_ms=5000), NOW - timedelta(seconds=1))
        await record_heartbeat(db, {"email": "same"}, Heartbeat(), NOW + timedelta(seconds=30))
    asyncio.run(scenario())
    assert users.values["web_presence.active_at"] == NOW
    assert users.values["web_presence.seen_at"] == NOW + timedelta(seconds=30)


def test_list_paginates_projects_and_excludes_disabled():
    class Cursor:
        def sort(self, value):
            assert value[0] == ("web_presence.active_at", -1)
            return self
        def skip(self, value):
            assert value == 50
            return self
        def limit(self, value):
            assert value == 50
            return self
        async def to_list(self, length): return [{"id": "u51", "name": "User 51"}]
    class Users:
        async def count_documents(self, query):
            assert query == {"$or": [{"status": "active"}, {"status": {"$exists": False}}]}
            return 51
        def find(self, query, projection):
            assert projection == {"_id": 1, "id": 1, "name": 1, "web_presence": 1}
            return Cursor()
    body = asyncio.run(list_presence(SimpleNamespace(users=Users()), 2, 50, NOW))
    assert body["total"] == 51 and len(body["rows"]) == 1
    assert body["rows"][0]["last_active_at"] is None


def test_routes_and_layout_use_users_view_not_finance_permission():
    async def identity(): return {"email": "user"}
    async def allowed(): return {"email": "viewer"}
    def require(permission):
        assert permission == "users.view"
        return allowed
    router = APIRouter()
    register_web_presence_routes(router, get_db=lambda: None, current_user=identity, require_perm=require)
    by_path = {route.path: route for route in router.routes}
    assert by_path["/auth/presence"].dependant.dependencies[0].call is identity
    assert by_path["/dashboard/active-users"].dependant.dependencies[0].call is allowed
    assert finance_widget_allowed("active_users", ["users.view"])
    assert not finance_widget_allowed("active_users", ["dashboard.finance.view"])
    assert SIZES["active_users"] == (4, 12, 3, 8, 6, 3)


def test_missing_authenticated_identity_cannot_write():
    with pytest.raises(HTTPException) as error:
        asyncio.run(record_heartbeat(None, {}, Heartbeat(), NOW))
    assert error.value.status_code == 401


def test_http_auth_validation_and_no_store():
    import json
    from fastapi import FastAPI
    users = SimpleNamespace(update_one=AsyncMock())
    async def identity(): return {"email": "authenticated@example.test"}
    async def denied(): raise HTTPException(403, "Permission required")
    app = FastAPI()
    register_web_presence_routes(app, get_db=lambda: SimpleNamespace(users=users),
                                 current_user=identity, require_perm=lambda _: denied)
    async def request(path, method, payload=None):
        data = json.dumps(payload or {}).encode()
        messages = []
        async def receive(): return {"type": "http.request", "body": data, "more_body": False}
        async def send(message): messages.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                   "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                   "query_string": b"", "headers": [(b"content-type", b"application/json")],
                   "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send)
        return messages
    good = asyncio.run(request("/auth/presence", "POST", {"activity_age_ms": 0}))
    assert good[0]["status"] == 200
    assert (b"cache-control", b"no-store") in good[0]["headers"]
    assert users.update_one.call_args.args[0] == {"email": "authenticated@example.test"}
    bad = asyncio.run(request("/auth/presence", "POST", {"user_id": "victim"}))
    assert bad[0]["status"] == 422
    forbidden = asyncio.run(request("/dashboard/active-users", "GET"))
    assert forbidden[0]["status"] == 403
    assert users.update_one.await_count == 1
