"""Read-only operator production analytics (Batch 1 + Batch 2).

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
}

INDEX_RECOMMENDATIONS = [
    "orders: {cancelled: 1, 'operator_splits.0': 1} — scan analytics saat ini",
    "orders: {cancelled: 1, 'operator_splits.operator_email': 1} — filter operator",
    "orders: {cancelled: 1, 'sample_files.uploaded_at': 1} — jika sample analytics menjadi berat",
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
    }


def compute_operator_kpis(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
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
        "sources": DATA_SOURCES,
        "index_recommendations": INDEX_RECOMMENDATIONS,
    }
