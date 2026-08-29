"""Stock Saat Ini — import reconciliation (set stock to file value, not +/-).

Preview-only until apply. Identity: accurate_item_id / master_item_id / item_code.
Never match by name alone when a stable id/code exists. Never create master items.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.current_stock import (
    compute_stock_current,
    identity_keys,
    parse_manual_qty,
    qty_num,
)
from services.import_parser import ImportParseError, _read_rows

PERM_IMPORT = "current_stock.import"

_ALIASES = {
    "item_id": [
        "item_id", "master_item_id", "stock_item_id", "id",
    ],
    "accurate_item_id": [
        "accurate_item_id", "accurate_id", "accurateid",
    ],
    "item_code": [
        "item_code", "kode", "kode_barang", "sku", "code",
    ],
    "item_name": [
        "item_name", "nama", "nama_item", "nama_barang", "name",
    ],
    "stock_file": [
        "stock_file", "stock", "stock_saat_ini", "qty", "quantity",
        "qty_stock", "stok", "stok_saat_ini",
    ],
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


def manual_qty_for_target_stock(
    *,
    stock_target: float,
    purchase_qty: Any,
    po_qty: Any,
    operator_print_qty: Any,
) -> float:
    """Invert stock_current = (manual + purchase) - po - print."""
    raw = qty_num(stock_target) - qty_num(purchase_qty) + qty_num(po_qty) + qty_num(operator_print_qty)
    if isinstance(raw, float) and raw == int(raw):
        return int(raw)
    return raw


def _row_dict(headers: List[str], cells: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm_header(h)
        val = cells[i] if i < len(cells) else ""
        out[key] = "" if val is None else str(val).strip()
    return out


def _find_master(masters: Iterable[dict], row: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Resolve master by stable id then unique item_code. Never name-only."""
    masters = list(masters)
    aid = str(row.get("accurate_item_id") or "").strip()
    mid = str(row.get("item_id") or "").strip()
    code = str(row.get("item_code") or "").strip()

    if aid:
        hits = [m for m in masters if str(m.get("accurate_item_id") or "").strip() == aid]
        if len(hits) == 1:
            return hits[0], None
        if not hits:
            return None, "TIDAK DITEMUKAN"
        return None, "ID AMBIGU"

    if mid:
        hits = [
            m for m in masters
            if str(m.get("id") or "").strip() == mid
            or str(m.get("master_item_id") or "").strip() == mid
        ]
        if len(hits) == 1:
            return hits[0], None
        if not hits:
            return None, "TIDAK DITEMUKAN"
        return None, "ID AMBIGU"

    if code:
        exact = [m for m in masters if str(m.get("item_code") or "").strip() == code]
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, "KODE AMBIGU"
        lower = [m for m in masters if str(m.get("item_code") or "").strip().lower() == code.lower()]
        if len(lower) == 1:
            return lower[0], None
        if len(lower) > 1:
            return None, "KODE AMBIGU"
        return None, "TIDAK DITEMUKAN"

    return None, "TIDAK DITEMUKAN"


def _identity_token(master: dict) -> str:
    keys = identity_keys(master)
    return keys[0] if keys else f"m:{master.get('id')}"


def build_stock_import_preview(
    data: bytes,
    filename: str,
    *,
    stock_rows: List[dict],
    masters: List[dict],
) -> dict:
    try:
        headers, raw_rows = _read_rows(data, filename)
    except ImportParseError:
        raise
    except Exception as e:
        raise ImportParseError(str(e)) from e

    if not raw_rows:
        raise ImportParseError("File tidak berisi baris data.")

    by_key: Dict[str, dict] = {}
    for r in stock_rows:
        for k in identity_keys(r):
            by_key[k] = r
        mid = str(r.get("master_item_id") or "").strip()
        if mid:
            by_key[f"m:{mid}"] = r
        aid = str(r.get("accurate_item_id") or "").strip()
        if aid:
            by_key[f"a:{aid}"] = r

    preview_rows: List[dict] = []
    seen_tokens: Dict[str, int] = {}

    for idx, cells in enumerate(raw_rows):
        parsed = _row_dict(headers, cells)
        row_id = f"r{idx}"
        master, miss = _find_master(masters, parsed)
        stock_system = None
        item_code = parsed.get("item_code") or ""
        item_name = parsed.get("item_name") or ""
        master_item_id = None
        accurate_item_id = None
        status = "VALID"
        reason = None
        selectable = True
        stock_file = None
        delta = None
        purchase_qty = 0
        po_qty = 0
        operator_print_qty = 0

        raw_qty = parsed.get("stock_file")
        try:
            stock_file = parse_manual_qty(raw_qty, allow_negative=True)
        except ValueError as e:
            status = "INVALID"
            reason = str(e)
            selectable = False

        if master:
            master_item_id = master.get("id") or master.get("master_item_id")
            accurate_item_id = master.get("accurate_item_id")
            item_code = master.get("item_code") or item_code
            item_name = master.get("item_name") or item_name
            token = _identity_token(master)
            if token in seen_tokens:
                status = "DUPLIKAT"
                reason = f"Item duplikat dengan baris {seen_tokens[token] + 1}"
                selectable = False
            else:
                seen_tokens[token] = idx
            stock_row = None
            for k in identity_keys(master):
                if k in by_key:
                    stock_row = by_key[k]
                    break
            if stock_row:
                stock_system = qty_num(stock_row.get("stock_current"))
                purchase_qty = qty_num(stock_row.get("purchase_qty"))
                po_qty = qty_num(stock_row.get("qty_po"))
                operator_print_qty = qty_num(stock_row.get("operator_print_qty"))
            else:
                stock_system = 0
            if status == "VALID" and stock_file is not None:
                delta = qty_num(stock_file) - qty_num(stock_system)
        else:
            status = "TIDAK DITEMUKAN" if miss == "TIDAK DITEMUKAN" else (miss or "TIDAK DITEMUKAN")
            reason = "Item tidak ditemukan di master"
            selectable = False
            if stock_file is None and status == "INVALID":
                pass
            elif stock_file is not None and miss:
                status = miss
                reason = "Item tidak ditemukan di master"
                selectable = False

        if status == "INVALID":
            selectable = False

        preview_rows.append({
            "row_id": row_id,
            "row_index": idx,
            "master_item_id": master_item_id,
            "accurate_item_id": accurate_item_id,
            "item_code": item_code,
            "item_name": item_name,
            "stock_system": stock_system,
            "stock_file": stock_file,
            "delta": delta,
            "purchase_qty": purchase_qty,
            "qty_po": po_qty,
            "operator_print_qty": operator_print_qty,
            "status": status,
            "reason": reason,
            "selectable": selectable,
            "selected": selectable,
        })

    file_fp = hashlib.sha256(data).hexdigest()[:16]
    valid = sum(1 for r in preview_rows if r["status"] == "VALID")
    invalid = sum(1 for r in preview_rows if r["status"] in ("INVALID", "TIDAK DITEMUKAN", "KODE AMBIGU", "ID AMBIGU"))
    dup = sum(1 for r in preview_rows if r["status"] == "DUPLIKAT")
    return {
        "filename": filename,
        "file_fingerprint": file_fp,
        "rows": preview_rows,
        "total": len(preview_rows),
        "valid_count": valid,
        "invalid_count": invalid,
        "duplicate_count": dup,
    }


def validate_apply_rows(
    selected: List[dict],
    *,
    stock_rows: List[dict],
    masters: List[dict],
) -> Tuple[List[dict], List[dict]]:
    """Authoritative re-validation of selected rows. Returns (accepted, rejected)."""
    by_master = {str(m.get("id") or ""): m for m in masters}
    by_acc = {
        str(m.get("accurate_item_id") or "").strip(): m
        for m in masters
        if str(m.get("accurate_item_id") or "").strip()
    }
    by_code = {}
    for m in masters:
        code = str(m.get("item_code") or "").strip()
        if code:
            by_code.setdefault(code, []).append(m)

    stock_by_key: Dict[str, dict] = {}
    for r in stock_rows:
        for k in identity_keys(r):
            stock_by_key[k] = r

    accepted: List[dict] = []
    rejected: List[dict] = []
    seen: set = set()

    for raw in selected or []:
        if not isinstance(raw, dict):
            rejected.append({"reason": "Baris tidak valid", "row": raw})
            continue
        mid = str(raw.get("master_item_id") or "").strip()
        aid = str(raw.get("accurate_item_id") or "").strip()
        code = str(raw.get("item_code") or "").strip()
        master = None
        if mid and mid in by_master:
            master = by_master[mid]
        elif aid and aid in by_acc:
            master = by_acc[aid]
        elif code and code in by_code and len(by_code[code]) == 1:
            master = by_code[code][0]
        if not master:
            rejected.append({**raw, "reason": "Item tidak ditemukan", "status": "TIDAK DITEMUKAN"})
            continue
        token = _identity_token(master)
        if token in seen:
            rejected.append({**raw, "reason": "Item duplikat dalam permintaan", "status": "DUPLIKAT"})
            continue
        seen.add(token)
        try:
            stock_file = parse_manual_qty(raw.get("stock_file"), allow_negative=True)
        except ValueError as e:
            rejected.append({**raw, "reason": str(e), "status": "INVALID"})
            continue

        stock_row = None
        for k in identity_keys(master):
            if k in stock_by_key:
                stock_row = stock_by_key[k]
                break
        purchase_qty = qty_num((stock_row or {}).get("purchase_qty"))
        po_qty = qty_num((stock_row or {}).get("qty_po"))
        op_qty = qty_num((stock_row or {}).get("operator_print_qty"))
        stock_before = qty_num((stock_row or {}).get("stock_current"))
        manual_target = manual_qty_for_target_stock(
            stock_target=stock_file,
            purchase_qty=purchase_qty,
            po_qty=po_qty,
            operator_print_qty=op_qty,
        )
        stock_after = compute_stock_current(po_qty, manual_target, purchase_qty, op_qty)
        accepted.append({
            "master_item_id": master.get("id"),
            "accurate_item_id": master.get("accurate_item_id"),
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or "",
            "stock_file": stock_file,
            "stock_before": stock_before,
            "stock_after": stock_after,
            "delta": qty_num(stock_after) - qty_num(stock_before),
            "manual_current_qty": manual_target,
            "purchase_qty": purchase_qty,
            "qty_po": po_qty,
            "operator_print_qty": op_qty,
        })
    return accepted, rejected
