"""Catatan Produksi append/edit permissions. Local test database only."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS  # noqa: E402
from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402

EXISTING = "SABLON WARNA HITAM\nPROMO SPOOKTOBER DEALS"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]
    yield
    app.dependency_overrides.pop(current_user, None)


def db():
    return server_mod.db


def _user(role, **override):
    return {
        "id": f"u-note-{role}",
        "email": f"{role}-production-note@example.com",
        "role": role,
        "name": role,
        "permissions_override": override,
    }


async def _seed_order(**extra):
    oid = f"ord-note-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": oid,
        "po_number": f"PO-NOTE-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Fixture Customer",
        "current_stage": 10,
        "status_label": "Cetak",
        "cancelled": False,
        "notes": EXISTING,
        "line_items": [{"name": "Cup", "quantity": 10, "notes": "Catatan sablon"}],
        "timeline": [],
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    doc.update(extra)
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


async def _notes(oid):
    return await db().orders.find_one({"id": oid}, {"_id": 0, "notes": 1, "timeline": 1, "current_stage": 1, "line_items": 1})


def test_permission_catalog_lists_production_note_keys():
    labeled = {key: (module, label) for module, key, label in PERMISSION_CATALOG}
    assert labeled["production_note.view"] == ("Catatan Produksi", "Lihat Catatan Produksi")
    assert labeled["production_note.create"] == ("Catatan Produksi", "Tambah Catatan Produksi")
    assert labeled["production_note.edit"] == ("Catatan Produksi", "Edit Catatan Produksi")
    assert "production_note.delete" not in labeled
    for key in ("production_note.view", "production_note.create", "production_note.edit"):
        assert key in ALL_PERMISSIONS
        assert key in ROLE_DEFAULT_PERMISSIONS["admin"]
        assert key not in ROLE_DEFAULT_PERMISSIONS["crm"]


@pytest.mark.anyio
async def test_legacy_non_crm_appends_without_new_permission():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("operator", **{"production_note.create": False})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/api/orders/{order['id']}/note",
            json={"note": "CUSTOMER MINTA DIPERCEPAT"},
        )
    assert r.status_code == 200, r.text
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING + "\nCUSTOMER MINTA DIPERCEPAT"
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_crm_without_create_is_forbidden():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("crm")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/orders/{order['id']}/note", json={"note": "tidak boleh"})
    assert r.status_code == 403
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_crm_with_create_appends():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("crm", **{"production_note.create": True})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/api/orders/{order['id']}/note",
            json={"note": "CUSTOMER MINTA DIPERCEPAT"},
        )
    assert r.status_code == 200, r.text
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING + "\nCUSTOMER MINTA DIPERCEPAT"
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_edit_without_permission_is_forbidden():
    order = await _seed_order()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: _user("crm")
        crm = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "SABLON WARNA MERAH"})
        app.dependency_overrides[current_user] = lambda: _user("operator")
        operator = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "SABLON WARNA MERAH"})
    assert crm.status_code == 403
    assert operator.status_code == 403
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_crm_with_edit_replaces_existing_note():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("crm", **{"production_note.edit": True})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "SABLON WARNA MERAH"})
    assert r.status_code == 200, r.text
    assert r.json()["order"]["notes"] == "SABLON WARNA MERAH"
    fresh = await _notes(order["id"])
    assert fresh["notes"] == "SABLON WARNA MERAH"
    assert "PROMO SPOOKTOBER" not in fresh["notes"]
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_edit_replaces_full_string_and_does_not_append():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("operator", **{"production_note.edit": True})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "SABLON WARNA MERAH"})
    assert r.status_code == 200, r.text
    body = r.json()["order"]["notes"]
    assert body == "SABLON WARNA MERAH"
    assert "PROMO" not in body
    assert not body.startswith(EXISTING)
    fresh = await _notes(order["id"])
    assert fresh["notes"] == "SABLON WARNA MERAH"
    assert fresh["timeline"] == []
    assert fresh["current_stage"] == 10
    assert fresh["line_items"][0]["notes"] == "Catatan sablon"
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_cancelled_po_blocks_edit_and_append():
    order = await _seed_order(cancelled=True)
    app.dependency_overrides[current_user] = lambda: _user(
        "operator", **{"production_note.edit": True, "production_note.create": True}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        edited = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "SABLON WARNA MERAH"})
        appended = await client.post(f"/api/orders/{order['id']}/note", json={"note": "tambah"})
    assert edited.status_code == 409
    assert appended.status_code == 409
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_blank_edit_is_rejected():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("operator", **{"production_note.edit": True})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.patch(f"/api/orders/{order['id']}/note", json={"note": ""})
        spaces = await client.patch(f"/api/orders/{order['id']}/note", json={"note": "   "})
    assert empty.status_code == 400
    assert spaces.status_code == 400
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING
    await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_delete_note_endpoint_is_not_implemented():
    order = await _seed_order()
    app.dependency_overrides[current_user] = lambda: _user("admin")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete(f"/api/orders/{order['id']}/note")
    assert r.status_code == 405
    fresh = await _notes(order["id"])
    assert fresh["notes"] == EXISTING
    await db().orders.delete_many({"id": order["id"]})
