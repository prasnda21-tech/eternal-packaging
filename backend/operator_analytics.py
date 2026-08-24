"""Read-only operator production analytics (Batch 1).

Source of truth:
- Assigned / target  = operator_splits.quantity  (allocation, NOT PO qty)
- Completed / actual = operator_splits.qty_completed (per-operator, no double count)
- Jobs               = unique PO ids the operator actually worked (qty_completed > 0)
- Active days/weeks  = dates with a positive progress delta (timeline Qty notes),
                       falling back to split.updated_at / work_date when no notes exist.

Week definition: ISO-8601 (Monday–Sunday) in WIB (UTC+7).
Period shorter than one week is safe: active_weeks is at least 1 when there is
activity, never a fractional/zero divisor.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

WIB = timezone(timedelta(hours=7))
QTY_NOTE_RE = re.compile(r"^Qty (.+): (\d+)/(\d+)\s*$")

ANALYTICS_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "cancelled": 1,
    "created_at": 1,
    "product_name": 1,
    "line_items.name": 1,
    "line_items.item_code": 1,
    "operator_splits.id": 1,
    "operator_splits.operator_email": 1,
    "operator_splits.operator_name": 1,
    "operator_splits.quantity": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.work_date": 1,
    "operator_splits.updated_at": 1,
    "operator_splits.line_idx": 1,
    "timeline.at": 1,
    "timeline.note": 1,
    "timeline.by_email": 1,
}


def safe_int(v, default=0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(v)
    except (ValueError, TypeError):
        return default


def achievement_pct(completed: int, assigned: int) -> float:
    """completed / assigned * 100. assigned=0 → 0.0 (never NaN/Infinity)."""
    if assigned <= 0:
        return 0.0
    return round((completed / assigned) * 100.0, 2)


def remaining_qty(assigned: int, completed: int) -> int:
    return max(0, assigned - completed)


def wib_today(now: Optional[datetime] = None) -> date:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB).date()


def iso_week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=WIB)
            return dt.astimezone(WIB).date()
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def parse_iso_datetime_date(value: Optional[str]) -> Optional[date]:
    """Parse a timeline/split timestamp to a WIB calendar date."""
    return parse_iso_date(value)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday=0


def resolve_period(
    period: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[date, date, str]:
    """Return (from, to, period_key). Dates are inclusive WIB calendar dates."""
    today = wib_today(now)
    preset = (period or "month").strip().lower()
    aliases = {
        "hari_ini": "today",
        "today": "today",
        "minggu_ini": "week",
        "week": "week",
        "this_week": "week",
        "bulan_ini": "month",
        "month": "month",
        "this_month": "month",
        "custom": "custom",
    }
    preset = aliases.get(preset, preset)

    parsed_from = parse_iso_date(date_from)
    parsed_to = parse_iso_date(date_to)

    if preset == "today":
        return today, today, "today"
    if preset == "week":
        start = monday_of(today)
        return start, start + timedelta(days=6), "week"
    if preset == "custom":
        start = parsed_from or today
        end = parsed_to or start
        if end < start:
            start, end = end, start
        return start, end, "custom"
    # default: this month
    start = today.replace(day=1)
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end, "month"


def in_period(d: Optional[date], start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def order_item_names(order: dict) -> List[str]:
    names = []
    for li in order.get("line_items") or []:
        name = (li.get("name") or "").strip()
        code = (li.get("item_code") or "").strip()
        if name:
            names.append(name)
        if code and code not in names:
            names.append(code)
    product = (order.get("product_name") or "").strip()
    if product and product not in names:
        names.append(product)
    return names


def order_matches_item(order: dict, item: Optional[str]) -> bool:
    if not item or item == "all":
        return True
    needle = item.strip().lower()
    if not needle:
        return True
    return any(needle == n.lower() or needle in n.lower() for n in order_item_names(order))


def parse_qty_note(note: str) -> Optional[Tuple[str, int, int]]:
    m = QTY_NOTE_RE.match((note or "").strip())
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2)), int(m.group(3))


def _timeline_qty_events(order: dict) -> List[dict]:
    events = []
    for t in order.get("timeline") or []:
        parsed = parse_qty_note(t.get("note") or "")
        if not parsed:
            continue
        name, qty, cap = parsed
        day = parse_iso_datetime_date(t.get("at"))
        events.append({
            "name": name,
            "qty": qty,
            "cap": cap,
            "day": day,
            "by_email": t.get("by_email"),
        })
    return events


def _split_fallback_day(split: dict) -> Optional[date]:
    return (
        parse_iso_datetime_date(split.get("updated_at"))
        or parse_iso_date(split.get("work_date"))
    )


def _match_events_for_split(split: dict, events: Sequence[dict]) -> List[dict]:
    """Bind Qty-note events to a split by operator name + allocation cap."""
    name = (split.get("operator_name") or "").strip()
    email = (split.get("operator_email") or "").strip()
    cap = safe_int(split.get("quantity"))
    matched = []
    for ev in events:
        if ev["cap"] != cap:
            continue
        if name and ev["name"] == name:
            matched.append(ev)
            continue
        if email and ev.get("by_email") == email:
            matched.append(ev)
    matched.sort(key=lambda e: e["day"] or date.min)
    return matched


def completed_in_period_for_split(
    split: dict,
    events: Sequence[dict],
    start: date,
    end: date,
) -> Tuple[int, List[date]]:
    """Return (completed_qty_in_period, active_days_with_positive_delta).

    Uses timeline deltas so multi-update POs are not counted as extra jobs
    and only qty actually finished in the window is attributed.
    """
    snapshot = max(0, safe_int(split.get("qty_completed")))
    matched = _match_events_for_split(split, events)
    active_days: List[date] = []

    if matched:
        prev = 0
        period_delta = 0
        for ev in matched:
            delta = ev["qty"] - prev
            prev = ev["qty"]
            if ev["day"] and in_period(ev["day"], start, end):
                period_delta += delta
                if delta > 0:
                    active_days.append(ev["day"])
        # Window delta only; never exceed the live snapshot (qty_completed).
        completed = max(0, min(snapshot, max(0, period_delta)))
        return completed, active_days

    # No Qty notes: attribute the current snapshot to the split's last activity day.
    day = _split_fallback_day(split)
    if snapshot > 0 and in_period(day, start, end):
        return snapshot, [day] if day else []
    return 0, []


def split_is_in_period(split: dict, events: Sequence[dict], start: date, end: date) -> bool:
    """A split is in-play during the period if assignment/work/progress lands in it."""
    if in_period(parse_iso_date(split.get("work_date")), start, end):
        return True
    if in_period(parse_iso_datetime_date(split.get("updated_at")), start, end):
        return True
    for ev in _match_events_for_split(split, events):
        if in_period(ev.get("day"), start, end):
            return True
    return False


def build_operator_analytics_pipeline(
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
) -> List[dict]:
    """Single Mongo aggregation: one scan, not per-operator queries.

    Facets keep filter-option lists independent of the operator/customer/item
    metric filters. Period filtering stays in Python (needs timeline deltas).
    """
    base_match: Dict[str, Any] = {
        "cancelled": {"$ne": True},
        "operator_splits.0": {"$exists": True},
    }
    doc_match: Dict[str, Any] = {}
    if operator_id and operator_id not in ("all", ""):
        doc_match["operator_splits.operator_email"] = operator_id
    if customer and customer not in ("all", ""):
        doc_match["customer"] = customer
    if item and item not in ("all", ""):
        doc_match["$or"] = [
            {"line_items.name": item},
            {"line_items.item_code": item},
            {"product_name": item},
        ]

    docs_stage: List[dict] = []
    if doc_match:
        docs_stage.append({"$match": doc_match})
    docs_stage.append({"$project": ANALYTICS_PROJECTION})

    return [
        {"$match": base_match},
        {"$facet": {
            "docs": docs_stage,
            "operators": [
                {"$unwind": "$operator_splits"},
                {"$group": {
                    "_id": "$operator_splits.operator_email",
                    "name": {"$first": "$operator_splits.operator_name"},
                }},
                {"$match": {"_id": {"$nin": [None, ""]}}},
                {"$sort": {"name": 1, "_id": 1}},
            ],
            "customers": [
                {"$group": {"_id": "$customer"}},
                {"$match": {"_id": {"$nin": [None, ""]}}},
                {"$sort": {"_id": 1}},
            ],
            "items": [
                {"$unwind": {"path": "$line_items", "preserveNullAndEmptyArrays": True}},
                {"$project": {
                    "name": {"$ifNull": ["$line_items.name", "$product_name"]},
                }},
                {"$match": {"name": {"$nin": [None, ""]}}},
                {"$group": {"_id": "$name"}},
                {"$sort": {"_id": 1}},
            ],
        }},
    ]


def _empty_operator(email: str, name: str) -> dict:
    return {
        "operator_id": email,
        "email": email,
        "name": name or email,
        "assigned": 0,
        "completed": 0,
        "remaining": 0,
        "avg_weekly_qty": 0.0,
        "avg_daily_qty": 0.0,
        "job_count": 0,
        "jobs_assigned": 0,
        "jobs_completed": 0,
        "jobs_remaining": 0,
        "achievement": 0.0,
        "active_days": 0,
        "active_weeks": 0,
    }


def compute_operator_kpis(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
) -> dict:
    """Pure KPI computation. Does not write. Does not use PO/target qty as completed."""
    ops: Dict[str, dict] = {}
    # per operator: set of po ids assigned / worked / fully complete; set of active days
    assigned_pos: Dict[str, set] = defaultdict(set)
    worked_pos: Dict[str, set] = defaultdict(set)
    done_pos: Dict[str, set] = defaultdict(set)
    active_days: Dict[str, set] = defaultdict(set)
    # remaining-per-PO for "fully complete" (all splits of this operator on that PO)
    po_assigned: Dict[Tuple[str, str], int] = defaultdict(int)
    po_completed: Dict[Tuple[str, str], int] = defaultdict(int)

    op_filter = (operator_id or "").strip()
    if op_filter in ("all", ""):
        op_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""

    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        po_id = order.get("id") or order.get("po_number")
        events = _timeline_qty_events(order)
        splits = order.get("operator_splits") or []
        for split in splits:
            email = (split.get("operator_email") or "").strip()
            if not email:
                continue
            if op_filter and email != op_filter:
                continue
            if not split_is_in_period(split, events, date_from, date_to):
                continue

            rec = ops.setdefault(email, _empty_operator(email, split.get("operator_name") or email))
            if split.get("operator_name"):
                rec["name"] = split["operator_name"]

            assigned = max(0, safe_int(split.get("quantity")))
            completed, days = completed_in_period_for_split(split, events, date_from, date_to)
            # Completed is never PO qty / never assigned-as-completed.
            completed = max(0, min(completed, assigned if assigned > 0 else completed))

            rec["assigned"] += assigned
            rec["completed"] += completed
            assigned_pos[email].add(po_id)
            po_assigned[(email, po_id)] += assigned
            po_completed[(email, po_id)] += completed
            if completed > 0:
                worked_pos[email].add(po_id)
            for d in days:
                active_days[email].add(d)

    for (email, po_id), asg in po_assigned.items():
        if asg > 0 and po_completed[(email, po_id)] >= asg:
            done_pos[email].add(po_id)

    rows = []
    for email, rec in ops.items():
        rec["remaining"] = remaining_qty(rec["assigned"], rec["completed"])
        rec["achievement"] = achievement_pct(rec["completed"], rec["assigned"])
        days = active_days.get(email) or set()
        weeks = {iso_week_key(d) for d in days}
        rec["active_days"] = len(days)
        rec["active_weeks"] = len(weeks)
        rec["avg_daily_qty"] = round(rec["completed"] / len(days), 2) if days else 0.0
        # < 1 week with activity → divide by 1 (the single active ISO week), never 0.
        rec["avg_weekly_qty"] = round(rec["completed"] / len(weeks), 2) if weeks else 0.0
        rec["job_count"] = len(worked_pos.get(email) or [])
        rec["jobs_assigned"] = len(assigned_pos.get(email) or [])
        rec["jobs_completed"] = len(done_pos.get(email) or [])
        rec["jobs_remaining"] = max(0, rec["jobs_assigned"] - rec["jobs_completed"])
        rows.append(rec)

    rows.sort(key=lambda r: (-r["completed"], r["name"] or r["email"]))

    total_assigned = sum(r["assigned"] for r in rows)
    total_completed = sum(r["completed"] for r in rows)
    union_days = set()
    union_weeks = set()
    union_jobs = set()
    union_assigned_jobs = set()
    union_done_jobs = set()
    for email, days in active_days.items():
        union_days |= days
        union_weeks |= {iso_week_key(d) for d in days}
        union_jobs |= worked_pos.get(email) or set()
        union_assigned_jobs |= assigned_pos.get(email) or set()
        union_done_jobs |= done_pos.get(email) or set()

    kpis = {
        "total_completed": total_completed,
        "total_assigned": total_assigned,
        "total_remaining": remaining_qty(total_assigned, total_completed),
        "avg_weekly_qty": round(total_completed / len(union_weeks), 2) if union_weeks else 0.0,
        "avg_daily_qty": round(total_completed / len(union_days), 2) if union_days else 0.0,
        "avg_achievement": achievement_pct(total_completed, total_assigned),
        "total_jobs": len(union_jobs),
        "jobs_assigned": len(union_assigned_jobs),
        "jobs_completed": len(union_done_jobs),
        "jobs_remaining": max(0, len(union_assigned_jobs) - len(union_done_jobs)),
        "active_days": len(union_days),
        "active_weeks": len(union_weeks),
    }
    return {
        "kpis": kpis,
        "operators": rows,
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "read_only": True,
    }
