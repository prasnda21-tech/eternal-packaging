"""Jadwal Produksi default-scope matching — qty/work_date based, not stage.

Mirrors frontend ``rowMatchesDefaultProductionScheduleScope`` using the same
scheduled-vs-completed comparison already stored on operator_splits. Does not
invent a new progress formula and does not treat current_stage as 100%.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from quantity_rules import is_print_line


def to_date_key(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()[:10]
        except Exception:
            return None
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def production_month_key(date_key: Any) -> Optional[str]:
    key = to_date_key(date_key)
    return key[:7] if key and len(key) >= 7 else None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            return None
    return n


def _split_printable(order: dict, split: dict) -> bool:
    """Match desktop schedule: missing line → not printable; non_print excluded."""
    try:
        idx = int(split.get("line_idx"))
    except (TypeError, ValueError):
        # FE isPrintableScheduleSplit treats missing line_printable as printable;
        # without line_idx we cannot prove print — treat as printable only when
        # line_printable already stamped true-ish, else allow assigned qty path.
        if "line_printable" in split:
            return split.get("line_printable") is not False
        return True
    lines = order.get("line_items") or []
    if 0 <= idx < len(lines):
        return is_print_line(lines[idx])
    return False


def _assigned_printable_qty(split: dict) -> Optional[int]:
    if split.get("quantity") is None or split.get("quantity") == "":
        return None
    n = _safe_int(split.get("quantity"))
    if n is None or n <= 0:
        return None
    return n


def work_date_keys(order: dict) -> List[str]:
    keys: List[str] = []
    own = to_date_key(order.get("work_date"))
    if own:
        keys.append(own)
    for split in order.get("operator_splits") or []:
        k = to_date_key(split.get("work_date"))
        if k:
            keys.append(k)
    return keys


def _work_units(order: dict) -> List[dict]:
    """Operator+work_date units with assigned printable qty (FE workUnitProgress)."""
    cancelled = bool(order.get("cancelled"))
    splits = [
        s for s in (order.get("operator_splits") or [])
        if isinstance(s, dict) and _split_printable(order, s)
    ]
    buckets: Dict[Tuple[str, str], List[dict]] = {}
    for index, split in enumerate(splits):
        work_date = to_date_key(split.get("work_date"))
        if not work_date:
            continue
        op_key = (
            str(split.get("operator_user_id") or split.get("operator_id") or "").strip()
            or str(split.get("operator_email") or "").strip().lower()
            or str(split.get("operator_name") or "").strip().lower()
            or f"unknown:{index}"
        )
        buckets.setdefault((op_key, work_date), []).append(split)

    units: List[dict] = []
    for (_op, work_date), group in buckets.items():
        scheduled = 0
        has_assigned = False
        completed = 0
        seen = set()
        for split in group:
            assigned = _assigned_printable_qty(split)
            if assigned is None:
                continue
            has_assigned = True
            scheduled += assigned
            sid = split.get("id") or split.get("split_id")
            key = f"split:{sid}" if sid else f"idx:{split.get('line_idx')}:{id(split)}"
            if key in seen:
                continue
            seen.add(key)
            done = _safe_int(split.get("qty_completed"))
            completed += max(0, done or 0)
        if not has_assigned:
            continue
        units.append({
            "workDate": work_date,
            "scheduled": scheduled,
            "completed": completed,
            "cancelled": cancelled,
        })
    return units


def is_scheduled_work_unfinished(unit: dict) -> bool:
    scheduled = unit.get("scheduled")
    completed = unit.get("completed")
    if not isinstance(scheduled, (int, float)) or not isinstance(completed, (int, float)):
        return False
    return scheduled > 0 and completed < scheduled


def order_matches_default_production_schedule_scope(
    order: dict,
    *,
    today_key: str,
) -> bool:
    """Include current WIB month work, prior unfinished/unknown, unscheduled aktif."""
    if not order:
        return False
    current_month = production_month_key(today_key)
    if not current_month:
        return True

    keys = work_date_keys(order)
    units = _work_units(order)
    months = [m for m in (production_month_key(k) for k in keys) if m]

    if any(m == current_month for m in months):
        return True

    past_keys = [k for k in keys if (production_month_key(k) or "") < current_month]
    future_only = bool(keys) and not past_keys and all(
        (production_month_key(k) or "") > current_month for k in keys
    )

    if past_keys:
        past_units = [
            u for u in units
            if not u.get("cancelled")
            and (production_month_key(u.get("workDate")) or "") < current_month
        ]
        if not past_units:
            # Dates exist but no measurable assigned qty → unknown ≠ 100%.
            return True
        if any(is_scheduled_work_unfinished(u) for u in past_units):
            return True
        return False

    if future_only:
        return False

    if not keys:
        if order.get("cancelled"):
            return False
        if int(order.get("current_stage") or 0) >= 14:
            return False
        return True

    return any(
        (not u.get("cancelled") and is_scheduled_work_unfinished(u))
        for u in units
    )


def unfinished_split_expr_clause() -> dict:
    """Document-level $expr: any split with quantity > qty_completed (incl. missing completed).

    Avoids illegal two-field comparison inside ``$elemMatch``. Python matcher
    still applies printable/work_date exactness after lean scan.
    """
    return {
        "$expr": {
            "$gt": [
                {
                    "$size": {
                        "$filter": {
                            "input": {"$ifNull": ["$operator_splits", []]},
                            "as": "s",
                            "cond": {
                                "$and": [
                                    {"$gt": [{"$ifNull": ["$$s.quantity", 0]}, 0]},
                                    {
                                        "$lt": [
                                            {"$ifNull": ["$$s.qty_completed", 0]},
                                            "$$s.quantity",
                                        ]
                                    },
                                ]
                            },
                        }
                    }
                },
                0,
            ]
        }
    }


def past_unknown_schedule_clause(month_start: str) -> dict:
    """Past work_date present but no assigned quantity > 0 (unknown ≠ finished)."""
    # Mongo cannot use $ne twice on the same key — use $nin / $and.
    past_date = {
        "$or": [
            {"$and": [
                {"work_date": {"$exists": True}},
                {"work_date": {"$nin": [None, ""]}},
                {"work_date": {"$lt": month_start}},
            ]},
            {"operator_splits": {"$elemMatch": {
                "work_date": {"$exists": True, "$nin": [None, ""], "$lt": month_start},
            }}},
        ]
    }
    no_assigned = {
        "$or": [
            {"operator_splits": {"$exists": False}},
            {"operator_splits": {"$size": 0}},
            {"operator_splits": {"$not": {"$elemMatch": {"quantity": {"$gt": 0}}}}},
        ]
    }
    return {"$and": [past_date, no_assigned]}


def default_scope_candidate_clause(month_start: str, month_end: str) -> dict:
    """Narrow Mongo prefilter; Python applies exact qty/work_date scope.

    Intentionally excludes finished assigned rows that are neither current-month
    nor unfinished (the previous ``quantity > 0`` clause scanned almost all history).
    """
    month_clause = {
        "$or": [
            {"work_date": {"$gte": month_start, "$lte": month_end}},
            {"operator_splits.work_date": {"$gte": month_start, "$lte": month_end}},
        ]
    }
    unscheduled_open = {
        "$and": [
            {"$or": [
                {"work_date": None},
                {"work_date": {"$exists": False}},
                {"work_date": ""},
            ]},
            {"$or": [
                {"operator_splits": {"$exists": False}},
                {"operator_splits": {"$size": 0}},
                {"operator_splits": {"$not": {"$elemMatch": {
                    "work_date": {"$exists": True, "$nin": [None, ""]},
                }}}},
            ]},
            # Unscheduled "selesai" uses stage≥14 in FE statusOfRow — not qty 100%.
            {"current_stage": {"$lt": 14}},
        ]
    }
    return {
        "$or": [
            month_clause,
            unfinished_split_expr_clause(),
            past_unknown_schedule_clause(month_start),
            unscheduled_open,
        ]
    }


def schedule_text_search_clause(needle: Optional[str]) -> Optional[dict]:
    """Server-side outlet/customer/PO search — same fields as desktop client filter."""
    text = str(needle or "").strip()
    if not text:
        return None
    from services.sales_order_list_filters import text_search_or_clause
    return text_search_or_clause(text, ("po_number", "customer", "customer_name", "outlet"))


def schedule_work_date_range_clause(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Optional[dict]:
    """Match order-level or split work_date in range (OR across keys, like FE)."""
    start = to_date_key(date_from)
    end = to_date_key(date_to)
    if not start and not end:
        return None
    bounds: Dict[str, str] = {}
    if start:
        bounds["$gte"] = start
    if end:
        bounds["$lte"] = end
    return {
        "$or": [
            {"work_date": dict(bounds)},
            {"operator_splits.work_date": dict(bounds)},
        ]
    }


def schedule_deadline_range_clause(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Optional[dict]:
    """Broad server prefilter for Tanggal Pengiriman columns used by the sheet.

    Includes legacy ``deadline`` plus snapshot fields so the server never
    silently drops a row the client filter would keep. Client filter remains
    authoritative for the displayed shipping-date chain.
    """
    start = to_date_key(date_from)
    end = to_date_key(date_to)
    if not start and not end:
        return None
    bounds: Dict[str, str] = {}
    if start:
        bounds["$gte"] = start
    if end:
        bounds["$lte"] = end
    fields = (
        "tanggal_pengiriman",
        "effective_delivery_date",
        "delivery_deadline_effective",
        "scheduled_delivery_date",
        "delivery_deadline_manual",
        "deadline",
    )
    return {"$or": [{field: dict(bounds)} for field in fields]}


def build_schedule_list_filter_clause(
    *,
    q: Optional[str] = None,
    work_date_from: Optional[str] = None,
    work_date_to: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
) -> Optional[dict]:
    parts: List[dict] = []
    text = schedule_text_search_clause(q)
    if text:
        parts.append(text)
    work = schedule_work_date_range_clause(work_date_from, work_date_to)
    if work:
        parts.append(work)
    deadline = schedule_deadline_range_clause(deadline_from, deadline_to)
    if deadline:
        parts.append(deadline)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


LEAN_SCOPE_SCAN_PROJECTION = {
    "_id": 0,
    "id": 1,
    "cancelled": 1,
    "current_stage": 1,
    "work_date": 1,
    "line_items.production_process": 1,
    "operator_splits.id": 1,
    "operator_splits.quantity": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.work_date": 1,
    "operator_splits.line_idx": 1,
    "operator_splits.line_printable": 1,
    "operator_splits.operator_user_id": 1,
    "operator_splits.operator_id": 1,
    "operator_splits.operator_email": 1,
    "operator_splits.operator_name": 1,
}
