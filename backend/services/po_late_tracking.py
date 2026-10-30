"""PO Terlambat Kirim — PO-grain active lateness (Owner final rules).

One row per PO. SJ is not required. Multi-SJ never inflates the count.
Calendar days use Asia/Jakarta. Read-only — no order/SJ mutation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantity_rules import is_fully_delivered, packing_required_total
from services.delivery_history import terkirim_qty_summary
from services.delivery_plan import _branch_label
from services.sales_order_list_filters import sales_order_process_label
from services.tanggal_pengiriman import (
    DEFAULT_LEAD_TIME_DAYS,
    parse_calendar_date,
    resolve_tanggal_pengiriman,
    wib_calendar_date,
)

WIB = timezone(timedelta(hours=7))

CAT_LEWAT_1 = "lewat_1_hari"
CAT_TERLAMBAT = "terlambat"
CAT_KRITIS = "kritis"

CATEGORY_LABELS = {
    CAT_LEWAT_1: "Lewat 1 Hari",
    CAT_TERLAMBAT: "Terlambat",
    CAT_KRITIS: "Kritis",
}

CATEGORY_VALUES = frozenset(CATEGORY_LABELS.keys())

SORT_FIELDS = frozenset({
    "days_late",
    "tanggal_pengiriman",
    "order_date",
    "po_number",
    "customer_name",
    "branch",
    "umur_po_days",
    "qty_remaining",
    "category",
    "id",
})

LATE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "order_date": 1,
    "created_at": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "branch": 1,
    "branch_id": 1,
    "operational_branch_id": 1,
    "branch_name": 1,
    "cabang": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_sla_v2": 1,
    "delivery_reschedules": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "status_label": 1,
    "hold": 1,
    "qty_delivered": 1,
    "total_quantity": 1,
    "quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "line_items.unit": 1,
    "line_items.production_process": 1,
    "line_items.physical_fulfillment": 1,
    "line_items.item_type": 1,
    "cancelled": 1,
    "buffer_stock": 1,
    "priority": 1,
}


def today_wib(now: Optional[datetime] = None) -> date:
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(WIB).date()


def today_key(now: Optional[datetime] = None) -> str:
    return today_wib(now).isoformat()


def calendar_days_between(earlier: str, later: str) -> Optional[int]:
    a = parse_calendar_date(earlier)
    b = parse_calendar_date(later)
    if not a or not b:
        return None
    try:
        da = date.fromisoformat(a)
        db = date.fromisoformat(b)
    except ValueError:
        return None
    return (db - da).days


def late_category(days_late: int) -> Optional[str]:
    if days_late <= 0:
        return None
    if days_late == 1:
        return CAT_LEWAT_1
    if days_late <= 3:
        return CAT_TERLAMBAT
    return CAT_KRITIS


def order_date_key(order: dict) -> Optional[str]:
    raw = order.get("order_date")
    key = wib_calendar_date(raw)
    if key:
        return key
    return parse_calendar_date(raw)


def mongo_match(
    *,
    sales_scope: Optional[dict] = None,
    branch_scope: Optional[dict] = None,
) -> dict:
    """Pre-filter only. Late classification stays in Python (computed tanggal)."""
    q: Dict[str, Any] = {
        "cancelled": {"$ne": True},
        "buffer_stock": {"$ne": True},
    }
    if sales_scope:
        q.update(sales_scope)
    if branch_scope:
        q.update(branch_scope)
    return q


def branch_mongo_scope(access: dict, requested_branch_id: Optional[str] = None) -> dict:
    """Build branch mongo fragment after access resolution / validation."""
    bid = str(requested_branch_id or "").strip()
    if bid:
        return {"branch_id": bid}
    if (access or {}).get("scope") == "ALL":
        return {}
    ids = [str(x).strip() for x in ((access or {}).get("branch_ids") or []) if str(x).strip()]
    if not ids:
        return {"branch_id": {"$in": []}}
    return {"branch_id": {"$in": ids}}


def classify_active_late(
    order: dict,
    *,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[list] = None,
) -> Dict[str, Any]:
    """Return classification for one PO. Never invents late/complete."""
    day = parse_calendar_date(today) or today_key()
    if order.get("cancelled"):
        return {"include": False, "reason": "cancelled"}
    if order.get("buffer_stock"):
        return {"include": False, "reason": "buffer_stock"}

    required = packing_required_total(order)
    if required <= 0:
        return {"include": False, "reason": "qty_basis_unreliable"}
    if is_fully_delivered(order):
        return {"include": False, "reason": "fully_delivered"}

    snap = resolve_tanggal_pengiriman(order, lead_time_days, holidays=holidays)
    eff = parse_calendar_date(snap.get("tanggal_pengiriman"))
    if not eff or snap.get("source") == "none":
        return {"include": False, "reason": "missing_effective_date"}

    days_late = calendar_days_between(eff, day)
    if days_late is None:
        return {"include": False, "reason": "invalid_effective_date"}
    if days_late <= 0:
        return {"include": False, "reason": "not_yet_late", "tanggal_pengiriman": eff, "days_late": days_late}

    category = late_category(days_late)
    if not category:
        return {"include": False, "reason": "not_yet_late", "tanggal_pengiriman": eff, "days_late": days_late}

    od = order_date_key(order)
    umur = calendar_days_between(od, day) if od else None
    qty = terkirim_qty_summary(order)
    return {
        "include": True,
        "reason": "active_late",
        "order_date": od,
        "umur_po_days": umur,
        "tanggal_pengiriman": eff,
        "tanggal_pengiriman_source": snap.get("source"),
        "days_late": days_late,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "qty_total": qty["total_po_qty"],
        "qty_delivered": qty["qty_delivered"],
        "qty_remaining": qty["qty_remaining"],
        "delivery_sla_effective": snap.get("delivery_sla_effective"),
    }


def build_late_row(
    order: dict,
    *,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[list] = None,
) -> Optional[dict]:
    fact = classify_active_late(
        order, today=today, lead_time_days=lead_time_days, holidays=holidays,
    )
    if not fact.get("include"):
        return None
    oid = order.get("id")
    return {
        "id": oid,
        "order_id": oid,
        "po_number": order.get("po_number") or "",
        "order_date": fact.get("order_date"),
        "customer_name": order.get("customer_name") or order.get("customer") or "",
        "customer_id": order.get("customer_id"),
        "branch": _branch_label(order),
        "branch_id": order.get("operational_branch_id") or order.get("branch_id"),
        "status_saat_ini": sales_order_process_label(order),
        "umur_po_days": fact.get("umur_po_days"),
        "tanggal_pengiriman": fact.get("tanggal_pengiriman"),
        "tanggal_pengiriman_source": fact.get("tanggal_pengiriman_source"),
        "days_late": fact.get("days_late"),
        "category": fact.get("category"),
        "category_label": fact.get("category_label"),
        "qty_total": fact.get("qty_total"),
        "qty_delivered": fact.get("qty_delivered"),
        "qty_remaining": fact.get("qty_remaining"),
        "delivery_status": order.get("delivery_status") or "",
        "priority": order.get("priority") or "normal",
        "buffer_stock": False,
        "detail_href": f"/po/{oid}" if oid else None,
        "delivery_sla_effective": fact.get("delivery_sla_effective"),
    }


def _match_search(row: dict, q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    hay = f"{row.get('po_number') or ''} {row.get('customer_name') or ''}".lower()
    return needle in hay


def filter_late_rows(
    rows: Sequence[dict],
    *,
    q: Optional[str] = None,
    category: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> List[dict]:
    out: List[dict] = []
    cat = str(category or "").strip().lower()
    bid = str(branch_id or "").strip()
    for row in rows:
        if not _match_search(row, q or ""):
            continue
        if cat and cat in CATEGORY_VALUES and row.get("category") != cat:
            continue
        if bid and str(row.get("branch_id") or "").strip() != bid:
            continue
        out.append(row)
    return out


def sort_late_rows(
    rows: Sequence[dict],
    *,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> List[dict]:
    field = str(sort_by or "days_late").strip()
    if field not in SORT_FIELDS:
        field = "days_late"
    descending = str(sort_dir or "desc").strip().lower() != "asc"

    def key(row: dict):
        raw = row.get(field)
        if field in ("days_late", "umur_po_days", "qty_remaining"):
            try:
                primary: Any = int(raw) if raw is not None else -1
            except (TypeError, ValueError):
                primary = -1
        elif field == "category":
            rank = {CAT_KRITIS: 3, CAT_TERLAMBAT: 2, CAT_LEWAT_1: 1}
            primary = rank.get(str(raw or ""), 0)
        else:
            primary = str(raw or "")
        # Stable tie-break: days_late desc, tanggal_pengiriman asc, id asc
        try:
            days = int(row.get("days_late") or 0)
        except (TypeError, ValueError):
            days = 0
        return (primary, days, str(row.get("tanggal_pengiriman") or ""), str(row.get("id") or ""))

    ordered = sorted(rows, key=key, reverse=descending)
    if field == "days_late" and descending:
        return ordered
    # When primary sort is not days_late, still prefer higher days_late inside equal primary via key tuple.
    return ordered


def category_counts(rows: Sequence[dict]) -> Dict[str, int]:
    counts = {CAT_LEWAT_1: 0, CAT_TERLAMBAT: 0, CAT_KRITIS: 0}
    for row in rows:
        cat = row.get("category")
        if cat in counts:
            counts[cat] += 1
    return counts


def paginate_rows(
    rows: Sequence[dict],
    *,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[dict], dict]:
    try:
        p = max(1, int(page or 1))
    except (TypeError, ValueError):
        p = 1
    try:
        size = int(page_size or 50)
    except (TypeError, ValueError):
        size = 50
    size = max(1, min(200, size))
    total = len(rows)
    total_pages = max(1, (total + size - 1) // size) if total else 1
    if p > total_pages:
        p = total_pages
    start = (p - 1) * size
    page_rows = list(rows[start:start + size])
    return page_rows, {
        "page": p,
        "page_size": size,
        "total": total,
        "total_pages": total_pages,
    }


def build_late_rows(
    orders: Sequence[dict],
    *,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[list] = None,
    q: Optional[str] = None,
    category: Optional[str] = None,
    branch_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> List[dict]:
    built: List[dict] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        row = build_late_row(
            order, today=today, lead_time_days=lead_time_days, holidays=holidays,
        )
        if row:
            built.append(row)
    filtered = filter_late_rows(built, q=q, category=category, branch_id=branch_id)
    return sort_late_rows(filtered, sort_by=sort_by, sort_dir=sort_dir)


def branch_options_from_orders(docs: Sequence[dict]) -> list:
    """Branch labels from already-scoped order docs only — no master expansion."""
    seen: dict = {}
    for order in docs or []:
        bid = str(order.get("operational_branch_id") or order.get("branch_id") or "").strip()
        if not bid:
            continue
        label = (_branch_label(order) or "").strip() or bid
        prev = seen.get(bid)
        if prev is None or (prev.get("name") == bid and label != bid):
            seen[bid] = {"id": bid, "name": label}
    return sorted(seen.values(), key=lambda row: str(row.get("name") or "").lower())


def branch_options_from_rows(rows: Sequence[dict]) -> list:
    seen: dict = {}
    for row in rows or []:
        bid = str(row.get("branch_id") or "").strip()
        if not bid:
            continue
        label = str(row.get("branch") or "").strip() or bid
        prev = seen.get(bid)
        if prev is None or (prev.get("name") == bid and label != bid):
            seen[bid] = {"id": bid, "name": label}
    return sorted(seen.values(), key=lambda row: str(row.get("name") or "").lower())


def summary_payload(
    rows: Sequence[dict],
    *,
    today: Optional[str] = None,
    scope: Optional[dict] = None,
    branch_options: Optional[list] = None,
) -> dict:
    counts = category_counts(rows)
    total = len(rows)
    return {
        "status": "complete" if total else "empty",
        "total": total,
        "counts": counts,
        "labels": dict(CATEGORY_LABELS),
        "today": parse_calendar_date(today) or today_key(),
        "timezone": "Asia/Jakarta",
        "grain": "po",
        "note": "PO aktif melewati tanggal pengiriman.",
        "scope": scope or {},
        "branch_options": list(branch_options) if branch_options is not None else branch_options_from_rows(rows),
        "drill_path": "/produksi/tracking-po-terlambat?late=1",
    }
