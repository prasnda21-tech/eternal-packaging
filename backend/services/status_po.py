"""Laporan → Status PO rows. Reuses dashboard filter predicates + shipment aggregation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from quantity_rules import (
    PRINT_STATUS_DONE,
    PRINT_STATUS_IN_PROGRESS,
    PRINT_STATUS_NOT_STARTED,
    find_shipment,
    packing_status_semantic,
    print_completed_qty,
    print_status,
    requires_printing,
    shipment_is_active,
)
from services.dashboard_today import is_shipment_today, is_work_today, wib_today
from services.delivery_loading_gate import dispatch_stage_states, po_is_fully_delivered
from services.shipment_status import shipment_status_row, status_percent, status_percent_display
from workflow_gates import (
    customer_design_approved,
    design_ok,
    helper_done,
    payment_evidence_present,
    payment_ok,
    payment_stage_done,
    penjadwalan_done,
    sample_ok,
    sample_uploaded,
    screener_done,
)

STAGES = (
    "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
    "Penjadwalan", "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample",
    "Cetak", "Packing", "Surat Jalan", "Loading", "Pengiriman",
)

STATUS_PO_VIEWS = (
    "active", "urgent", "due_today", "today", "h1", "h6", "overdue",
    "design", "payment", "hold", "completed", "cancelled", "all",
    "work_today", "delivery_today",
)

STATUS_PO_DEFAULT_PAGE_SIZE = 50
STATUS_PO_MAX_PAGE_SIZE = 50

STATUS_PO_STAGE_KEYS = (
    "order", "design", "appr_design", "payment", "appr_payment",
    "jadwal", "screen", "helper", "operator", "sample",
    "cetak", "packing", "sj", "loading", "kirim",
)


def _has_value(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _has_file_records(files: Any) -> bool:
    if not isinstance(files, list) or not files:
        return False
    return any(isinstance(f, dict) and f for f in files)


def item_line_names(order: dict) -> List[str]:
    """Item names only from existing line_items. No qty. One PO stays one row."""
    names: List[str] = []
    seen = set()
    for li in order.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        name = str(li.get("name") or li.get("item_name") or li.get("product_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _overlay_row_flags(state: str, *, cancelled: bool, hold: bool) -> str:
    if cancelled:
        return "cancelled"
    if hold and state == "active":
        return "hold"
    return state


def status_po_workflow_detail(order: dict) -> tuple:
    """Return (workflow states, reasons). Reasons are display-only."""
    wf = status_po_workflow(order)
    _states, reasons = dispatch_stage_states(order or {})
    return wf, reasons


def status_po_workflow(order: dict) -> Dict[str, str]:
    """Explicit per-stage state from existing order evidence. Never sequential current_stage completion."""
    order = order or {}
    cancelled = bool(order.get("cancelled"))
    hold = bool(order.get("hold"))

    def out(state: str) -> str:
        return _overlay_row_flags(state, cancelled=cancelled, hold=hold)

    workflow: Dict[str, str] = {}

    if order.get("id") or _has_value(order.get("po_number")):
        workflow["order"] = out("completed")
    else:
        workflow["order"] = out("unknown")

    if order.get("design_bypassed") or _has_file_records(order.get("design_files")):
        workflow["design"] = out("completed")
    else:
        workflow["design"] = out("pending")

    if design_ok(order):
        workflow["appr_design"] = out("completed")
    elif _has_file_records(order.get("design_files")):
        workflow["appr_design"] = out("active")
    else:
        workflow["appr_design"] = out("pending")

    if payment_stage_done(order):
        workflow["payment"] = out("completed")
    else:
        workflow["payment"] = out("pending")

    if payment_ok(order):
        workflow["appr_payment"] = out("completed")
    elif payment_evidence_present(order):
        workflow["appr_payment"] = out("active")
    else:
        workflow["appr_payment"] = out("pending")

    if penjadwalan_done(order) or (order.get("operator_splits") or []):
        workflow["jadwal"] = out("completed")
    else:
        workflow["jadwal"] = out("pending")

    if screener_done(order):
        workflow["screen"] = out("completed")
    else:
        workflow["screen"] = out("pending")

    if helper_done(order):
        workflow["helper"] = out("completed")
    else:
        workflow["helper"] = out("pending")

    if not requires_printing(order):
        workflow["operator"] = out("completed" if penjadwalan_done(order) else "pending")
    elif sample_uploaded(order):
        workflow["operator"] = out("completed")
    else:
        workflow["operator"] = out("pending")

    if sample_ok(order):
        workflow["sample"] = out("completed")
    elif sample_uploaded(order):
        workflow["sample"] = out("active")
    else:
        workflow["sample"] = out("pending")

    if not requires_printing(order):
        workflow["cetak"] = out("completed")
    else:
        pst = print_status(order)
        if pst == PRINT_STATUS_DONE:
            workflow["cetak"] = out("completed")
        elif pst == PRINT_STATUS_IN_PROGRESS:
            workflow["cetak"] = out("active")
        elif pst == PRINT_STATUS_NOT_STARTED:
            workflow["cetak"] = out("pending")
        else:
            workflow["cetak"] = out("unknown")

    pack = packing_status_semantic(order)
    if pack == "Sudah":
        workflow["packing"] = out("completed")
    elif pack == "Berjalan":
        workflow["packing"] = out("active")
    elif pack == "Belum":
        workflow["packing"] = out("pending")
    else:
        workflow["packing"] = out("unknown")

    dispatch, _reasons = dispatch_stage_states(order)
    workflow["sj"] = out(dispatch["sj"])
    workflow["loading"] = out(dispatch["loading"])
    workflow["kirim"] = out(dispatch["kirim"])

    return {key: workflow.get(key, "unknown") for key in STATUS_PO_STAGE_KEYS}


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
    dispatch, _reasons = dispatch_stage_states(order or {})
    if dispatch.get("kirim") == "completed":
        return "Terkirim"
    if dispatch.get("kirim") == "active":
        return "Parsial Terkirim"
    if dispatch.get("loading") == "active":
        return "Loading"
    if dispatch.get("loading") == "completed" and dispatch.get("kirim") == "pending":
        return "Pengiriman"
    if dispatch.get("sj") == "active":
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
    if order.get("design_status") == "Approved" or customer_design_approved(order):
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
    """Filter orders for Status PO tabs and dashboard deep-links.

    Tab views (empty/all, active, completed, cancelled):
      - Semua = non-cancelled (aktif + selesai)
      - Aktif = non-cancelled and not po_is_fully_delivered
      - Selesai = non-cancelled and po_is_fully_delivered
      - Dibatalkan = cancelled only
    Special dashboard views keep prior stage-based active subset semantics.
    """
    now = now or datetime.now(timezone.utc)
    today_iso = now.strftime("%Y-%m-%d")
    today = wib_today(now)
    key = (view or "").strip().lower()
    if key == "today":
        key = "due_today"
    if key in ("semua",):
        key = "all"

    if key == "cancelled":
        return [o for o in orders if o.get("cancelled")]

    src = [o for o in orders if not o.get("cancelled")]
    if not key or key == "all":
        return src

    # Four-tab classification uses canonical delivery evidence.
    if key == "active":
        return [o for o in src if not po_is_fully_delivered(o)]
    if key == "completed":
        return [o for o in src if po_is_fully_delivered(o)]

    if key not in STATUS_PO_VIEWS:
        return src

    # Special dashboard views: preserve stage < 14 active-subset semantics.
    active = [o for o in src if (o.get("current_stage") or 0) < 14]
    if key == "urgent":
        return [o for o in active if o.get("priority") == "urgent"]
    if key == "hold":
        return [o for o in active if o.get("hold")]
    if key == "design":
        return [o for o in active if not design_ok(o)]
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


def _search_haystack_from_order(order: dict) -> str:
    parts = [
        str(order.get("po_number") or ""),
        str(order.get("customer") or ""),
    ]
    for name in item_line_names(order):
        parts.append(name)
    return " ".join(parts).lower()


def _search_haystack_from_row(row: dict) -> str:
    parts = [
        str(row.get("po_number") or ""),
        str(row.get("customer") or ""),
    ]
    for name in row.get("item_lines") or []:
        parts.append(str(name or ""))
    return " ".join(parts).lower()


def order_matches_search_date_filters(order: dict, filters: Optional[dict] = None) -> bool:
    """Text/date filters on raw orders (before status_po_row). Status handled separately."""
    if not filters:
        return True
    po = str(filters.get("po") or "").strip().lower()
    customer = str(filters.get("customer") or "").strip().lower()
    date_from = str(filters.get("date_from") or filters.get("dateFrom") or "").strip()
    date_to = str(filters.get("date_to") or filters.get("dateTo") or "").strip()
    if po and po not in _search_haystack_from_order(order):
        return False
    if customer and customer not in str(order.get("customer") or "").lower():
        return False
    key = _date_key(order.get("order_date"))
    if date_from and (not key or key < date_from):
        return False
    if date_to and (not key or key > date_to):
        return False
    return True


def matches_status_po_filters(row: dict, filters: Optional[dict] = None) -> bool:
    """AND filters on Status PO rows. Empty filter = match. Does not change status calc.

    Search `po` matches No PO, pelanggan, or produk (item_lines) — same semantics as FE.
    """
    if not filters:
        return True
    po = str(filters.get("po") or "").strip().lower()
    customer = str(filters.get("customer") or "").strip().lower()
    status = str(filters.get("status") or "").strip()
    date_from = str(filters.get("date_from") or filters.get("dateFrom") or "").strip()
    date_to = str(filters.get("date_to") or filters.get("dateTo") or "").strip()
    if po and po not in _search_haystack_from_row(row):
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


def order_sort_key(order: dict) -> tuple:
    return (
        str(order.get("order_date") or ""),
        str(order.get("po_number") or ""),
        str(order.get("id") or ""),
    )


def select_status_po_eligible(
    orders: Iterable[dict],
    view: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    filters: Optional[dict] = None,
) -> Dict[str, Any]:
    """Stream-friendly eligibility: view + search/date + status label.

    Does not build status_po_row. Returns sorted id list, total, and global
    status_options for the tab/search/date/customer scope (before status filter).
    """
    status_filter = str((filters or {}).get("status") or "").strip()
    scoped = filter_orders_for_view(orders, view, now=now)
    status_options: set = set()
    eligible: List[tuple] = []
    docs_scanned = 0
    for order in scoped:
        docs_scanned += 1
        if not order_matches_search_date_filters(order, filters):
            continue
        label = effective_workflow_status(order)
        if label:
            status_options.add(label)
        if status_filter and label != status_filter:
            continue
        eligible.append(order_sort_key(order))
    eligible.sort()
    return {
        "eligible_keys": eligible,
        "ids": [key[2] for key in eligible if key[2]],
        "total_count": len(eligible),
        "status_options": sorted(status_options, key=lambda s: s.lower()),
        "docs_classified": docs_scanned,
    }


def slice_eligible_page(eligible_keys: List[tuple], page: Any = 1, page_size: Any = STATUS_PO_DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    total = len(eligible_keys)
    page_n, size = _normalize_page(page, page_size)
    total_pages = (total + size - 1) // size if total else 0
    if total_pages and page_n > total_pages:
        page_n = total_pages
    start = (page_n - 1) * size if total else 0
    page_keys = eligible_keys[start : start + size]
    return {
        "page": page_n,
        "page_size": size,
        "total_count": total,
        "total_pages": total_pages,
        "has_next": bool(total_pages and page_n < total_pages),
        "has_prev": bool(total and page_n > 1),
        "page_ids": [key[2] for key in page_keys if key[2]],
        "page_keys": page_keys,
    }


def _normalize_page(page: Any, page_size: Any) -> tuple:
    try:
        size = int(page_size if page_size is not None else STATUS_PO_DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        size = STATUS_PO_DEFAULT_PAGE_SIZE
    size = max(1, min(size, STATUS_PO_MAX_PAGE_SIZE))
    try:
        pg = int(page if page is not None else 1)
    except (TypeError, ValueError):
        pg = 1
    pg = max(1, pg)
    return pg, size


def _confirmation_is_valid_delivery(order: dict, rec: dict) -> bool:
    """Konfirmasi Terkirim on an active SJ. Cancelled/superseded SJ is ignored."""
    qty = rec.get("qty")
    if qty is not None and str(qty).strip() != "":
        try:
            if int(qty) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    sid = str(rec.get("shipment_id") or "").strip()
    if not sid:
        return True
    shipment = find_shipment(order, sid)
    return bool(shipment) and shipment_is_active(shipment)


def _consider_delivery_instant(best: Optional[tuple], raw: Any) -> Optional[tuple]:
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return best
    dt = _parse_dt(text)
    if dt is None:
        return best
    if best is None or dt > best[0]:
        return (dt, text)
    return best


def _latest_valid_delivery_at(order: dict) -> Optional[str]:
    """Latest successful delivery instant from an active shipment.

    Uses confirmation confirmed_at and active shipment delivered_at.
    orders.delivered_at is not a completion proof: partial delivery writes it,
    and a later stage advance leaves that stamp in place.
    """
    best: Optional[tuple] = None

    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        if not _confirmation_is_valid_delivery(order, rec):
            continue
        best = _consider_delivery_instant(best, rec.get("confirmed_at"))

    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        marked = bool(sh.get("delivered_at")) or str(sh.get("delivery_status") or "").strip() == "Terkirim"
        if not marked:
            continue
        if not shipment_is_active(sh):
            continue
        best = _consider_delivery_instant(best, sh.get("delivered_at"))

    if best:
        return best[1]
    return None


def po_age_bounds(order: dict) -> dict:
    """Umur stops only when the whole order is delivered (existing qty rule).

    current_stage alone is not completion. Open and partial orders keep
    age_end_at empty so the client continues through today.
    """
    order = order or {}
    if not po_is_fully_delivered(order):
        return {"age_closed": False, "age_end_at": None}
    return {"age_closed": True, "age_end_at": _latest_valid_delivery_at(order)}


def status_po_row(order: dict) -> dict:
    ship = shipment_status_row(order)
    workflow, reasons = status_po_workflow_detail(order)
    age = po_age_bounds(order)
    return {
        "id": order.get("id"),
        "po_number": order.get("po_number") or ship.get("order_number") or "",
        "order_date": order.get("order_date") or ship.get("order_date"),
        "customer": order.get("customer") or ship.get("customer_name") or "",
        "item_lines": item_line_names(order),
        "status": effective_workflow_status(order),
        "percent_delivered": ship.get("percent_delivered"),
        "percent_delivered_display": ship.get("percent_delivered_display"),
        "qty_delivered": ship.get("qty_delivered"),
        "qty_total": ship.get("qty_total"),
        "current_stage": order.get("current_stage"),
        "cancelled": bool(order.get("cancelled")),
        "hold": bool(order.get("hold")),
        "priority": order.get("priority") or "normal",
        "urgent": str(order.get("priority") or "").lower() == "urgent",
        "buffer_stock": bool(order.get("buffer_stock")),
        "workflow": workflow,
        "workflow_reasons": reasons,
        "age_closed": age["age_closed"],
        "age_end_at": age["age_end_at"],
        "delivery_sla_effective": order.get("delivery_sla_effective"),
        "delivery_deadline_status": order.get("delivery_deadline_status"),
        "delivery_deadline_status_basis": order.get("delivery_deadline_status_basis"),
        "tanggal_pengiriman": order.get("tanggal_pengiriman"),
    }


def build_status_po_page_rows(
    page_orders: Iterable[dict],
    *,
    page_ids: Optional[List[str]] = None,
) -> List[dict]:
    """Build display rows for one page only, preserving page_ids order when given."""
    by_id = {}
    for order in page_orders or []:
        oid = str(order.get("id") or "")
        if oid:
            by_id[oid] = order
    if page_ids:
        ordered = [by_id[i] for i in page_ids if i in by_id]
    else:
        ordered = list(page_orders or [])
    return [status_po_row(o) for o in ordered]


def build_status_po(
    orders: Iterable[dict],
    view: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    filters: Optional[dict] = None,
    page: Any = 1,
    page_size: Any = STATUS_PO_DEFAULT_PAGE_SIZE,
    paginate: bool = True,
) -> dict:
    """Classify → filter → sort → count → paginate; build rows only for the page.

    Classification (po_is_fully_delivered) runs on the scoped set before
    pagination so totals and page membership are globally accurate.
    """
    orders_list = list(orders)
    selected = select_status_po_eligible(orders_list, view, now=now, filters=filters)
    view_key = (view or "").strip().lower() or None
    if view_key == "semua":
        view_key = "all"
    by_id = {str(o.get("id") or ""): o for o in orders_list if o.get("id") is not None}

    if not paginate:
        page_ids = selected["ids"]
        rows = build_status_po_page_rows(
            [by_id[i] for i in page_ids if i in by_id],
            page_ids=page_ids,
        )
        total = selected["total_count"]
        return {
            "view": view_key,
            "rows": rows,
            "total": total,
            "total_count": total,
            "page": 1,
            "page_size": total or STATUS_PO_DEFAULT_PAGE_SIZE,
            "total_pages": 1 if total else 0,
            "has_next": False,
            "has_prev": False,
            "status_options": selected["status_options"],
            "docs_classified": selected["docs_classified"],
            "detail_docs_loaded": len(rows),
        }

    sliced = slice_eligible_page(selected["eligible_keys"], page=page, page_size=page_size)
    page_ids = sliced["page_ids"]
    rows = build_status_po_page_rows(
        [by_id[i] for i in page_ids if i in by_id],
        page_ids=page_ids,
    )
    return {
        "view": view_key,
        "rows": rows,
        "total": sliced["total_count"],
        "total_count": sliced["total_count"],
        "page": sliced["page"],
        "page_size": sliced["page_size"],
        "total_pages": sliced["total_pages"],
        "has_next": sliced["has_next"],
        "has_prev": sliced["has_prev"],
        "status_options": selected["status_options"],
        "docs_classified": selected["docs_classified"],
        "detail_docs_loaded": len(rows),
    }
