"""Unit tests for staff Detail PO inline attachment strip + decode."""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.inline_attachments import (
    ALLOWED_KINDS,
    decode_inline_attachment,
    field_for_kind,
    find_inline_attachment,
    strip_inline_attachment_bytes,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
PDF_BYTES = b"%PDF-1.4\n%%EOF\n"
PDF_DATA_URL = "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii")


def _file(fid, name, data_url, **extra):
    row = {
        "id": fid,
        "name": name,
        "content_type": extra.pop("content_type", "image/png"),
        "data_url": data_url,
        "uploaded_by": "designer@example.com",
        "uploaded_by_role": "designer",
        "uploaded_by_name": "Designer",
        "uploaded_at": "2026-09-19T00:00:00+00:00",
    }
    row.update(extra)
    return row


def test_strip_keeps_metadata_drops_only_data_url():
    original = {
        "id": "po-1",
        "design_files": [_file("d1", "art.png", PNG_DATA_URL)],
        "payment_files": [_file("p1", "pay.png", PNG_DATA_URL, content_type="image/png")],
        "sample_files": [_file("s1", "sample.png", PNG_DATA_URL)],
    }
    out = strip_inline_attachment_bytes(original)
    assert out is not original
    assert "data_url" not in out["design_files"][0]
    assert "data_url" not in out["payment_files"][0]
    assert out["sample_files"][0]["data_url"] == PNG_DATA_URL
    for key in ("id", "name", "content_type", "uploaded_by", "uploaded_by_role", "uploaded_by_name", "uploaded_at"):
        assert out["design_files"][0][key] == original["design_files"][0][key]
        assert out["payment_files"][0][key] == original["payment_files"][0][key]
    assert original["design_files"][0]["data_url"] == PNG_DATA_URL
    assert "url" not in out["design_files"][0]


def test_strip_noop_when_already_metadata_only():
    order = {"id": "po-2", "design_files": [{"id": "d1", "name": "a.png"}]}
    assert strip_inline_attachment_bytes(order) is order


def test_find_exact_file_and_kind_allowlist():
    order = {
        "design_files": [_file("d1", "a.png", PNG_DATA_URL), _file("d2", "b.png", PNG_DATA_URL)],
        "payment_files": [_file("p1", "c.png", PNG_DATA_URL)],
    }
    assert find_inline_attachment(order, "design", "d2")["name"] == "b.png"
    assert find_inline_attachment(order, "payment", "p1")["id"] == "p1"
    assert find_inline_attachment(order, "design", "p1") is None
    assert find_inline_attachment(order, "sample", "d1") is None
    assert find_inline_attachment(order, "design", "missing") is None
    assert ALLOWED_KINDS == frozenset({"design", "payment"})
    assert field_for_kind("design") == "design_files"
    assert field_for_kind("payment") == "payment_files"
    assert field_for_kind("sample") is None


def test_decode_inline_attachment_returns_raw_bytes():
    data, ct = decode_inline_attachment(_file("d1", "art.png", PNG_DATA_URL, content_type="image/png"))
    assert data == PNG_BYTES
    assert ct == "image/png"
    data, ct = decode_inline_attachment(_file("p1", "bukti.pdf", PDF_DATA_URL, content_type="application/pdf"))
    assert data == PDF_BYTES
    assert ct == "application/pdf"
