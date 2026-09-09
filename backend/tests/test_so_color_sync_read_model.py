"""Sales Order color persistence and GET read-model.

Structured Accurate Warna is persisted as line_items[].color.
GET /orders/{id} returns that persisted value and does not parse notes.
"""
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

from server import app, current_user, _persist_imported_po
from services.accurate import _normalize_accurate_so


ADMIN = {
    "email": "admin@eternalpackaging.com",
    "name": "Admin Tester",
    "role": "admin",
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


def test_case1_structured_color_persisted_exactly():
    raw = {
        "id": 201,
        "number": "SO-SYNC-1",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "charField2": "RED",
                "detailNotes": "Warna Cetak: BLUE",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED"


def test_case2_same_name_lines_independent():
    raw = {
        "id": 202,
        "number": "SO-SYNC-2",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 11,
                "quantity": 100,
                "charField2": "RED",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 12,
                "quantity": 200,
                "charField2": "BLUE",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED"
    assert so["line_items"][1]["color"] == "BLUE"
    assert so["line_items"][0]["accurate_line_id"] == "11"
    assert so["line_items"][1]["accurate_line_id"] == "12"


def test_case3_multi_color_preserves_source_order():
    raw = {
        "id": 203,
        "number": "SO-SYNC-3",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "charField2": "PUTIH, HIJAU, PINK",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"


def test_notes_do_not_become_color():
    raw = {
        "id": 204,
        "number": "SO-SYNC-4",
        "customer": {"name": "A"},
        "description": "Warna Cetak: WHITE",
        "detailItem": [
            {
                "id": 21,
                "quantity": 10,
                "detailNotes": "Warna Cetak: RED",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 22,
                "quantity": 10,
                "detailNotes": "Warna Cetak: BLUE",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] is None


@pytest.mark.anyio
async def test_get_orders_id_returns_persisted_null_not_notes_color():
    preview = {
        "accurate_id": f"acc-{uuid.uuid4().hex[:8]}",
        "po_number": f"PO-NULL-{uuid.uuid4().hex[:8].upper()}",
        "customer": "A",
        "order_date": "2026-09-09",
        "estimated_delivery": "2026-09-12",
        "deadline": "2026-09-12",
        "notes": "",
        "line_items": [
            {
                "name": "Preview Cup",
                "color": None,
                "notes": "Warna Cetak: HITAM",
                "quantity": 1,
                "unit": "pcs",
                "accurate_item_id": "9",
            }
        ],
        "total_quantity": 1,
        "item_count": 1,
    }
    saved = await _persist_imported_po(preview, source="accurate_mock", user=ADMIN)
    app.dependency_overrides[current_user] = lambda: ADMIN
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/orders/{saved['id']}")
            assert res.status_code == 200, res.text
            line = res.json()["order"]["line_items"][0]
            assert line["color"] is None
            assert line["notes"] == "Warna Cetak: HITAM"
    finally:
        app.dependency_overrides.pop(current_user, None)
