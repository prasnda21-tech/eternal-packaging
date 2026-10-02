"""PWA Sales payloads. Scoped by orders.sales_user_id == session users.id.

Read-only, except one case: an active customer approval on an order that does
not yet have a live-tracking token gets that existing opener persisted.
That does not create or replace a customer-approval token.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote

from fastapi import HTTPException

from services.customer_identity import (
    build_identity_context,
    display_customer_name,
    extract_stable_customer_id,
)
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
    customer_group_key,
    decode_customer_key,
    is_valid_business_order,
    order_date_of,
    wib_today,
)
from services.status_po import (
    STATUS_PO_STAGE_KEYS,
    effective_workflow_status,
    item_line_names,
    status_po_workflow,
    status_po_workflow_detail,
)
from services.tanggal_pengiriman import (
    DEFAULT_LEAD_TIME_DAYS,
    attach_tanggal_pengiriman_snapshot,
    parse_calendar_date,
)
from services.customer_tracking import ensure_tracking_token, public_design_approval
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

# One card per outlet. A section cap is applied only after today/overdue/upcoming split.
AGENDA_SECTION_LIMIT = 30
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
    "customer_code": 1,
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
    "tracking_token": 1,
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
    "delivery_note_created_by": 1,
    "loading_completed": 1,
    "loading_qty_status": 1,
    "loading_info": 1,
    "loading_confirmations": 1,
    "legacy_loading_allocations": 1,
    "delivery_confirmations": 1,
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
    found = None
    for key in STATUS_PO_STAGE_KEYS:
        if workflow.get(key) in ("active", "hold"):
            found = key
    return found


def _customer_code(order: dict) -> str:
    """Stable customer code already stored on the PO. No schema write."""
    explicit = str(order.get("customer_code") or order.get("kode_pelanggan") or "").strip()
    stable = extract_stable_customer_id(order) or ""
    return " ".join(part for part in (explicit, stable) if part)


def _search_blob(order: dict) -> str:
    names = item_line_names(order)
    return _norm(" ".join([
        str(order.get("po_number") or ""),
        display_customer_name(order),
        _customer_code(order),
        str(order.get("outlet") or ""),
        " ".join(names),
    ]))


def _global_keywords(q: str) -> List[str]:
    """Each search box is one keyword. A keyword is not split into customer_q and po_q."""
    needles: List[str] = []
    for line in str(q or "").splitlines():
        needle = _norm(line)
        if needle and needle not in needles:
            needles.append(needle)
    return needles


def _matches_global_keywords(order: dict, q: str) -> bool:
    needles = _global_keywords(q)
    if not needles:
        return True
    blob = _search_blob(order)
    return all(needle in blob for needle in needles)


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


def _matches_customer_po_filter(order: dict, customer_needle: str, po_needle: str) -> bool:
    """Specific filters stay independent. Both set means AND, including when the text is the same."""
    if customer_needle:
        customer_blob = _norm(" ".join([
            display_customer_name(order),
            str(order.get("outlet") or ""),
        ]))
        if customer_needle not in customer_blob:
            return False
    if po_needle and po_needle not in _norm(order.get("po_number")):
        return False
    return True


def _matches_list_constraints(
    order: dict,
    workflow: dict,
    *,
    q: str,
    customer_needle: str,
    po_needle: str,
    stage_needle: str,
    buffer_key: str,
    date_from: str,
    date_to: str,
    progress_min: Optional[int],
    progress_max: Optional[int],
) -> bool:
    if not _matches_global_keywords(order, q):
        return False
    if not _matches_customer_po_filter(order, customer_needle, po_needle):
        return False
    stage_label = _current_stage_label(order, workflow)
    if stage_needle and stage_needle not in _norm(stage_label):
        return False
    if buffer_key in ("ya", "true", "1", "buffer") and not order.get("buffer_stock"):
        return False
    if buffer_key in ("tidak", "false", "0", "non-buffer") and order.get("buffer_stock"):
        return False
    order_day = parse_calendar_date(order.get("order_date")) or str(order.get("order_date") or "")[:10]
    if not _date_in_range(order_day, date_from, date_to):
        return False
    progress = _workflow_progress(workflow)
    if progress_min is not None and progress < max(0, min(int(progress_min), 100)):
        return False
    if progress_max is not None and progress > max(0, min(int(progress_max), 100)):
        return False
    return True


def _empty_status_counts() -> dict:
    return {
        "semua": 0,
        "berjalan": 0,
        "hold": 0,
        "parsial": 0,
        "selesai": 0,
    }


def select_sales_pwa_order_rows(
    raw: List[dict],
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
) -> tuple:
    """Return rows for the selected status, and chip totals for the same other filters.

    Search, stage, date, buffer, and progress narrow the set first. Chip totals
    are the status breakdown of that set, not of every owned PO. The selected
    status then picks the rows. Status membership rules are unchanged.
    """
    customer_needle = _norm(customer_q)
    po_needle = _norm(po_q)
    stage_needle = _norm(stage)
    buffer_key = _norm(buffer)
    date_from = parse_calendar_date(date_from) or ""
    date_to = parse_calendar_date(date_to) or ""
    filtered: List[dict] = []
    counts = _empty_status_counts()
    for order in raw:
        workflow = status_po_workflow(order)
        if not _matches_list_constraints(
            order,
            workflow,
            q=q,
            customer_needle=customer_needle,
            po_needle=po_needle,
            stage_needle=stage_needle,
            buffer_key=buffer_key,
            date_from=date_from,
            date_to=date_to,
            progress_min=progress_min,
            progress_max=progress_max,
        ):
            continue
        counts["semua"] += 1
        if _matches_filter(order, FILTER_RUNNING, workflow):
            counts["berjalan"] += 1
        if _matches_filter(order, FILTER_HOLD, workflow):
            counts["hold"] += 1
        if _matches_filter(order, FILTER_PARTIAL, workflow):
            counts["parsial"] += 1
        if _matches_filter(order, FILTER_DONE, workflow):
            counts["selesai"] += 1
        if _matches_filter(order, status, workflow):
            filtered.append(order)
    return filtered, counts


async def _cursor_rows(cursor) -> List[dict]:
    """Materialize one already-scoped query without a silent row cap."""
    rows: List[dict] = []
    async for row in cursor:
        rows.append(row)
    return rows


def _strip_file_bytes(files: Any) -> List[dict]:
    """Internal file index. Storage paths stay here and must not be copied to API rows."""
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        out.append({
            "id": f.get("id"),
            "name": f.get("name") or f.get("original_filename"),
            "storage_path": str(f.get("storage_path") or "").strip(),
            "signed_storage_path": str(f.get("signed_storage_path") or "").strip(),
            "signed_at": str(f.get("signed_at") or "").strip(),
            "signed_recipient_name": str(f.get("signed_recipient_name") or "").strip(),
            "content_type": f.get("content_type"),
            "shipment_id": f.get("shipment_id"),
        })
    return out


def sj_file_for_shipment(files: Any, shipment_id: str) -> Optional[dict]:
    """Match the existing PWA identity: surat_jalan_files.shipment_id == sales_deliveries.id."""
    sid = str(shipment_id or "").strip()
    if not sid:
        return None
    for entry in files or []:
        if isinstance(entry, dict) and str(entry.get("shipment_id") or "").strip() == sid:
            return entry
    return None


def sj_byte_source(entry: Optional[dict]) -> tuple:
    """Exclusive byte source. A recorded signed rendition is never replaced by the original.

    Returns (kind, path) where kind is "signed", "original", or "none".
    The path is internal and must not be copied into an API response.
    """
    if not isinstance(entry, dict):
        return "none", ""
    signed = str(entry.get("signed_storage_path") or "").strip()
    if signed:
        return "signed", signed
    original = str(entry.get("storage_path") or "").strip()
    if original:
        return "original", original
    return "none", ""


def _sj_document_row(delivery: dict, file_meta: Optional[dict]) -> dict:
    meta = file_meta or {}
    signed_path = str(meta.get("signed_storage_path") or "").strip()
    storage_path = str(meta.get("storage_path") or "").strip()
    row = {
        "id": delivery.get("id"),
        "number": delivery.get("sj_number"),
        "qty": delivery.get("total_qty"),
        "date": delivery.get("delivery_date"),
        "status": delivery.get("status"),
        "available": bool(signed_path or storage_path),
        "has_signed": bool(signed_path),
    }
    if signed_path:
        signed_at = str(meta.get("signed_at") or "").strip()
        recipient_name = str(meta.get("signed_recipient_name") or "").strip()
        if signed_at:
            row["signed_at"] = signed_at
        if recipient_name:
            row["recipient_name"] = recipient_name
    return row


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


# Same labels as the internal Customer Approval panel. Status itself comes
# from the approval record, with the same expiry derivation as the public link.
_APPROVAL_STATUS_LABEL = {
    "active": "Aktif · Menunggu customer",
    "approved": "Disetujui oleh customer",
    "rejected": "Ditolak oleh customer",
    "expired": "Kedaluwarsa",
    "revoked": "Dicabut",
    "invalidated": "Design diperbarui (tidak berlaku)",
}


def effective_customer_approval_status(rec: Optional[dict], now: Optional[datetime] = None) -> str:
    """Match customer_approval._derive_status. Does not write the record."""
    if not isinstance(rec, dict):
        return ""
    status = str(rec.get("status") or "active").strip() or "active"
    if status in ("approved", "rejected", "revoked", "invalidated"):
        return status
    try:
        expires = datetime.fromisoformat(str(rec.get("expires_at") or ""))
    except Exception:
        return status
    current = now or datetime.now(timezone.utc)
    if current >= expires:
        return "expired"
    return status


def _approval_record_id(rec: dict) -> str:
    return str(rec.get("approval_id") or rec.get("id") or "").strip()


def _bound_design_name(order: dict, version: Any) -> str:
    """Name of the design file the approval record is bound to. No other file."""
    if not version:
        return ""
    for file in order.get("design_files") or []:
        if isinstance(file, dict) and file.get("id") == version:
            return str(file.get("name") or file.get("original_filename") or "").strip()
    return ""


def _approval_item_labels(order: dict) -> List[dict]:
    """Line identity kept distinct. Same item name with a different color stays."""
    labels: List[dict] = []
    for idx, line in enumerate(order.get("line_items") or []):
        if not isinstance(line, dict):
            continue
        name = str(line.get("name") or line.get("item_name") or line.get("product_name") or "").strip()
        color = str(line.get("color") or "").strip()
        if not name and not color:
            continue
        label = name or "Item"
        if color:
            label = f"{label} · {color}"
        labels.append({"idx": idx, "label": label})
    return labels


def _latest_approval_per_design(records: List[dict]) -> List[dict]:
    chosen: Dict[str, dict] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        version = str(rec.get("design_version") or "")
        previous = chosen.get(version)
        if previous is None or str(rec.get("generated_at") or "") >= str(previous.get("generated_at") or ""):
            chosen[version] = rec
    return sorted(chosen.values(), key=lambda rec: str(rec.get("generated_at") or ""), reverse=True)


def _approval_snapshot(order: dict) -> Optional[dict]:
    snap = order.get("customer_approval")
    return snap if isinstance(snap, dict) else None


def approval_needs_tracking_opener(order: dict) -> bool:
    snap = _approval_snapshot(order)
    if not snap or not _approval_record_id(snap):
        return False
    return effective_customer_approval_status(snap) == "active"


def design_approval_rows(order: dict, records: Optional[List[dict]] = None) -> List[dict]:
    """Approval rows for one owned order.

    The open path is the live-tracking opener from public_design_approval.
    It is attached only to the snapshot approval that opener actually serves,
    and only while that approval is still active. Other design versions keep
    their own name and status and do not reuse that path.
    """
    snap = _approval_snapshot(order)
    source = [rec for rec in (records or []) if isinstance(rec, dict)]
    snap_id = _approval_record_id(snap or {})
    if snap and snap_id and snap_id not in {_approval_record_id(rec) for rec in source}:
        source.append(snap)
    elif not source and snap:
        source.append(snap)
    latest = _latest_approval_per_design(source)
    token = str(order.get("tracking_token") or "").strip()
    official_path = ""
    if snap and token and effective_customer_approval_status(snap) == "active":
        official = public_design_approval(order, token) or {}
        official_path = str(official.get("path") or "")
    rows: List[dict] = []
    for rec in latest:
        status = effective_customer_approval_status(rec)
        label = _APPROVAL_STATUS_LABEL.get(status)
        if not label:
            continue
        version = rec.get("design_version")
        if not version and snap and _approval_record_id(rec) == snap_id:
            version = snap.get("design_version")
        row: Dict[str, Any] = {
            "id": _approval_record_id(rec) or str(version or "approval"),
            "status": status,
            "status_label": label,
        }
        design_name = _bound_design_name(order, version)
        if design_name:
            row["design_name"] = design_name
        if official_path and status == "active" and snap_id and _approval_record_id(rec) == snap_id:
            row["path"] = official_path
        rows.append(row)
    if len(rows) == 1:
        items = _approval_item_labels(order)
        if items:
            rows[0]["items"] = items
    return rows


async def _load_approval_records(db, order_ids: List[str]) -> Dict[str, List[dict]]:
    ids = [str(oid) for oid in order_ids if oid]
    if not ids:
        return {}
    try:
        found = await _cursor_rows(db.customer_approvals.find(
            {"order_id": {"$in": ids}},
            {"_id": 0, "token_hash": 0, "signature_storage_path": 0},
        ))
    except Exception:
        return {}
    grouped: Dict[str, List[dict]] = {}
    for rec in found:
        oid = str(rec.get("order_id") or "")
        if oid:
            grouped.setdefault(oid, []).append(rec)
    return grouped


async def attach_page_design_approvals(db, page: List[dict], raw_by_id: Dict[str, dict]) -> None:
    """Fill design_approvals on an already scoped page. Does not mint an approval token."""
    orders = [raw_by_id[row["id"]] for row in page if row.get("id") in raw_by_id]
    for order in orders:
        if approval_needs_tracking_opener(order) and not str(order.get("tracking_token") or "").strip():
            try:
                await ensure_tracking_token(db, order)
            except HTTPException:
                pass
    grouped = await _load_approval_records(db, [order.get("id") for order in orders])
    for row in page:
        order = raw_by_id.get(row.get("id"))
        if not order:
            row["design_approvals"] = []
            continue
        row["design_approvals"] = design_approval_rows(order, grouped.get(str(order.get("id") or ""), []))


def serialize_list_row(order: dict, deliveries: List[dict], invoices: List[dict]) -> dict:
    attach_tanggal_pengiriman_snapshot(order)
    workflow, reasons = status_po_workflow_detail(order)
    counts = _document_counts(order, deliveries, invoices)
    kirim = order.get("tanggal_pengiriman")
    today = wib_today().isoformat()
    overdue = bool(
        not bool(order.get("buffer_stock"))
        and kirim
        and kirim < today
        and effective_workflow_status(order) not in ("Terkirim",)
    )
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
        "workflow_reasons": reasons,
        "active_stage": _active_stage_key(workflow),
        "progress_percent": _workflow_progress(workflow),
        "item_names": item_line_names(order),
        "design_approvals": design_approval_rows(order),
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

    filtered, counts = select_sales_pwa_order_rows(
        raw,
        q=q,
        customer_q=customer_q,
        po_q=po_q,
        stage=stage,
        buffer=buffer,
        progress_min=progress_min,
        progress_max=progress_max,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )

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
    raw_by_id = {order.get("id"): order for order in filtered if order.get("id")}
    await attach_page_design_approvals(db, page, raw_by_id)

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


def _dashboard_paid_and_outstanding(grand: float, paid_raw: float) -> tuple:
    """Same cap used by the dashboard card. Receipt amount only, never allocated_amount."""
    inv_paid = min(max(paid_raw, 0.0), grand)
    outstanding = max(grand - inv_paid, 0.0)
    return inv_paid, outstanding


def _dashboard_payment_status(grand: float, paid: float) -> str:
    if paid <= 0:
        return "BELUM DIBAYAR"
    if paid < grand:
        return "DP"
    return "LUNAS"


def _finance_customer_name(inv: dict) -> str:
    customer = inv.get("customer")
    if isinstance(customer, dict):
        return str(customer.get("name") or customer.get("customer_name") or "").strip()
    if customer not in (None, ""):
        return str(customer).strip()
    return str(inv.get("customer_name") or "").strip()


def _finance_day(value: Any) -> str:
    return parse_calendar_date(value) or str(value or "")[:10]


async def _load_owned_finalized_finance(db, user: dict) -> tuple:
    """Finalized invoices on non-cancelled POs owned by the session sales user.

    Read only. Ownership is orders.sales_user_id. Receipt rows that are cancelled
    or reversed do not reduce outstanding. This is the dashboard invoice set.
    """
    owned = await _cursor_rows(db.orders.find(
        {"cancelled": {"$ne": True}, **sales_owner_match(user)},
        {"_id": 0, "id": 1},
    ))
    po_ids = [str(row.get("id")) for row in owned if row.get("id")]
    if not po_ids:
        return [], {}
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
            "invoice_number": 1,
            "invoice_date": 1,
            "due_date": 1,
            "customer": 1,
            "customer_name": 1,
            "grand_total": 1,
        },
    ))
    inv_ids = [str(inv.get("id")) for inv in invoices if inv.get("id")]
    receipts = []
    if inv_ids:
        receipts = await _cursor_rows(db.sales_receipts.find(
            {"invoice_id": {"$in": inv_ids}},
            {"_id": 0, "invoice_id": 1, "amount": 1, "financial_status": 1, "cancelled": 1},
        ))
    from services.sales_receipt_posting import receipt_counts_toward_outstanding

    paid_by_invoice: Dict[str, float] = {}
    for receipt in receipts:
        if not receipt_counts_toward_outstanding(receipt):
            continue
        iid = str(receipt.get("invoice_id") or "")
        paid_by_invoice[iid] = paid_by_invoice.get(iid, 0.0) + _number(receipt.get("amount"))
    return invoices, paid_by_invoice


def _summarize_owned_finance(invoices: List[dict], paid_by_invoice: Dict[str, float], month: str) -> dict:
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
        inv_paid, outstanding = _dashboard_paid_and_outstanding(
            grand,
            paid_by_invoice.get(str(inv.get("id") or ""), 0.0),
        )
        paid += inv_paid
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


async def _sales_financial_summary(db, user: dict, month: str) -> dict:
    """Invoice-authoritative metrics for POs owned by the authenticated sales user.

    Revenue uses finalized invoice_date in the requested month. Receivables use
    finalized invoices minus live sales_receipts. Draft/cancelled invoices never
    contribute.
    """
    invoices, paid_by_invoice = await _load_owned_finalized_finance(db, user)
    return _summarize_owned_finance(invoices, paid_by_invoice, month)


def _public_finance_invoice(inv: dict, grand: float, inv_paid: float) -> dict:
    invoice_day = _finance_day(inv.get("invoice_date"))
    return {
        "id": inv.get("id"),
        "invoice_number": inv.get("invoice_number") or "",
        "customer_name": _finance_customer_name(inv),
        "invoice_date": invoice_day or None,
        "grand_total": round(grand, 2),
        "payment_status": _dashboard_payment_status(grand, inv_paid),
    }


def _public_finance_receivable(inv: dict, grand: float, inv_paid: float, outstanding: float) -> dict:
    invoice_day = _finance_day(inv.get("invoice_date"))
    due_day = _finance_day(inv.get("due_date"))
    return {
        "id": inv.get("id"),
        "invoice_number": inv.get("invoice_number") or "",
        "customer_name": _finance_customer_name(inv),
        "invoice_date": invoice_day or None,
        "due_date": due_day or None,
        "grand_total": round(grand, 2),
        "paid_amount": round(inv_paid, 2),
        "outstanding_amount": round(outstanding, 2),
    }


async def list_sales_pwa_invoices(db, user: dict, *, month: str = "") -> dict:
    """Finalized invoices whose invoice_date is in the requested month.

    summary.total_amount is metrics.revenue for the same session and month.
    """
    require_sales_pwa(user)
    month = _month_key(month)
    invoices, paid_by_invoice = await _load_owned_finalized_finance(db, user)
    summary = _summarize_owned_finance(invoices, paid_by_invoice, month)
    rows = []
    for inv in invoices:
        grand = _number(inv.get("grand_total"))
        invoice_day = parse_calendar_date(inv.get("invoice_date")) or str(inv.get("invoice_date") or "")[:10]
        if not invoice_day.startswith(f"{month}-"):
            continue
        inv_paid, _outstanding = _dashboard_paid_and_outstanding(
            grand,
            paid_by_invoice.get(str(inv.get("id") or ""), 0.0),
        )
        rows.append(_public_finance_invoice(inv, grand, inv_paid))
    rows.sort(key=lambda row: (row.get("invoice_date") or "", row.get("invoice_number") or ""), reverse=True)
    return {
        "month": month,
        "invoices": rows,
        "summary": {
            "total_amount": summary["revenue"],
            "invoice_count": summary["invoice_count"],
        },
    }


async def list_sales_pwa_receivables(db, user: dict) -> dict:
    """Outstanding finalized invoices for this session, every invoice_date.

    summary.outstanding_total is metrics.receivables. Month does not apply.
    """
    require_sales_pwa(user)
    invoices, paid_by_invoice = await _load_owned_finalized_finance(db, user)
    summary = _summarize_owned_finance(invoices, paid_by_invoice, _month_key(""))
    rows = []
    for inv in invoices:
        grand = _number(inv.get("grand_total"))
        inv_paid, outstanding = _dashboard_paid_and_outstanding(
            grand,
            paid_by_invoice.get(str(inv.get("id") or ""), 0.0),
        )
        if outstanding <= 0:
            continue
        rows.append(_public_finance_receivable(inv, grand, inv_paid, outstanding))
    rows.sort(key=lambda row: (row.get("due_date") or "9999-99-99", row.get("invoice_date") or "", row.get("invoice_number") or ""))
    return {
        "receivables": rows,
        "summary": {
            "outstanding_total": summary["receivables"],
            "invoice_count": summary["receivable_invoice_count"],
        },
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
            "updated_at": 1,
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
                "linked_customer_id": 1,
            },
        ))
    return ownerships, outlets


def _parse_follow_instant(value: Any) -> Optional[datetime]:
    """Absolute instant. Naive values match the historical activity writer (UTC)."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_pwa_local_datetime(value: Any) -> Optional[str]:
    """PWA datetime-local has no offset. Treat that wall clock as Asia/Jakarta.

    Aware values stay absolute. Returns a UTC ISO string for storage.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WIB)
    return parsed.astimezone(timezone.utc).isoformat()


def _wib_follow_fields(value: Any) -> tuple:
    instant = _parse_follow_instant(value)
    if instant is None:
        day = str(value or "")[:10]
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            return day, str(value)
        return None, None
    local = instant.astimezone(WIB)
    return local.date().isoformat(), local.isoformat()


def _stable_customer_link(value: Any) -> Optional[str]:
    """Exact customer id only. Name keys and legacy Name|Outlet composites are not links."""
    text = str(value or "").strip()
    if not text or text.startswith("name:") or "|" in text:
        return None
    return text


def _latest_visit_by_outlet(activities: List[dict]) -> Dict[str, dict]:
    latest: Dict[str, dict] = {}
    for activity in activities:
        outlet_id = str(activity.get("outlet_id") or "")
        if not outlet_id or outlet_id in latest:
            continue
        latest[outlet_id] = activity
    return latest


def _classify_reminders(reminders: List[dict], today: str) -> dict:
    return {
        "agenda_today": [row for row in reminders if row.get("date") == today],
        "follow_up_overdue": [row for row in reminders if str(row.get("date") or "") < today],
        "follow_up_upcoming": [row for row in reminders if str(row.get("date") or "") > today],
    }


def _cap_agenda(rows: List[dict]) -> List[dict]:
    return rows[:AGENDA_SECTION_LIMIT]


def _same_follow_instant(left: Any, right: Any) -> bool:
    a = _parse_follow_instant(left)
    b = _parse_follow_instant(right)
    if a is not None and b is not None:
        return a == b
    return False


def _written_after(left: Any, right: Any) -> bool:
    """True when left is a strictly later absolute instant than right.

    Parsed instants win over raw string order so `Z`, `+00:00`, and fractional
    seconds cannot invert the newest intent.
    """
    a = _parse_follow_instant(left)
    b = _parse_follow_instant(right)
    if a is not None and b is not None:
        return a > b
    return str(left or "") > str(right or "")


def _reminder_for_outlet(outlet_id: str, ownership: dict, outlet: dict, latest_activity: dict, today: str) -> Optional[dict]:
    """One reminder per owned outlet.

    Precedence, one active intent, newest write wins:
    - latest visit follow-up when ownership has none, or activity_at is strictly after ownership.updated_at
    - otherwise ownership.next_follow_up_at
    Equal write times keep the ownership slot. A later schedule or a visit follow-up
    copied onto that slot replaces the previous instant. There is still one card.
    Kind is "schedule" only when the shown instant is not the latest visit follow-up.
    """
    latest_activity = latest_activity or {}
    own_raw = ownership.get("next_follow_up_at")
    act_raw = latest_activity.get("next_follow_up_at")
    own_day, own_at = _wib_follow_fields(own_raw)
    act_day, act_at = _wib_follow_fields(act_raw)
    act_when = str(latest_activity.get("activity_at") or "")
    own_when = str(ownership.get("updated_at") or "")
    use_activity = False
    if act_day and not own_day:
        use_activity = True
    elif act_day and own_day:
        if act_when and own_when:
            use_activity = _written_after(act_when, own_when)
        elif act_when and not own_when:
            use_activity = True
    if use_activity:
        day, at, source = act_day, act_at, "activity"
    elif own_day:
        day, at, source = own_day, own_at, "ownership"
    else:
        return None
    shown_raw = act_raw if use_activity else own_raw
    kind = "follow_up" if _same_follow_instant(shown_raw, act_raw) else "schedule"
    action = str(latest_activity.get("next_action") or "").strip()
    address = str((outlet or {}).get("address") or "").strip()
    return {
        "id": f"followup:{outlet_id}:{day}",
        "outlet_id": outlet_id,
        "outlet": (outlet or {}).get("name") or "Outlet",
        "address": address or None,
        "date": day,
        "at": at,
        "kind": kind,
        "next_action": action or None,
        "overdue": day < today,
        "source": source,
    }


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
    latest_by_outlet = _latest_visit_by_outlet(activities)
    for outlet_id, ownership in own_by_outlet.items():
        reminder = _reminder_for_outlet(
            outlet_id,
            ownership,
            outlet_by_id.get(outlet_id) or {},
            latest_by_outlet.get(outlet_id) or {},
            today,
        )
        if reminder:
            reminders.append(reminder)
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
    buckets = _classify_reminders(reminders, today)
    agenda_today = buckets["agenda_today"]
    follow_up_overdue = buckets["follow_up_overdue"]
    follow_up_upcoming = buckets["follow_up_upcoming"]
    return {
        "month": month,
        "outlets": outlet_rows,
        "visits": rows,
        "reminders": reminders,
        "agenda_today": _cap_agenda(agenda_today),
        "follow_up_overdue": _cap_agenda(follow_up_overdue),
        "follow_up_upcoming": _cap_agenda(follow_up_upcoming),
        "counts": {
            "owned_outlets": len(ownerships),
            "visit_month": visit_month_count,
            "reminder_month": len(reminders),
            "agenda_today": len(agenda_today),
            "overdue": len(follow_up_overdue),
            "upcoming": len(follow_up_upcoming),
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
        "agenda_today": visits["agenda_today"],
        "agenda_today_total": visits["counts"]["agenda_today"],
        "follow_up_overdue": visits["follow_up_overdue"],
        "follow_up_overdue_total": visits["counts"]["overdue"],
        "follow_up_upcoming": visits["follow_up_upcoming"],
        "follow_up_upcoming_total": visits["counts"]["upcoming"],
        "upcoming_visits": (
            visits["agenda_today"] + visits["follow_up_overdue"] + visits["follow_up_upcoming"]
        ),
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

    raw_follow = payload.get("next_follow_up_at")
    normalized_follow = normalize_pwa_local_datetime(raw_follow)
    if str(raw_follow or "").strip() and normalized_follow is None:
        raise HTTPException(400, "Waktu follow-up tidak valid")
    clean = {
        "outlet_id": str(payload.get("outlet_id") or "").strip(),
        "activity_type": "visit",
        "result": str(payload.get("result") or "").strip(),
        "next_action": str(payload.get("next_action") or "").strip(),
        "next_follow_up_at": normalized_follow,
        "notes": str(payload.get("notes") or "").strip(),
        "visit_photo_ids": [
            str(value).strip()
            for value in (payload.get("visit_photo_ids") or [])
            if str(value or "").strip()
        ],
    }
    # create_native_activity validates current_owner_id, in_pool, activity
    # settings, required photo/location, and always writes session users.id.
    created = await activity_svc.create_native_activity(
        db,
        clean,
        user,
        {PERM_ACTIVITY_CREATE},
    )
    if normalized_follow:
        # The ownership slot is the single active intent. A new visit follow-up
        # replaces a previous schedule. An empty follow-up does not clear it.
        # Automation timers stay untouched. A missed write does not fail the visit.
        await _set_active_follow_slot(db, user, clean["outlet_id"], normalized_follow, required=False)
    return created


async def _set_active_follow_slot(db, user: dict, outlet_id: str, normalized: str, *, required: bool) -> bool:
    """Write the one ownership.next_follow_up_at slot for this session's outlet."""
    uid = session_user_id(user)
    snap = await db[COL_OWNERSHIP].find_one({"outlet_id": outlet_id}, {"_id": 0})
    if not snap or str(snap.get("current_owner_id") or "") != uid or snap.get("in_pool"):
        if required:
            raise HTTPException(404, "Outlet tidak ditemukan")
        return False
    if _same_follow_instant(snap.get("next_follow_up_at"), normalized):
        return True
    now = datetime.now(timezone.utc).isoformat()
    query = {"outlet_id": outlet_id, "current_owner_id": uid, "in_pool": {"$ne": True}}
    update: Dict[str, Any] = {"$set": {"next_follow_up_at": normalized, "updated_at": now}}
    if snap.get("version") is not None:
        query["version"] = int(snap["version"])
        update["$inc"] = {"version": 1}
    result = await db[COL_OWNERSHIP].update_one(query, update)
    if result.matched_count != 1:
        if required:
            raise HTTPException(409, "Jadwal tidak tersimpan. Muat ulang lalu coba lagi.")
        return False
    return True


async def schedule_sales_pwa_visit(db, user: dict, payload: dict) -> dict:
    """Plan a visit on ownership.next_follow_up_at. Does not insert a completed activity."""
    require_sales_pwa(user)
    outlet_id = str(payload.get("outlet_id") or "").strip()
    raw_follow = payload.get("next_follow_up_at")
    normalized = normalize_pwa_local_datetime(raw_follow)
    if not outlet_id:
        raise HTTPException(400, "outlet_id wajib")
    if not normalized:
        raise HTTPException(400, "Jadwal visit wajib")
    await _set_active_follow_slot(db, user, outlet_id, normalized, required=True)
    day, at = _wib_follow_fields(normalized)
    return {
        "scheduled": True,
        "outlet_id": outlet_id,
        "next_follow_up_at": normalized,
        "date": day,
        "at": at,
    }


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
    await attach_page_design_approvals(db, [row], {order.get("id"): order})
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
        file_meta = sj_files.get(str(d.get("id") or ""))
        sj_rows.append(_sj_document_row(d, file_meta))
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

    pred = await build_sales_pwa_predictions(db, user, orders=orders, include_membership=True)
    membership = pred.pop("membership", {}) or {}
    pred_by_key = {r.get("customer_key"): r for r in pred.get("rows") or []}
    needle = _norm(q)
    rows = []
    for g in groups.values():
        outlets = sorted(g["outlets"])
        mapped = {
            membership.get(str(order_id))
            for order_id in (g.get("order_ids") or [])
            if membership.get(str(order_id))
        }
        pred_row = pred_by_key.get(next(iter(mapped))) if len(mapped) == 1 else None
        rows.append({
            "customer_key": g["customer_key"],
            "prediction_key": _prediction_link_key(g["customer_key"], pred_row),
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
    await _attach_linked_visit_history(db, user, rows)
    return {"customers": rows, "total": len(rows), "loaded_at": datetime.now(timezone.utc).isoformat()}


async def _attach_linked_visit_history(db, user: dict, rows: List[dict]) -> None:
    """Attach visit history only through sales_outlets.linked_customer_id == customer id."""
    uid = session_user_id(user)
    ownerships, outlets = await _owned_visit_context(db, user)
    del ownerships
    linked_outlets: Dict[str, List[str]] = {}
    outlet_name: Dict[str, str] = {}
    outlet_ids: List[str] = []
    for outlet in outlets:
        link = _stable_customer_link(outlet.get("linked_customer_id"))
        outlet_id = str(outlet.get("id") or "")
        if not link or not outlet_id:
            continue
        linked_outlets.setdefault(link, []).append(outlet_id)
        outlet_name[outlet_id] = outlet.get("name") or "Outlet"
        outlet_ids.append(outlet_id)
    activities: List[dict] = []
    if outlet_ids:
        activities = await db[COL_ACTIVITIES].find(
            {"sales_user_id": uid, "activity_type": "visit", "outlet_id": {"$in": outlet_ids}},
            {
                "_id": 0,
                "id": 1,
                "outlet_id": 1,
                "activity_at": 1,
                "result": 1,
                "next_action": 1,
                "next_follow_up_at": 1,
                "notes": 1,
            },
        ).sort("activity_at", -1).to_list(1000)
    for row in rows:
        link = _stable_customer_link(row.get("customer_key"))
        ids = linked_outlets.get(link or "") or []
        if not link or not ids:
            row["visit_link"] = "unlinked"
            row["visit_history"] = []
            continue
        id_set = set(ids)
        history = []
        for activity in activities:
            if str(activity.get("outlet_id") or "") not in id_set:
                continue
            history.append({
                "id": activity.get("id"),
                "outlet_id": activity.get("outlet_id"),
                "outlet": outlet_name.get(str(activity.get("outlet_id")), "Outlet"),
                "activity_at": activity.get("activity_at"),
                "result": activity.get("result") or "",
                "next_action": activity.get("next_action") or "",
                "next_follow_up_at": activity.get("next_follow_up_at"),
                "notes": activity.get("notes") or "",
            })
        row["visit_link"] = "linked"
        row["visit_history"] = history


# Presentation order for the sales list. Does not change analyze_group status rules.
PWA_PREDICTION_STATUS_ORDER = (
    STATUS_RISIKO,
    STATUS_TERLAMBAT,
    STATUS_JATUH_TEMPO,
    STATUS_WAKTU_FOLLOW_UP,
    STATUS_TIDAK_AKTIF,
    STATUS_BELUM_WAKTUNYA,
    STATUS_DATA_BELUM_CUKUP,
)
IMPORT_HISTORY_SOURCE = "IMPORT HISTORIS"


def _prediction_link_key(customer_key: str, pred_row: Optional[dict]) -> str:
    """Route key only when the prediction engine returned one. Never invent a key."""
    del customer_key
    return str((pred_row or {}).get("customer_key") or "").strip()


def _prediction_identity(customer_key: str) -> str:
    key = decode_customer_key(str(customer_key or ""))
    if key.startswith("id:"):
        return "stable"
    if key.startswith("ambiguous:"):
        return "ambiguous"
    return "name"


def _confirmed_stable_customer_id(customer_key: str, orders: List[dict]) -> Optional[str]:
    """Agenda id only when every order in the group carries that same stable id."""
    key = decode_customer_key(str(customer_key or ""))
    if not key.startswith("id:"):
        return None
    expected = key[3:].strip()
    if not expected or "|" in expected or not orders:
        return None
    for order in orders:
        if extract_stable_customer_id(order) != expected:
            return None
    return expected


def _prediction_status_rank(status: str) -> int:
    try:
        return PWA_PREDICTION_STATUS_ORDER.index(status)
    except ValueError:
        return 99


def _iso_day(value: Any) -> Optional[date]:
    text = parse_calendar_date(value) or str(value or "")[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _shape_prediction_row(row: dict, today: date) -> dict:
    """Expose the existing engine fields under the sales-list names. No second formula."""
    status = str(row.get("followup_status") or "")
    end = _iso_day(row.get("prediction_end"))
    days_late = (today - end).days if end and today > end else None
    reason = str(row.get("recommendation") or "").strip()
    if days_late:
        late = f"Lewat {days_late} hari dari akhir perkiraan."
        reason = f"{reason} {late}".strip()
    shaped = dict(row)
    shaped.update({
        "customer_name": row.get("customer") or "",
        "status": status,
        "prediction_center": row.get("prediction_mid"),
        "history_count": row.get("total_orders") or 0,
        "reason": reason,
        "days_late": days_late,
        "evidence": _evidence_text(row),
        "min_orders": MIN_ORDERS,
        "bucket": _prediction_bucket(row, today),
    })
    if not str(row.get("customer_key") or "").startswith("id:"):
        shaped["customer_id"] = None
    return shaped


def _prediction_sort_key(row: dict):
    return (
        _prediction_status_rank(str(row.get("status") or "")),
        row.get("followup_start") or row.get("prediction_center") or row.get("last_order_date") or "9999-99-99",
        _norm(row.get("customer_name")),
    )


def _decimal_or_none(raw: Any) -> Optional[Decimal]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        return Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _non_negative_decimal(raw: Any) -> Optional[Decimal]:
    number = _decimal_or_none(raw)
    if number is None or number < 0:
        return None
    return number


def _money(amount: Decimal) -> float:
    return float(amount.quantize(Decimal("0.01")))


def _product_key(line: dict) -> Optional[str]:
    accurate = str(line.get("accurate_item_id") or "").strip()
    if accurate:
        return f"a:{accurate}"
    master = str(line.get("master_item_id") or "").strip()
    if master:
        return f"m:{master}"
    name = _norm(line.get("name") or line.get("item_name") or line.get("product_name"))
    if not name:
        return None
    return f"name:{name}"


def _product_name(line: dict) -> str:
    return str(line.get("name") or line.get("item_name") or line.get("product_name") or "").strip()


def _live_prediction_order(order: dict) -> bool:
    source = str(order.get("history_source") or order.get("source") or "ORDER MASUK").strip()
    return source != IMPORT_HISTORY_SOURCE


def _order_line_value(order: dict) -> dict:
    """Line value only when quantity and unit_price are both valid. Missing stays null."""
    if not _live_prediction_order(order):
        return {"value_status": "unknown", "order_value": None, "known_value": None}
    lines = [line for line in (order.get("line_items") or []) if isinstance(line, dict)]
    if not lines:
        return {"value_status": "unknown", "order_value": None, "known_value": None}
    known: List[Decimal] = []
    missing = 0
    for line in lines:
        qty = _non_negative_decimal(line.get("quantity"))
        price = _non_negative_decimal(line.get("unit_price"))
        if qty is None or price is None:
            missing += 1
            continue
        known.append(qty * price)
    if not known:
        return {"value_status": "unknown", "order_value": None, "known_value": None}
    total = _money(sum(known, Decimal("0")))
    if missing:
        return {"value_status": "partial", "order_value": None, "known_value": total}
    return {"value_status": "complete", "order_value": total, "known_value": total}


def _customer_order_value(orders: List[dict]) -> dict:
    live = [order for order in orders if _live_prediction_order(order)]
    valued = [_order_line_value(order) for order in live]
    complete = [row["order_value"] for row in valued if row["value_status"] == "complete"]
    if not valued or all(row["value_status"] == "unknown" for row in valued):
        return {
            "value_status": "unknown",
            "historical_order_value": None,
            "average_order_value": None,
            "known_value": None,
        }
    known_parts = [Decimal(str(row["known_value"])) for row in valued if row.get("known_value") is not None]
    known = _money(sum(known_parts, Decimal("0"))) if known_parts else None
    if len(complete) == len(valued):
        exact = sum((Decimal(str(amount)) for amount in complete), Decimal("0"))
        total = _money(exact)
        average = _money(exact / Decimal(len(complete)))
        return {
            "value_status": "complete",
            "historical_order_value": total,
            "average_order_value": average,
            "known_value": total,
        }
    return {
        "value_status": "partial",
        "historical_order_value": None,
        "average_order_value": None,
        "known_value": known,
    }


def _prediction_products(orders: List[dict]) -> List[dict]:
    grouped: Dict[str, dict] = {}
    dated = []
    for order in orders:
        if not _live_prediction_order(order):
            continue
        day = order_date_of(order)
        dated.append((day.isoformat() if day else "", order))
    for day, order in dated:
        seen = set()
        qty_by_key: Dict[str, Decimal] = {}
        qty_missing = set()
        names: Dict[str, str] = {}
        units: Dict[str, str] = {}
        for line in order.get("line_items") or []:
            if not isinstance(line, dict):
                continue
            key = _product_key(line)
            if not key:
                continue
            seen.add(key)
            names[key] = _product_name(line) or names.get(key) or key
            unit = str(line.get("unit") or "").strip()
            if unit:
                units[key] = unit
            qty = _non_negative_decimal(line.get("quantity"))
            if qty is None:
                qty_missing.add(key)
            else:
                qty_by_key[key] = qty_by_key.get(key, Decimal("0")) + qty
        for key in seen:
            row = grouped.setdefault(key, {
                "product_key": key,
                "name": names.get(key) or "",
                "unit": units.get(key) or "",
                "purchase_count": 0,
                "total_quantity": Decimal("0"),
                "quantity_complete": True,
                "last_purchase_date": None,
                "last_quantity": None,
            })
            row["purchase_count"] += 1
            if names.get(key):
                row["name"] = names[key]
            if units.get(key):
                row["unit"] = units[key]
            if key in qty_missing:
                row["quantity_complete"] = False
            else:
                row["total_quantity"] += qty_by_key.get(key, Decimal("0"))
            if day and (not row["last_purchase_date"] or day > row["last_purchase_date"]):
                row["last_purchase_date"] = day
                last_qty = None if key in qty_missing else qty_by_key.get(key)
                row["last_quantity"] = float(last_qty) if last_qty is not None else None
                if names.get(key):
                    row["name"] = names[key]
                if units.get(key):
                    row["unit"] = units[key]
    rows = []
    for row in grouped.values():
        qty = row["total_quantity"]
        rows.append({
            "product_key": row["product_key"],
            "name": row["name"],
            "unit": row["unit"] or None,
            "purchase_count": row["purchase_count"],
            "total_quantity": None if (not row["quantity_complete"] and qty == 0) else float(qty),
            "quantity_complete": row["quantity_complete"],
            "last_purchase_date": row["last_purchase_date"],
            "last_quantity": row["last_quantity"],
        })
    rows.sort(key=lambda row: row.get("last_purchase_date") or "", reverse=True)
    rows.sort(key=lambda row: -int(row["purchase_count"]))
    return rows


def _valid_owned_prediction_orders(orders: List[dict], supplemental: List[dict]) -> List[dict]:
    from services.order_prediction_import import merge_prediction_orders

    combined = merge_prediction_orders(orders, supplemental or [])
    return [order for order in combined if is_valid_business_order(order)]


def _orders_for_prediction_key(orders: List[dict], masters: List[dict], customer_key: str) -> List[dict]:
    context = build_identity_context(masters, orders, [])
    key = decode_customer_key(customer_key)
    return [
        order for order in orders
        if customer_group_key(order, context, allow_name_fallback=False) == key
    ]


def _history_with_value(history: List[dict], orders: List[dict]) -> List[dict]:
    by_id = {}
    for order in orders:
        if order.get("id"):
            by_id[str(order.get("id"))] = _order_line_value(order)
    rows = []
    for item in history or []:
        info = by_id.get(str(item.get("id") or ""), {
            "value_status": "unknown",
            "order_value": None,
            "known_value": None,
        })
        row = dict(item)
        row["order_value"] = info["order_value"]
        row["known_value"] = info["known_value"]
        row["value_status"] = info["value_status"]
        rows.append(row)
    rows.sort(key=lambda row: (row.get("order_date") or "", row.get("po_number") or ""), reverse=True)
    return rows


async def _prediction_agenda(db, user: dict, customer_id: Optional[str]) -> dict:
    """Exact linked_customer_id outlets owned by this session. No name match."""
    link = _stable_customer_link(customer_id)
    if not link:
        return {"linked": False, "customer_id": None, "outlets": []}
    uid = session_user_id(user)
    outlets = await db[COL_OUTLETS].find(
        {"linked_customer_id": link},
        {"_id": 0, "id": 1, "name": 1, "linked_customer_id": 1},
    ).to_list(100)
    ids = [str(outlet.get("id") or "") for outlet in outlets if outlet.get("id")]
    owned = []
    if ids:
        owned = await db[COL_OWNERSHIP].find(
            {"outlet_id": {"$in": ids}, "current_owner_id": uid, "in_pool": {"$ne": True}},
            {"_id": 0, "outlet_id": 1, "next_follow_up_at": 1},
        ).to_list(100)
    by_outlet = {str(row.get("outlet_id") or ""): row for row in owned}
    visible = []
    for outlet in outlets:
        outlet_id = str(outlet.get("id") or "")
        slot = by_outlet.get(outlet_id)
        if not slot:
            continue
        follow = slot.get("next_follow_up_at")
        visible.append({
            "outlet_id": outlet_id,
            "name": outlet.get("name") or "Outlet",
            "next_follow_up_at": follow or None,
            "has_active_agenda": bool(str(follow or "").strip()),
        })
    visible.sort(key=lambda row: _norm(row.get("name")))
    return {"linked": bool(visible), "customer_id": link, "outlets": visible}


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


async def build_sales_pwa_predictions(
    db,
    user: dict,
    *,
    orders: Optional[List[dict]] = None,
    customer_key: Optional[str] = None,
    include_membership: bool = False,
) -> dict:
    require_sales_pwa(user)
    if orders is None:
        proj = dict(ORDER_PREDICTION_PROJECTION)
        proj["sales_user_id"] = 1
        proj["line_items"] = 1
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
    today = wib_today()
    if customer_key:
        detail = build_order_prediction_detail(
            orders,
            customer_key,
            supplemental=supplemental,
            masters=masters,
            allow_name_fallback=False,
        )
        if not detail:
            raise HTTPException(404, "Prediksi tidak ditemukan")
        valid = _valid_owned_prediction_orders(orders, supplemental)
        grouped = _orders_for_prediction_key(valid, masters, customer_key)
        analysis = _shape_prediction_row(detail.get("analysis") or {}, today)
        history = _history_with_value(detail.get("history") or [], grouped)
        stable_id = _confirmed_stable_customer_id(analysis.get("customer_key"), grouped)
        identity = _prediction_identity(analysis.get("customer_key"))
        analysis["customer_id"] = stable_id
        detail["analysis"] = analysis
        detail["history"] = history
        detail["products"] = _prediction_products(grouped)
        detail["value"] = _customer_order_value(grouped)
        if stable_id:
            agenda = await _prediction_agenda(db, user, stable_id)
            agenda["identity"] = "stable"
        else:
            agenda = {
                "linked": False,
                "customer_id": None,
                "outlets": [],
                "identity": identity,
            }
        detail["agenda"] = agenda
        return detail
    data = build_order_prediction(
        orders,
        supplemental=supplemental,
        masters=masters,
        sales="",
        skip=0,
        limit=2000,
        allow_name_fallback=False,
        include_membership=include_membership,
    )
    buckets = {
        "follow_up": [],
        "tujuh_hari": [],
        "berikutnya": [],
        "belum_cukup": [],
    }
    shaped = [_shape_prediction_row(row, today) for row in (data.get("rows") or [])]
    shaped.sort(key=_prediction_sort_key)
    for row in shaped:
        buckets.setdefault(row.get("bucket") or "berikutnya", []).append(row)
    data["rows"] = shaped
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
