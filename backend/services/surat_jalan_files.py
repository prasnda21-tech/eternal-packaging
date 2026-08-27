"""Surat Jalan file metadata + scheduling eligibility.

Files stay on `order.surat_jalan_files` (existing architecture). API responses
never include data_url / storage_path / public object URLs. Bytes are served
only through the permission-checked file endpoint.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from quantity_rules import (
    is_fully_delivered,
    qty_delivered,
    shipments_stored,
    _safe_int,
)

PERM_VIEW_FILE = "delivery_note.view_file"
PERM_MANAGE = "delivery_note.manage"

_PUBLIC_FILE_KEYS = (
    "id",
    "name",
    "content_type",
    "uploaded_by",
    "uploaded_by_name",
    "uploaded_by_role",
    "uploaded_at",
    "shipment_id",
    "lines",
    "legacy",
)


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def file_entries(order: dict) -> List[dict]:
    raw = order.get("surat_jalan_files")
    return [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []


def find_file(order: dict, file_id: str) -> Optional[dict]:
    want = _safe_str(file_id)
    if not want:
        return None
    for f in file_entries(order):
        if _safe_str(f.get("id")) == want:
            return f
    return None


def file_has_blob(entry: Optional[dict]) -> bool:
    if not isinstance(entry, dict):
        return False
    if _safe_str(entry.get("data_url")).startswith("data:"):
        return True
    if _safe_str(entry.get("storage_path")):
        return True
    # Metadata-only projection still means a stored file record exists.
    return bool(_safe_str(entry.get("id")) and (
        _safe_str(entry.get("name")) or _safe_str(entry.get("content_type")) or entry.get("uploaded_at")
    ))


def entry_lines(entry: Optional[dict]) -> List[dict]:
    if not isinstance(entry, dict):
        return []
    raw = entry.get("lines")
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def lines_qty_total(lines: List[dict]) -> int:
    total = 0
    for ln in lines or []:
        total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
    return total


def has_valid_item_qty(entry: Optional[dict] = None, shipment: Optional[dict] = None) -> bool:
    lines = entry_lines(entry)
    if not lines and isinstance(shipment, dict):
        lines = entry_lines(shipment)
    if not lines:
        return False
    if any(max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"))) <= 0 for ln in lines):
        return False
    return lines_qty_total(lines) > 0


def file_for_shipment(order: dict, shipment: dict) -> Optional[dict]:
    if not isinstance(shipment, dict):
        return None
    fid = _safe_str(shipment.get("file_id"))
    found = find_file(order, fid) if fid else None
    if found:
        return found
    sid = _safe_str(shipment.get("id") or shipment.get("accurate_shipment_id"))
    if not sid:
        return None
    for f in file_entries(order):
        if _safe_str(f.get("shipment_id")) == sid:
            return f
    return None


def shipment_is_schedule_ready(order: dict, shipment: dict) -> bool:
    """Valid active SJ: stored file + >= 1 valid item/qty row. Legacy incomplete is not ready."""
    if not isinstance(shipment, dict):
        return False
    f = file_for_shipment(order, shipment)
    if not f or not file_has_blob(f):
        return False
    return has_valid_item_qty(f, shipment)


def valid_sj_qty(order: dict) -> int:
    """Sum of item/qty on valid (file + lines) Surat Jalan records. No loaded_qty."""
    total = 0
    counted_files = set()
    for sh in shipments_stored(order):
        if not shipment_is_schedule_ready(order, sh):
            continue
        f = file_for_shipment(order, sh)
        fid = _safe_str((f or {}).get("id") or sh.get("id"))
        if fid:
            counted_files.add(fid)
        lines = entry_lines(sh) or entry_lines(f)
        total += lines_qty_total(lines)
    for f in file_entries(order):
        fid = _safe_str(f.get("id"))
        if fid and fid in counted_files:
            continue
        if not file_has_blob(f) or not has_valid_item_qty(f, None):
            continue
        total += lines_qty_total(entry_lines(f))
    return total


def remaining_schedulable_qty(order: dict) -> int:
    """Legacy helper for historical schedule records. Create-schedule is inactive."""
    if is_fully_delivered(order):
        return 0
    return max(0, valid_sj_qty(order) - qty_delivered(order))


def public_file_entry(entry: dict) -> dict:
    out = {k: entry.get(k) for k in _PUBLIC_FILE_KEYS if entry.get(k) is not None}
    lines = entry_lines(entry)
    out["lines"] = lines
    out["has_item_qty"] = has_valid_item_qty(entry, None)
    if not out["has_item_qty"]:
        out["legacy_incomplete"] = True
    out.pop("data_url", None)
    out.pop("url", None)
    out.pop("storage_path", None)
    return out


def public_files(order: dict) -> List[dict]:
    return [public_file_entry(f) for f in file_entries(order)]


def strip_sj_bytes_from_order(order: dict) -> dict:
    """Shallow-copy order with file bytes/URLs removed. Safe for API responses."""
    files = order.get("surat_jalan_files")
    if not isinstance(files, list) or not files:
        return order
    public = dict(order)
    public["surat_jalan_files"] = public_files(order)
    return public


def decode_data_url(data_url: str) -> Tuple[bytes, str]:
    raw = (data_url or "").strip()
    if not raw.startswith("data:"):
        raise ValueError("Format data URL tidak valid")
    header, _, b64 = raw.partition(",")
    if not b64:
        raise ValueError("Isi file kosong")
    ct = "application/octet-stream"
    try:
        ct = header.split("data:", 1)[1].split(";", 1)[0].lower() or ct
    except Exception:
        pass
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise ValueError("File tidak dapat dibaca") from e
    if not data:
        raise ValueError("Isi file kosong")
    return data, ct


def sniffer_content_type(data: bytes, filename: str = "", claimed: str = "") -> str:
    """Force a preview-safe MIME. PDFs must be application/pdf, never octet-stream."""
    name = (filename or "").lower()
    claimed = (claimed or "").split(";", 1)[0].strip().lower()
    head = data[:8] if data else b""
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if name.endswith(".pdf") or "pdf" in claimed:
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff") or claimed in ("image/jpeg", "image/jpg") or name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if head.startswith(b"\x89PNG") or claimed == "image/png" or name.endswith(".png"):
        return "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a") or claimed == "image/gif" or name.endswith(".gif"):
        return "image/gif"
    if claimed.startswith("image/webp") or name.endswith(".webp"):
        return "image/webp"
    if claimed and claimed != "application/octet-stream":
        return claimed
    return "application/octet-stream"


def content_disposition(filename: str, *, download: bool) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "surat-jalan").strip()) or "surat-jalan"
    disp = "attachment" if download else "inline"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "surat-jalan"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def history_entries(order: dict) -> List[dict]:
    """Per-SJ history for Detail PO / Jadwal / Driver: file + item/qty, no bytes."""
    out = []
    seen = set()
    for sh in shipments_stored(order):
        f = file_for_shipment(order, sh)
        fid = _safe_str((f or {}).get("id") or sh.get("file_id") or sh.get("id"))
        if fid:
            seen.add(fid)
        lines = entry_lines(sh) or entry_lines(f)
        has_qty = has_valid_item_qty(f, sh)
        has_file = bool(f and file_has_blob(f))
        out.append({
            "id": fid or f"sj-{len(out)}",
            "file_id": _safe_str((f or {}).get("id")) or None,
            "shipment_id": _safe_str(sh.get("id")) or None,
            "name": (f or {}).get("name") or sh.get("file_name") or sh.get("number") or "Surat Jalan",
            "uploaded_by": (f or {}).get("uploaded_by") or sh.get("created_by"),
            "uploaded_by_name": (f or {}).get("uploaded_by_name") or sh.get("created_by_name"),
            "uploaded_at": (f or {}).get("uploaded_at") or sh.get("created_at"),
            "lines": lines,
            "has_item_qty": has_qty,
            "has_file": has_file,
            "legacy_incomplete": bool(has_file and not has_qty) or bool(has_qty and not has_file),
            "schedule_ready": shipment_is_schedule_ready(order, sh),
        })
    for f in file_entries(order):
        fid = _safe_str(f.get("id"))
        if fid and fid in seen:
            continue
        has_qty = has_valid_item_qty(f, None)
        out.append({
            "id": fid or f"sj-{len(out)}",
            "file_id": fid or None,
            "shipment_id": _safe_str(f.get("shipment_id")) or None,
            "name": f.get("name") or "Surat Jalan",
            "uploaded_by": f.get("uploaded_by"),
            "uploaded_by_name": f.get("uploaded_by_name"),
            "uploaded_at": f.get("uploaded_at"),
            "lines": entry_lines(f),
            "has_item_qty": has_qty,
            "has_file": file_has_blob(f),
            "legacy_incomplete": not has_qty,
            "schedule_ready": bool(file_has_blob(f) and has_qty),
        })
    return out
