"""Staff Detail PO: strip inline design/payment bytes from GET, serve one file on demand.

Storage stays on the order document (`data_url`). This module only transforms
outbound copies and locates a single attachment for the authenticated GET.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple
from urllib.parse import quote
import re

from services.surat_jalan_files import decode_data_url, sniffer_content_type

KIND_FIELDS = {
    "design": "design_files",
    "payment": "payment_files",
}

ALLOWED_KINDS = frozenset(KIND_FIELDS)


def strip_inline_attachment_bytes(order: dict) -> dict:
    """Shallow-copy order and drop design/payment `data_url` from the response.

    Does not mutate the original document or Mongo. Other metadata fields
    (id, name, content_type, uploaded_*) are kept. Does not invent `url`.
    """
    if not isinstance(order, dict):
        return order
    public = None
    for field in KIND_FIELDS.values():
        files = order.get(field)
        if not isinstance(files, list) or not files:
            continue
        if not any(isinstance(f, dict) and "data_url" in f for f in files):
            continue
        if public is None:
            public = dict(order)
        public[field] = [_without_data_url(f) for f in files]
    return public if public is not None else order


def _without_data_url(entry: Any) -> Any:
    if not isinstance(entry, dict) or "data_url" not in entry:
        return entry
    out = dict(entry)
    out.pop("data_url", None)
    return out


def find_inline_attachment(order: Optional[dict], kind: str, file_id: str) -> Optional[dict]:
    field = KIND_FIELDS.get(kind)
    if not field or not isinstance(order, dict):
        return None
    fid = str(file_id or "").strip()
    if not fid:
        return None
    for entry in order.get(field) or []:
        if isinstance(entry, dict) and str(entry.get("id") or "") == fid:
            return entry
    return None


def decode_inline_attachment(entry: dict) -> Tuple[bytes, str]:
    """Return raw bytes and a preview-safe content type from an existing row."""
    data_url = str((entry or {}).get("data_url") or "").strip()
    data, decoded_ct = decode_data_url(data_url)
    claimed = str((entry or {}).get("content_type") or "").strip()
    filename = str((entry or {}).get("name") or "")
    ct = sniffer_content_type(data, filename, claimed or decoded_ct)
    return data, ct


def content_disposition(filename: str) -> str:
    raw = (filename or "file").strip() or "file"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw) or "file"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "file"
    return f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'


def field_for_kind(kind: str) -> Optional[str]:
    return KIND_FIELDS.get(kind)
