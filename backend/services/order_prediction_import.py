"""Supplemental historical orders for Prediksi Order.

NOT production POs. Stored separately and merged into the existing prediction engine.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.import_parser import ImportParseError, _read_rows
from services.order_prediction import (
    WIB,
    customer_group_key,
    normalize_name,
    order_date_of,
    parse_iso_date,
    wib_today,
)

PERM_IMPORT = "reports.order_prediction.import"
COLLECTION = "order_prediction_history"
SOURCE_LIVE = "ORDER MASUK"
SOURCE_IMPORT = "IMPORT HISTORIS"

_ALIASES = {
    "customer": ["customer", "nama_customer", "pelanggan", "customer_name", "nama_pelanggan"],
    "outlet": ["outlet", "cabang", "toko", "outlet_cabang", "branch"],
    "order_date": ["order_date", "tanggal_order", "tanggal", "tgl_order", "tgl", "date"],
    "customer_id": ["customer_id", "id_customer", "cust_id"],
    "outlet_id": ["outlet_id", "delivery_outlet_id", "id_outlet"],
    "po_number": ["po_number", "no_order", "no_po", "nomor_po", "nomor_order", "order_number"],
    "sales": ["sales", "salesperson", "tenaga_penjual"],
    "notes": ["notes", "catatan", "keterangan"],
    "omzet": ["omzet", "revenue", "amount", "nilai"],
}
_HEADER_LOOKUP: Dict[str, str] = {}
for canon, aliases in _ALIASES.items():
    for a in aliases:
        _HEADER_LOOKUP[a] = canon

_WS = re.compile(r"[\s\-\.]+")


def _norm_header(k: str) -> str:
    k = (k or "").strip().lower()
    k = _WS.sub("_", k)
    return _HEADER_LOOKUP.get(k, k)


def _row_dict(headers: List[str], cells: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm_header(h)
        val = cells[i] if i < len(cells) else ""
        out[key] = "" if val is None else str(val).strip()
    return out


def _parse_omzet(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None, None
    s = str(raw).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    # id-ID: 1.234.567,89 or plain 1234567.89
    if re.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None, "Omzet harus angka atau kosong"
    if n != n or n == float("inf") or n == float("-inf"):
        return None, "Omzet tidak valid"
    if n < 0:
        return None, "Omzet tidak boleh negatif"
    if n == int(n):
        return int(n), None
    return n, None


def _parse_order_date(raw: Any, today: date) -> Tuple[Optional[date], Optional[str]]:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None, "Tanggal Order wajib"
    if isinstance(raw, datetime):
        d = raw.astimezone(WIB).date() if raw.tzinfo else raw.replace(tzinfo=WIB).astimezone(WIB).date()
    elif isinstance(raw, date) and not isinstance(raw, datetime):
        d = raw
    else:
        s = str(raw).strip()
        d = parse_iso_date(s)
        if d is None:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y"):
                try:
                    d = datetime.strptime(s, fmt).date()
                    break
                except ValueError:
                    continue
        if d is None:
            return None, "Tanggal Order tidak valid"
    if d > today:
        return None, "Tanggal Order tidak boleh di masa depan"
    return d, None


def history_fingerprint(doc: dict) -> str:
    """Deterministic dedup key for imported (and live) history rows."""
    po = str(doc.get("po_number") or "").strip().lower()
    if po:
        return f"po:{po}"
    cid = str(doc.get("customer_id") or "").strip()
    oid = str(doc.get("outlet_id") or doc.get("delivery_outlet_id") or "").strip()
    d = order_date_of(doc)
    date_s = d.isoformat() if d else ""
    if cid or oid:
        return f"id:{cid}|oid:{oid}|d:{date_s}|po:{po}"
    cname = normalize_name(doc.get("customer") or doc.get("customer_name"))
    oname = normalize_name(doc.get("outlet"))
    notes = normalize_name(doc.get("notes"))
    blob = f"name:{cname}|out:{oname}|d:{date_s}|po:{po}|n:{notes}"
    return "fp:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def as_prediction_order(doc: dict) -> dict:
    """Shape compatible with order_prediction.is_valid_business_order + analyze_group."""
    omzet = doc.get("omzet")
    if omzet is not None and str(omzet).strip() == "":
        omzet = None
    return {
        "id": doc.get("id"),
        "po_number": doc.get("po_number") or "",
        "order_date": doc.get("order_date"),
        "customer": doc.get("customer") or doc.get("customer_name") or "",
        "customer_name": doc.get("customer") or doc.get("customer_name") or "",
        "customer_id": doc.get("customer_id"),
        "outlet": doc.get("outlet") or "",
        "delivery_outlet_id": doc.get("outlet_id") or doc.get("delivery_outlet_id"),
        "outlet_id": doc.get("outlet_id") or doc.get("delivery_outlet_id"),
        "sales": doc.get("sales") or "",
        "notes": doc.get("notes") or "",
        "omzet": omzet,
        "cancelled": False,
        "source": SOURCE_IMPORT,
        "history_source": SOURCE_IMPORT,
        "_fingerprint": history_fingerprint(doc),
    }


def live_as_prediction_order(order: dict) -> dict:
    out = dict(order)
    out["source"] = SOURCE_LIVE
    out["history_source"] = SOURCE_LIVE
    out["omzet"] = order.get("omzet") if order.get("omzet") not in ("",) else None
    if out.get("omzet") is None:
        out["omzet"] = None
    out["_fingerprint"] = history_fingerprint(out)
    return out


def merge_prediction_orders(
    live_orders: Sequence[dict],
    imported: Sequence[dict],
) -> List[dict]:
    """Live Order Masuk wins on fingerprint collision."""
    merged: List[dict] = []
    seen: set = set()
    for o in live_orders:
        row = live_as_prediction_order(o)
        fp = row["_fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
    for o in imported:
        row = as_prediction_order(o)
        fp = row["_fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
    return merged


def build_history_import_preview(
    data: bytes,
    filename: str,
    *,
    live_orders: Sequence[dict],
    existing_imports: Sequence[dict],
    now: Optional[datetime] = None,
) -> dict:
    try:
        headers, raw_rows = _read_rows(data, filename)
    except ImportParseError:
        raise
    except Exception as e:
        raise ImportParseError(str(e)) from e
    if not raw_rows:
        raise ImportParseError("File tidak berisi baris data.")

    today = wib_today(now)
    live_fps = {history_fingerprint(o) for o in live_orders}
    import_fps = {history_fingerprint(o) for o in existing_imports}
    file_seen: Dict[str, int] = {}

    rows: List[dict] = []
    for idx, cells in enumerate(raw_rows):
        parsed = _row_dict(headers, cells)
        customer = parsed.get("customer") or ""
        outlet = parsed.get("outlet") or ""
        status = "VALID"
        reason = None
        selectable = True
        order_date, date_err = _parse_order_date(parsed.get("order_date"), today)
        omzet, omzet_err = _parse_omzet(parsed.get("omzet"))
        if date_err:
            status = "INVALID"
            reason = date_err
            selectable = False
        if omzet_err:
            status = "INVALID"
            reason = omzet_err
            selectable = False
        if not customer.strip() and not str(parsed.get("customer_id") or "").strip():
            status = "INVALID"
            reason = "Nama Customer wajib"
            selectable = False

        draft = {
            "customer": customer,
            "customer_name": customer,
            "customer_id": parsed.get("customer_id") or None,
            "outlet": outlet,
            "outlet_id": parsed.get("outlet_id") or None,
            "delivery_outlet_id": parsed.get("outlet_id") or None,
            "order_date": order_date.isoformat() if order_date else None,
            "po_number": parsed.get("po_number") or "",
            "sales": parsed.get("sales") or "",
            "notes": parsed.get("notes") or "",
            "omzet": omzet,
        }
        fp = history_fingerprint(draft) if order_date or draft["po_number"] else f"row:{idx}"

        if status == "VALID":
            if fp in live_fps:
                status = "DUPLIKAT"
                reason = "Duplikat dengan Order Masuk (Order Masuk menang)"
                selectable = False
            elif fp in import_fps:
                status = "DUPLIKAT"
                reason = "Sudah pernah diimpor"
                selectable = False
            elif fp in file_seen:
                status = "DUPLIKAT"
                reason = f"Duplikat dalam file (baris {file_seen[fp] + 1})"
                selectable = False
            else:
                file_seen[fp] = idx

        rows.append({
            "row_id": f"r{idx}",
            "row_index": idx,
            "customer": customer,
            "outlet": outlet,
            "customer_id": draft["customer_id"],
            "outlet_id": draft["outlet_id"],
            "order_date": draft["order_date"],
            "po_number": draft["po_number"],
            "sales": draft["sales"] or None,
            "notes": draft["notes"] or None,
            "omzet": omzet,
            "fingerprint": fp,
            "status": status,
            "reason": reason,
            "selectable": selectable,
            "selected": selectable,
        })

    file_fp = hashlib.sha256(data).hexdigest()[:16]
    return {
        "filename": filename,
        "file_fingerprint": file_fp,
        "rows": rows,
        "total": len(rows),
        "valid_count": sum(1 for r in rows if r["status"] == "VALID"),
        "invalid_count": sum(1 for r in rows if r["status"] == "INVALID"),
        "duplicate_count": sum(1 for r in rows if r["status"] == "DUPLIKAT"),
    }


def validate_history_apply_rows(
    selected: List[dict],
    *,
    live_orders: Sequence[dict],
    existing_imports: Sequence[dict],
    now: Optional[datetime] = None,
) -> Tuple[List[dict], List[dict]]:
    today = wib_today(now)
    live_fps = {history_fingerprint(o) for o in live_orders}
    import_fps = {history_fingerprint(o) for o in existing_imports}
    accepted: List[dict] = []
    rejected: List[dict] = []
    seen: set = set()

    for raw in selected or []:
        if not isinstance(raw, dict):
            rejected.append({"reason": "Baris tidak valid"})
            continue
        customer = str(raw.get("customer") or raw.get("customer_name") or "").strip()
        outlet = str(raw.get("outlet") or "").strip()
        if not customer and not str(raw.get("customer_id") or "").strip():
            rejected.append({**raw, "status": "INVALID", "reason": "Nama Customer wajib"})
            continue
        order_date, date_err = _parse_order_date(raw.get("order_date"), today)
        if date_err:
            rejected.append({**raw, "status": "INVALID", "reason": date_err})
            continue
        omzet, omzet_err = _parse_omzet(raw.get("omzet"))
        if omzet_err:
            rejected.append({**raw, "status": "INVALID", "reason": omzet_err})
            continue
        draft = {
            "id": str(uuid.uuid4()),
            "customer": customer,
            "customer_name": customer,
            "customer_id": str(raw.get("customer_id") or "").strip() or None,
            "outlet": outlet,
            "outlet_id": str(raw.get("outlet_id") or "").strip() or None,
            "delivery_outlet_id": str(raw.get("outlet_id") or raw.get("delivery_outlet_id") or "").strip() or None,
            "order_date": order_date.isoformat(),
            "po_number": str(raw.get("po_number") or "").strip(),
            "sales": str(raw.get("sales") or "").strip(),
            "notes": str(raw.get("notes") or "").strip(),
            "omzet": omzet,
            "source": SOURCE_IMPORT,
            "fingerprint": "",
        }
        draft["fingerprint"] = history_fingerprint(draft)
        fp = draft["fingerprint"]
        if fp in live_fps or fp in import_fps or fp in seen:
            rejected.append({**raw, "status": "DUPLIKAT", "reason": "Duplikat — dilewati"})
            continue
        seen.add(fp)
        accepted.append(draft)
    return accepted, rejected
