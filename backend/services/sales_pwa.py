"""Read-only PWA Sales payloads. Scoped by orders.sales_user_id == session users.id."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote

from fastapi import HTTPException

from services.customer_identity import display_customer_name, extract_stable_customer_id
from services.order_prediction import (
    MIN_ORDERS,
    ORDER_PREDICTION_PROJECTION,
    STATUS_BELUM_WAKTUNYA,
    STATUS_DATA_BELUM_CUKUP,
    STATUS_JATUH_TEMPO,
    STATUS_RISIKO,
    STATUS_TERLAMBAT,
    STATUS_TIDAK_AKTIF,
    STATUS_WAKTU_FOLLOW_UP,
    build_order_prediction,
    build_order_prediction_detail,
    decode_customer_key,
    wib_today,
)
from services.status_po import (
    STATUS_PO_STAGE_KEYS,
    effective_workflow_status,
    item_line_names,
    status_po_workflow,
)
from services.tanggal_pengiriman import (
    DEFAULT_LEAD_TIME_DAYS,
    attach_tanggal_pengiriman_snapshot,
    parse_calendar_date,
)
from services.sales_user_mapping import (
    SALES_PWA_ROLE,
    is_sales_pwa_role,
    sales_owner_match,
    session_user_id,
)
from services.sales_activity.constants import (
    COL_ACTIVITIES,
    COL_OUTLETS,
    COL_OWNERSHIP,
)
from services.sales_invoices import FINANCIAL_FINALIZED

WIB = timezone(timedelta(hours=7))

FILTER_ALL = "semua"
FILTER_RUNNING = "berjalan"
FILTER_HOLD = "hold"
FILTER_PARTIAL = "parsial"
FILTER_DONE = "selesai"

_WS_RE = re.compile(r"\s+")

LIST_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "outlet": 1,
    "sales": 1,
    "sales_user_id": 1,
    "order_date": 1,
    "created_at": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "qty_delivered": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "total_quantity": 1,
    "quantity": 1,
    "packing_required_total": 1,
    "line_items": 1,
    "requires_printing": 1,
    "operator_splits": 1,
    "shipments": 1,
    "cancelled": 1,
    "hold": 1,
    "priority": 1,
    "buffer_stock": 1,
    "status_label": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "design_files": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "payment_files": 1,
    "customer_approval": 1,
    "screener_info": 1,
    "helper_prep": 1,
    "sample_files": 1,
    "sample_approved": 1,
    "packing_status": 1,
    "packing_info": 1,
    "surat_jalan_created": 1,
    "surat_jalan_status_semantic": 1,
    "delivery_note_number": 1,
    "delivery_note_created_at": 1,
    "loading_completed": 1,
    "loading_info": 1,
    "terkirim_confirmed": 1,
    "print_status": 1,
    "print_completed_qty": 1,
    "source_pdf": 1,
    "delivery_proofs": 1,
    "surat_jalan_files": 1,
    "timeline": 1,
    "address": 1,
    "recipient": 1,
    "notes": 1,
    "estimated_delivery": 1,
}

DETAIL_STRIP_KEYS = (
    "design_files",
    "payment_files",
    "sample_files",
    "surat_jalan_files",
    "delivery_proofs",
    "operator_splits",
    "loading_info",
    "packing_info",
    "screener_info",
    "helper_prep",
)


def _norm(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "").strip().lower())


def require_sales_pwa(user: dict) -> str:
    if not is_sales_pwa_role(user):
        raise HTTPException(403, "PWA Sales hanya untuk role sales_eternal")
    uid = session_user_id(user)
    if not uid:
        raise HTTPException(401, "Sesi tidak memiliki user ID")
    return uid


def owns_order(order: Optional[dict], user: dict) -> bool:
    if not order:
        return False
    return str(order.get("sales_user_id") or "").strip() == session_user_id(user)


async def require_owned_order(db, user: dict, order_id: str) -> dict:
    require_sales_pwa(user)
    oid = str(order_id or "").strip()
    if not oid:
        raise HTTPException(404, "PO tidak ditemukan")
    order = await db.orders.find_one({"id": oid, **sales_owner_match(user)}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    return order


async def _lead_time_days(db) -> int:
    doc = await db.app_settings.find_one({"key": "delivery_deadline"}, {"_id": 0, "lead_time_days": 1})
    try:
        return int((doc or {}).get("lead_time_days") or DEFAULT_LEAD_TIME_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_LEAD_TIME_DAYS


def _workflow_progress(workflow: dict) -> int:
    done = 0
    for key in STATUS_PO_STAGE_KEYS:
        state = workflow.get(key)
        if state in ("completed", "skipped", "na", "not_applicable"):
            done += 1
    if not STATUS_PO_STAGE_KEYS:
        return 0
    return int(round(100 * done / len(STATUS_PO_STAGE_KEYS)))


def _current_stage_label(order: dict, workflow: dict) -> str:
    status = effective_workflow_status(order)
    if str(status or "").strip().upper() == "HOLD" or order.get("hold"):
        return "HOLD"
    return status or order.get("status_label") or "—"


def _active_stage_key(workflow: dict) -> Optional[str]:
    for key in STATUS_PO_STAGE_KEYS:
        if workflow.get(key) in ("active", "hold"):
            return key
    return None


def _search_blob(order: dict) -> str:
    names = item_line_names(order)
    return _norm(" ".join([
        str(order.get("po_number") or ""),
        display_customer_name(order),
        str(order.get("outlet") or ""),
        " ".join(names),
    ]))


def _matches_filter(order: dict, status: str, workflow: dict) -> bool:
    key = (status or FILTER_ALL).strip().lower()
    if key in ("", FILTER_ALL, "all", "semua"):
        return True
    hold = bool(order.get("hold")) or effective_workflow_status(order) == "HOLD"
    label = effective_workflow_status(order)
    if key in (FILTER_HOLD, "perlu_perhatian", "perlu-perhatian"):
        return hold
    if key in (FILTER_PARTIAL, "terkirim_sebagian", "parsial_terkirim"):
        return label in ("Parsial Terkirim", "SJ parsial")
    if key in (FILTER_DONE, "selesai", "terkirim"):
        return label in ("Terkirim",) or _workflow_progress(workflow) >= 100
    if key in (FILTER_RUNNING, "berjalan", "aktif"):
        if hold or order.get("cancelled"):
            return False
        if label in ("Terkirim",):
            return False
        return _workflow_progress(workflow) < 100
    return True


def _date_in_range(day: Optional[str], date_from: str, date_to: str) -> bool:
    if date_from and (not day or day < date_from):
        return False
    if date_to and (not day or day > date_to):
        return False
    return True


async def _cursor_rows(cursor) -> List[dict]:
    """Materialize one already-scoped query without a silent row cap."""
    rows: List[dict] = []
    async for row in cursor:
        rows.append(row)
    return rows


def _strip_file_bytes(files: Any) -> List[dict]:
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        out.append({
            "id": f.get("id"),
            "name": f.get("name") or f.get("original_filename"),
            "storage_path": f.get("storage_path"),
            "content_type": f.get("content_type"),
            "shipment_id": f.get("shipment_id"),
        })
    return out


def _proof_public(proof: dict) -> dict:
    return {
        "id": proof.get("id"),
        "shipment_id": proof.get("shipment_id") or proof.get("sales_delivery_id"),
        "note": proof.get("note"),
        "uploaded_at": proof.get("uploaded_at") or proof.get("created_at"),
        "storage_path": proof.get("storage_path"),
        "content_type": proof.get("content_type"),
    }


def _document_counts(order: dict, deliveries: List[dict], invoices: List[dict]) -> dict:
    po_ok = bool((order.get("source_pdf") or {}).get("storage_path"))
    sj_n = len([d for d in deliveries if not d.get("is_cancelled")])
    inv_n = len([i for i in invoices if not i.get("is_cancelled")])
    proofs = [p for p in (order.get("delivery_proofs") or []) if isinstance(p, dict)]
    proof_n = len(proofs)
    total = (1 if po_ok else 0) + sj_n + inv_n + proof_n
    return {
        "po": 1 if po_ok else 0,
        "sj": sj_n,
        "invoice": inv_n,
        "bukti_kirim": proof_n,
        "total": total,
    }


async def _load_docs_for_pos(db, po_ids: List[str]) -> tuple:
    if not po_ids:
        return {}, {}
    deliveries: Dict[str, List[dict]] = {pid: [] for pid in po_ids}
    invoices: Dict[str, List[dict]] = {pid: [] for pid in po_ids}
    async for d in db.sales_deliveries.find(
        {"po_id": {"$in": po_ids}, "is_cancelled": {"$ne": True}},
        {
            "_id": 0,
            "id": 1,
            "po_id": 1,
            "sj_number": 1,
            "shipment_index": 1,
            "delivery_date": 1,
            "status": 1,
            "total_qty": 1,
            "item_count": 1,
            "is_cancelled": 1,
        },
    ):
        deliveries.setdefault(d.get("po_id"), []).append(d)
    async for inv in db.sales_invoices.find(
        {"po_id": {"$in": po_ids}, "is_cancelled": {"$ne": True}},
        {
            "_id": 0,
            "id": 1,
            "po_id": 1,
            "invoice_number": 1,
            "status": 1,
            "invoice_date": 1,
            "grand_total": 1,
            "is_cancelled": 1,
        },
    ):
        invoices.setdefault(inv.get("po_id"), []).append(inv)
    return deliveries, invoices


def _sort_value(row: dict, key: str) -> Any:
    if key in ("order_date", "tanggal_po"):
        return row.get("order_date") or ""
    if key == "customer":
        return _norm(row.get("customer"))
    if key == "progress":
        return row.get("progress_percent") or 0
    if key == "po":
        return str(row.get("po_number") or "")
    return str(row.get("updated_at") or row.get("order_date") or "")


def serialize_list_row(order: dict, deliveries: List[dict], invoices: List[dict]) -> dict:
    attach_tanggal_pengiriman_snapshot(order)
    workflow = status_po_workflow(order)
    counts = _document_counts(order, deliveries, invoices)
    kirim = order.get("tanggal_pengiriman")
    today = wib_today().isoformat()
    overdue = bool(kirim and kirim < today and effective_workflow_status(order) not in ("Terkirim",))
    return {
        "id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer": display_customer_name(order),
        "customer_id": extract_stable_customer_id(order),
        "outlet": str(order.get("outlet") or "").strip() or None,
        "sales": order.get("sales"),
        "order_date": parse_calendar_date(order.get("order_date")) or str(order.get("order_date") or "")[:10] or None,
        "buffer_stock": bool(order.get("buffer_stock")),
        "tanggal_kirim": kirim,
        "tanggal_kirim_source": order.get("tanggal_pengiriman_source"),
        "tanggal_kirim_overdue": overdue,
        "status": _current_stage_label(order, workflow),
        "hold": bool(order.get("hold")),
        "cancelled": bool(order.get("cancelled")),
        "workflow": workflow,
        "active_stage": _active_stage_key(workflow),
        "progress_percent": _workflow_progress(workflow),
        "item_names": item_line_names(order),
        "documents": counts,
        "partial_delivery": effective_workflow_status(order) in ("Parsial Terkirim", "SJ parsial"),
    }


async def list_sales_pwa_orders(
    db,
    user: dict,
    *,
    q: str = "",
    customer_q: str = "",
    po_q: str = "",
    stage: str = "",
    buffer: str = "",
    progress_min: Optional[int] = None,
    progress_max: Optional[int] = None,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "order_date",
    direction: str = "desc",
    skip: int = 0,
    limit: int = 50,
) -> dict:
    require_sales_pwa(user)
    match = {"cancelled": {"$ne": True}, **sales_owner_match(user)}
    raw = await _cursor_rows(db.orders.find(match, LIST_PROJECTION))
    lead = await _lead_time_days(db)
    for o in raw:
        attach_tanggal_pengiriman_snapshot(o, lead)

    needle = _norm(q)
    customer_needle = _norm(customer_q)
    po_needle = _norm(po_q)
    stage_needle = _norm(stage)
    buffer_key = _norm(buffer)
    date_from = parse_calendar_date(date_from) or ""
    date_to = parse_calendar_date(date_to) or ""
    filtered = []
    for o in raw:
        wf = status_po_workflow(o)
        if needle and needle not in _search_blob(o):
            continue
        if customer_needle and customer_needle not in _norm(" ".join([
            display_customer_name(o),
            str(o.get("outlet") or ""),
        ])):
            continue
        if po_needle and po_needle not in _norm(o.get("po_number")):
            continue
        if not _matches_filter(o, status, wf):
            continue
        stage_label = _current_stage_label(o, wf)
        if stage_needle and stage_needle not in _norm(stage_label):
            continue
        if buffer_key in ("ya", "true", "1", "buffer") and not o.get("buffer_stock"):
            continue
        if buffer_key in ("tidak", "false", "0", "non-buffer") and o.get("buffer_stock"):
            continue
        order_day = parse_calendar_date(o.get("order_date")) or str(o.get("order_date") or "")[:10]
        if not _date_in_range(order_day, date_from, date_to):
            continue
        progress = _workflow_progress(wf)
        if progress_min is not None and progress < max(0, min(int(progress_min), 100)):
            continue
        if progress_max is not None and progress > max(0, min(int(progress_max), 100)):
            continue
        filtered.append(o)

    po_ids = [o.get("id") for o in filtered if o.get("id")]
    deliveries_map, invoices_map = await _load_docs_for_pos(db, po_ids)
    rows = [
        serialize_list_row(o, deliveries_map.get(o.get("id"), []), invoices_map.get(o.get("id"), []))
        for o in filtered
    ]
    reverse = str(direction or "asc").lower() == "desc"
    sort_key = (sort or "order_date").strip()
    rows.sort(key=lambda r: (_sort_value(r, sort_key), r.get("po_number") or ""), reverse=reverse)

    skip = max(0, int(skip or 0))
    limit = max(1, min(int(limit or 50), 200))
    page = rows[skip:skip + limit]

    counts = {
        "semua": 0,
        "berjalan": 0,
        "hold": 0,
        "parsial": 0,
        "selesai": 0,
    }
    for o in raw:
        wf = status_po_workflow(o)
        counts["semua"] += 1
        if _matches_filter(o, FILTER_RUNNING, wf):
            counts["berjalan"] += 1
        if _matches_filter(o, FILTER_HOLD, wf):
            counts["hold"] += 1
        if _matches_filter(o, FILTER_PARTIAL, wf):
            counts["parsial"] += 1
        if _matches_filter(o, FILTER_DONE, wf):
            counts["selesai"] += 1

    return {
        "orders": page,
        "total": len(rows),
        "skip": skip,
        "limit": limit,
        "counts": counts,
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "min_orders_prediction": MIN_ORDERS,
        "source_count": len(raw),
        "source_truncated": False,
    }


def _month_key(value: str = "") -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        try:
            date.fromisoformat(f"{raw}-01")
            return raw
        except ValueError:
            pass
    return wib_today().strftime("%Y-%m")


def _next_month_key(month: str) -> str:
    year, value = [int(part) for part in month.split("-")]
    if value == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{value + 1:02d}"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def _sales_financial_summary(db, user: dict, month: str) -> dict:
    """Invoice-authoritative metrics for POs owned by the authenticated sales user.

    Revenue uses finalized invoice_date in the requested month. Receivables use
    finalized invoices minus live sales_receipts. Draft/cancelled invoices never
    contribute.
    """
    owned = await _cursor_rows(db.orders.find(
        {"cancelled": {"$ne": True}, **sales_owner_match(user)},
        {"_id": 0, "id": 1},
    ))
    po_ids = [str(row.get("id")) for row in owned if row.get("id")]
    if not po_ids:
        return {
            "month": month,
            "revenue": 0.0,
            "receivables": 0.0,
            "paid": 0.0,
            "invoice_count": 0,
            "receivable_invoice_count": 0,
        }
    invoices = await _cursor_rows(db.sales_invoices.find(
        {
            "po_id": {"$in": po_ids},
            "financial_status": FINANCIAL_FINALIZED,
            "is_cancelled": {"$ne": True},
        },
        {
            "_id": 0,
            "id": 1,
            "po_id": 1,
            "invoice_date": 1,
            "grand_total": 1,
        },
    ))
    inv_ids = [str(inv.get("id")) for inv in invoices if inv.get("id")]
    receipts = []
    if inv_ids:
        receipts = await _cursor_rows(db.sales_receipts.find(
            {"invoice_id": {"$in": inv_ids}},
            {"_id": 0, "invoice_id": 1, "amount": 1},
        ))
    paid_by_invoice: Dict[str, float] = {}
    for receipt in receipts:
        iid = str(receipt.get("invoice_id") or "")
        paid_by_invoice[iid] = paid_by_invoice.get(iid, 0.0) + _number(receipt.get("amount"))
    revenue = 0.0
    receivables = 0.0
    paid = 0.0
    receivable_count = 0
    month_count = 0
    for inv in invoices:
        grand = _number(inv.get("grand_total"))
        invoice_day = parse_calendar_date(inv.get("invoice_date")) or str(inv.get("invoice_date") or "")[:10]
        if invoice_day.startswith(f"{month}-"):
            revenue += grand
            month_count += 1
        inv_paid = min(max(paid_by_invoice.get(str(inv.get("id") or ""), 0.0), 0.0), grand)
        paid += inv_paid
        outstanding = max(grand - inv_paid, 0.0)
        receivables += outstanding
        if outstanding > 0:
            receivable_count += 1
    return {
        "month": month,
        "revenue": round(revenue, 2),
        "receivables": round(receivables, 2),
        "paid": round(paid, 2),
        "invoice_count": month_count,
        "receivable_invoice_count": receivable_count,
    }


async def _owned_visit_context(db, user: dict) -> tuple:
    uid = session_user_id(user)
    ownerships = await _cursor_rows(db[COL_OWNERSHIP].find(
        {"current_owner_id": uid, "in_pool": {"$ne": True}},
        {
            "_id": 0,
            "outlet_id": 1,
            "current_owner_id": 1,
            "customer_kind": 1,
            "automation_status": 1,
            "last_activity_at": 1,
            "next_follow_up_at": 1,
            "eligible_at": 1,
            "warning_at": 1,
        },
    ))
    outlet_ids = [str(row.get("outlet_id")) for row in ownerships if row.get("outlet_id")]
    outlets = []
    if outlet_ids:
        outlets = await _cursor_rows(db[COL_OUTLETS].find(
            {"id": {"$in": outlet_ids}},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "address": 1,
                "pic_name": 1,
                "whatsapp": 1,
                "maps_url": 1,
                "customer_kind": 1,
            },
        ))
    return ownerships, outlets


async def list_sales_pwa_visits(db, user: dict, *, month: str = "", q: str = "") -> dict:
    require_sales_pwa(user)
    month = _month_key(month)
    from services.sales_activity.settings import activity_type_map, load_settings
    settings = await load_settings(db)
    visit_cfg = activity_type_map(settings).get("visit") or {}
    uid = session_user_id(user)
    ownerships, outlets = await _owned_visit_context(db, user)
    own_by_outlet = {str(row.get("outlet_id")): row for row in ownerships}
    outlet_by_id = {str(row.get("id")): row for row in outlets}
    needle = _norm(q)
    activities = await db[COL_ACTIVITIES].find(
        {"sales_user_id": uid, "activity_type": "visit"},
        {
            "_id": 0,
            "id": 1,
            "outlet_id": 1,
            "activity_at": 1,
            "result": 1,
            "next_action": 1,
            "next_follow_up_at": 1,
            "notes": 1,
            "visit_photo_count": 1,
            "has_visit_location": 1,
        },
    ).sort("activity_at", -1).to_list(1000)
    visit_month_count = await db[COL_ACTIVITIES].count_documents({
        "sales_user_id": uid,
        "activity_type": "visit",
        "activity_at": {
            "$gte": f"{month}-01T00:00:00",
            "$lt": f"{_next_month_key(month)}-01T00:00:00",
        },
    })
    reminders = []
    today = wib_today().isoformat()
    for outlet_id, ownership in own_by_outlet.items():
        outlet = outlet_by_id.get(outlet_id) or {}
        follow = str(ownership.get("next_follow_up_at") or "")[:10] or None
        if follow and follow.startswith(f"{month}-"):
            reminders.append({
                "id": f"followup:{outlet_id}:{follow}",
                "outlet_id": outlet_id,
                "outlet": outlet.get("name") or "Outlet",
                "date": follow,
                "at": ownership.get("next_follow_up_at"),
                "kind": "follow_up",
                "overdue": follow < today,
            })
    rows = []
    for activity in activities:
        outlet = outlet_by_id.get(str(activity.get("outlet_id"))) or {}
        row = {
            **activity,
            "outlet": outlet.get("name") or "Outlet",
            "address": outlet.get("address"),
        }
        if needle and needle not in _norm(" ".join([
            row.get("outlet") or "",
            row.get("result") or "",
            row.get("next_action") or "",
            row.get("notes") or "",
        ])):
            continue
        rows.append(row)
    outlet_rows = []
    for outlet_id, ownership in own_by_outlet.items():
        outlet = outlet_by_id.get(outlet_id) or {}
        row = {
            **outlet,
            "ownership": ownership,
        }
        if needle and needle not in _norm(" ".join([
            outlet.get("name") or "",
            outlet.get("address") or "",
            outlet.get("pic_name") or "",
            outlet.get("whatsapp") or "",
        ])):
            continue
        outlet_rows.append(row)
    outlet_rows.sort(key=lambda row: (
        str((row.get("ownership") or {}).get("next_follow_up_at") or "9999"),
        _norm(row.get("name")),
    ))
    reminders.sort(key=lambda row: (row.get("date") or "", _norm(row.get("outlet"))))
    return {
        "month": month,
        "outlets": outlet_rows,
        "visits": rows,
        "reminders": reminders,
        "counts": {
            "owned_outlets": len(ownerships),
            "visit_month": visit_month_count,
            "reminder_month": len(reminders),
            "overdue": sum(1 for row in reminders if row.get("overdue")),
        },
        "visit_config": {
            "photo_required": bool(visit_cfg.get("visit_photo_required") or visit_cfg.get("evidence_required")),
            "next_follow_up_required": bool(visit_cfg.get("next_follow_up_required")),
        },
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }


async def sales_pwa_dashboard(db, user: dict, *, month: str = "") -> dict:
    require_sales_pwa(user)
    month = _month_key(month)
    match = {"cancelled": {"$ne": True}, **sales_owner_match(user)}
    orders = await _cursor_rows(db.orders.find(
        match,
        {"_id": 0, "id": 1, "current_stage": 1, "terkirim_confirmed": 1, "buffer_stock": 1},
    ))
    visits = await list_sales_pwa_visits(db, user, month=month)
    financial = await _sales_financial_summary(db, user, month)
    active = sum(1 for row in orders if not row.get("terkirim_confirmed") and int(row.get("current_stage") or 0) < 14)
    return {
        "month": month,
        "metrics": {
            "revenue": financial["revenue"],
            "receivables": financial["receivables"],
            "paid": financial["paid"],
            "invoice_count": financial["invoice_count"],
            "receivable_invoice_count": financial["receivable_invoice_count"],
            "active_po": active,
            "buffer_po": sum(1 for row in orders if row.get("buffer_stock")),
            "visit_month": visits["counts"]["visit_month"],
            "visit_reminders": visits["counts"]["reminder_month"],
            "visit_overdue": visits["counts"]["overdue"],
        },
        "upcoming_visits": visits["reminders"][:8],
        "sources": {
            "revenue": "sales_invoices.finalized / invoice_date",
            "receivables": "sales_invoices.finalized - sales_receipts",
            "orders": "orders.sales_user_id",
            "visits": "sales_ownership.current_owner_id + sales_activities.sales_user_id",
        },
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }


async def create_sales_pwa_visit(db, user: dict, payload: dict) -> dict:
    """Record a visit only for an outlet currently owned by this session user."""
    require_sales_pwa(user)
    from services.sales_activity import activities as activity_svc
    from services.sales_activity.constants import PERM_ACTIVITY_CREATE

    clean = {
        "outlet_id": str(payload.get("outlet_id") or "").strip(),
        "activity_type": "visit",
        "result": str(payload.get("result") or "").strip(),
        "next_action": str(payload.get("next_action") or "").strip(),
        "next_follow_up_at": payload.get("next_follow_up_at"),
        "notes": str(payload.get("notes") or "").strip(),
        "visit_photo_ids": [
            str(value).strip()
            for value in (payload.get("visit_photo_ids") or [])
            if str(value or "").strip()
        ],
    }
    # create_native_activity validates current_owner_id, in_pool, activity
    # settings, required photo/location, and always writes session users.id.
    return await activity_svc.create_native_activity(
        db,
        clean,
        user,
        {PERM_ACTIVITY_CREATE},
    )


def _line_public(li: dict, idx: int) -> dict:
    return {
        "idx": idx,
        "name": li.get("name") or li.get("item_name") or li.get("product_name") or "",
        "quantity": li.get("quantity"),
        "unit": li.get("unit"),
        "color": li.get("color"),
        "production_process": li.get("production_process"),
    }


async def get_sales_pwa_order(db, user: dict, order_id: str) -> dict:
    order = await require_owned_order(db, user, order_id)
    lead = await _lead_time_days(db)
    attach_tanggal_pengiriman_snapshot(order, lead)
    deliveries_map, invoices_map = await _load_docs_for_pos(db, [order.get("id")])
    deliveries = deliveries_map.get(order.get("id"), [])
    invoices = invoices_map.get(order.get("id"), [])
    row = serialize_list_row(order, deliveries, invoices)
    timeline = []
    for ev in order.get("timeline") or []:
        if not isinstance(ev, dict):
            continue
        timeline.append({
            "stage": ev.get("stage"),
            "stage_label": ev.get("stage_label"),
            "at": ev.get("at"),
            "note": ev.get("note"),
            "by_name": ev.get("by_name"),
            "by_role": ev.get("by_role"),
        })
    lines = [
        _line_public(li, i)
        for i, li in enumerate(order.get("line_items") or [])
        if isinstance(li, dict)
    ]
    return {
        "order": {
            **row,
            "address": order.get("address"),
            "recipient": order.get("recipient"),
            "notes": order.get("notes"),
            "estimated_delivery": order.get("estimated_delivery"),
            "sales": order.get("sales"),
            "line_items": lines,
            "timeline": timeline,
            "partial_delivery": row["partial_delivery"],
        },
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_sales_pwa_documents(db, user: dict, order_id: str) -> dict:
    order = await require_owned_order(db, user, order_id)
    deliveries_map, invoices_map = await _load_docs_for_pos(db, [order.get("id")])
    deliveries = deliveries_map.get(order.get("id"), [])
    invoices = invoices_map.get(order.get("id"), [])
    counts = _document_counts(order, deliveries, invoices)
    po_file = order.get("source_pdf") or {}
    po_available = bool(po_file.get("storage_path"))
    proofs = [_proof_public(p) for p in (order.get("delivery_proofs") or []) if isinstance(p, dict)]
    sj_files = {
        str(f.get("shipment_id") or ""): f
        for f in _strip_file_bytes(order.get("surat_jalan_files"))
        if f.get("shipment_id")
    }
    sj_rows = []
    for d in sorted(deliveries, key=lambda x: (x.get("shipment_index") or 0, x.get("sj_number") or "")):
        sid = d.get("id")
        file_meta = sj_files.get(str(sid or ""))
        sj_rows.append({
            "id": sid,
            "number": d.get("sj_number"),
            "qty": d.get("total_qty"),
            "date": d.get("delivery_date"),
            "status": d.get("status"),
            "available": bool(file_meta and file_meta.get("storage_path")),
        })
    inv_rows = []
    for inv in invoices:
        inv_rows.append({
            "id": inv.get("id"),
            "number": inv.get("invoice_number"),
            "date": inv.get("invoice_date"),
            "status": inv.get("status"),
            "available": True,
        })
    return {
        "po_number": order.get("po_number"),
        "customer": display_customer_name(order),
        "counts": counts,
        "po": {
            "available": po_available,
            "filename": po_file.get("original_filename") or po_file.get("name"),
        } if po_available else {"available": False},
        "sj": sj_rows,
        "invoices": inv_rows,
        "bukti_kirim": proofs,
    }


async def list_sales_pwa_customers(db, user: dict, *, q: str = "") -> dict:
    require_sales_pwa(user)
    match = {"cancelled": {"$ne": True}, **sales_owner_match(user)}
    orders = await _cursor_rows(db.orders.find(match, LIST_PROJECTION))
    lead = await _lead_time_days(db)
    groups: Dict[str, dict] = {}
    for o in orders:
        attach_tanggal_pengiriman_snapshot(o, lead)
        key = extract_stable_customer_id(o) or f"name:{_norm(display_customer_name(o))}"
        if not key:
            continue
        g = groups.setdefault(key, {
            "customer_key": key,
            "customer": display_customer_name(o),
            "outlets": set(),
            "po_count": 0,
            "last_order_date": None,
            "active_status": None,
            "order_ids": [],
        })
        g["po_count"] += 1
        g["order_ids"].append(o.get("id"))
        outlet = str(o.get("outlet") or "").strip()
        if outlet:
            g["outlets"].add(outlet)
        day = parse_calendar_date(o.get("order_date")) or str(o.get("order_date") or "")[:10]
        if day and (not g["last_order_date"] or day > g["last_order_date"]):
            g["last_order_date"] = day
            g["active_status"] = effective_workflow_status(o)
            g["customer"] = display_customer_name(o) or g["customer"]

    pred = await build_sales_pwa_predictions(db, user, orders=orders)
    pred_by_key = {r.get("customer_key"): r for r in pred.get("rows") or []}
    needle = _norm(q)
    rows = []
    for g in groups.values():
        outlets = sorted(g["outlets"])
        pred_row = pred_by_key.get(g["customer_key"]) or pred_by_key.get(f"id:{g['customer_key']}")
        rows.append({
            "customer_key": g["customer_key"],
            "customer": g["customer"],
            "outlets": outlets,
            "outlet": ", ".join(outlets) if outlets else None,
            "po_count": g["po_count"],
            "last_order_date": g["last_order_date"],
            "active_status": g["active_status"],
            "prediction": {
                "prediction_start": (pred_row or {}).get("prediction_start"),
                "prediction_end": (pred_row or {}).get("prediction_end"),
                "followup_status": (pred_row or {}).get("followup_status"),
                "total_orders": (pred_row or {}).get("total_orders"),
            } if pred_row else None,
        })
    if needle:
        kept = []
        po_hits = {
            extract_stable_customer_id(o) or f"name:{_norm(display_customer_name(o))}"
            for o in orders
            if needle in _norm(o.get("po_number") or "")
        }
        for r in rows:
            blob = _norm(" ".join([
                r.get("customer") or "",
                r.get("outlet") or "",
                r.get("customer_key") or "",
            ]))
            if needle in blob or r["customer_key"] in po_hits:
                kept.append(r)
        rows = kept
    rows.sort(key=lambda r: _norm(r.get("customer")))
    return {"customers": rows, "total": len(rows), "loaded_at": datetime.now(timezone.utc).isoformat()}


def _prediction_bucket(row: dict, today: date) -> str:
    status = str(row.get("followup_status") or "")
    if status == STATUS_DATA_BELUM_CUKUP:
        return "belum_cukup"
    if status in (STATUS_WAKTU_FOLLOW_UP, STATUS_JATUH_TEMPO, STATUS_TERLAMBAT, STATUS_RISIKO, STATUS_TIDAK_AKTIF):
        return "follow_up"
    start = parse_calendar_date(row.get("prediction_start") or row.get("prediction_mid"))
    if start:
        try:
            d = date.fromisoformat(start)
        except ValueError:
            d = None
        if d and today <= d <= today + timedelta(days=7):
            return "tujuh_hari"
    if status == STATUS_BELUM_WAKTUNYA:
        return "berikutnya"
    return "berikutnya"


def _evidence_text(row: dict) -> str:
    total = row.get("total_orders") or 0
    if total < MIN_ORDERS:
        return f"Belum cukup data — membutuhkan minimal {MIN_ORDERS} order"
    start = row.get("prediction_start")
    end = row.get("prediction_end")
    med = row.get("median_gap")
    med_txt = f"median {int(med)} hari" if med is not None else "median tidak tersedia"
    if start and end:
        return f"Prediksi {start}–{end} • berdasarkan {total} order terakhir • {med_txt}"
    if start:
        return f"Prediksi {start} • berdasarkan {total} order terakhir • {med_txt}"
    return f"Berdasarkan {total} order terakhir • {med_txt}"


async def build_sales_pwa_predictions(db, user: dict, *, orders: Optional[List[dict]] = None, customer_key: Optional[str] = None) -> dict:
    require_sales_pwa(user)
    if orders is None:
        proj = dict(ORDER_PREDICTION_PROJECTION)
        proj["sales_user_id"] = 1
        orders = await db.orders.find(sales_owner_match(user), proj).to_list(5000)
    else:
        uid = session_user_id(user)
        orders = [o for o in orders if str(o.get("sales_user_id") or "") == uid]
    masters = await db.sales_customers.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
    # Imported history only if it already carries this sales_user_id. Never name-match.
    supplemental = await db.order_prediction_history.find(
        {"cancelled": {"$ne": True}, "sales_user_id": session_user_id(user)},
        {"_id": 0},
    ).to_list(10000)
    if customer_key:
        detail = build_order_prediction_detail(
            orders,
            customer_key,
            supplemental=supplemental,
            masters=masters,
        )
        if not detail:
            raise HTTPException(404, "Prediksi tidak ditemukan")
        analysis = detail.get("analysis") or {}
        analysis["evidence"] = _evidence_text(analysis)
        analysis["bucket"] = _prediction_bucket(analysis, wib_today())
        analysis["min_orders"] = MIN_ORDERS
        return detail
    data = build_order_prediction(
        orders,
        supplemental=supplemental,
        masters=masters,
        sales="",
        skip=0,
        limit=2000,
    )
    today = wib_today()
    buckets = {
        "follow_up": [],
        "tujuh_hari": [],
        "berikutnya": [],
        "belum_cukup": [],
    }
    for row in data.get("rows") or []:
        row["evidence"] = _evidence_text(row)
        row["min_orders"] = MIN_ORDERS
        bucket = _prediction_bucket(row, today)
        row["bucket"] = bucket
        buckets.setdefault(bucket, []).append(row)
    data["buckets"] = buckets
    data["min_orders"] = MIN_ORDERS
    data["loaded_at"] = datetime.now(timezone.utc).isoformat()
    return data


async def profile_summary(db, user: dict) -> dict:
    require_sales_pwa(user)
    match = {"cancelled": {"$ne": True}, **sales_owner_match(user)}
    raw = await db.orders.find(match, {"_id": 0, "id": 1, "hold": 1, "current_stage": 1, "delivery_status": 1, "terkirim_confirmed": 1, "qty_delivered": 1, "total_quantity": 1}).to_list(2000)
    active = 0
    for o in raw:
        if (o.get("current_stage") or 0) >= 14 or o.get("terkirim_confirmed"):
            continue
        active += 1
    branch = None
    db_user = None
    uid = session_user_id(user)
    if uid:
        db_user = await db.users.find_one(
            {"id": uid},
            {"_id": 0, "branch": 1, "branch_id": 1, "cabang": 1, "cabang_id": 1, "name": 1, "role": 1, "email": 1},
        )
    if db_user:
        branch = db_user.get("branch") or db_user.get("cabang") or None
    return {
        "name": user.get("name") or (db_user or {}).get("name"),
        "role": SALES_PWA_ROLE,
        "role_label": "Sales Eternal",
        "branch": branch,
        "active_po_count": active,
        "po_count": len(raw),
    }
