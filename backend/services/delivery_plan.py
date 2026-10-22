"""Rencana Jadwal Kirim — read-only PO list with valid Tanggal Pengiriman.

Reuses services.tanggal_pengiriman (no new estimation formula) and
quantity_rules / terkirim_qty_summary for qty (no naive SJ summing).

Exclude: cancelled, fully delivered, missing/invalid date.
Buffer Stock remains visible; schedule_status_code never marks it terlambat.
SJ is NOT required for inclusion.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantity_rules import is_fully_delivered
from services.delivery_history import terkirim_qty_summary
from services.delivery_schedule import schedule_status_code, schedule_status_label
from services.paint_table_query import apply_filters, parse_header_filters, parse_sort, sort_rows
from services.tanggal_pengiriman import DEFAULT_LEAD_TIME_DAYS, resolve_tanggal_pengiriman
from workflow_gates import design_ok, payment_ok

PLAN_SORT_FIELDS = (
    "tanggal_pengiriman",
    "po_number",
    "customer_name",
    "branch",
    "tanggal_pengiriman_source",
    "qty_total",
    "qty_delivered",
    "qty_remaining",
    "delivery_schedule_status",
)
PLAN_FILTER_FIELDS = PLAN_SORT_FIELDS

PLAN_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "branch": 1,
    "branch_id": 1,
    "branch_name": 1,
    "cabang": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_reschedules": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "qty_delivered": 1,
    "total_quantity": 1,
    "quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "line_items.physical_fulfillment": 1,
    "line_items.item_type": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "customer_approval": 1,
    "cancelled": 1,
    "buffer_stock": 1,
    "priority": 1,
}


def _branch_label(order: dict) -> str:
    for key in ("branch_name", "branch", "cabang"):
        val = order.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    bid = order.get("branch_id")
    if bid is not None and str(bid).strip():
        return str(bid).strip()
    return ""


def plan_row(
    order: dict,
    *,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    today: Optional[str] = None,
) -> Optional[dict]:
    """Build one plan row or None when excluded."""
    if order.get("cancelled"):
        return None
    if is_fully_delivered(order):
        return None
    snap = resolve_tanggal_pengiriman(order, lead_time_days)
    date_key = snap.get("tanggal_pengiriman")
    if not date_key:
        return None
    source = snap.get("source") or "none"
    if source not in ("manual", "automatic"):
        return None
    qty = terkirim_qty_summary(order)
    status = schedule_status_code(order, today=today, lead_time_days=lead_time_days, schedule_date=date_key)
    return {
        "id": order.get("id"),
        "order_id": order.get("id"),
        "po_number": order.get("po_number") or "",
        "customer_name": order.get("customer_name") or order.get("customer") or "",
        "branch": _branch_label(order),
        "branch_id": order.get("branch_id"),
        "tanggal_pengiriman": date_key,
        "tanggal_pengiriman_source": source,
        "effective_delivery_date": date_key,
        "qty_total": qty["total_po_qty"],
        "qty_delivered": qty["qty_delivered"],
        "qty_remaining": qty["qty_remaining"],
        "delivery_schedule_status": status,
        "delivery_schedule_status_label": schedule_status_label(status),
        "design_ready": design_ok(order),
        "payment_ready": payment_ok(order),
        "design_status": order.get("design_status"),
        "payment_status": order.get("payment_status"),
        "design_bypassed": bool(order.get("design_bypassed")),
        "payment_bypassed": bool(order.get("payment_bypassed")),
        "delivery_status": order.get("delivery_status") or "",
        "priority": order.get("priority") or "normal",
        "urgent": str(order.get("priority") or "").lower() == "urgent",
        "buffer_stock": bool(order.get("buffer_stock")),
    }


def _match_search(row: dict, q: str) -> bool:
    if not q:
        return True
    needle = q.strip().lower()
    if not needle:
        return True
    hay = f"{row.get('po_number') or ''} {row.get('customer_name') or ''}".lower()
    return needle in hay


def _match_filters(row: dict, filters: dict) -> bool:
    f = filters or {}
    q = (f.get("q") or "").strip()
    if q and not _match_search(row, q):
        return False
    source = (f.get("source") or "").strip().lower()
    if source in ("manual", "automatic") and row.get("tanggal_pengiriman_source") != source:
        return False
    status = (f.get("status") or "").strip().lower()
    if status and status not in ("", "semua", "all") and row.get("delivery_schedule_status") != status:
        return False
    date_from = (f.get("date_from") or "").strip()[:10]
    date_to = (f.get("date_to") or "").strip()[:10]
    d = row.get("tanggal_pengiriman") or ""
    if date_from and (not d or d < date_from):
        return False
    if date_to and (not d or d > date_to):
        return False
    return True


def build_plan_rows(
    orders: List[dict],
    *,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    today: Optional[str] = None,
    filters: Optional[dict] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hf: Optional[Sequence[str]] = None,
) -> List[dict]:
    """Eligibility → toolbar filters → header filters → stable sort (before pagination)."""
    rows: List[dict] = []
    for order in orders:
        row = plan_row(order, lead_time_days=lead_time_days, today=today)
        if row and _match_filters(row, filters or {}):
            rows.append(row)
    header_filters = parse_header_filters(hf, PLAN_FILTER_FIELDS)
    rows = apply_filters(rows, header_filters)
    field, direction = parse_sort(sort_by, sort_dir, PLAN_SORT_FIELDS, "tanggal_pengiriman")
    # Default chronological ASC when caller omits sort_by (existing UX).
    if not (sort_by or "").strip():
        field, direction = "tanggal_pengiriman", 1
    return sort_rows(rows, sort_by=field, sort_dir=direction, tie_break="id")


def _aggregate_totals(rows: List[dict]) -> Dict[str, Any]:
    """KPI over entire filtered eligible set (not current page)."""
    total_po = len(rows)
    qty_total = 0
    qty_delivered = 0
    qty_remaining = 0
    for r in rows:
        try:
            qty_total += int(r.get("qty_total") or 0)
        except (TypeError, ValueError):
            pass
        try:
            qty_delivered += int(r.get("qty_delivered") or 0)
        except (TypeError, ValueError):
            pass
        try:
            qty_remaining += int(r.get("qty_remaining") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "total_po": total_po,
        "qty_total": qty_total,
        "qty_delivered": qty_delivered,
        "qty_remaining": qty_remaining,
    }


def paginate_rows(
    rows: List[dict],
    *,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[dict], dict]:
    size = max(1, min(int(page_size or 50), 200))
    total = len(rows)
    pages = max(1, (total + size - 1) // size) if total else 1
    current = max(1, min(int(page or 1), pages))
    start = (current - 1) * size
    slice_rows = rows[start : start + size]
    status_counts: Dict[str, int] = {}
    source_counts = {"manual": 0, "automatic": 0}
    for r in rows:
        st = r.get("delivery_schedule_status") or "unknown"
        status_counts[st] = status_counts.get(st, 0) + 1
        src = r.get("tanggal_pengiriman_source")
        if src in source_counts:
            source_counts[src] += 1
    meta = {
        "page": current,
        "page_size": size,
        "total": total,
        "total_pages": pages,
        "counts": {
            "total": total,
            "by_status": status_counts,
            "by_source": source_counts,
        },
        "totals": _aggregate_totals(rows),
    }
    return slice_rows, meta


def mongo_match(extra: Optional[dict] = None) -> dict:
    """Base Mongo filter — cancelled excluded server-side.

    Buffer Stock stays in the plan; lateness is handled by schedule_status_code.
    """
    q: Dict[str, Any] = {
        "cancelled": {"$ne": True},
    }
    if extra:
        q.update(extra)
    return q
