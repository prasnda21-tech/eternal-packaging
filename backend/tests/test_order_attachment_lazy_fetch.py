"""GET /orders/:id strips design/payment data_url; authenticated one-file GET serves bytes."""
import base64
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
PDF_BYTES = b"%PDF-1.4\n%%EOF\n"
PDF_DATA_URL = "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii")
HTML_DATA_URL = "data:text/html;base64," + base64.b64encode(b"<html><body>x</body></html>").decode("ascii")
SVG_DATA_URL = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").decode("ascii")
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    app.dependency_overrides.pop(current_user, None)
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    return {
        "id": "admin-1",
        "user_id": "admin-1",
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }


def _no_view_user():
    return {
        "id": "noview-1",
        "user_id": "noview-1",
        "email": "noview@example.com",
        "name": "No View",
        "role": "no_access",
    }


def _file(fid, name):
    return {
        "id": fid,
        "name": name,
        "content_type": "image/png",
        "data_url": PNG_DATA_URL,
        "uploaded_by": "designer@eternalpackaging.com",
        "uploaded_by_role": "designer",
        "uploaded_by_name": "Designer",
        "uploaded_at": "2026-09-19T08:00:00+00:00",
    }


def _order(design=True, payment=True):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "po_number": f"PO-LAZY-{uuid.uuid4().hex[:6]}",
        "customer": "Lazy Fetch",
        "customer_name": "Lazy Fetch",
        "cancelled": False,
        "current_stage": 3,
        "status_label": "Design",
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs"}],
        "design_files": [_file("design-1", "art.png")] if design else [],
        "payment_files": [_file("pay-1", "bukti.png")] if payment else [],
        "created_at": now,
        "updated_at": now,
    }


async def _client(user=None):
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_get_order_strips_design_and_payment_data_url():
    import server
    order = _order()
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}")
    assert r.status_code == 200, r.text
    body = r.json()["order"]
    design = body["design_files"][0]
    payment = body["payment_files"][0]
    assert design["id"] == "design-1"
    assert design["name"] == "art.png"
    assert design["content_type"] == "image/png"
    assert design["uploaded_by"] == "designer@eternalpackaging.com"
    assert design["uploaded_by_role"] == "designer"
    assert design["uploaded_by_name"] == "Designer"
    assert design["uploaded_at"]
    assert "data_url" not in design
    assert "url" not in design
    assert payment["id"] == "pay-1"
    assert payment["name"] == "bukti.png"
    assert "data_url" not in payment
    stored = await server.db.orders.find_one({"id": order["id"]})
    assert stored["design_files"][0]["data_url"].startswith("data:image/png")
    assert stored["payment_files"][0]["data_url"].startswith("data:image/png")
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_get_design_attachment_raw_bytes():
    import server
    order = _order(payment=False)
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/design-1")
    assert r.status_code == 200
    assert r.content == PNG_BYTES
    assert r.headers["content-type"].startswith("image/png")
    assert "inline" in r.headers.get("content-disposition", "")
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_get_payment_attachment_raw_bytes():
    import server
    order = _order(design=False)
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/payment/pay-1")
    assert r.status_code == 200
    assert r.content == PNG_BYTES
    assert r.headers["content-type"].startswith("image/png")
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_historical_png_attachment_ok():
    import server
    order = _order(payment=False)
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/design-1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers.get("x-content-type-options") == "nosniff"
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_historical_pdf_attachment_ok():
    import server
    order = _order(design=False, payment=False)
    order["design_files"] = [{
        "id": "pdf-1",
        "name": "old.pdf",
        "content_type": "application/pdf",
        "data_url": PDF_DATA_URL,
    }]
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/pdf-1")
    assert r.status_code == 200
    assert r.content == PDF_BYTES
    assert r.headers["content-type"].startswith("application/pdf")
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_historical_claimed_html_rejected():
    import server
    order = _order(design=False, payment=False)
    order["design_files"] = [{
        "id": "html-1",
        "name": "page.html",
        "content_type": "text/html",
        "data_url": HTML_DATA_URL,
    }]
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/html-1")
    assert r.status_code == 415
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_historical_claimed_svg_rejected():
    import server
    order = _order(design=False, payment=False)
    order["payment_files"] = [{
        "id": "svg-1",
        "name": "icon.svg",
        "content_type": "image/svg+xml",
        "data_url": SVG_DATA_URL,
    }]
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/payment/svg-1")
    assert r.status_code == 415
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_wrong_file_id_404():
    import server
    order = _order()
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/no-such-file")
    assert r.status_code == 404
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_invalid_kind_400():
    import server
    order = _order()
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/sample/design-1")
    assert r.status_code == 400
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_unauthorized_denied():
    import server
    order = _order()
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/design-1")
    assert r.status_code == 401
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_forbidden_without_view_perm(monkeypatch):
    import server
    order = _order()
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    monkeypatch.setattr(server, "user_permissions", AsyncMock(return_value=set()))
    async with await _client(_no_view_user()) as client:
        r = await client.get(f"/api/orders/{order['id']}/attachments/design/design-1")
    assert r.status_code == 403
    await server.db.orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_old_inline_only_po_preview_works():
    import server
    now = datetime.now(timezone.utc).isoformat()
    order = {
        "id": str(uuid.uuid4()),
        "po_number": f"PO-OLD-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "current_stage": 1,
        "line_items": [],
        "design_files": [{
            "id": "legacy-d",
            "name": "old.png",
            "data_url": PNG_DATA_URL,
        }],
        "payment_files": [{
            "id": "legacy-p",
            "name": "old-pay.png",
            "data_url": PNG_DATA_URL,
        }],
        "created_at": now,
        "updated_at": now,
    }
    await server.db.orders.delete_many({"id": order["id"]})
    await server.db.orders.insert_one(dict(order))
    async with await _client(_admin()) as client:
        listed = await client.get(f"/api/orders/{order['id']}")
        assert listed.status_code == 200
        assert "data_url" not in listed.json()["order"]["design_files"][0]
        assert listed.json()["order"]["design_files"][0]["name"] == "old.png"
        design = await client.get(f"/api/orders/{order['id']}/attachments/design/legacy-d")
        payment = await client.get(f"/api/orders/{order['id']}/attachments/payment/legacy-p")
    assert design.status_code == 200
    assert design.content == PNG_BYTES
    assert payment.status_code == 200
    assert payment.content == PNG_BYTES
    await server.db.orders.delete_many({"id": order["id"]})
