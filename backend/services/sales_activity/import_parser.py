"""Parse SISTEM SALES Excel. Preview-only: never writes business data."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import load_workbook

from services.sales_activity.clock import parse_dt
from services.sales_activity.constants import MAX_ACTIVITY_ROWS, MAX_IMPORT_BYTES, MAX_OUTLET_ROWS
from services.sales_activity.util import is_blank, normalize_text, strong_text

_WS = re.compile(r"[\s\-\./\\|]+")
_PARENS = re.compile(r"\([^)]*\)")
_BOM = "\ufeff"

OUTLET_SHEETS = {
    "input data", "master outlet", "formulir input", "outlet", "outlets", "data outlet",
}
ACTIVITY_SHEETS = {
    "input activity", "activity", "aktivitas", "sales activity", "input aktivitas",
}
SALES_SHEETS = {"sales_master", "sales master", "master sales"}

OUTLET_ALIASES = {
    "outlet_code": ["outlet_id", "id_outlet", "kode_outlet", "outlet_code", "id"],
    "name": ["nama_outlet", "outlet", "nama", "customer", "nama_customer"],
    "address": ["alamat", "address"],
    "maps_url": ["maps", "map", "google_maps", "maps_url"],
    "instagram": ["ig", "instagram"],
    "marketplace": ["marketplace", "shopee", "tokopedia"],
    "category": ["kategori", "category"],
    "pic_name": ["pic_outlet", "pic", "nama_pic"],
    "pic_title": ["jabatan", "title", "jabatan_pic"],
    "whatsapp": ["whatsapp", "wa", "no_wa", "telepon", "phone"],
    "lead_source": ["sumber_lead", "sumber", "lead_source"],
    "sales_name": ["pic_sales", "sales", "nama_sales"],
    "claimed_at": ["tanggal_klaim", "tgl_klaim", "claim_date"],
}

ACTIVITY_ALIASES = {
    "activity_at": ["tanggal_aktivitas", "tanggal", "tgl_aktivitas", "activity_date", "date"],
    "sales_name": ["sales", "nama_sales", "pic_sales"],
    "outlet_name": ["outlet", "nama_outlet", "customer"],
    "outlet_code": ["outlet_id", "id_outlet", "kode_outlet"],
    "activity_type": ["jenis_aktivitas", "jenis", "tipe", "activity_type"],
    "result": ["hasil", "result"],
    "next_action": ["next_action", "aksi_selanjutnya"],
    "next_follow_up_at": ["next_follow_up", "follow_up", "tgl_follow_up"],
    "notes": ["notes", "catatan", "keterangan"],
    "evidence_type": ["evidence_type", "tipe_evidence"],
    "evidence_link": ["evidence_link", "link_evidence", "bukti"],
}


class SalesImportParseError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _norm_header(k: str) -> str:
    k = (k or "").replace(_BOM, "")
    k = k.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    k = _PARENS.sub(" ", k)
    k = k.strip().lower()
    k = _WS.sub("_", k)
    return re.sub(r"_+", "_", k).strip("_")


def _build_lookup(aliases: Dict[str, List[str]]) -> Dict[str, str]:
    lookup = {}
    for canon, names in aliases.items():
        lookup[canon] = canon
        for name in names:
            lookup[_norm_header(name)] = canon
    return lookup


OUTLET_LOOKUP = _build_lookup(OUTLET_ALIASES)
ACTIVITY_LOOKUP = _build_lookup(ACTIVITY_ALIASES)


def _map_headers(raw: Sequence[Any], lookup: Dict[str, str]) -> List[str]:
    out = []
    for h in raw:
        key = _norm_header("" if h is None else str(h))
        out.append(lookup.get(key, key))
    return out


def _is_blank_row(cells: Sequence[Any]) -> bool:
    for c in cells:
        if c is None:
            continue
        if isinstance(c, str) and not c.strip():
            continue
        return False
    return True


def _parse_excel_date(val: Any) -> Optional[str]:
    if val is None or is_blank(val):
        return None
    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, tzinfo=timezone.utc).isoformat()
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            from openpyxl.utils.datetime import from_excel
            dt = from_excel(val)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
        except Exception:
            return None
    parsed = parse_dt(str(val).strip())
    return parsed.isoformat() if parsed else None


def _row_dict(headers: List[str], cells: Sequence[Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for i, key in enumerate(headers):
        val = cells[i] if i < len(cells) else None
        if isinstance(val, str):
            out[key] = val.strip()
        else:
            out[key] = val
    return out


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _looks_like_outlet(headers: List[str]) -> bool:
    s = set(headers)
    return "name" in s or "outlet_code" in s


def _looks_like_activity(headers: List[str]) -> bool:
    s = set(headers)
    return "activity_at" in s or "activity_type" in s


def parse_sales_workbook(filename: str, content: bytes) -> dict:
    if not filename.lower().endswith(".xlsx"):
        raise SalesImportParseError("Hanya file .xlsx yang didukung")
    if len(content) > MAX_IMPORT_BYTES:
        raise SalesImportParseError("File terlalu besar")
    if not content:
        raise SalesImportParseError("File kosong")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise SalesImportParseError(f"File Excel tidak valid: {exc}") from exc

    outlets: List[dict] = []
    activities: List[dict] = []
    sales_names: List[str] = []
    row_errors: List[dict] = []
    sheets_used = []

    try:
        for ws in wb.worksheets:
            title = normalize_text(ws.title)
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header_idx = 0
            headers_raw = None
            for i, row in enumerate(rows[:15]):
                if row and not _is_blank_row(row):
                    headers_raw = row
                    header_idx = i
                    break
            if headers_raw is None:
                continue
            if title in ACTIVITY_SHEETS or (title not in OUTLET_SHEETS and _looks_like_activity(_map_headers(headers_raw, ACTIVITY_LOOKUP))):
                headers = _map_headers(headers_raw, ACTIVITY_LOOKUP)
                if not _looks_like_activity(headers):
                    continue
                sheets_used.append({"name": ws.title, "kind": "activity"})
                for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if _is_blank_row(row or []):
                        continue
                    if len(activities) >= MAX_ACTIVITY_ROWS:
                        row_errors.append({"sheet": ws.title, "row": offset, "error": "Batas baris aktivitas terlampaui"})
                        break
                    parsed = _row_dict(headers, row or [])
                    outlet_name = strong_text(parsed.get("outlet_name"))
                    if not outlet_name and not strong_text(parsed.get("outlet_code")):
                        row_errors.append({"sheet": ws.title, "row": offset, "error": "Outlet aktivitas kosong"})
                        continue
                    at = _parse_excel_date(parsed.get("activity_at"))
                    if not at:
                        row_errors.append({"sheet": ws.title, "row": offset, "error": "Tanggal aktivitas tidak valid"})
                        continue
                    activities.append({
                        "source_row": offset,
                        "source_sheet": ws.title,
                        "outlet_name": outlet_name,
                        "outlet_code": strong_text(parsed.get("outlet_code")),
                        "sales_name": strong_text(parsed.get("sales_name")),
                        "activity_at": at,
                        "activity_type": str(parsed.get("activity_type") or "").strip() or "visit",
                        "result": str(parsed.get("result") or "").strip(),
                        "next_action": str(parsed.get("next_action") or "").strip(),
                        "next_follow_up_at": _parse_excel_date(parsed.get("next_follow_up_at")),
                        "notes": str(parsed.get("notes") or "").strip(),
                        "evidence_type": str(parsed.get("evidence_type") or "").strip(),
                        "evidence_link": str(parsed.get("evidence_link") or "").strip(),
                    })
                continue
            if title in SALES_SHEETS:
                sheets_used.append({"name": ws.title, "kind": "sales"})
                for row in rows[header_idx + 1 :]:
                    if _is_blank_row(row or []):
                        continue
                    name = strong_text((row or [None])[0])
                    if name:
                        sales_names.append(name)
                continue
            headers = _map_headers(headers_raw, OUTLET_LOOKUP)
            if title in OUTLET_SHEETS or _looks_like_outlet(headers):
                if not _looks_like_outlet(headers):
                    continue
                sheets_used.append({"name": ws.title, "kind": "outlet"})
                for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if _is_blank_row(row or []):
                        continue
                    if len(outlets) >= MAX_OUTLET_ROWS:
                        row_errors.append({"sheet": ws.title, "row": offset, "error": "Batas baris outlet terlampaui"})
                        break
                    parsed = _row_dict(headers, row or [])
                    name = strong_text(parsed.get("name"))
                    if not name:
                        row_errors.append({"sheet": ws.title, "row": offset, "error": "Nama outlet kosong"})
                        continue
                    outlets.append({
                        "source_row": offset,
                        "source_sheet": ws.title,
                        "outlet_code": strong_text(parsed.get("outlet_code")),
                        "name": name,
                        "address": str(parsed.get("address") or "").strip(),
                        "maps_url": str(parsed.get("maps_url") or "").strip(),
                        "instagram": str(parsed.get("instagram") or "").strip(),
                        "marketplace": str(parsed.get("marketplace") or "").strip(),
                        "category": str(parsed.get("category") or "").strip(),
                        "pic_name": str(parsed.get("pic_name") or "").strip(),
                        "pic_title": str(parsed.get("pic_title") or "").strip(),
                        "whatsapp": str(parsed.get("whatsapp") or "").strip(),
                        "lead_source": str(parsed.get("lead_source") or "").strip(),
                        "sales_name": strong_text(parsed.get("sales_name")),
                        "claimed_at": _parse_excel_date(parsed.get("claimed_at")),
                    })
    finally:
        wb.close()

    if not outlets and not activities:
        raise SalesImportParseError("Tidak ada sheet outlet/aktivitas yang dikenali")

    return {
        "filename": filename,
        "file_hash": file_hash(content),
        "sheets": sheets_used,
        "outlets": outlets,
        "activities": activities,
        "sales_names": sales_names,
        "row_errors": row_errors,
        "total_outlet_rows": len(outlets),
        "total_activity_rows": len(activities),
    }
