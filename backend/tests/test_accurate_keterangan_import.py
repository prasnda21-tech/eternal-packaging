"""Accurate header Keterangan → import preview → persist → GET order."""
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user, _build_preview, _persist_imported_po
from services.accurate import _normalize_accurate_so

HEADER_KETERANGAN = (
    "RETUR LID DARI MERK TPP KE SJP 1000PCS\n"
    "KIRIM TANGGAL : 07/09/2026\n"
    "(SEKALIAN NARIK MERK TPP 1000PCS)"
)

ADMIN = {
    "email": "admin@eternalpackaging.com",
    "name": "Admin Tester",
    "role": "admin",
}


def _ket_raw(*, number="SO-KET-HTTP"):
    return {
        "id": abs(hash(number)) % 100000,
        "number": number,
        "customer": {"name": "Merk TPP"},
        "description": HEADER_KETERANGAN,
        "detailItem": [
            {
                "id": 501,
                "quantity": 500,
                "itemUnitName": "pcs",
                "detailNotes": "SABLON WARNA ORANGE",
                "item": {"id": 801, "no": "CUP-14", "name": "14 oz Inject Datar Bening Klir"},
            },
            {
                "id": 502,
                "quantity": 500,
                "itemUnitName": "pcs",
                "item": {"id": 802, "no": "LID-CON", "name": "LID Connect Inject Bening Klir"},
            },
        ],
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def test_preview_includes_header_keterangan():
    so = _normalize_accurate_so(_ket_raw())
    preview = _build_preview(so)
    assert preview["notes"] == HEADER_KETERANGAN
    assert preview["line_items"][0]["notes"] == "SABLON WARNA ORANGE"
    assert preview["line_items"][1]["notes"] in (None, "")
    assert preview["line_items"][0]["color"] is None
    assert preview["line_items"][1]["color"] is None


def test_preview_blank_keterangan_does_not_invent():
    raw = {
        "id": 1,
        "number": "SO-BLANK",
        "customer": {"name": "A"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    preview = _build_preview(_normalize_accurate_so(raw))
    assert preview["notes"] == ""


@pytest.mark.anyio
async def test_preview_http_returns_header_keterangan(monkeypatch):
    import server

    so = _normalize_accurate_so(_ket_raw(number="SO-KET-PREV"))

    async def _fake_fetch(so_number):
        return so

    monkeypatch.setattr(server._accurate_client, "fetch_single_sales_order", _fake_fetch)
    app.dependency_overrides[current_user] = lambda: ADMIN
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/accurate/import/preview", json={"so_number": "SO-KET-PREV"})
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["preview"]["notes"] == HEADER_KETERANGAN
            lines = body["preview"]["line_items"]
            assert lines[0]["color"] is None
            assert lines[1]["color"] is None
            assert lines[0]["notes"] != HEADER_KETERANGAN
            assert lines[1]["notes"] != HEADER_KETERANGAN
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_confirm_persists_and_get_returns_header_keterangan():
    so = _normalize_accurate_so(_ket_raw(number=f"SO-KET-{uuid.uuid4().hex[:8].upper()}"))
    preview = _build_preview(so)
    preview["po_number"] = f"PO-KET-{uuid.uuid4().hex[:8].upper()}"
    saved = await _persist_imported_po(preview, source="accurate_mock", user=ADMIN)
    assert saved["notes"] == HEADER_KETERANGAN
    assert saved["line_items"][0]["color"] is None
    assert saved["line_items"][1]["color"] is None
    assert saved["line_items"][0]["quantity"] == 500
    assert saved["line_items"][1]["quantity"] == 500

    app.dependency_overrides[current_user] = lambda: ADMIN
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/orders/{saved['id']}")
            assert res.status_code == 200, res.text
            order = res.json()["order"]
            assert order["notes"] == HEADER_KETERANGAN
            assert order["notes"] == preview["notes"]
            assert order["line_items"][0]["color"] is None
            assert order["line_items"][1]["color"] is None
            assert order["line_items"][0]["notes"] == "SABLON WARNA ORANGE"
            assert order["line_items"][1]["notes"] in (None, "")
    finally:
        app.dependency_overrides.pop(current_user, None)
