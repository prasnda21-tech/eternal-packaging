"""Rencana Jadwal Kirim — read-only PO list with valid Tanggal Pengiriman.

Reuses services.tanggal_pengiriman (no new estimation formula) and
quantity_rules / terkirim_qty_summary for qty (no naive SJ summing).

Exclude: cancelled, fully delivered, missing/invalid date.
Buffer Stock remains visible; schedule_status_code never marks it terlambat.
SJ is NOT required for inclusion.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantity_rules import is_fully_delivered, is_process_only_service_line
from services.delivery_history import terkirim_qty_summary
from services.product_unit_conversion import normalize_uom
from services.delivery_schedule import schedule_status_code, schedule_status_label
from services.paint_table_query import apply_filters, parse_header_filters, parse_sort, sort_rows
from services.tanggal_pengiriman import DEFAULT_LEAD_TIME_DAYS, resolve_tanggal_pengiriman
from workflow_gates import design_ok, payment_ok

PLAN_SORT_FIELDS = (
    "tanggal_pengiriman",
    "po_number",
    "customer_name",
    "branch",
    "delivery_zone",
    "notes",
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
    "delivery_zone": 1,
    "notes": 1,
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
    holidays: Optional[list] = None,
) -> Optional[dict]:
    """Build one plan row or None when excluded."""
    if order.get("cancelled"):
        return None
    if is_fully_delivered(order):
        return None
    snap = resolve_tanggal_pengiriman(order, lead_time_days, holidays=holidays)
    date_key = snap.get("tanggal_pengiriman")
    if not date_key:
        return None
    source = snap.get("source") or "none"
    if source not in ("manual", "automatic"):
        return None
    qty = terkirim_qty_summary(order)
    status = schedule_status_code(
        order,
        today=today,
        lead_time_days=lead_time_days,
        schedule_date=date_key,
        holidays=holidays,
    )
    return {
        "id": order.get("id"),
        "order_id": order.get("id"),
        "po_number": order.get("po_number") or "",
        "customer_name": order.get("customer_name") or order.get("customer") or "",
        "branch": _branch_label(order),
        "branch_id": order.get("branch_id"),
        "delivery_zone": (str(order.get("delivery_zone") or "").strip()),
        "notes": (str(order.get("notes") or "").strip()),
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
        "delivery_sla_effective": snap.get("delivery_sla_effective"),
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
    holidays: Optional[list] = None,
    filters: Optional[dict] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hf: Optional[Sequence[str]] = None,
) -> List[dict]:
    """Eligibility → toolbar filters → header filters → stable sort (before pagination)."""
    rows: List[dict] = []
    for order in orders:
        row = plan_row(order, lead_time_days=lead_time_days, today=today, holidays=holidays)
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


QTY_WIDGET_ID = "delivery_qty"
QTY_HORIZONS = (7, 30)
UNIT_NONE = "tanpa_satuan"
UNIT_MIXED = "campuran"
NOTE_NO_UNIT = "Qty tanpa satuan tidak dijumlahkan dan tidak dianggap pcs."
NOTE_MIXED = (
    "PO dengan lebih dari satu satuan ditampilkan terpisah sebagai campuran "
    "dan tidak dijumlahkan ke satuan lain."
)

QTY_WIDGET_PROJECTION = {
    **PLAN_PROJECTION,
    "line_items.unit": 1,
}


def _positive_line_qty(value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def plan_qty_unit(order: dict) -> str:
    """Unit bucket for one PO's remaining qty.

    Remaining qty stays the PO-level integer from terkirim_qty_summary.
    Lines that do not count toward packing are ignored. One shared unit keeps
    that unit. Blank units stay blank and are never treated as pcs. More than
    one unit stays in campuran so the PO integer is not added to either series.
    """
    seen: List[str] = []
    any_physical = False
    for line in order.get("line_items") or []:
        if not isinstance(line, dict) or is_process_only_service_line(line):
            continue
        any_physical = True
        if not _positive_line_qty(line.get("quantity")):
            continue
        key = normalize_uom(line.get("unit")) or UNIT_NONE
        if key not in seen:
            seen.append(key)
    if not any_physical or not seen or seen == [UNIT_NONE]:
        return UNIT_NONE
    if len(seen) == 1:
        return seen[0]
    return UNIT_MIXED


def _today_date(today: Any) -> date:
    if isinstance(today, date):
        return today
    if isinstance(today, str):
        return date.fromisoformat(today[:10])
    raise TypeError("today harus tanggal")


def aggregate_delivery_plan_qty(
    orders: List[dict],
    *,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    today: Any,
    days: int = 7,
    holidays: Optional[list] = None,
) -> Dict[str, Any]:
    """Active-plan snapshot grouped by effective delivery date and unit.

    Same inclusion as plan_row. Not a stored history and not delivery realization.
    Chart window is today through today+days-1 (WIB). Overdue qty uses the same
    schedule_status_code rule as Rencana Jadwal Kirim (Buffer Stock is never
    terlambat) and is never added into today or the forward chart.
    Every eligible order in ``orders`` is counted. This function does not page.
    """
    if days not in QTY_HORIZONS:
        raise ValueError("Periode harus 7 atau 30 hari")
    start = _today_date(today)
    today_key = start.isoformat()
    end = start + timedelta(days=days - 1)
    end_key = end.isoformat()
    dates = [(start + timedelta(days=offset)).isoformat() for offset in range(days)]
    forward: Dict[str, Dict[str, int]] = {}
    forward_po: Dict[str, Dict[str, set]] = {}
    today_qty: Dict[str, int] = {}
    today_po: Dict[str, set] = {}
    overdue_qty: Dict[str, int] = {}
    overdue_po: Dict[str, set] = {}
    seen_ids = set()
    for order in orders:
        row = plan_row(order, lead_time_days=lead_time_days, today=today_key, holidays=holidays)
        if not row:
            continue
        order_id = str(row.get("id") or "").strip()
        if order_id:
            if order_id in seen_ids:
                continue
            seen_ids.add(order_id)
        try:
            remaining = int(row.get("qty_remaining") or 0)
        except (TypeError, ValueError):
            remaining = 0
        if remaining <= 0:
            continue
        unit = plan_qty_unit(order)
        plan_date = str(row.get("tanggal_pengiriman") or "")
        status_code = row.get("delivery_schedule_status") or ""
        po_key = order_id or f"anon:{id(order)}"
        if status_code == "terlambat":
            overdue_qty[unit] = overdue_qty.get(unit, 0) + remaining
            overdue_po.setdefault(unit, set()).add(po_key)
            continue
        if plan_date == today_key:
            today_qty[unit] = today_qty.get(unit, 0) + remaining
            today_po.setdefault(unit, set()).add(po_key)
        if today_key <= plan_date <= end_key:
            day_bucket = forward.setdefault(unit, {})
            day_bucket[plan_date] = day_bucket.get(plan_date, 0) + remaining
            day_po = forward_po.setdefault(unit, {})
            day_po.setdefault(plan_date, set()).add(po_key)

    def _rank(unit: str) -> tuple:
        if unit == UNIT_NONE:
            return (1, unit)
        if unit == UNIT_MIXED:
            return (2, unit)
        return (0, unit)

    units = set(forward) | set(today_qty) | set(overdue_qty)
    series = []
    for unit in sorted(units, key=_rank):
        day_bucket = forward.get(unit) or {}
        day_po = forward_po.get(unit) or {}
        points = [
            {
                "date": day,
                "qty": int(day_bucket.get(day, 0)),
                "po_count": len(day_po.get(day) or ()),
            }
            for day in dates
        ]
        series.append({
            "unit": unit,
            "today_qty": int(today_qty.get(unit, 0)),
            "today_po_count": len(today_po.get(unit) or ()),
            "overdue_qty": int(overdue_qty.get(unit, 0)),
            "overdue_po_count": len(overdue_po.get(unit) or ()),
            "points": points,
        })
    notes: List[str] = []
    if any(item["unit"] == UNIT_NONE for item in series):
        notes.append(NOTE_NO_UNIT)
    if any(item["unit"] == UNIT_MIXED for item in series):
        notes.append(NOTE_MIXED)
    has_signal = any(
        item["today_qty"] or item["overdue_qty"] or any(point["qty"] for point in item["points"])
        for item in series
    )
    status = "complete" if has_signal else "empty"
    return {
        "status": status,
        "days": days,
        "today": today_key,
        "start": today_key,
        "end": end_key,
        "timezone": "Asia/Jakarta",
        "snapshot": True,
        "series": series if has_signal else [],
        "notes": notes if has_signal else [],
        "detail": None if has_signal else "Belum ada rencana pengiriman aktif pada rentang ini.",
    }


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
