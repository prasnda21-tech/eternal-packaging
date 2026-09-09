"""Accurate Sales Order line color = structured Warna only.

No header Keterangan, notes, item-name, or sibling-line fallback.
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

from server import app, current_user, _build_preview, _persist_imported_po
from services.accurate import (
    _normalize_accurate_so,
    _structured_so_line_warna,
)


ADMIN = {
    "email": "admin@eternalpackaging.com",
    "name": "Admin Tester",
    "role": "admin",
}

PROD_HEADER = "SABLON WARNA PUTIH, HIJAU, PINK\nBUFFER STOCK"


def _prod_raw(*, header=PROD_HEADER, line1_warna="PUTIH, HIJAU, PINK", line2_warna="BENING",
              line1_notes="", line2_notes=""):
    line1 = {
        "id": 1,
        "quantity": 10000,
        "itemUnitName": "pcs",
        "detailNotes": line1_notes,
        "item": {"id": 100003, "no": "100003", "name": "12 oz PET Datar 11 gr SJP"},
    }
    line2 = {
        "id": 2,
        "quantity": 10000,
        "itemUnitName": "pcs",
        "detailNotes": line2_notes,
        "item": {"id": 100123, "no": "100123", "name": "LID PET Strawless Starindo"},
    }
    if line1_warna is not None:
        line1["searchCharField1"] = {"name": line1_warna}
        line1["charField1"] = line1_warna
    if line2_warna is not None:
        line2["searchCharField1"] = {"name": line2_warna}
        line2["charField1"] = line2_warna
    return {
        "id": 91290,
        "number": "PO/2026/09/09/000018",
        "customer": {"name": "WELCOME TO PLEASURE"},
        "description": header,
        "detailItem": [line1, line2],
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


def test_structured_extractor_preserves_multi_color_order():
    li = {
        "searchCharField1": {"name": "PUTIH, HIJAU, PINK"},
        "charField1": "PUTIH, HIJAU, PINK",
        "detailNotes": "Warna Cetak: HITAM",
    }
    assert _structured_so_line_warna(li) == "PUTIH, HIJAU, PINK"


def test_structured_extractor_ignores_notes_and_nested_item():
    li = {
        "detailNotes": "Warna Cetak: HITAM",
        "item": {"color": "MERAH", "name": "Cup Bening"},
    }
    assert _structured_so_line_warna(li) is None


def test_case_a_100003_multi_color():
    so = _normalize_accurate_so(_prod_raw())
    cup = so["line_items"][0]
    assert cup["item_code"] == "100003"
    assert cup["color"] == "PUTIH, HIJAU, PINK"
    assert cup["accurate_line_id"] == "1"


def test_case_b_100123_bening():
    so = _normalize_accurate_so(_prod_raw())
    lid = so["line_items"][1]
    assert lid["item_code"] == "100123"
    assert lid["color"] == "BENING"
    assert lid["accurate_line_id"] == "2"


def test_case_c_header_sablon_does_not_override_structured():
    raw = _prod_raw(header="SABLON WARNA MERAH, PUTIH", line1_warna="BENING", line2_warna="BENING")
    so = _normalize_accurate_so(raw)
    assert so["notes"].startswith("SABLON WARNA MERAH, PUTIH")
    assert so["line_items"][0]["color"] == "BENING"
    assert so["line_items"][1]["color"] == "BENING"


def test_case_d_header_only_structured_null():
    raw = _prod_raw(header="SABLON WARNA MERAH, PUTIH", line1_warna=None, line2_warna=None)
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] is None


def test_case_e_notes_ignored_when_structured_present():
    raw = _prod_raw(line1_warna="BENING", line2_warna="BENING",
                    line1_notes="Warna Cetak: HITAM", line2_notes="Warna Cetak: HITAM")
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "BENING"
    assert so["line_items"][1]["color"] == "BENING"
    assert so["line_items"][0]["notes"] == "Warna Cetak: HITAM"


def test_case_f_notes_do_not_fill_null_structured():
    raw = _prod_raw(line1_warna=None, line2_warna=None,
                    line1_notes="Warna Cetak: HITAM", line2_notes="Warna Cetak: HITAM")
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][1]["color"] is None


def test_header_merah_vs_structured_mixed():
    raw = {
        "id": 9,
        "number": "SO-HDR-MIX",
        "customer": {"name": "A"},
        "description": "SABLON WARNA PUTIH, HIJAU, PINK\nBUFFER STOCK",
        "detailItem": [
            {
                "id": 1,
                "quantity": 1,
                "charField1": "MERAH",
                "item": {"id": 1, "no": "A", "name": "Cup"},
            },
            {
                "id": 2,
                "quantity": 1,
                "charField1": "BENING",
                "item": {"id": 2, "no": "B", "name": "Lid"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "MERAH"
    assert so["line_items"][1]["color"] == "BENING"


def test_same_name_different_structured_warna():
    raw = {
        "id": 10,
        "number": "SO-SAME",
        "customer": {"name": "A"},
        "description": "SABLON WARNA HITAM",
        "detailItem": [
            {
                "id": 11,
                "quantity": 100,
                "charField1": "PUTIH, HIJAU, PINK",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 12,
                "quantity": 200,
                "charField1": "BENING",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["name"] == so["line_items"][1]["name"]
    assert so["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
    assert so["line_items"][1]["color"] == "BENING"
    assert so["line_items"][0]["accurate_line_id"] == "11"
    assert so["line_items"][1]["accurate_line_id"] == "12"


def test_preview_copies_structured_colors():
    preview = _build_preview(_normalize_accurate_so(_prod_raw()))
    assert preview["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
    assert preview["line_items"][1]["color"] == "BENING"


@pytest.mark.anyio
async def test_preview_http_and_persist_and_get_match():
    so = _normalize_accurate_so(_prod_raw())

    async def _fake_fetch(so_number):
        return so

    import server
    monkey_client = server._accurate_client
    orig = monkey_client.fetch_single_sales_order
    monkey_client.fetch_single_sales_order = _fake_fetch
    app.dependency_overrides[current_user] = lambda: ADMIN
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/accurate/import/preview",
                json={"so_number": "PO/2026/09/09/000018"},
            )
            assert res.status_code == 200, res.text
            lines = res.json()["preview"]["line_items"]
            assert lines[0]["color"] == "PUTIH, HIJAU, PINK"
            assert lines[1]["color"] == "BENING"

        preview = _build_preview(so)
        preview["po_number"] = f"PO-STR-{uuid.uuid4().hex[:8].upper()}"
        saved = await _persist_imported_po(preview, source="accurate_mock", user=ADMIN)
        assert saved["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
        assert saved["line_items"][1]["color"] == "BENING"

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            got = await client.get(f"/api/orders/{saved['id']}")
            assert got.status_code == 200, got.text
            order = got.json()["order"]
            assert order["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
            assert order["line_items"][1]["color"] == "BENING"
    finally:
        monkey_client.fetch_single_sales_order = orig
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_get_order_does_not_synthesize_color_from_notes():
    preview = {
        "accurate_id": f"acc-{uuid.uuid4().hex[:8]}",
        "po_number": f"PO-LEGACY-{uuid.uuid4().hex[:8].upper()}",
        "customer": "Legacy",
        "order_date": "2026-09-09",
        "estimated_delivery": "2026-09-12",
        "deadline": "2026-09-12",
        "notes": "SABLON WARNA PUTIH, HIJAU, PINK",
        "line_items": [
            {
                "accurate_item_id": "100003",
                "item_code": "100003",
                "name": "12 oz PET Datar 11 gr SJP",
                "quantity": 10,
                "unit": "pcs",
                "color": None,
                "notes": "Warna Cetak: HITAM",
            }
        ],
        "total_quantity": 10,
        "item_count": 1,
    }
    saved = await _persist_imported_po(preview, source="accurate_mock", user=ADMIN)
    app.dependency_overrides[current_user] = lambda: ADMIN
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            got = await client.get(f"/api/orders/{saved['id']}")
            assert got.status_code == 200, got.text
            line = got.json()["order"]["line_items"][0]
            assert line["color"] is None
            assert line["notes"] == "Warna Cetak: HITAM"
    finally:
        app.dependency_overrides.pop(current_user, None)
