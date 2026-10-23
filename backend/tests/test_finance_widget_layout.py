"""Dashboard Keuangan layout persistence. Isolated database. No production writes."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_finance_layout_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.finance_widget_layout import (
    COLLECTION,
    LayoutError,
    default_widgets,
    get_finance_layout,
    reset_finance_layout,
    save_finance_layout,
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


@pytest.fixture
async def clean_db(bind_motor):
    await server.db[COLLECTION].delete_many({})
    yield
    await server.db[COLLECTION].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(uid, role="helper", perms=None):
    override = {key: True for key in (perms or [])}
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@example.com",
        "name": uid,
        "role": role,
        "status": "active",
        "permissions_override": override,
    }


def _cash(x=0, y=0, w=4, h=3):
    return {"widget_id": "cash", "x": x, "y": y, "w": w, "h": h, "settings": {"period_mode": "global"}}


@pytest.mark.asyncio
async def test_user_layout_is_private_and_round_trips(clean_db):
    sales = {"dashboard.sales.view"}
    finance = {"dashboard.finance.view"}
    user_a = {"user_id": "user-a", "id": "user-a"}
    user_b = {"user_id": "user-b", "id": "user-b"}
    empty = await get_finance_layout(server.db, user_a, sales)
    assert empty["version"] == 0
    assert empty["source"] == "default"
    assert [row["widget_id"] for row in empty["widgets"]] == ["sales", "trend", "products", "sellers"]
    saved = await save_finance_layout(server.db, user_a, sales, 0, [empty["widgets"][0]])
    assert saved["version"] == 1
    assert [row["widget_id"] for row in saved["widgets"]] == ["sales"]
    other = await get_finance_layout(server.db, user_b, finance)
    assert other["version"] == 0
    assert "sales" not in [row["widget_id"] for row in other["widgets"]]
    assert [row["widget_id"] for row in other["widgets"]] == ["cash", "expense"]
    again = await get_finance_layout(server.db, user_a, sales)
    assert again["version"] == 1
    assert again["widgets"][0]["widget_id"] == "sales"
    stored = await server.db[COLLECTION].find({"user_id": "user-b"}).to_list(5)
    assert stored == []


@pytest.mark.asyncio
async def test_cas_rejects_stale_tab(clean_db):
    user = {"user_id": "user-a", "id": "user-a"}
    perms = {"dashboard.finance.view"}
    first = await save_finance_layout(server.db, user, perms, 0, [_cash()])
    assert first["version"] == 1
    second = await save_finance_layout(server.db, user, perms, 1, [_cash(x=4)])
    assert second["version"] == 2
    with pytest.raises(LayoutError) as caught:
        await save_finance_layout(server.db, user, perms, 1, [_cash(x=8)])
    assert caught.value.status_code == 409
    assert caught.value.code == "LAYOUT_CONFLICT"
    current = await get_finance_layout(server.db, user, perms)
    assert current["version"] == 2
    assert current["widgets"][0]["x"] == 4


@pytest.mark.asyncio
async def test_unknown_invalid_and_forbidden_widgets(clean_db):
    user = {"user_id": "user-a", "id": "user-a"}
    perms = {"dashboard.sales.view"}
    with pytest.raises(LayoutError) as unknown:
        await save_finance_layout(server.db, user, perms, 0, [{"widget_id": "brand", "x": 0, "y": 0, "w": 4, "h": 3, "settings": {}}])
    assert unknown.value.code == "LAYOUT_WIDGET_UNKNOWN"
    with pytest.raises(LayoutError) as size:
        await save_finance_layout(server.db, user, perms, 0, [{"widget_id": "sales", "x": 0, "y": 0, "w": 2, "h": 3, "settings": {}}])
    assert size.value.code == "LAYOUT_INVALID"
    with pytest.raises(LayoutError) as forbidden:
        await save_finance_layout(server.db, user, perms, 0, [_cash()])
    assert forbidden.value.status_code == 403
    await server.db[COLLECTION].insert_one({
        "id": "legacy",
        "user_id": "user-a",
        "dashboard_key": "keuangan",
        "version": 3,
        "widgets": [
            {"widget_id": "future_widget", "x": 0, "y": 0, "w": 4, "h": 3, "settings": {}},
            _cash(),
            {"widget_id": "sales", "x": 4, "y": 0, "w": 4, "h": 3, "settings": {"period_mode": "global"}},
        ],
    })
    viewed = await get_finance_layout(server.db, user, perms)
    assert viewed["version"] == 3
    assert [row["widget_id"] for row in viewed["widgets"]] == ["sales"]
    assert viewed["ignored"][0]["widget_id"] == "future_widget"
    assert viewed["withheld"][0]["widget_id"] == "cash"
    raw = await server.db[COLLECTION].find_one({"user_id": "user-a"})
    assert len(raw["widgets"]) == 3


@pytest.mark.asyncio
async def test_save_preserves_withheld_widget_and_reset_returns_default(clean_db):
    user = {"user_id": "user-a", "id": "user-a"}
    await server.db[COLLECTION].insert_one({
        "id": "mix",
        "user_id": "user-a",
        "dashboard_key": "keuangan",
        "version": 4,
        "widgets": [
            _cash(),
            {"widget_id": "sales", "x": 4, "y": 0, "w": 4, "h": 3, "settings": {"period_mode": "month"}},
        ],
    })
    saved = await save_finance_layout(
        server.db,
        user,
        {"dashboard.sales.view"},
        4,
        [{"widget_id": "sales", "x": 0, "y": 0, "w": 6, "h": 3, "settings": {"period_mode": "month"}}],
    )
    assert saved["version"] == 5
    assert [row["widget_id"] for row in saved["widgets"]] == ["sales"]
    raw = await server.db[COLLECTION].find_one({"user_id": "user-a"}, {"_id": 0})
    assert {row["widget_id"] for row in raw["widgets"]} == {"sales", "cash"}
    assert "dashboard.finance.view" not in str(raw)
    reset = await reset_finance_layout(server.db, user, {"dashboard.sales.view", "profit_loss.view"})
    assert reset["version"] == 0
    assert reset["source"] == "default"
    assert [row["widget_id"] for row in reset["widgets"]] == ["sales", "profit", "trend", "products", "sellers"]
    assert await server.db[COLLECTION].count_documents({"user_id": "user-a"}) == 0
    assert default_widgets({"dashboard.sales.view"})[0]["widget_id"] == "sales"


@pytest.mark.asyncio
async def test_http_uses_session_user_not_body(clean_db):
    user = _user("session-user", perms=["dashboard.finance.view"])

    async def override():
        return user

    app.dependency_overrides[current_user] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put("/api/dashboard/keuangan/layout", json={
            "version": 0,
            "user_id": "someone-else",
            "widgets": [_cash()],
        })
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["version"] == 1
        assert body["widgets"][0]["widget_id"] == "cash"
        listed = await server.db[COLLECTION].find({}, {"_id": 0, "user_id": 1}).to_list(5)
        assert listed == [{"user_id": "session-user"}]
        conflict = await client.put("/api/dashboard/keuangan/layout", json={"version": 0, "widgets": [_cash(x=3)]})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "LAYOUT_CONFLICT"
