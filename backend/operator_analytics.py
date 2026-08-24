"""Read-only operator production analytics (Batch 1 + Batch 2 + Batch 3).

Source of truth:
- Assigned / target  = operator_splits.quantity  (allocation, NOT PO qty)
- Completed / actual = operator_splits.qty_completed (per-operator, no double count)
- Remaining          = max(assigned - completed, 0)  — never negative
- Jobs (Batch 1)     = unique PO ids the operator actually worked (qty_completed > 0)
- Assignments (B2)   = unique operator_splits.id (progress updates are not extra jobs)
- Active days/weeks  = dates with a positive progress delta (timeline Qty notes),
                       falling back to split.updated_at / work_date when no notes exist.
- Speed              = qty / active work days (Batch 1). pcs/jam is NOT computed:
                       no shift / jam-kerja source exists.
- Completion duration= first Qty-note (or work_date) → first Qty-note that reaches
                       assigned (or updated_at). Missing timestamps → N/A, never invented.
- On-time            = assignment completion_date vs order.deadline (PO deadline
                       attributed per assignment; no per-split deadline field).
- Sample             = sample_files.uploaded_by (operator, never designer) +
                       sample_approval_history (approve/reject), not current_stage.

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
PACKING_NOTE_RE = re.compile(r"^Qty Packing:\s*(\d+)\s*pcs\s*\((.*)\)\s*$")
PACKING_LINE_RE = re.compile(r"#(\d+)=(\d+)")
PREP_NOTE_RE = re.compile(r"^Persiapan Helper(?: \(per-line\))?:\s*(\d+)/(\d+)")
SCREEN_NOTE_RE = re.compile(r"^Screen qty:\s*(\d+)\s*$")
UNATTRIBUTED_ID = "__unattributed__"
UNATTRIBUTED_NAME = "N/A / Unattributed"

RANK_METRICS = ("output_qty", "achievement", "on_time_pct", "sample_approval_pct", "job_count")
JOB_PAGE_DEFAULT = 50
JOB_PAGE_MAX = 100

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
    "line_items.quantity": 1,
    "line_items.color": 1,
    "line_items.production_process": 1,
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
    "timeline.by_role": 1,
    "timeline.by_name": 1,
    "current_stage": 1,
    "deadline": 1,
    "sample_approved": 1,
    "sample_approved_at": 1,
    "sample_files.uploaded_by": 1,
    "sample_files.uploaded_by_role": 1,
    "sample_files.uploaded_by_name": 1,
    "sample_files.uploaded_at": 1,
    "sample_approval_history.action": 1,
    "sample_approval_history.at": 1,
}

DESIGNER_ROLES = {"designer"}
SAMPLE_DECISIONS = {"approve", "reject"}


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


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp or date-only to a timezone-aware WIB datetime.

    Date-only values become midnight WIB. Invalid / empty → None (never invented).
    """
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
            return dt.astimezone(WIB)
        d = date.fromisoformat(s[:10])
        return datetime(d.year, d.month, d.day, tzinfo=WIB)
    except (ValueError, TypeError):
        return None


def parse_iso_datetime_date(value: Optional[str]) -> Optional[date]:
    """Parse a timeline/split timestamp to a WIB calendar date."""
    return parse_iso_date(value)


def duration_days(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    """Calendar duration in days from real timestamps. Missing or inverted → None."""
    if start is None or end is None:
        return None
    if end < start:
        return None
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return None
    return round(seconds / 86400.0, 2)


def ratio_pct(numerator: int, denominator: int) -> Optional[float]:
    """numerator/denominator*100. denominator=0 → None (N/A, never 0/NaN/Inf)."""
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 2)


def assignment_id(split: dict, email: str, po_id: Any) -> str:
    sid = (split.get("id") or "").strip()
    if sid:
        return sid
    return f"{email}:{po_id}:{split.get('line_idx')}"


def assignment_deadline(order: dict) -> Optional[date]:
    """No per-assignment deadline exists. Attribute PO `deadline` to the split.

    Does not use delivery_deadline_effective (shipping lead-time) or invent dates.
    work_date is a schedule hint, not a completion deadline.
    """
    return parse_iso_date(order.get("deadline"))


def classify_on_time(
    *,
    is_complete: bool,
    completion_day: Optional[date],
    deadline: Optional[date],
    today: date,
) -> Optional[str]:
    """Return on_time | late | overdue_active | on_progress, or None if not classifiable."""
    if deadline is None:
        return None
    if is_complete:
        if completion_day is None:
            return None
        return "on_time" if completion_day <= deadline else "late"
    if today > deadline:
        return "overdue_active"
    return "on_progress"


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday=0


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def _month_span(today: date, n_months: int) -> Tuple[date, date]:
    """Inclusive calendar span of `n_months` months ending today (current month counts as 1)."""
    n = max(1, int(n_months))
    start = _add_months(today.replace(day=1), -(n - 1))
    return start, today


def series_grouping(preset: str, start: date, end: date) -> str:
    """Chart/table bucket: day | week | month. Never 365 daily points for a year."""
    p = (preset or "month").strip().lower()
    if p in ("today", "daily"):
        return "day"
    if p in ("week",):
        return "week"
    if p in ("month",):
        return "week"
    if p in ("months_3", "months_6", "months_12", "year"):
        return "month"
    span = (end - start).days
    if span <= 14:
        return "day"
    if span <= 90:
        return "week"
    return "month"


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def bucket_key(d: date, grouping: str) -> str:
    g = (grouping or "week").strip().lower()
    if g == "day":
        return d.isoformat()
    if g == "month":
        return month_key(d)
    return iso_week_key(d)


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
        "harian": "daily",
        "daily": "daily",
        "minggu_ini": "week",
        "mingguan": "week",
        "week": "week",
        "this_week": "week",
        "bulan_ini": "month",
        "bulanan": "month",
        "month": "month",
        "this_month": "month",
        "3_bulan": "months_3",
        "3bulan": "months_3",
        "months_3": "months_3",
        "6_bulan": "months_6",
        "6bulan": "months_6",
        "months_6": "months_6",
        "12_bulan": "months_12",
        "12bulan": "months_12",
        "months_12": "months_12",
        "tahunan": "year",
        "year": "year",
        "yearly": "year",
        "custom": "custom",
    }
    preset = aliases.get(preset, preset)

    parsed_from = parse_iso_date(date_from)
    parsed_to = parse_iso_date(date_to)

    if preset == "today":
        return today, today, "today"
    if preset == "daily":
        return today - timedelta(days=6), today, "daily"
    if preset == "week":
        start = monday_of(today)
        return start, start + timedelta(days=6), "week"
    if preset == "months_3":
        start, end = _month_span(today, 3)
        return start, end, "months_3"
    if preset == "months_6":
        start, end = _month_span(today, 6)
        return start, end, "months_6"
    if preset == "months_12":
        start, end = _month_span(today, 12)
        return start, end, "months_12"
    if preset == "year":
        return date(today.year, 1, 1), today, "year"
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


def line_item_label(order: dict, line_idx: Any) -> str:
    lis = order.get("line_items") or []
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        idx = -1
    if 0 <= idx < len(lis):
        name = (lis[idx].get("name") or "").strip()
        code = (lis[idx].get("item_code") or "").strip()
        if name and code and code not in name:
            return f"{name} ({code})"
        return name or code or f"Line #{idx + 1}"
    product = (order.get("product_name") or "").strip()
    return product or "Item"


def line_item_name(order: dict, line_idx: Any) -> str:
    """Cup / item name from the actual line_items field — never inferred from free text."""
    lis = order.get("line_items") or []
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        idx = -1
    if 0 <= idx < len(lis):
        return (lis[idx].get("name") or lis[idx].get("item_code") or "").strip() or line_item_label(order, idx)
    return (order.get("product_name") or "Item").strip() or "Item"


def _order_po_number(order: dict) -> str:
    return (order.get("po_number") or order.get("id") or "").strip()


def order_matches_po(order: dict, po: Optional[str]) -> bool:
    if not po or po == "all":
        return True
    needle = po.strip().lower()
    if not needle:
        return True
    num = _order_po_number(order).lower()
    oid = str(order.get("id") or "").lower()
    return needle == num or needle in num or needle == oid


def is_print_line_item(li: dict) -> bool:
    p = (li.get("production_process") or "print").strip().lower()
    return p != "non_print"


def design_line_identity(order: dict, line_idx: Any, split: Optional[dict] = None) -> Tuple[str, str]:
    """Distinct printable line identity.

    Schema has no per-line design_id / design attachment. design_files is PO-level.
    Fallback unit = unique printable line (po + line_idx), labeled 'Design / Line Cetak'.
    """
    po_id = str(order.get("id") or order.get("po_number") or "")
    try:
        idx = int(line_idx)
        has_idx = True
    except (TypeError, ValueError):
        idx = -1
        has_idx = False
    if has_idx and idx >= 0:
        key = f"{po_id}:{idx}"
    else:
        sid = ((split or {}).get("id") or "").strip()
        key = f"{po_id}:split:{sid}" if sid else f"{po_id}:unknown"
    label = line_item_label(order, line_idx if has_idx else None)
    return key, f"{label} · Line #{(idx + 1) if has_idx and idx >= 0 else '?'}"


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
            "at_dt": parse_iso_datetime(t.get("at")),
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


def _dt_from_day(d: Optional[date]) -> Optional[datetime]:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=WIB)


def assignment_start_dt(split: dict, events: Sequence[dict]) -> Optional[datetime]:
    """Earliest real start evidence: first Qty-note timestamp, else work_date midnight."""
    matched = _match_events_for_split(split, events)
    for ev in matched:
        if ev.get("at_dt"):
            return ev["at_dt"]
        if ev.get("day"):
            return _dt_from_day(ev["day"])
    wd = parse_iso_date(split.get("work_date"))
    if wd:
        return _dt_from_day(wd)
    return None


def assignment_end_dt(split: dict, events: Sequence[dict], *, assigned: int) -> Optional[datetime]:
    """First timestamp where qty reaches assigned; else updated_at if live-complete."""
    if assigned <= 0:
        return None
    matched = _match_events_for_split(split, events)
    for ev in matched:
        if ev["qty"] >= assigned:
            if ev.get("at_dt"):
                return ev["at_dt"]
            if ev.get("day"):
                return _dt_from_day(ev["day"])
    live = max(0, safe_int(split.get("qty_completed")))
    if live >= assigned:
        return parse_iso_datetime(split.get("updated_at"))
    return None


def assignment_completion_day(split: dict, events: Sequence[dict], *, assigned: int) -> Optional[date]:
    end = assignment_end_dt(split, events, assigned=assigned)
    return end.date() if end else None


def _operator_emails(order: dict) -> set:
    return {
        (s.get("operator_email") or "").strip()
        for s in (order.get("operator_splits") or [])
        if (s.get("operator_email") or "").strip()
    }


def attribute_sample_operator(order: dict) -> Optional[Tuple[str, str]]:
    """Operator who actually uploaded Cetak Sample. Designer is never the owner.

    Returns (email, name) of the first qualifying upload, or None.
    """
    emails = _operator_emails(order)
    names = {
        (s.get("operator_email") or "").strip(): (s.get("operator_name") or "").strip()
        for s in (order.get("operator_splits") or [])
        if (s.get("operator_email") or "").strip()
    }
    files = list(order.get("sample_files") or [])
    files.sort(key=lambda f: parse_iso_datetime(f.get("uploaded_at")) or datetime.min.replace(tzinfo=WIB))
    for f in files:
        email = (f.get("uploaded_by") or "").strip()
        role = (f.get("uploaded_by_role") or "").strip().lower()
        if not email:
            continue
        if role in DESIGNER_ROLES:
            continue
        if role == "operator" or email in emails:
            name = (f.get("uploaded_by_name") or "").strip() or names.get(email) or email
            return email, name
    return None


def analyze_sample_job(order: dict) -> Optional[dict]:
    """One unique sample workflow per PO (resubmits are the same job).

    Status comes from sample_approved + sample_approval_history, never current_stage.
    """
    files = list(order.get("sample_files") or [])
    if not files:
        return None
    attributed = attribute_sample_operator(order)
    if not attributed:
        return None
    email, name = attributed
    submissions = sum(
        1 for f in files
        if (f.get("uploaded_by") or "").strip() == email
    )
    if submissions <= 0:
        submissions = len(files)
    history = list(order.get("sample_approval_history") or [])

    first_decision = None
    for h in history:
        action = (h.get("action") or "").strip().lower()
        if action in SAMPLE_DECISIONS:
            first_decision = action
            break

    last_upload = None
    for f in files:
        dt = parse_iso_datetime(f.get("uploaded_at"))
        if dt and (last_upload is None or dt > last_upload):
            last_upload = dt

    last_decision = None
    last_decision_at = None
    for h in history:
        action = (h.get("action") or "").strip().lower()
        if action not in ("approve", "reject", "revoke"):
            continue
        dt = parse_iso_datetime(h.get("at"))
        if last_decision_at is None or (dt is not None and (last_decision_at is None or dt >= last_decision_at)):
            last_decision = action
            last_decision_at = dt

    if order.get("sample_approved"):
        status = "approved"
    elif last_upload and last_decision_at and last_upload > last_decision_at:
        status = "pending"
    elif last_decision == "reject":
        status = "rejected"
    elif last_decision == "revoke":
        status = "pending"
    elif files:
        status = "pending"
    else:
        return None

    activity_days: List[date] = []
    for f in files:
        d = parse_iso_date(f.get("uploaded_at"))
        if d:
            activity_days.append(d)
    for h in history:
        d = parse_iso_date(h.get("at"))
        if d:
            activity_days.append(d)

    return {
        "operator_email": email,
        "operator_name": name,
        "submissions": len(files),
        "status": status,
        "first_pass": True if first_decision == "approve" else (False if first_decision == "reject" else None),
        "has_decision": first_decision is not None,
        "activity_days": activity_days,
    }


def build_operator_analytics_pipeline(
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
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
    if po and po not in ("all", ""):
        doc_match["po_number"] = po
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


DATA_SOURCES = {
    "assigned": "operator_splits.quantity (alokasi per operator, bukan qty PO)",
    "completed": "operator_splits.qty_completed + delta timeline 'Qty {name}: n/cap'",
    "remaining": "max(assigned - completed, 0)",
    "active_jobs": "unique operator_splits.id dengan qty_completed < quantity, PO tidak cancelled",
    "completed_jobs": "unique operator_splits.id dengan qty_completed >= quantity",
    "speed": "qty selesai / hari aktif (tanggal progress Qty note). pcs/jam tidak dihitung — tidak ada data jam kerja/shift",
    "avg_completion_days": "timestamp Qty note pertama (atau work_date) → timestamp Qty note yang mencapai assigned (atau updated_at). Tanpa kedua ujung → N/A",
    "deadline": "order.deadline (Deadline PO) diatribusikan ke assignment operator. Tidak ada deadline per split. Bukan delivery_deadline_effective.",
    "sample": "sample_files.uploaded_by (role operator / email di operator_splits). Designer diabaikan. Status dari sample_approved + sample_approval_history, bukan current_stage.",
    "weekly_trend": "delta Qty-note (kumulatif) di-bucket ISO week WIB. Bukan jumlah snapshot.",
    "wow": "(minggu ini − minggu lalu) / minggu lalu * 100. previous=0 → N/A (bukan Infinity).",
    "ranking": "metric dipilih user (output/achievement/on-time/sample/job). Tidak ada overall score.",
    "packing": "timeline 'Qty Packing: total pcs (#idx=qty)' — packed_qty di packing_info.lines adalah kumulatif; activity = delta positif per line per Save. Actor = timeline.by_email (user yang Save Packing), bukan user yang hanya membuka PO. Tanpa actor → N/A / Unattributed.",
    "packing_fallback": "Jika tidak ada note Qty Packing: snapshot packing_info diatribusikan ke packing_info.updated_by pada updated_at (last-saver only). Tanpa updated_by → Unattributed.",
    "preparation": "timeline 'Persiapan Helper' / 'Persiapan Helper (per-line): prepared/required' — qty_prepared kumulatif; activity = delta positif total. Actor = timeline.by_email. Field cup_qty tidak ada; Cup = line item Cetak (production_process != non_print) di helper_prep.items.",
    "preparation_per_line": "helper_prep.items[] adalah snapshot terkini per line. History per-line per-user tidak tersimpan di timeline (hanya total PO). Jangan mengatribusikan seluruh line ke user terakhir jika ada lebih dari satu actor.",
    "screener": "timeline 'Screen qty: N' — screener_info.screen_qty adalah snapshot kumulatif PO-level (bukan per-line). Activity = delta positif per Save. Actor = timeline.by_email. Tanpa note: snapshot screener_info.updated_by pada updated_at.",
    "screener_line": "Tidak ada qty screener per line. Cup/Item = line_items cetak pada PO yang sama; qty per cup tidak dipecah jika PO multi-line (tidak dikarang).",
    "design": "Tidak ada design_id per line. Unit = unique printable line (po_id + line_idx), dilabeli 'Design / Line Cetak'. Hanya line dengan qty_completed operator > 0 pada periode. Progress update berulang bukan design baru. design_files (upload) tidak dihitung.",
    "daily_target": "app_settings.key=operator_output_targets, keyed by operator_id (user id) + email. Achievement = actual avg/hari aktif vs target/hari. Tidak ada kalender kerja — tidak dikarang target mingguan 5/6/7 hari. Target yang dipakai = current target (tidak ada effective_from history pada settings).",
}

INDEX_RECOMMENDATIONS = [
    "orders: {cancelled: 1, 'operator_splits.0': 1} — scan analytics saat ini",
    "orders: {cancelled: 1, 'operator_splits.operator_email': 1} — filter operator",
    "orders: {cancelled: 1, 'sample_files.uploaded_at': 1} — jika sample analytics menjadi berat",
    "orders: {cancelled: 1, 'packing_info.updated_at': 1} — laporan packing",
    "orders: {cancelled: 1, 'helper_prep.updated_at': 1} — laporan persiapan",
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
        "active_jobs": 0,
        "completed_jobs": 0,
        "avg_completion_days": None,
        "pcs_per_hour": None,
        "on_time": 0,
        "late": 0,
        "overdue_active": 0,
        "on_progress": 0,
        "on_time_basis": 0,
        "on_time_pct": None,
        "sample_jobs": 0,
        "sample_submissions": 0,
        "samples_approved": 0,
        "samples_rejected": 0,
        "samples_pending": 0,
        "first_pass_approved": 0,
        "first_pass_decided": 0,
        "first_pass_rate": None,
        "sample_approval_pct": None,
        "design_count": 0,
        "last_activity_at": None,
        "this_week_qty": 0,
        "daily_target": None,
        "target_gap": None,
        "target_achievement_pct": None,
        "cups": [],
    }


def apply_daily_target(rec: dict, daily_targets: Optional[Dict[str, int]]) -> None:
    """Compare actual avg/active-day vs current daily target. No invented weekly target."""
    email = (rec.get("email") or rec.get("operator_id") or "").strip()
    qty = None
    if daily_targets:
        qty = daily_targets.get(email)
        if qty is None:
            qty = daily_targets.get(rec.get("operator_id"))
    if qty is None:
        rec["daily_target"] = None
        rec["target_gap"] = None
        rec["target_achievement_pct"] = None
        return
    target = max(0, safe_int(qty))
    rec["daily_target"] = target
    avg = float(rec.get("avg_daily_qty") or 0)
    rec["target_gap"] = round(avg - target, 2)
    rec["target_achievement_pct"] = achievement_pct(int(round(avg)), target) if target > 0 else None


def compute_operator_kpis(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    daily_targets: Optional[Dict[str, int]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Pure KPI computation. Does not write. Does not use PO/target qty as completed."""
    ops: Dict[str, dict] = {}
    # per operator: set of po ids assigned / worked / fully complete; set of active days
    assigned_pos: Dict[str, set] = defaultdict(set)
    worked_pos: Dict[str, set] = defaultdict(set)
    done_pos: Dict[str, set] = defaultdict(set)
    active_days: Dict[str, set] = defaultdict(set)
    seen_assignments: Dict[str, set] = defaultdict(set)
    duration_sum: Dict[str, float] = defaultdict(float)
    duration_n: Dict[str, int] = defaultdict(int)
    design_keys: Dict[str, set] = defaultdict(set)
    cup_acc: Dict[str, Dict[str, dict]] = defaultdict(dict)
    # remaining-per-PO for "fully complete" (all splits of this operator on that PO)
    po_assigned: Dict[Tuple[str, str], int] = defaultdict(int)
    po_completed: Dict[Tuple[str, str], int] = defaultdict(int)
    today = wib_today(now)

    op_filter = (operator_id or "").strip()
    if op_filter in ("all", ""):
        op_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""

    order_list = list(orders)
    for order in order_list:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        events = _timeline_qty_events(order)
        splits = order.get("operator_splits") or []
        deadline = assignment_deadline(order)
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
            live_completed = max(0, safe_int(split.get("qty_completed")))
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
                dkey, _dlabel = design_line_identity(order, split.get("line_idx"), split)
                design_keys[email].add(dkey)
                item_name = line_item_name(order, split.get("line_idx"))
                cup = cup_acc[email].setdefault(item_name, {
                    "item": item_name,
                    "qty": 0,
                    "pos": set(),
                    "lines": set(),
                    "last_day": None,
                })
                cup["qty"] += completed
                cup["pos"].add(po_id)
                cup["lines"].add((po_id, split.get("line_idx")))
                if days:
                    last = max(days)
                    if cup["last_day"] is None or last > cup["last_day"]:
                        cup["last_day"] = last
            for d in days:
                active_days[email].add(d)

            aid = assignment_id(split, email, po_id)
            if assigned > 0 and aid not in seen_assignments[email]:
                seen_assignments[email].add(aid)
                is_complete = live_completed >= assigned
                if is_complete:
                    rec["completed_jobs"] += 1
                else:
                    rec["active_jobs"] += 1

                completion_day = (
                    assignment_completion_day(split, events, assigned=assigned)
                    if is_complete else None
                )
                status = classify_on_time(
                    is_complete=is_complete,
                    completion_day=completion_day,
                    deadline=deadline,
                    today=today,
                )
                if status == "on_time":
                    rec["on_time"] += 1
                    rec["on_time_basis"] += 1
                elif status == "late":
                    rec["late"] += 1
                    rec["on_time_basis"] += 1
                elif status == "overdue_active":
                    rec["overdue_active"] += 1
                elif status == "on_progress":
                    rec["on_progress"] += 1

                if is_complete:
                    start = assignment_start_dt(split, events)
                    end = assignment_end_dt(split, events, assigned=assigned)
                    dur = duration_days(start, end)
                    if dur is not None:
                        duration_sum[email] += dur
                        duration_n[email] += 1

        sample = analyze_sample_job(order)
        if sample:
            semail = sample["operator_email"]
            if not op_filter or semail == op_filter:
                activity = sample["activity_days"]
                in_win = any(in_period(d, date_from, date_to) for d in activity) if activity else False
                if in_win:
                    srec = ops.setdefault(semail, _empty_operator(semail, sample["operator_name"]))
                    if sample["operator_name"] and srec["name"] == semail:
                        srec["name"] = sample["operator_name"]
                    srec["sample_jobs"] += 1
                    srec["sample_submissions"] += sample["submissions"]
                    if sample["status"] == "approved":
                        srec["samples_approved"] += 1
                    elif sample["status"] == "rejected":
                        srec["samples_rejected"] += 1
                    else:
                        srec["samples_pending"] += 1
                    if sample["has_decision"]:
                        srec["first_pass_decided"] += 1
                        if sample["first_pass"]:
                            srec["first_pass_approved"] += 1

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
        n_dur = duration_n.get(email) or 0
        rec["avg_completion_days"] = round(duration_sum[email] / n_dur, 2) if n_dur else None
        rec["pcs_per_hour"] = None
        rec["on_time_pct"] = ratio_pct(rec["on_time"], rec["on_time_basis"])
        rec["first_pass_rate"] = ratio_pct(rec["first_pass_approved"], rec["first_pass_decided"])
        rec["sample_approval_pct"] = ratio_pct(rec["samples_approved"], rec["sample_jobs"])
        rec["design_count"] = len(design_keys.get(email) or [])
        rec["last_activity_at"] = max(days).isoformat() if days else None
        cups = []
        for cup in (cup_acc.get(email) or {}).values():
            cups.append({
                "item": cup["item"],
                "qty": cup["qty"],
                "po_count": len(cup["pos"]),
                "line_count": len(cup["lines"]),
                "last_activity_at": cup["last_day"].isoformat() if cup.get("last_day") else None,
            })
        cups.sort(key=lambda c: (-c["qty"], c["item"] or ""))
        rec["cups"] = cups
        apply_daily_target(rec, daily_targets)
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

    total_on_time = sum(r["on_time"] for r in rows)
    total_on_time_basis = sum(r["on_time_basis"] for r in rows)
    total_fp_ok = sum(r["first_pass_approved"] for r in rows)
    total_fp_n = sum(r["first_pass_decided"] for r in rows)
    total_dur_n = sum(duration_n.values())
    total_dur = sum(duration_sum.values())

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
        "active_jobs": sum(r["active_jobs"] for r in rows),
        "completed_jobs": sum(r["completed_jobs"] for r in rows),
        "avg_completion_days": round(total_dur / total_dur_n, 2) if total_dur_n else None,
        "pcs_per_hour": None,
        "on_time": total_on_time,
        "late": sum(r["late"] for r in rows),
        "overdue_active": sum(r["overdue_active"] for r in rows),
        "on_progress": sum(r["on_progress"] for r in rows),
        "on_time_basis": total_on_time_basis,
        "on_time_pct": ratio_pct(total_on_time, total_on_time_basis),
        "sample_jobs": sum(r["sample_jobs"] for r in rows),
        "sample_submissions": sum(r["sample_submissions"] for r in rows),
        "samples_approved": sum(r["samples_approved"] for r in rows),
        "samples_rejected": sum(r["samples_rejected"] for r in rows),
        "samples_pending": sum(r["samples_pending"] for r in rows),
        "first_pass_rate": ratio_pct(total_fp_ok, total_fp_n),
        "sample_approval_pct": ratio_pct(
            sum(r["samples_approved"] for r in rows),
            sum(r["sample_jobs"] for r in rows),
        ),
        "total_designs": len(set().union(*design_keys.values()) if design_keys else set()),
        "this_week_qty": 0,
        "daily_target": None,
        "target_achievement_pct": None,
    }
    weekly = compute_weekly_trend(
        order_list,
        date_from=date_from,
        date_to=date_to,
        operator_id=operator_id,
        customer=customer,
        item=item,
        po=po,
    )
    this_week = iso_week_key(today)
    this_week_total = 0
    for ow in weekly.get("operators") or []:
        wqty = int((ow.get("weeks") or {}).get(this_week, 0) or 0)
        rec = next((r for r in rows if r.get("email") == ow.get("email")), None)
        if rec is not None:
            rec["this_week_qty"] = wqty
        this_week_total += wqty
    kpis["this_week_qty"] = this_week_total
    if len(rows) == 1:
        kpis["daily_target"] = rows[0].get("daily_target")
        kpis["target_achievement_pct"] = rows[0].get("target_achievement_pct")
        kpis["target_gap"] = rows[0].get("target_gap")
    grouping = series_grouping("custom", date_from, date_to)
    output_series = output_series_from_weekly(weekly, grouping) if grouping == "week" else None
    return {
        "kpis": kpis,
        "operators": rows,
        "weekly_trend": weekly,
        "output_series": output_series,
        "ranking": rank_operators(rows, "output_qty"),
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
            "grouping": grouping,
            "design_identity": "Design / Line Cetak (po + line_idx; no per-line design_id in schema)",
            "daily_target_limitation": DATA_SOURCES["daily_target"],
        },
        "read_only": True,
        "sources": DATA_SOURCES,
        "index_recommendations": INDEX_RECOMMENDATIONS,
        "utilization": None,
        "utilization_note": "Tidak dihitung — sistem tidak punya capacity maksimum / shift / jam kerja yang reliable.",
        "load_band": None,
    }


# ---------- Batch 3: weekly trend, ranking, drill-down, packing, persiapan ----------

def wow_pct(current: int, previous: Optional[int]) -> Optional[float]:
    """(current - previous) / previous * 100. previous 0/None → None (N/A, never Inf)."""
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100.0, 2)


def iso_week_monday(week_key: str) -> Optional[date]:
    try:
        year_s, week_s = week_key.split("-W")
        return date.fromisocalendar(int(year_s), int(week_s), 1)
    except (ValueError, TypeError):
        return None


def weeks_covering(start: date, end: date) -> List[dict]:
    """Inclusive ISO weeks overlapping [start, end] (Monday–Sunday WIB)."""
    d = monday_of(start)
    last = monday_of(end)
    out = []
    while d <= last:
        iso = d.isocalendar()
        out.append({
            "week": f"{iso.year}-W{iso.week:02d}",
            "from": d.isoformat(),
            "to": (d + timedelta(days=6)).isoformat(),
            "label": f"W{iso.week} {iso.year}",
        })
        d += timedelta(days=7)
    return out


def completed_by_week_for_split(
    split: dict,
    events: Sequence[dict],
    start: date,
    end: date,
) -> Dict[str, int]:
    """Positive qty deltas bucketed by ISO week. Caps to live snapshot (no double count)."""
    snapshot = max(0, safe_int(split.get("qty_completed")))
    assigned = max(0, safe_int(split.get("quantity")))
    cap = min(snapshot, assigned) if assigned > 0 else snapshot
    matched = _match_events_for_split(split, events)
    buckets: Dict[str, int] = defaultdict(int)

    if matched:
        prev = 0
        running = 0
        for ev in matched:
            delta = ev["qty"] - prev
            prev = ev["qty"]
            if delta <= 0 or not ev["day"] or not in_period(ev["day"], start, end):
                continue
            take = min(delta, max(0, cap - running))
            if take <= 0:
                continue
            buckets[iso_week_key(ev["day"])] += take
            running += take
        return dict(buckets)

    day = _split_fallback_day(split)
    qty = max(0, min(snapshot, assigned if assigned > 0 else snapshot))
    if qty > 0 and in_period(day, start, end) and day:
        return {iso_week_key(day): qty}
    return {}


def compute_weekly_trend(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    """Aggregate completed qty per ISO week. Respects the same filters as KPIs."""
    week_meta = weeks_covering(date_from, date_to)
    week_keys = [w["week"] for w in week_meta]
    totals = {k: 0 for k in week_keys}
    per_op: Dict[str, dict] = {}

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
        if not order_matches_po(order, po):
            continue
        events = _timeline_qty_events(order)
        for split in order.get("operator_splits") or []:
            email = (split.get("operator_email") or "").strip()
            if not email:
                continue
            if op_filter and email != op_filter:
                continue
            if not split_is_in_period(split, events, date_from, date_to):
                continue
            rec = per_op.setdefault(email, {
                "operator_id": email,
                "email": email,
                "name": (split.get("operator_name") or email),
                "weeks": {k: 0 for k in week_keys},
            })
            if split.get("operator_name"):
                rec["name"] = split["operator_name"]
            for wk, qty in completed_by_week_for_split(split, events, date_from, date_to).items():
                if wk in rec["weeks"]:
                    rec["weeks"][wk] += qty
                    totals[wk] += qty

    series = []
    prev_qty = None
    for meta in week_meta:
        qty = totals.get(meta["week"], 0)
        series.append({
            **meta,
            "qty": qty,
            "wow_pct": wow_pct(qty, prev_qty),
        })
        prev_qty = qty

    last = series[-1]["qty"] if series else 0
    prev = series[-2]["qty"] if len(series) >= 2 else None
    operators = sorted(per_op.values(), key=lambda r: r["name"] or r["email"])
    return {
        "weeks": series,
        "operators": operators,
        "wow_pct": wow_pct(last, prev) if len(series) >= 2 else None,
        "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
    }


def rank_operators(rows: Sequence[dict], metric: str = "output_qty") -> List[dict]:
    """Selectable ranking. N/A stays N/A and sorts after numeric values. No overall score."""
    key = (metric or "output_qty").strip().lower()
    if key not in RANK_METRICS:
        key = "output_qty"
    field = {
        "output_qty": "completed",
        "achievement": "achievement",
        "on_time_pct": "on_time_pct",
        "sample_approval_pct": "sample_approval_pct",
        "job_count": "job_count",
    }[key]
    na_is_missing = key in ("on_time_pct", "sample_approval_pct")

    def sort_tuple(r):
        v = r.get(field)
        if na_is_missing and v is None:
            return (1, 0.0, (r.get("name") or r.get("email") or ""))
        return (0, -float(v or 0), (r.get("name") or r.get("email") or ""))

    ranked = []
    n = 0
    for r in sorted(rows, key=sort_tuple):
        v = r.get(field)
        is_na = na_is_missing and v is None
        if not is_na:
            n += 1
            rank_n = n
        else:
            rank_n = None
        ranked.append({
            "rank": rank_n,
            "metric": key,
            "value": v,
            "value_na": is_na,
            "operator_id": r.get("operator_id") or r.get("email"),
            "email": r.get("email"),
            "name": r.get("name"),
            "completed": r.get("completed", 0),
            "assigned": r.get("assigned", 0),
            "achievement": r.get("achievement", 0),
            "on_time_pct": r.get("on_time_pct"),
            "sample_approval_pct": r.get("sample_approval_pct"),
            "job_count": r.get("job_count", 0),
            "active_jobs": r.get("active_jobs", 0),
            "completed_jobs": r.get("completed_jobs", 0),
            "remaining": r.get("remaining", 0),
        })
    return ranked


def _job_status_label(on_time_class: Optional[str], is_complete: bool) -> str:
    if is_complete:
        return "completed"
    if on_time_class == "overdue_active":
        return "overdue"
    return "active"


def compute_operator_detail(
    orders: Iterable[dict],
    *,
    operator_id: str,
    date_from: date,
    date_to: date,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    daily_targets: Optional[Dict[str, int]] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = JOB_PAGE_DEFAULT,
    now: Optional[datetime] = None,
) -> dict:
    """Drill-down for one operator. Job history is paginated; never unbounded."""
    email = (operator_id or "").strip()
    bundle = compute_operator_kpis(
        orders,
        date_from=date_from,
        date_to=date_to,
        operator_id=email,
        customer=customer,
        item=item,
        po=po,
        daily_targets=daily_targets,
        now=now,
    )
    rec = next((r for r in bundle["operators"] if r.get("email") == email), None)
    if rec is None:
        rec = _empty_operator(email, email)

    jobs: List[dict] = []
    today = wib_today(now)
    status_f = (status or "all").strip().lower()
    if status_f in ("", "all"):
        status_f = "all"

    for order in orders:
        if order.get("cancelled"):
            continue
        cust = (order.get("customer") or "").strip()
        if customer and customer not in ("all", "") and cust != customer:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        events = _timeline_qty_events(order)
        deadline = assignment_deadline(order)
        sample = analyze_sample_job(order)
        sample_status = None
        if sample and sample.get("operator_email") == email:
            sample_status = sample.get("status")
        for split in order.get("operator_splits") or []:
            if (split.get("operator_email") or "").strip() != email:
                continue
            if not split_is_in_period(split, events, date_from, date_to):
                continue
            assigned = max(0, safe_int(split.get("quantity")))
            live = max(0, safe_int(split.get("qty_completed")))
            completed, days = completed_in_period_for_split(split, events, date_from, date_to)
            completed = max(0, min(completed, assigned if assigned > 0 else completed))
            is_complete = assigned > 0 and live >= assigned
            completion_day = (
                assignment_completion_day(split, events, assigned=assigned) if is_complete else None
            )
            ot_class = classify_on_time(
                is_complete=is_complete,
                completion_day=completion_day,
                deadline=deadline,
                today=today,
            )
            job_status = _job_status_label(ot_class, is_complete)
            if status_f != "all" and job_status != status_f:
                continue
            line_idx = split.get("line_idx")
            dkey, dlabel = design_line_identity(order, line_idx, split)
            last_day = max(days) if days else None
            try:
                line_no = int(line_idx) + 1
            except (TypeError, ValueError):
                line_no = None
            jobs.append({
                "assignment_id": assignment_id(split, email, po_id),
                "po_id": po_id,
                "po_number": _order_po_number(order) or str(po_id),
                "customer": cust,
                "item": line_item_label(order, line_idx),
                "cup": line_item_name(order, line_idx),
                "line_idx": line_idx,
                "line": line_no,
                "design_id": dkey,
                "design_label": dlabel,
                "assigned_qty": assigned,
                "completed_qty": completed,
                "remaining": remaining_qty(assigned, completed),
                "work_date": split.get("work_date") or None,
                "last_activity_at": last_day.isoformat() if last_day else None,
                "deadline": deadline.isoformat() if deadline else None,
                "sample_status": sample_status,
                "job_status": job_status,
                "on_time_class": ot_class,
            })

    jobs.sort(key=lambda j: (j.get("work_date") or "", j.get("po_number") or ""), reverse=True)
    try:
        page_n = max(1, int(page or 1))
    except (TypeError, ValueError):
        page_n = 1
    try:
        size = int(page_size or JOB_PAGE_DEFAULT)
    except (TypeError, ValueError):
        size = JOB_PAGE_DEFAULT
    size = max(1, min(JOB_PAGE_MAX, size))
    total = len(jobs)
    start_i = (page_n - 1) * size
    page_jobs = jobs[start_i:start_i + size]

    op_weeks = {}
    for ow in bundle.get("weekly_trend", {}).get("operators") or []:
        if ow.get("email") == email:
            op_weeks = ow.get("weeks") or {}
            break
    weekly_series = []
    prev = None
    for w in bundle.get("weekly_trend", {}).get("weeks") or []:
        qty = int(op_weeks.get(w["week"], 0) or 0)
        weekly_series.append({
            "week": w["week"], "from": w["from"], "to": w["to"], "label": w["label"],
            "qty": qty, "wow_pct": wow_pct(qty, prev),
        })
        prev = qty

    return {
        "operator": rec,
        "weekly_trend": {
            "weeks": weekly_series,
            "wow_pct": wow_pct(weekly_series[-1]["qty"], weekly_series[-2]["qty"]) if len(weekly_series) >= 2 else None,
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "jobs": page_jobs,
        "cups": rec.get("cups") or [],
        "jobs_page": {
            "page": page_n,
            "page_size": size,
            "total": total,
            "has_more": start_i + size < total,
        },
        "period": bundle["period"],
        "read_only": True,
        "sources": DATA_SOURCES,
    }


ACTIVITY_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "cancelled": 1,
    "product_name": 1,
    "line_items.name": 1,
    "line_items.item_code": 1,
    "line_items.quantity": 1,
    "line_items.color": 1,
    "line_items.production_process": 1,
    "packing_info": 1,
    "helper_prep": 1,
    "screener_info": 1,
    "packing_status": 1,
    "current_stage": 1,
    "timeline.at": 1,
    "timeline.note": 1,
    "timeline.by_email": 1,
    "timeline.by_role": 1,
    "timeline.by_name": 1,
}


def build_activity_analytics_pipeline(
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> List[dict]:
    """Single scan for packing + persiapan reports (not per-user queries)."""
    base_match: Dict[str, Any] = {
        "cancelled": {"$ne": True},
        "$or": [
            {"packing_info.qty_packed": {"$gt": 0}},
            {"packing_info.lines.0": {"$exists": True}},
            {"helper_prep.qty_prepared": {"$gte": 0}},
            {"helper_prep.items.0": {"$exists": True}},
            {"screener_info.screen_qty": {"$gte": 0}},
        ],
    }
    doc_match: Dict[str, Any] = {}
    if customer and customer not in ("all", ""):
        doc_match["customer"] = customer
    if po and po not in ("all", ""):
        doc_match["po_number"] = po
    if item and item not in ("all", ""):
        doc_match["$or"] = [
            {"line_items.name": item},
            {"line_items.item_code": item},
            {"product_name": item},
        ]
    docs_stage: List[dict] = []
    if doc_match:
        docs_stage.append({"$match": doc_match})
    docs_stage.append({"$project": ACTIVITY_PROJECTION})
    return [
        {"$match": base_match},
        {"$facet": {
            "docs": docs_stage,
            "customers": [
                {"$group": {"_id": "$customer"}},
                {"$match": {"_id": {"$nin": [None, ""]}}},
                {"$sort": {"_id": 1}},
            ],
            "items": [
                {"$unwind": {"path": "$line_items", "preserveNullAndEmptyArrays": True}},
                {"$project": {"name": {"$ifNull": ["$line_items.name", "$product_name"]}}},
                {"$match": {"name": {"$nin": [None, ""]}}},
                {"$group": {"_id": "$name"}},
                {"$sort": {"_id": 1}},
            ],
            "pos": [
                {"$group": {"_id": "$po_number"}},
                {"$match": {"_id": {"$nin": [None, ""]}}},
                {"$sort": {"_id": 1}},
            ],
        }},
    ]


def parse_packing_note(note: str) -> Optional[Tuple[int, Dict[int, int]]]:
    m = PACKING_NOTE_RE.match((note or "").strip())
    if not m:
        return None
    total = int(m.group(1))
    lines: Dict[int, int] = {}
    for lm in PACKING_LINE_RE.finditer(m.group(2) or ""):
        lines[int(lm.group(1))] = int(lm.group(2))
    return total, lines


def parse_prep_note(note: str) -> Optional[Tuple[int, int]]:
    m = PREP_NOTE_RE.match((note or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def packing_activity_events(order: dict) -> List[dict]:
    """Positive per-line deltas from cumulative Qty Packing timeline notes.

    packing_info.lines[].packed_qty is a CUMULATIVE snapshot. Summing snapshots
    double-counts. Activity = max(0, new_qty - previous_qty) per line per Save.
    Actor is timeline.by_email (the user who saved packing), never a viewer.
    """
    events: List[dict] = []
    prev_lines: Dict[int, int] = {}
    prev_total = 0
    found_notes = False
    for t in order.get("timeline") or []:
        parsed = parse_packing_note(t.get("note") or "")
        if not parsed:
            continue
        found_notes = True
        total, lines = parsed
        email = (t.get("by_email") or "").strip() or UNATTRIBUTED_ID
        day = parse_iso_datetime_date(t.get("at"))
        name = (t.get("by_name") or "").strip() or (UNATTRIBUTED_NAME if email == UNATTRIBUTED_ID else email)
        role = (t.get("by_role") or "").strip()
        attr = "unattributed" if email == UNATTRIBUTED_ID else "timeline_delta"
        if lines:
            for idx, qty in lines.items():
                delta = qty - prev_lines.get(idx, 0)
                prev_lines[idx] = qty
                if delta > 0:
                    events.append({
                        "email": email,
                        "name": name,
                        "role": role,
                        "day": day,
                        "line_idx": idx,
                        "delta": delta,
                        "attribution": attr,
                    })
            prev_total = total
        else:
            delta = total - prev_total
            prev_total = total
            if delta > 0:
                events.append({
                    "email": email,
                    "name": name,
                    "role": role,
                    "day": day,
                    "line_idx": None,
                    "delta": delta,
                    "attribution": attr,
                })
    if found_notes:
        return events

    pi = order.get("packing_info") or {}
    email = (pi.get("updated_by") or "").strip() or UNATTRIBUTED_ID
    day = parse_iso_datetime_date(pi.get("updated_at"))
    name = (pi.get("updated_by_name") or "").strip() or (UNATTRIBUTED_NAME if email == UNATTRIBUTED_ID else email)
    attr = "unattributed" if email == UNATTRIBUTED_ID else "last_saver_snapshot"
    lines = pi.get("lines") if isinstance(pi.get("lines"), list) else []
    if lines:
        for it in lines:
            qty = max(0, safe_int(it.get("packed_qty")))
            if qty <= 0:
                continue
            events.append({
                "email": email,
                "name": name,
                "role": "",
                "day": day,
                "line_idx": it.get("line_idx"),
                "delta": qty,
                "attribution": attr,
            })
        return events
    qty = max(0, safe_int(pi.get("qty_packed")))
    if qty > 0:
        events.append({
            "email": email,
            "name": name,
            "role": "",
            "day": day,
            "line_idx": 0,
            "delta": qty,
            "attribution": attr,
        })
    return events


def prep_activity_events(order: dict) -> List[dict]:
    """Positive PO-total deltas from cumulative Persiapan Helper timeline notes.

    helper_prep.qty_prepared / items[].qty_prepared are CUMULATIVE. Timeline
    notes only store the PO total, not per-line history. Per-user line split
    is therefore not invented when multiple actors save the same PO.
    """
    events: List[dict] = []
    prev = 0
    found_notes = False
    for t in order.get("timeline") or []:
        parsed = parse_prep_note(t.get("note") or "")
        if not parsed:
            continue
        found_notes = True
        prepared, _required = parsed
        email = (t.get("by_email") or "").strip()
        delta = prepared - prev
        prev = prepared
        if not email or delta <= 0:
            continue
        events.append({
            "email": email,
            "name": (t.get("by_name") or "").strip() or email,
            "role": (t.get("by_role") or "").strip(),
            "day": parse_iso_datetime_date(t.get("at")),
            "delta": delta,
            "attribution": "timeline_delta",
        })
    if found_notes:
        return events

    hp = order.get("helper_prep") or {}
    email = (hp.get("updated_by") or "").strip()
    qty = max(0, safe_int(hp.get("qty_prepared")))
    if not email or qty <= 0:
        return []
    return [{
        "email": email,
        "name": (hp.get("updated_by_name") or "").strip() or email,
        "role": "",
        "day": parse_iso_datetime_date(hp.get("updated_at")),
        "delta": qty,
        "attribution": "last_saver_snapshot",
    }]


def _empty_activity_user(email: str, name: str, role: str) -> dict:
    return {
        "user_id": email,
        "email": email,
        "name": name or email,
        "role": role or "",
        "total_qty": 0,
        "po_count": 0,
        "line_count": 0,
        "avg_daily_qty": 0.0,
        "last_activity_at": None,
        "partial_jobs": 0,
        "completed_jobs": 0,
        "active_days": 0,
        "attribution": "timeline_delta",
    }


def _print_line_indices(order: dict) -> List[int]:
    lis = order.get("line_items") or []
    out = []
    for i, li in enumerate(lis):
        p = (li.get("production_process") or "print").strip().lower()
        if p != "non_print":
            out.append(i)
    return out if lis else [0]


def compute_packing_report(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    """Read-only packing activity. Deltas from cumulative snapshots — never sum snapshots."""
    users: Dict[str, dict] = {}
    pos: Dict[str, set] = defaultdict(set)
    lines_seen: Dict[str, set] = defaultdict(set)
    days: Dict[str, set] = defaultdict(set)
    last_day: Dict[str, Optional[date]] = {}
    week_meta = weeks_covering(date_from, date_to)
    week_keys = [w["week"] for w in week_meta]
    week_totals = {k: 0 for k in week_keys}
    per_user_weeks: Dict[str, Dict[str, int]] = defaultdict(lambda: {k: 0 for k in week_keys})
    line_rows: List[dict] = []
    used_fallback = False
    user_filter = (user_id or "").strip()
    if user_filter in ("all", ""):
        user_filter = ""
    role_filter = (role or "").strip()
    if role_filter in ("all", ""):
        role_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""

    roles: set = set()
    user_opts: Dict[str, str] = {}

    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        po_num = _order_po_number(order) or str(po_id)
        cust = (order.get("customer") or "").strip()
        events = packing_activity_events(order)
        packed_map: Dict[int, int] = defaultdict(int)
        required_map: Dict[int, int] = {}
        lis = order.get("line_items") or []
        for i, li in enumerate(lis):
            required_map[i] = max(0, safe_int(li.get("quantity")))
        if not lis:
            required_map[0] = max(0, safe_int((order.get("packing_info") or {}).get("qty_packed")))

        for ev in events:
            if ev.get("attribution") == "last_saver_snapshot":
                used_fallback = True
            email = ev["email"]
            user_opts[email] = ev.get("name") or email
            if ev.get("role"):
                roles.add(ev["role"])
            if user_filter and email != user_filter:
                continue
            if role_filter and (ev.get("role") or "") != role_filter:
                continue
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            rec = users.setdefault(email, _empty_activity_user(email, ev.get("name") or email, ev.get("role") or ""))
            if ev.get("name"):
                rec["name"] = ev["name"]
            if ev.get("role"):
                rec["role"] = ev["role"]
            if ev.get("attribution") == "last_saver_snapshot":
                rec["attribution"] = "last_saver_snapshot"
            rec["total_qty"] += ev["delta"]
            pos[email].add(po_id)
            idx = ev.get("line_idx")
            if idx is None:
                idx = 0
            lines_seen[email].add((po_id, idx))
            packed_map[idx] += ev["delta"]
            if ev.get("day"):
                days[email].add(ev["day"])
                prev = last_day.get(email)
                if prev is None or ev["day"] > prev:
                    last_day[email] = ev["day"]
                wk = iso_week_key(ev["day"])
                if wk in week_totals:
                    week_totals[wk] += ev["delta"]
                    per_user_weeks[email][wk] += ev["delta"]
            line_rows.append({
                "user_id": email,
                "user_name": rec["name"],
                "role": rec["role"],
                "po_id": po_id,
                "po_number": po_num,
                "customer": cust,
                "line_idx": idx,
                "item": line_item_label(order, idx),
                "packed_qty": ev["delta"],
                "day": ev["day"].isoformat() if ev.get("day") else None,
            })

        if packed_map:
            required_total = sum(required_map.values()) if required_map else 0
            packed_total = sum(packed_map.values())
            emails_on_po = {e["email"] for e in events if in_period(e.get("day"), date_from, date_to)}
            if user_filter:
                emails_on_po = {e for e in emails_on_po if e == user_filter}
            complete = required_total > 0 and packed_total >= required_total
            for em in emails_on_po:
                if em in users:
                    if complete:
                        users[em]["completed_jobs"] += 1
                    else:
                        users[em]["partial_jobs"] += 1

    collapsed: Dict[Tuple[str, Any, Any], dict] = {}
    for row in line_rows:
        key = (row["user_id"], row["po_id"], row["line_idx"])
        if key not in collapsed:
            collapsed[key] = dict(row)
        else:
            collapsed[key]["packed_qty"] += row["packed_qty"]
            if row.get("day") and (not collapsed[key].get("day") or row["day"] > collapsed[key]["day"]):
                collapsed[key]["day"] = row["day"]
    line_rows = sorted(
        collapsed.values(),
        key=lambda r: (r.get("po_number") or "", int(r.get("line_idx") or 0), r.get("user_name") or ""),
    )

    rows = []
    for email, rec in users.items():
        rec["po_count"] = len(pos.get(email) or [])
        rec["line_count"] = len(lines_seen.get(email) or [])
        adays = days.get(email) or set()
        rec["active_days"] = len(adays)
        rec["avg_daily_qty"] = round(rec["total_qty"] / len(adays), 2) if adays else 0.0
        rec["last_activity_at"] = last_day[email].isoformat() if last_day.get(email) else None
        rec["weekly"] = per_user_weeks.get(email) or {k: 0 for k in week_keys}
        rows.append(rec)
    rows.sort(key=lambda r: (-r["total_qty"], r["name"] or r["email"]))

    series = []
    prev_qty = None
    for meta in week_meta:
        qty = week_totals.get(meta["week"], 0)
        series.append({**meta, "qty": qty, "wow_pct": wow_pct(qty, prev_qty)})
        prev_qty = qty

    total_qty = sum(r["total_qty"] for r in rows)
    return {
        "kpis": {
            "total_qty": total_qty,
            "user_count": len(rows),
            "po_count": len(set().union(*pos.values()) if pos else set()),
            "line_count": sum(r["line_count"] for r in rows),
        },
        "users": rows,
        "lines": line_rows,
        "weekly_trend": {
            "weeks": series,
            "wow_pct": wow_pct(series[-1]["qty"], series[-2]["qty"]) if len(series) >= 2 else None,
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "filters": {
            "users": [{"id": e, "name": n} for e, n in sorted(user_opts.items(), key=lambda x: x[1] or x[0])],
            "roles": sorted(roles),
        },
        "attribution": {
            "model": "timeline_delta" if not used_fallback else "mixed",
            "cumulative_field": "packing_info.lines[].packed_qty / packing_info.qty_packed",
            "actor": "timeline.by_email pada note 'Qty Packing' (user Save Packing)",
            "timestamp": "timeline.at",
            "per_line": True,
            "note": DATA_SOURCES["packing"],
            "fallback_used": used_fallback,
        },
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "read_only": True,
        "sources": DATA_SOURCES,
        "index_recommendations": INDEX_RECOMMENDATIONS,
    }


def _prep_line_snapshot(order: dict) -> List[dict]:
    hp = order.get("helper_prep") or {}
    items = hp.get("items") if isinstance(hp.get("items"), list) else []
    out = []
    print_idxs = set(_print_line_indices(order))
    if items:
        for it in items:
            idx = it.get("line_idx")
            target = max(0, safe_int(it.get("qty_required")))
            prepared = max(0, safe_int(it.get("qty_prepared")))
            out.append({
                "line_idx": idx,
                "item": (it.get("name") or line_item_label(order, idx)),
                "target": target,
                "prepared": prepared,
                "remaining": remaining_qty(target, prepared),
            })
        return out
    # Legacy: single total, only print lines (Cup/barang cetak) — no invented cup_qty.
    total_prep = max(0, safe_int(hp.get("qty_prepared")))
    total_req = max(0, safe_int(hp.get("qty_required")))
    lis = order.get("line_items") or []
    if not lis:
        if total_prep or total_req:
            out.append({
                "line_idx": 0,
                "item": (order.get("product_name") or "Item"),
                "target": total_req,
                "prepared": total_prep,
                "remaining": remaining_qty(total_req, total_prep),
            })
        return out
    if len(print_idxs) == 1:
        idx = next(iter(print_idxs))
        out.append({
            "line_idx": idx,
            "item": line_item_label(order, idx),
            "target": total_req,
            "prepared": total_prep,
            "remaining": remaining_qty(total_req, total_prep),
        })
        return out
    for idx in sorted(print_idxs):
        li = lis[idx]
        target = max(0, safe_int(li.get("quantity")))
        out.append({
            "line_idx": idx,
            "item": line_item_label(order, idx),
            "target": target,
            "prepared": None,
            "remaining": None,
            "note": "Qty per-line tidak tersedia pada snapshot legacy (hanya total PO).",
        })
    return out


def compute_preparation_report(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    """Read-only persiapan (Cup = line cetak). User qty from timeline deltas, not last saver."""
    users: Dict[str, dict] = {}
    pos: Dict[str, set] = defaultdict(set)
    lines_seen: Dict[str, set] = defaultdict(set)
    days: Dict[str, set] = defaultdict(set)
    last_day: Dict[str, Optional[date]] = {}
    week_meta = weeks_covering(date_from, date_to)
    week_keys = [w["week"] for w in week_meta]
    week_totals = {k: 0 for k in week_keys}
    per_user_weeks: Dict[str, Dict[str, int]] = defaultdict(lambda: {k: 0 for k in week_keys})
    po_rows: List[dict] = []
    used_fallback = False
    multi_user_pos = 0
    cup_acc: Dict[str, Dict[str, dict]] = defaultdict(dict)
    cup_lines: List[dict] = []
    user_filter = (user_id or "").strip()
    if user_filter in ("all", ""):
        user_filter = ""
    role_filter = (role or "").strip()
    if role_filter in ("all", ""):
        role_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""

    roles: set = set()
    user_opts: Dict[str, str] = {}

    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        po_num = _order_po_number(order) or str(po_id)
        cust = (order.get("customer") or "").strip()
        events = prep_activity_events(order)
        in_win = []
        for ev in events:
            if ev.get("attribution") == "last_saver_snapshot":
                used_fallback = True
            email = ev["email"]
            user_opts[email] = ev.get("name") or email
            if ev.get("role"):
                roles.add(ev["role"])
            if user_filter and email != user_filter:
                continue
            if role_filter and (ev.get("role") or "") != role_filter:
                continue
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            in_win.append(ev)
            rec = users.setdefault(email, _empty_activity_user(email, ev.get("name") or email, ev.get("role") or ""))
            if ev.get("name"):
                rec["name"] = ev["name"]
            if ev.get("role"):
                rec["role"] = ev["role"]
            if ev.get("attribution") == "last_saver_snapshot":
                rec["attribution"] = "last_saver_snapshot"
            rec["total_qty"] += ev["delta"]
            pos[email].add(po_id)
            if ev.get("day"):
                days[email].add(ev["day"])
                prev = last_day.get(email)
                if prev is None or ev["day"] > prev:
                    last_day[email] = ev["day"]
                wk = iso_week_key(ev["day"])
                if wk in week_totals:
                    week_totals[wk] += ev["delta"]
                    per_user_weeks[email][wk] += ev["delta"]

        actors = {(e["email"], e.get("name"), e.get("role")) for e in in_win}
        if len({a[0] for a in actors}) > 1:
            multi_user_pos += 1
        line_snap = _prep_line_snapshot(order)
        for li in line_snap:
            for em, _n, _r in actors:
                lines_seen[em].add((po_id, li.get("line_idx")))
        user_qtys = defaultdict(int)
        for ev in in_win:
            user_qtys[ev["email"]] += ev["delta"]
        if in_win:
            po_rows.append({
                "po_id": po_id,
                "po_number": po_num,
                "customer": cust,
                "users": [
                    {
                        "user_id": em,
                        "name": users[em]["name"] if em in users else em,
                        "role": users[em]["role"] if em in users else "",
                        "qty_prepared": qty,
                    }
                    for em, qty in user_qtys.items()
                ],
                "lines": line_snap,
                "per_line_user_history": False if len(user_qtys) > 1 else True,
                "limitation": (
                    "Timeline persiapan hanya menyimpan total PO. Qty per user di bawah "
                    "adalah delta total, bukan split per line. Snapshot line = kondisi terkini."
                    if len(user_qtys) > 1 else None
                ),
            })
            if len(user_qtys) == 1:
                em = next(iter(user_qtys))
                last_ev_day = None
                for ev in in_win:
                    if ev.get("day") and (last_ev_day is None or ev["day"] > last_ev_day):
                        last_ev_day = ev["day"]
                for li in line_snap:
                    item_name = li.get("item") or line_item_name(order, li.get("line_idx"))
                    qty = li.get("prepared")
                    if qty is None:
                        continue
                    cup = cup_acc[em].setdefault(item_name, {
                        "item": item_name, "qty": 0, "pos": set(), "lines": set(), "last_day": None,
                    })
                    cup["qty"] += max(0, safe_int(qty))
                    cup["pos"].add(po_id)
                    cup["lines"].add((po_id, li.get("line_idx")))
                    if last_ev_day and (cup["last_day"] is None or last_ev_day > cup["last_day"]):
                        cup["last_day"] = last_ev_day
                    cup_lines.append({
                        "user_id": em,
                        "user_name": users[em]["name"] if em in users else em,
                        "po_id": po_id,
                        "po_number": po_num,
                        "customer": cust,
                        "line_idx": li.get("line_idx"),
                        "item": item_name,
                        "qty_prepared": max(0, safe_int(qty)),
                        "day": last_ev_day.isoformat() if last_ev_day else None,
                        "attribution": "single_actor_snapshot",
                    })

    rows = []
    for email, rec in users.items():
        rec["po_count"] = len(pos.get(email) or [])
        rec["line_count"] = len(lines_seen.get(email) or [])
        adays = days.get(email) or set()
        rec["active_days"] = len(adays)
        rec["avg_daily_qty"] = round(rec["total_qty"] / len(adays), 2) if adays else 0.0
        rec["last_activity_at"] = last_day[email].isoformat() if last_day.get(email) else None
        rec["weekly"] = per_user_weeks.get(email) or {k: 0 for k in week_keys}
        this_wk = iso_week_key(date_to)
        today = wib_today()
        if date_from <= today <= date_to:
            this_wk = iso_week_key(today)
        rec["this_week_qty"] = int((rec["weekly"] or {}).get(this_wk, 0) or 0)
        cups = []
        for cup in (cup_acc.get(email) or {}).values():
            cups.append({
                "item": cup["item"],
                "qty": cup["qty"],
                "po_count": len(cup["pos"]),
                "line_count": len(cup["lines"]),
                "last_activity_at": cup["last_day"].isoformat() if cup.get("last_day") else None,
            })
        cups.sort(key=lambda c: (-c["qty"], c["item"] or ""))
        rec["cups"] = cups
        rows.append(rec)
    rows.sort(key=lambda r: (-r["total_qty"], r["name"] or r["email"]))

    series = []
    prev_qty = None
    for meta in week_meta:
        qty = week_totals.get(meta["week"], 0)
        series.append({**meta, "qty": qty, "wow_pct": wow_pct(qty, prev_qty)})
        prev_qty = qty

    return {
        "kpis": {
            "total_qty": sum(r["total_qty"] for r in rows),
            "user_count": len(rows),
            "po_count": len(set().union(*pos.values()) if pos else set()),
            "line_count": sum(r["line_count"] for r in rows),
            "this_week_qty": sum(r.get("this_week_qty") or 0 for r in rows),
            "avg_daily_qty": round(
                sum(r["total_qty"] for r in rows) / max(1, len(set().union(*days.values()) if days else set())),
                2,
            ) if days else 0.0,
        },
        "users": rows,
        "pos": po_rows,
        "cup_lines": cup_lines,
        "weekly_trend": {
            "weeks": series,
            "wow_pct": wow_pct(series[-1]["qty"], series[-2]["qty"]) if len(series) >= 2 else None,
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "filters": {
            "users": [{"id": e, "name": n} for e, n in sorted(user_opts.items(), key=lambda x: x[1] or x[0])],
            "roles": sorted(roles),
        },
        "attribution": {
            "model": "timeline_delta" if not used_fallback else "mixed",
            "cumulative_field": "helper_prep.qty_prepared / helper_prep.items[].qty_prepared",
            "actor": "timeline.by_email pada note 'Persiapan Helper' (user Save persiapan)",
            "timestamp": "timeline.at",
            "per_line_snapshot": "helper_prep.items (kondisi terkini, bukan history per user)",
            "per_line_user_history": False,
            "cup_field": None,
            "cup_note": "Tidak ada field cup_qty. Cup/barang = line item Cetak (production_process != non_print).",
            "multi_user_pos": multi_user_pos,
            "fallback_used": used_fallback,
            "note": DATA_SOURCES["preparation"],
        },
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "read_only": True,
        "sources": DATA_SOURCES,
        "index_recommendations": INDEX_RECOMMENDATIONS,
    }


def output_series_from_weekly(weekly: dict, grouping: str = "week") -> List[dict]:
    """Reuse weekly_trend weeks as the compact chart series when grouping is week."""
    return list(weekly.get("weeks") or [])
