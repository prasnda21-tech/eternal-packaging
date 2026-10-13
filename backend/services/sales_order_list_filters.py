"""Pesanan Penjualan list header-filter helpers (read-only matching).

Keeps filter semantics aligned with list cell presentation without mutating orders.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from services.sales_date_presets import _parse_day
from services.sales_order_invoice_status import CREATOR_UNKNOWN_LABEL, creator_display
from services.status_po import STAGES

PROCESS_HOLD = "HOLD"
PROCESS_CANCELLED = "Cancelled"
CREATOR_UNKNOWN_TOKEN = "__unknown__"


def parse_iso_day(raw: Any) -> Optional[str]:
    day = _parse_day(raw)
    return day.isoformat() if day else None


def validate_date_range(date_from: Optional[str], date_to: Optional[str]) -> Optional[str]:
    """Return error message when both bounds exist and from > to; else None."""
    start = parse_iso_day(date_from)
    end = parse_iso_day(date_to)
    if start and end and start > end:
        return "Tanggal Dari tidak boleh lebih besar dari Sampai."
    return None


def order_date_mongo_clause(date_from: Optional[str], date_to: Optional[str]) -> dict:
    clause: Dict[str, str] = {}
    start = (parse_iso_day(date_from) or "").strip()[:10]
    end = (parse_iso_day(date_to) or "").strip()[:10]
    if start:
        clause["$gte"] = start
    if end:
        clause["$lte"] = f"{end}T23:59:59.999999"
    return {"order_date": clause} if clause else {}


def text_search_or_clause(needle: str, fields: Sequence[str]) -> Optional[dict]:
    text = str(needle or "").strip()
    if not text:
        return None
    pattern = re.escape(text)
    return {
        "$or": [{field: {"$regex": pattern, "$options": "i"}} for field in fields]
    }


def creator_mongo_clause(created_by: Optional[str]) -> Optional[dict]:
    raw = str(created_by or "").strip()
    if not raw:
        return None
    if raw in {CREATOR_UNKNOWN_TOKEN, CREATOR_UNKNOWN_LABEL}:
        return {
            "$or": [
                {"created_by_user": {"$exists": False}},
                {"created_by_user": None},
                {"created_by_user": ""},
                {"created_by_user": {"$regex": r"^\s*$"}},
            ]
        }
    return {"created_by_user": raw}


def sales_order_process_label(order: Optional[dict]) -> str:
    """Mirror Pesanan Penjualan list badge: Cancelled → HOLD → stage → status_label."""
    if not order:
        return ""
    if order.get("cancelled"):
        return PROCESS_CANCELLED
    if order.get("hold"):
        return PROCESS_HOLD
    try:
        stage = int(order.get("current_stage"))
    except (TypeError, ValueError):
        stage = None
    if stage is not None and 0 <= stage < len(STAGES):
        return STAGES[stage]
    label = str(order.get("status_label") or "").strip()
    return label


def process_label_options() -> List[str]:
    return [PROCESS_HOLD, PROCESS_CANCELLED, *list(STAGES)]


def parse_csv_tokens(raw: Optional[str]) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if str(part or "").strip()]


def match_calendar_date_range(
    date_key: Any,
    date_from: Optional[str],
    date_to: Optional[str],
) -> bool:
    start = parse_iso_day(date_from)
    end = parse_iso_day(date_to)
    if not start and not end:
        return True
    day = _parse_day(date_key)
    if day is None:
        return False
    iso = day.isoformat()
    if start and iso < start:
        return False
    if end and iso > end:
        return False
    return True


def _parse_nonneg_number(raw: Any) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value != value:  # NaN
        return None
    return value


def validate_qty_range(qty_min: Any, qty_max: Any) -> Optional[str]:
    lo = _parse_nonneg_number(qty_min) if qty_min not in (None, "") else None
    hi = _parse_nonneg_number(qty_max) if qty_max not in (None, "") else None
    if qty_min not in (None, "") and lo is None:
        return "Qty minimum tidak valid."
    if qty_max not in (None, "") and hi is None:
        return "Qty maksimum tidak valid."
    if lo is not None and hi is not None and lo > hi:
        return "Qty minimum tidak boleh lebih besar dari maksimum."
    return None


def match_qty_range(
    order: Optional[dict],
    qty_min: Any = None,
    qty_max: Any = None,
    qty_unit: Optional[str] = None,
) -> bool:
    """Safe quantity match: never fold mixed units into one comparable number."""
    lo = _parse_nonneg_number(qty_min) if qty_min not in (None, "") else None
    hi = _parse_nonneg_number(qty_max) if qty_max not in (None, "") else None
    unit = str(qty_unit or "").strip()
    if lo is None and hi is None and not unit:
        return True

    parts = order.get("quantity_by_unit") if order else None
    if not isinstance(parts, list) or not parts:
        return False

    if unit:
        matched = next(
            (
                part
                for part in parts
                if isinstance(part, dict) and str(part.get("unit") or "").strip() == unit
            ),
            None,
        )
        if not matched:
            return False
        try:
            qty = float(matched.get("quantity"))
        except (TypeError, ValueError):
            return False
    else:
        if len(parts) != 1 or not isinstance(parts[0], dict):
            return False
        try:
            qty = float(parts[0].get("quantity"))
        except (TypeError, ValueError):
            return False

    if lo is not None and qty < lo:
        return False
    if hi is not None and qty > hi:
        return False
    return True


def match_process_labels(order: Optional[dict], labels: Iterable[str]) -> bool:
    wanted = {str(item).strip() for item in labels if str(item).strip()}
    if not wanted:
        return True
    return sales_order_process_label(order) in wanted


def scoped_creator_option_values(raw_values: Iterable[Any]) -> List[str]:
    """Unique creator display labels; empty snapshots collapse to Tidak diketahui."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in raw_values:
        label = creator_display(raw)
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    out.sort(key=lambda item: (item == CREATOR_UNKNOWN_LABEL, item.lower()))
    return out
