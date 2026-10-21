"""CRM Daily Reports — independent activity log. No PO/customer/stock/HPP side effects."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.sales_activity.util import normalize_whatsapp
from services.sales_customers import COLLECTION as SALES_CUSTOMERS, customer_is_active
from services.tanggal_pengiriman import WIB

COLLECTION = "crm_daily_reports"
AUDIT_COLLECTION = "audit_logs"

PERM_CREATE = "crm.daily_report.create"
PERM_VIEW_OWN = "crm.daily_report.view_own"
PERM_EDIT_OWN = "crm.daily_report.edit_own"
PERM_VIEW_ALL = "crm.daily_report.view_all"

STATUS_CUSTOMER_NEW = "New"
STATUS_CUSTOMER_REPEAT = "Repeat"
STATUS_ORDER_ORDER = "ORDER"
STATUS_ORDER_NO = "TIDAK ORDER"

_PUBLIC_FIELDS = (
    "id",
    "report_date",
    "created_by_id",
    "created_by_name",
    "status_customer",
    "customer_id",
    "outlet_name",
    "whatsapp",
    "whatsapp_norm",
    "order_id",
    "po_number",
    "sales_master_id",
    "sales_label",
    "sales_user_id",
    "status_order",
    "no_order_reason_id",
    "no_order_reason",
    "has_complaint",
    "complaint_category_id",
    "complaint_description",
    "has_issue",
    "issue_category_id",
    "issue_description",
    "notes",
    "wa_match_warning",
    "created_at",
    "updated_at",
)

_EDITABLE = (
    "report_date",
    "status_customer",
    "customer_id",
    "outlet_name",
    "whatsapp",
    "order_id",
    "po_number",
    "sales_master_id",
    "sales_label",
    "sales_user_id",
    "status_order",
    "no_order_reason_id",
    "no_order_reason",
    "has_complaint",
    "complaint_category_id",
    "complaint_description",
    "has_issue",
    "issue_category_id",
    "issue_description",
    "notes",
)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_wib_date() -> str:
    return datetime.now(timezone.utc).astimezone(WIB).date().isoformat()


def actor_user_id(user: dict) -> str:
    return str(user.get("id") or user.get("user_id") or "").strip()


def actor_name(user: dict) -> str:
    return str(user.get("name") or user.get("email") or "").strip()


def can_create(perms: set) -> bool:
    return PERM_CREATE in perms


def can_view_own(perms: set) -> bool:
    return PERM_VIEW_OWN in perms or PERM_CREATE in perms or PERM_EDIT_OWN in perms


def can_view_all(perms: set) -> bool:
    return PERM_VIEW_ALL in perms


def can_edit_own(perms: set) -> bool:
    return PERM_EDIT_OWN in perms


def can_read_any(perms: set) -> bool:
    return can_view_all(perms) or can_view_own(perms)


def public_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {k: doc.get(k) for k in _PUBLIC_FIELDS if k in doc or k in (
        "id", "report_date", "created_by_id", "created_by_name", "status_customer",
        "status_order", "has_complaint", "has_issue", "created_at", "updated_at",
    )}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in ("1", "true", "ya", "yes", "y"):
        return True
    if text in ("0", "false", "tidak", "no", "n", ""):
        return False
    return bool(value)


def _normalize_status_customer(value: Any) -> str:
    raw = _text(value).lower().replace("_", " ")
    if raw in ("new", "new customer", "baru"):
        return STATUS_CUSTOMER_NEW
    if raw in ("repeat", "repeat customer", "ulang"):
        return STATUS_CUSTOMER_REPEAT
    raise HTTPException(400, "Status Customer harus New atau Repeat")


def _normalize_status_order(value: Any) -> str:
    raw = _text(value).upper().replace("_", " ")
    if raw in ("ORDER", "YA ORDER"):
        return STATUS_ORDER_ORDER
    if raw in ("TIDAK ORDER", "NO ORDER", "TIDAK"):
        return STATUS_ORDER_NO
    raise HTTPException(400, "Status Order harus ORDER atau TIDAK ORDER")


def _normalize_report_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return today_wib_date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    # Accept ISO timestamps → WIB calendar date
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WIB)
        return dt.astimezone(WIB).date().isoformat()
    except ValueError as exc:
        raise HTTPException(400, "Tanggal laporan tidak valid") from exc


def _validate_whatsapp_display(raw: str) -> Tuple[str, str]:
    """Return (display, normalized). Raises if invalid for Indonesia-safe digits."""
    display = _text(raw)
    if not display:
        raise HTTPException(400, "Nomor WhatsApp wajib")
    norm = normalize_whatsapp(display)
    if not norm:
        raise HTTPException(400, "Nomor WhatsApp tidak valid")
    digits = re.sub(r"\D", "", display)
    if len(digits) < 8 or len(digits) > 15:
        raise HTTPException(400, "Nomor WhatsApp tidak valid")
    return display, norm


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("report_date")
    await col.create_index("created_by_id")
    await col.create_index("customer_id")
    await col.create_index([("report_date", -1), ("created_by_id", 1)])
    await col.create_index("whatsapp_norm")
    await col.create_index("created_at")


async def _write_audit(db, *, action: str, actor: dict, before=None, after=None, note: str = "") -> None:
    def _scrub(v):
        if not isinstance(v, dict):
            return v
        return {
            k: ("[REDACTED]" if k in ("password", "password_hash", "access_token", "token") else vv)
            for k, vv in v.items()
        }

    await db[AUDIT_COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "action": action,
        "actor_email": actor.get("email"),
        "actor_role": actor.get("role"),
        "actor_name": actor.get("name"),
        "actor_id": actor_user_id(actor),
        "target_email": None,
        "before": _scrub(before) if isinstance(before, dict) else before,
        "after": _scrub(after) if isinstance(after, dict) else after,
        "note": note,
        "at": now_utc_iso(),
    })


async def _find_customer(db, customer_id: str) -> dict:
    cid = _text(customer_id)
    if not cid:
        raise HTTPException(400, "Customer wajib dipilih untuk Repeat")
    doc = await db[SALES_CUSTOMERS].find_one({"id": cid}, {"_id": 0})
    if not doc:
        raise HTTPException(400, "Customer tidak ditemukan di master pelanggan")
    if not customer_is_active(doc):
        raise HTTPException(400, "Customer tidak aktif")
    return doc


async def _wa_match_warning(db, whatsapp_norm: str, *, exclude_customer_id: str = "") -> Optional[str]:
    if not whatsapp_norm:
        return None
    query: Dict[str, Any] = {
        "$or": [
            {"phone": {"$regex": re.escape(whatsapp_norm[-10:]), "$options": "i"}},
            {"whatsapp": {"$regex": re.escape(whatsapp_norm[-10:]), "$options": "i"}},
        ]
    }
    if exclude_customer_id:
        query["id"] = {"$ne": exclude_customer_id}
    hit = await db[SALES_CUSTOMERS].find_one(query, {"_id": 0, "id": 1, "name": 1, "phone": 1})
    if not hit:
        # Also compare normalized
        rows = await db[SALES_CUSTOMERS].find({}, {"_id": 0, "id": 1, "name": 1, "phone": 1}).to_list(500)
        for row in rows:
            if exclude_customer_id and row.get("id") == exclude_customer_id:
                continue
            if normalize_whatsapp(row.get("phone")) == whatsapp_norm:
                hit = row
                break
    if not hit:
        return None
    return f"Nomor WhatsApp mirip pelanggan existing: {hit.get('name') or hit.get('id')}"


async def _resolve_po(
    db,
    *,
    order_id: str,
    po_number: str,
    customer_id: str,
    user: dict,
    perms: set,
) -> Tuple[Optional[str], Optional[str]]:
    oid = _text(order_id)
    po = _text(po_number)
    if not oid and not po:
        return None, None
    if "po.view" not in perms and "sales_order.view" not in perms:
        raise HTTPException(403, "Tidak berwenang mereferensikan PO")
    query: Dict[str, Any] = {"cancelled": {"$ne": True}}
    if oid:
        query["id"] = oid
    elif po:
        query["po_number"] = po
    order = await db.orders.find_one(query, {"_id": 0, "id": 1, "po_number": 1, "customer_id": 1, "customer": 1})
    if not order:
        raise HTTPException(400, "PO tidak ditemukan atau tidak valid")
    order_cid = _text(order.get("customer_id"))
    if customer_id and order_cid and order_cid != customer_id:
        raise HTTPException(400, "PO tidak sesuai dengan customer laporan")
    return _text(order.get("id")) or None, _text(order.get("po_number")) or po or None


def _assert_payload_core(payload: dict) -> dict:
    status_customer = _normalize_status_customer(payload.get("status_customer"))
    status_order = _normalize_status_order(payload.get("status_order"))
    report_date = _normalize_report_date(payload.get("report_date"))
    sales_master_id = _text(payload.get("sales_master_id")) or None
    sales_label = _text(payload.get("sales_label") or payload.get("sales"))
    has_complaint = _boolish(payload.get("has_complaint"))
    has_issue = _boolish(payload.get("has_issue"))
    complaint_category_id = _text(payload.get("complaint_category_id")) or None
    issue_category_id = _text(payload.get("issue_category_id")) or None
    no_order_reason_id = _text(payload.get("no_order_reason_id")) or None
    complaint_description = _text(payload.get("complaint_description"))
    issue_description = _text(payload.get("issue_description"))
    no_order_reason = _text(payload.get("no_order_reason"))
    notes = _text(payload.get("notes"))

    if status_order != STATUS_ORDER_NO:
        no_order_reason = ""
        no_order_reason_id = None
    if not has_complaint:
        complaint_description = ""
        complaint_category_id = None
    if not has_issue:
        issue_description = ""
        issue_category_id = None

    return {
        "report_date": report_date,
        "status_customer": status_customer,
        "status_order": status_order,
        "sales_master_id": sales_master_id,
        "sales_label": sales_label,
        "sales_user_id": _text(payload.get("sales_user_id")) or None,
        "has_complaint": has_complaint,
        "complaint_category_id": complaint_category_id,
        "complaint_description": complaint_description,
        "has_issue": has_issue,
        "issue_category_id": issue_category_id,
        "issue_description": issue_description,
        "no_order_reason_id": no_order_reason_id,
        "no_order_reason": no_order_reason,
        "notes": notes,
        "order_id": _text(payload.get("order_id")),
        "po_number": _text(payload.get("po_number")),
        "customer_id": _text(payload.get("customer_id")),
        "outlet_name": _text(payload.get("outlet_name")),
        "whatsapp": _text(payload.get("whatsapp")),
        "client_submit_id": _text(payload.get("client_submit_id")),
    }


async def _resolve_sales_for_report(db, payload_core: dict, user: dict) -> tuple:
    """Return (sales_master_id, sales_label, sales_user_id). Dual-read legacy free text."""
    from services import sales_master as sm
    from services.sales_user_mapping import exact_sales_label, load_mapping_doc, mapping_dict_from_entries

    mid = payload_core.get("sales_master_id")
    legacy_label = exact_sales_label(payload_core.get("sales_label"))
    if mid:
        master = await sm.require_active_sales(db, mid, user=user)
        label = exact_sales_label(master.get("sales_name"))
        uid = str(master.get("user_id") or "").strip() or None
        if not uid and label:
            mapping = mapping_dict_from_entries((await load_mapping_doc(db)).get("entries"))
            uid = str(mapping.get(label) or "").strip() or None
        return master["id"], label, uid
    # Legacy edit path: keep snapshot text when master id absent
    if legacy_label:
        return None, legacy_label, payload_core.get("sales_user_id")
    raise HTTPException(400, "Sales wajib dipilih dari Master Sales")


async def _resolve_crm_option(
    db,
    *,
    category_key: str,
    option_id: Optional[str],
    free_text: str,
    required: bool,
    other_requires_text: bool,
    legacy_ok: bool,
) -> tuple:
    """Return (option_id, snapshot_label_or_text)."""
    from services import crm_daily_report_settings as opts

    settings = await opts.load_settings(db)
    oid = _text(option_id) or None
    text = _text(free_text)
    if not required:
        return None, ""
    if oid:
        opt = opts.option_by_id(settings, category_key, oid)
        if not opt:
            raise HTTPException(400, f"Opsi {category_key} tidak ditemukan")
        if not opt.get("active"):
            raise HTTPException(400, f"Opsi {category_key} nonaktif tidak dapat dipilih")
        label = _text(opt.get("label"))
        if opt.get("is_other") or oid == opts.OTHER_OPTION_ID:
            if other_requires_text and not text:
                raise HTTPException(400, "Opsi Lainnya membutuhkan penjelasan")
            return opts.OTHER_OPTION_ID, text or label
        # Non-other: prefer free text if provided (extra note), else option label
        return oid, text or label
    if legacy_ok and text:
        return None, text
    raise HTTPException(400, f"Pilihan {category_key} wajib diisi")


async def build_normalized_doc(db, payload: dict, user: dict, perms: set) -> dict:
    core = _assert_payload_core(payload)
    uid = actor_user_id(user)
    if not uid:
        raise HTTPException(400, "User tidak memiliki id")

    customer_id = None
    outlet_name = core["outlet_name"]
    whatsapp = core["whatsapp"]
    whatsapp_norm = None
    wa_warning = None

    if core["status_customer"] == STATUS_CUSTOMER_NEW:
        customer_id = None
        display, whatsapp_norm = _validate_whatsapp_display(whatsapp)
        whatsapp = display
        # outlet_name optional for New
        wa_warning = await _wa_match_warning(db, whatsapp_norm)
    else:
        cust = await _find_customer(db, core["customer_id"])
        customer_id = cust["id"]
        outlet_name = _text(cust.get("name"))
        whatsapp = _text(cust.get("phone") or cust.get("whatsapp"))
        whatsapp_norm = normalize_whatsapp(whatsapp) or None
        if not whatsapp:
            raise HTTPException(400, "Customer master tidak memiliki nomor WhatsApp")

    order_id, po_number = await _resolve_po(
        db,
        order_id=core["order_id"],
        po_number=core["po_number"],
        customer_id=customer_id or "",
        user=user,
        perms=perms,
    )

    sales_master_id, sales_label, sales_user_id = await _resolve_sales_for_report(db, core, user)

    no_order_reason_id, no_order_reason = await _resolve_crm_option(
        db,
        category_key="no_order_reasons",
        option_id=core["no_order_reason_id"],
        free_text=core["no_order_reason"],
        required=core["status_order"] == STATUS_ORDER_NO,
        other_requires_text=True,
        legacy_ok=True,
    )
    complaint_category_id, complaint_description = await _resolve_crm_option(
        db,
        category_key="complaint_categories",
        option_id=core["complaint_category_id"],
        free_text=core["complaint_description"],
        required=core["has_complaint"],
        other_requires_text=True,
        legacy_ok=True,
    )
    issue_category_id, issue_description = await _resolve_crm_option(
        db,
        category_key="issue_categories",
        option_id=core["issue_category_id"],
        free_text=core["issue_description"],
        required=core["has_issue"],
        other_requires_text=True,
        legacy_ok=True,
    )

    return {
        "report_date": core["report_date"],
        "created_by_id": uid,
        "created_by_name": actor_name(user),
        "status_customer": core["status_customer"],
        "customer_id": customer_id,
        "outlet_name": outlet_name,
        "whatsapp": whatsapp,
        "whatsapp_norm": whatsapp_norm,
        "order_id": order_id,
        "po_number": po_number or "",
        "sales_master_id": sales_master_id,
        "sales_label": sales_label,
        "sales_user_id": sales_user_id,
        "status_order": core["status_order"],
        "no_order_reason_id": no_order_reason_id,
        "no_order_reason": no_order_reason,
        "has_complaint": core["has_complaint"],
        "complaint_category_id": complaint_category_id,
        "complaint_description": complaint_description,
        "has_issue": core["has_issue"],
        "issue_category_id": issue_category_id,
        "issue_description": issue_description,
        "notes": core["notes"],
        "wa_match_warning": wa_warning,
        "client_submit_id": core["client_submit_id"] or None,
    }


async def create_report(db, payload: dict, user: dict, perms: set) -> dict:
    if not can_create(perms):
        raise HTTPException(403, f"Permission '{PERM_CREATE}' diperlukan")
    # Reject forged creator
    if payload.get("created_by_id") and _text(payload.get("created_by_id")) != actor_user_id(user):
        raise HTTPException(403, "Tidak boleh memalsukan CRM pelapor")

    submit_id = _text(payload.get("client_submit_id"))
    if submit_id:
        existing = await db[COLLECTION].find_one(
            {"created_by_id": actor_user_id(user), "client_submit_id": submit_id},
            {"_id": 0},
        )
        if existing:
            return public_doc(existing)

    fields = await build_normalized_doc(db, payload, user, perms)
    now = now_utc_iso()
    doc = {
        "id": str(uuid.uuid4()),
        **fields,
        "created_at": now,
        "updated_at": now,
    }
    await db[COLLECTION].insert_one(doc)
    await _write_audit(
        db,
        action="crm.daily_report.create",
        actor=user,
        before=None,
        after=public_doc(doc),
        note=f"create {doc['id']}",
    )
    return public_doc(doc)


def _list_match(user: dict, perms: set, filters: dict) -> dict:
    if not can_read_any(perms):
        raise HTTPException(403, "Permission lihat laporan diperlukan")
    query: Dict[str, Any] = {}
    if not can_view_all(perms):
        query["created_by_id"] = actor_user_id(user)
    else:
        crm_id = _text(filters.get("crm_user_id") or filters.get("created_by_id"))
        if crm_id:
            query["created_by_id"] = crm_id

    date_from = _text(filters.get("date_from"))
    date_to = _text(filters.get("date_to"))
    if date_from or date_to:
        qdate: Dict[str, str] = {}
        if date_from:
            qdate["$gte"] = date_from[:10]
        if date_to:
            qdate["$lte"] = date_to[:10]
        query["report_date"] = qdate

    sales = _text(filters.get("sales") or filters.get("sales_label"))
    if sales:
        query["sales_label"] = {"$regex": re.escape(sales), "$options": "i"}

    status_order = _text(filters.get("status_order"))
    if status_order:
        query["status_order"] = _normalize_status_order(status_order)

    status_customer = _text(filters.get("status_customer"))
    if status_customer:
        query["status_customer"] = _normalize_status_customer(status_customer)

    if _text(filters.get("has_complaint")) != "":
        query["has_complaint"] = _boolish(filters.get("has_complaint"))
    if _text(filters.get("has_issue")) != "":
        query["has_issue"] = _boolish(filters.get("has_issue"))

    q = _text(filters.get("q"))
    if q:
        rgx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [
            {"outlet_name": rgx},
            {"whatsapp": rgx},
            {"po_number": rgx},
            {"sales_label": rgx},
            {"created_by_name": rgx},
            {"no_order_reason": rgx},
            {"issue_description": rgx},
            {"complaint_description": rgx},
        ]
    return query


async def list_reports(
    db,
    user: dict,
    perms: set,
    *,
    filters: Optional[dict] = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    filters = filters or {}
    query = _list_match(user, perms, filters)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 10), 100))
    total = await db[COLLECTION].count_documents(query)
    rows = await (
        db[COLLECTION]
        .find(query, {"_id": 0})
        .sort([("report_date", -1), ("created_at", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )

    # Summary consistent with same filter scope
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "order_count": {"$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_ORDER]}, 1, 0]}},
            "no_order_count": {"$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_NO]}, 1, 0]}},
            "issue_count": {"$sum": {"$cond": ["$has_issue", 1, 0]}},
            "complaint_count": {"$sum": {"$cond": ["$has_complaint", 1, 0]}},
        }},
    ]
    agg = await db[COLLECTION].aggregate(pipeline).to_list(1)
    summary = agg[0] if agg else {
        "total": 0, "order_count": 0, "no_order_count": 0, "issue_count": 0, "complaint_count": 0,
    }
    summary.pop("_id", None)

    today = today_wib_date()
    uid = actor_user_id(user)
    items = []
    for row in rows:
        item = public_doc(row)
        item["can_edit"] = bool(
            can_edit_own(perms)
            and row.get("created_by_id") == uid
            and row.get("report_date") == today
        )
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size),
        "summary": summary,
        "today_wib": today,
    }


async def get_report(db, report_id: str, user: dict, perms: set) -> dict:
    if not can_read_any(perms):
        raise HTTPException(403, "Permission lihat laporan diperlukan")
    doc = await db[COLLECTION].find_one({"id": _text(report_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Laporan tidak ditemukan")
    if not can_view_all(perms) and doc.get("created_by_id") != actor_user_id(user):
        raise HTTPException(403, "Anda tidak berhak melihat laporan ini")
    out = public_doc(doc)
    today = today_wib_date()
    out["can_edit"] = bool(
        can_edit_own(perms)
        and doc.get("created_by_id") == actor_user_id(user)
        and doc.get("report_date") == today
    )
    return out


async def update_report(db, report_id: str, payload: dict, user: dict, perms: set) -> dict:
    if not can_edit_own(perms):
        raise HTTPException(403, f"Permission '{PERM_EDIT_OWN}' diperlukan")
    if can_view_all(perms) and not can_edit_own(perms):
        raise HTTPException(403, "Holding/Direktur tidak dapat mengedit laporan CRM")

    doc = await db[COLLECTION].find_one({"id": _text(report_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Laporan tidak ditemukan")
    if doc.get("created_by_id") != actor_user_id(user):
        raise HTTPException(403, "Tidak boleh mengedit laporan CRM lain")
    today = today_wib_date()
    if doc.get("report_date") != today:
        raise HTTPException(403, "Laporan hanya dapat diedit pada tanggal bisnis WIB yang sama")

    # Merge editable fields onto existing for re-validation
    merged = {k: doc.get(k) for k in _EDITABLE}
    for key in _EDITABLE:
        if key in payload:
            merged[key] = payload.get(key)
    # Preserve status_customer/customer linkage keys from payload when present
    for key in (
        "status_customer", "customer_id", "outlet_name", "whatsapp", "order_id", "po_number",
        "sales_master_id", "sales_label", "sales", "sales_user_id", "status_order",
        "no_order_reason_id", "no_order_reason",
        "has_complaint", "complaint_category_id", "complaint_description",
        "has_issue", "issue_category_id", "issue_description", "notes",
        "report_date",
    ):
        if key in payload:
            merged[key] = payload.get(key)

    # Same-day edit must keep report_date == today (or allow changing only within today)
    new_date = _normalize_report_date(merged.get("report_date"))
    if new_date != today:
        raise HTTPException(403, "Tanggal laporan tidak boleh diubah ke hari lain saat edit same-day")
    merged["report_date"] = new_date

    fields = await build_normalized_doc(db, merged, user, perms)
    # Keep original creator snapshots
    fields["created_by_id"] = doc["created_by_id"]
    fields["created_by_name"] = doc.get("created_by_name") or actor_name(user)
    fields.pop("client_submit_id", None)

    before = public_doc(doc)
    update = {**fields, "updated_at": now_utc_iso()}
    await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": update})
    updated = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    after = public_doc(updated)
    await _write_audit(
        db,
        action="crm.daily_report.update",
        actor=user,
        before=before,
        after=after,
        note=f"update {doc['id']}",
    )
    after["can_edit"] = True
    return after


async def search_customers_for_report(db, q: str = "") -> List[dict]:
    """Active master customers for Repeat picker. Read-only."""
    from services.sales_customers import list_sales_customers
    rows = await list_sales_customers(db, q=q or None)
    out = []
    for row in rows:
        if not customer_is_active(row):
            continue
        out.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "phone": row.get("phone"),
            "city": row.get("city"),
            "pic": row.get("pic"),
        })
    return out[:100]


async def search_orders_for_report(
    db,
    *,
    q: str = "",
    customer_id: str = "",
    perms: set,
    limit: int = 20,
) -> List[dict]:
    if "po.view" not in perms and "sales_order.view" not in perms:
        return []
    query: Dict[str, Any] = {"cancelled": {"$ne": True}}
    cid = _text(customer_id)
    if cid:
        query["customer_id"] = cid
    needle = _text(q)
    if needle:
        query["$or"] = [
            {"po_number": {"$regex": re.escape(needle), "$options": "i"}},
            {"customer": {"$regex": re.escape(needle), "$options": "i"}},
        ]
    rows = await db.orders.find(
        query,
        {"_id": 0, "id": 1, "po_number": 1, "customer": 1, "customer_id": 1, "sales": 1, "order_date": 1},
    ).sort([("order_date", -1), ("created_at", -1)]).to_list(max(1, min(int(limit or 20), 50)))
    return rows
