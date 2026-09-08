"""Public Approval Design PDF — token-scoped view URL and MIME."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from customer_approval import (
    PUBLIC_DESIGN_VIEW_STATUSES,
    _design_disposition_headers,
    _hash_token,
    _parse_byte_range,
    _public_view,
    bound_design_file,
    build_router,
    load_bound_design_bytes,
    public_design_file_path,
    public_design_media_type,
)

TINY_PDF = b"%PDF-1.4 test"


def _rec(**extra):
    rec = {
        "id": "ca-1",
        "order_id": "ord-1",
        "design_version": "d-bound",
        "status": "active",
        "generated_at": "2026-09-08T00:00:00+00:00",
        "expires_at": "2026-09-08T04:00:00+00:00",
        "expiry_hours": 4,
    }
    rec.update(extra)
    return rec


def _order(*files):
    return {
        "po_number": "PO/2026/TEST",
        "customer": "PT Contoh",
        "outlet": "Outlet A",
        "line_items": [{"name": "Karton", "color": "Putih", "quantity": 10, "unit": "pcs"}],
        "design_files": list(files),
    }


def test_bound_file_is_design_version_only():
    other = {"id": "d-other", "name": "other.pdf"}
    bound = {"id": "d-bound", "name": "APPROVAL.pdf"}
    assert bound_design_file(_rec(), _order(other, bound))["name"] == "APPROVAL.pdf"
    assert bound_design_file(_rec(design_version="missing"), _order(bound)) is None


def test_public_view_exposes_token_scoped_url_not_storage_path():
    token = "tok_public_abc"
    pdf = {
        "id": "d-bound",
        "name": "APPROVAL NILU.pdf",
        "content_type": "application/pdf",
        "storage_path": "eternal-packaging/secret/design.pdf",
        "data_url": "data:application/pdf;base64,JVBERi0=",
    }
    view = _public_view(_rec(), _order(pdf), {}, True, token=token)
    blob = json.dumps(view)
    assert view["design_file"]["view_url"] == public_design_file_path(token)
    assert view["design_file"]["data_url"] is None
    assert "storage_path" not in view["design_file"]
    assert "eternal-packaging/secret" not in blob
    assert "tok_public_abc" in view["design_file"]["view_url"]


def test_public_view_hides_file_bytes_when_link_not_viewable():
    pdf = {"id": "d-bound", "name": "x.pdf", "content_type": "application/pdf", "data_url": "data:application/pdf;base64,AAA"}
    view = _public_view(_rec(status="expired"), _order(pdf), {}, False, token="tok")
    assert view["design_file"]["view_url"] is None
    assert view["design_file"]["data_url"] is None


def test_public_view_keeps_image_data_url():
    img = {
        "id": "d-bound",
        "name": "x.png",
        "content_type": "image/png",
        "data_url": "data:image/png;base64,eA==",
    }
    view = _public_view(_rec(), _order(img), {}, True, token="tok")
    assert view["design_file"]["data_url"].startswith("data:image/png")
    assert view["design_file"]["view_url"] == public_design_file_path("tok")


def test_load_bytes_from_data_url_and_pdf_media_type():
    file = {
        "id": "d-bound",
        "name": "design.bin",
        "content_type": "application/octet-stream",
        "data_url": "data:application/octet-stream;base64," + base64.b64encode(TINY_PDF).decode("ascii"),
    }
    data, media = load_bound_design_bytes(file, obj_storage=None)
    assert data.startswith(b"%PDF")
    assert media == "application/pdf"


def test_media_type_helper_does_not_rewrite_images():
    assert public_design_media_type({"name": "x.png"}, b"\x89PNG", "image/png") == "image/png"


def test_viewable_statuses_unchanged():
    assert PUBLIC_DESIGN_VIEW_STATUSES == ("active", "approved", "rejected")


def test_design_disposition_headers_inline_and_attachment():
    inline = _design_disposition_headers("My Design (v1).pdf", disposition="inline")
    assert inline["Content-Disposition"].startswith("inline;")
    assert "filename*=UTF-8''My%20Design%20%28v1%29.pdf" in inline["Content-Disposition"]
    assert inline["Accept-Ranges"] == "bytes"
    assert inline["X-Content-Type-Options"] == "nosniff"

    attachment = _design_disposition_headers("My Design.pdf", disposition="attachment")
    assert attachment["Content-Disposition"].startswith("attachment;")
    assert attachment["Accept-Ranges"] == "bytes"

    download = _design_disposition_headers("My Design.pdf", disposition="download")
    assert download["Content-Disposition"].startswith("attachment;")


def test_parse_byte_range_variants():
    total = 1000
    # Normal range: 0-499
    assert _parse_byte_range("bytes=0-499", total) == (0, 499)
    # Open ended range: 500-
    assert _parse_byte_range("bytes=500-", total) == (500, 999)
    # Suffix range: -200
    assert _parse_byte_range("bytes=-200", total) == (800, 999)
    # Clamped end range: 900-2000
    assert _parse_byte_range("bytes=900-2000", total) == (900, 999)
    # Invalid ranges
    assert _parse_byte_range("bytes=1000-", total) is None
    assert _parse_byte_range("bytes=500-100", total) is None
    assert _parse_byte_range("items=0-10", total) is None
    assert _parse_byte_range("bytes=0-10,20-30", total) is None


def test_endpoint_serves_pdf_with_range_and_disposition():
    import logging
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    token = "test_tok_123"
    token_hash = _hash_token(token)
    pdf_bytes = b"%PDF-1.4 sample stream content 0123456789"

    mock_db = MagicMock()
    mock_order = _order({
        "id": "d-bound",
        "name": "Design Approval.pdf",
        "content_type": "application/pdf",
        "data_url": "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii"),
    })
    mock_rec = _rec(
        id="ca-1",
        token_hash=token_hash,
        design_version="d-bound",
        status="active",
        expires_at="2099-01-01T00:00:00+00:00",
    )

    mock_db.customer_approvals.find_one = AsyncMock(side_effect=lambda q, *_: mock_rec if q.get("token_hash") == token_hash else None)
    mock_db.customer_approvals.update_one = AsyncMock()
    mock_db.orders.find_one = AsyncMock(return_value=mock_order)
    mock_db.app_settings.find_one = AsyncMock(return_value=None)

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "test@test.com"},
        "require_perm": AsyncMock(),
        "persist_order": AsyncMock(),
        "audit_po": AsyncMock(),
        "add_timeline": AsyncMock(),
        "obj_storage": None,
        "logger": logging.getLogger("test"),
    }

    app = FastAPI()
    router = build_router(deps)
    app.include_router(router)
    client = TestClient(app)

    # 1. Standard GET -> 200 OK, application/pdf, inline, Accept-Ranges
    res = client.get(f"/api/public/customer-approval/{token}/design")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["content-length"] == str(len(pdf_bytes))
    assert res.headers["content-disposition"].startswith("inline;")
    assert res.content == pdf_bytes

    # 2. HEAD -> 200 OK, empty body, matching Content-Length
    res_head = client.head(f"/api/public/customer-approval/{token}/design")
    assert res_head.status_code == 200
    assert res_head.headers["content-length"] == str(len(pdf_bytes))
    assert res_head.content == b""

    # 3. GET with disposition=attachment -> Content-Disposition: attachment
    res_down = client.get(f"/api/public/customer-approval/{token}/design?disposition=attachment")
    assert res_down.status_code == 200
    assert res_down.headers["content-disposition"].startswith("attachment;")
    assert res_down.content == pdf_bytes

    # 4. GET with Range: bytes=0-4 -> 206 Partial Content
    res_range = client.get(f"/api/public/customer-approval/{token}/design", headers={"Range": "bytes=0-4"})
    assert res_range.status_code == 206
    assert res_range.headers["content-range"] == f"bytes 0-4/{len(pdf_bytes)}"
    assert res_range.headers["content-length"] == "5"
    assert res_range.content == pdf_bytes[:5]

    # 5. Invalid token -> 404
    res_404 = client.get("/api/public/customer-approval/invalid_token/design")
    assert res_404.status_code == 404

    # 6. Zero business mutation: persist_order never called during viewing
    deps["persist_order"].assert_not_called()
