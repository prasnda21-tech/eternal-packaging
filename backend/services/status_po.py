"""Laporan → Status PO rows. Reuses dashboard filter predicates + shipment aggregation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from quantity_rules import (
    PRINT_STATUS_DONE,
    PRINT_STATUS_IN_PROGRESS,
    is_fully_delivered,
    packing_status_semantic,
    print_completed_qty,
    print_status,
    qty_delivered,
)
from services.dashboard_today import is_shipment_today, is_work_today, wib_today
from services.shipment_status import shipment_status_row, status_percent, status_percent_display

STAGES = (
    "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
    "Penjadwalan", "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample",
    "Cetak", "Packing", "Surat Jalan", "Loading", "Terkirim",
)

STATUS_PO_VIEWS = (
    "active", "urgent", "due_today", "today", "h1", "h6", "overdue",
    "design", "payment", "hold", "completed", "work_today", "delivery_today",
)


def _has_value(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _surat_jalan_created(order: dict) -> bool:
    if order.get("surat_jalan_created"):
        return True
    if order.get("delivery_note_number") or order.get("delivery_note_created_at"):
        return True
    return bool(order.get("shipments"))


def effective_workflow_status(order: dict) -> str:
    """Current effective workflow status from evidence, not idx < current_stage."""
    if order.get("cancelled"):
        return "Cancelled"
    if order.get("hold"):
        return "HOLD"
    if is_fully_delivered(order) or order.get("terkirim_confirmed") or order.get("delivery_status") == "Terkirim":
        if qty_delivered(order) > 0:
            required = order.get("packing_required_total") or order.get("total_quantity") or 0
            delivered = qty_delivered(order)
            try:
                required_n = int(required or 0)
            except (TypeError, ValueError):
                required_n = 0
            if required_n and delivered < required_n:
                return "Parsial Terkirim"
        if is_fully_delivered(order) or order.get("terkirim_confirmed") or (
            order.get("delivery_status") == "Terkirim" and (order.get("current_stage") or 0) >= 14
        ):
            return "Terkirim"
    delivered = qty_delivered(order)
    if delivered > 0 or order.get("delivery_status") == "Sebagian Terkirim":
        return "Parsial Terkirim"
    if _surat_jalan_created(order):
        semantic = order.get("surat_jalan_status_semantic")
        if semantic == "partial":
            return "SJ parsial"
        return "Surat Jalan"
    pack = packing_status_semantic(order)
    if pack == "Sudah":
        return "Packing"
    if pack == "Berjalan":
        return "Packing berjalan"
    pst = print_status(order)
    if pst == PRINT_STATUS_DONE:
        return "Selesai Cetak"
    if pst == PRINT_STATUS_IN_PROGRESS or print_completed_qty(order) > 0:
        return "Cetak berjalan"
    if order.get("sample_approved"):
        return "Konfirmasi Sample"
    if order.get("sample_files"):
        return "Operator"
    hp = order.get("helper_prep") or {}
    if _has_value(hp.get("qty_prepared")):
        return "Persiapan Helper"
    sc = order.get("screener_info") or {}
    if _has_value(sc.get("screen_qty")):
        return "Screener"
    if order.get("operator_splits"):
        return "Penjadwalan"
    if order.get("payment_status") == "Approved":
        return "Approval Payment"
    if order.get("payment_bypassed"):
        return "Payment"
    if order.get("payment_files"):
        return "Payment"
    if order.get("design_status") == "Approved":
        return "Approval Design"
    if order.get("design_bypassed"):
        return "Design"
    if order.get("design_files"):
        return "Design"
    idx = int(order.get("current_stage") or 0)
    if 0 <= idx < len(STAGES):
        return STAGES[idx]
    return order.get("status_label") or "—"


def _parse_dt(iso: Any):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def filter_orders_for_view(orders: Iterable[dict], view: Optional[str], *, now: Optional[datetime] = None) -> List[dict]:
    """Reuse Dashboard card subsets. Unknown/empty view = all non-cancelled."""
    now = now or datetime.now(timezone.utc)
    today_iso = now.strftime("%Y-%m-%d")
    today = wib_today(now)
    key = (view or "").strip().lower()
    if key == "today":
        key = "due_today"
    src = [o for o in orders if not o.get("cancelled")]
    if not key:
        return src
    if key not in STATUS_PO_VIEWS:
        return src

    active = [o for o in src if (o.get("current_stage") or 0) < 14]
    if key == "active":
        return active
    if key == "urgent":
        return [o for o in active if o.get("priority") == "urgent"]
    if key == "completed":
        return [o for o in src if (o.get("current_stage") or 0) >= 14]
    if key == "hold":
        return [o for o in active if o.get("hold")]
    if key == "design":
        return [o for o in active if o.get("design_status") != "Approved" and not o.get("design_bypassed")]
    if key == "payment":
        return [o for o in active if o.get("payment_status") != "Approved" and not o.get("payment_bypassed")]
    if key == "due_today":
        out = []
        for o in active:
            d = _parse_dt(o.get("deadline"))
            if d and d >= now and d.strftime("%Y-%m-%d") == today_iso:
                out.append(o)
        return out
    if key == "overdue":
        return [o for o in active if o.get("delivery_deadline_status") == "overdue"]
    if key == "h1":
        return [o for o in active if o.get("delivery_deadline_status") == "h1"]
    if key == "h6":
        return [o for o in active if o.get("delivery_deadline_status") == "h6"]
    if key == "work_today":
        return [o for o in src if is_work_today(o, today)]
    if key == "delivery_today":
        return [o for o in src if is_shipment_today(o, today)]
    return src


def _date_key(iso: Any) -> str:
    if not iso:
        return ""
    text = str(iso).strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    dt = _parse_dt(iso)
    return dt.strftime("%Y-%m-%d") if dt else ""


def matches_status_po_filters(row: dict, filters: Optional[dict] = None) -> bool:
    """AND filters on Status PO rows. Empty filter = match. Does not change status calc."""
    if not filters:
        return True
    po = str(filters.get("po") or "").strip().lower()
    customer = str(filters.get("customer") or "").strip().lower()
    status = str(filters.get("status") or "").strip()
    date_from = str(filters.get("date_from") or filters.get("dateFrom") or "").strip()
    date_to = str(filters.get("date_to") or filters.get("dateTo") or "").strip()
    if po and po not in str(row.get("po_number") or "").lower():
        return False
    if customer and customer not in str(row.get("customer") or "").lower():
        return False
    if status and str(row.get("status") or "") != status:
        return False
    key = _date_key(row.get("order_date"))
    if date_from and (not key or key < date_from):
        return False
    if date_to and (not key or key > date_to):
        return False
    return True


def status_po_row(order: dict) -> dict:
    ship = shipment_status_row(order)
    return {
        "id": order.get("id"),
        "po_number": order.get("po_number") or ship.get("order_number") or "",
        "order_date": order.get("order_date") or ship.get("order_date"),
        "customer": order.get("customer") or ship.get("customer_name") or "",
        "status": effective_workflow_status(order),
        "percent_delivered": ship.get("percent_delivered"),
        "percent_delivered_display": ship.get("percent_delivered_display"),
        "qty_delivered": ship.get("qty_delivered"),
        "qty_total": ship.get("qty_total"),
        "current_stage": order.get("current_stage"),
        "cancelled": bool(order.get("cancelled")),
        "hold": bool(order.get("hold")),
    }


def build_status_po(
    orders: Iterable[dict],
    view: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    filters: Optional[dict] = None,
) -> dict:
    filtered = filter_orders_for_view(orders, view, now=now)
    rows = [status_po_row(o) for o in filtered]
    if filters:
        rows = [r for r in rows if matches_status_po_filters(r, filters)]
    rows.sort(key=lambda r: (r.get("order_date") or "", r.get("po_number") or ""))
    return {
        "view": (view or "").strip().lower() or None,
        "rows": rows,
        "total": len(rows),
    }
