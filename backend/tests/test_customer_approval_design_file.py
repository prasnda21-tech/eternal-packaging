"""Public Approval Design PDF — token-scoped view URL and MIME."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from customer_approval import (
    PUBLIC_DESIGN_VIEW_STATUSES,
    _public_view,
    bound_design_file,
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
