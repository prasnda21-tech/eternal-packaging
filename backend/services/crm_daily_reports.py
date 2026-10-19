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
STATUS_ORDER_PENGAJUAN_PO = "PENGAJUAN PO"
STATUS_ORDER_PENGAJUAN_DITOLAK = "PENGAJUAN DITOLAK"
STATUS_ORDER_FOLLOW_UP = "FOLLOW UP"

STATUS_ORDER_OPTIONS = (
    STATUS_ORDER_ORDER,
    STATUS_ORDER_NO,
    STATUS_ORDER_PENGAJUAN_PO,
    STATUS_ORDER_PENGAJUAN_DITOLAK,
    STATUS_ORDER_FOLLOW_UP,
)

# Exact Excel texts for Keterangan Tidak Order (plus UI-only Lainnya → free text).
NO_ORDER_REASON_PRESETS = (
    "Harga terlalu mahal",
    "Slow Respon Customer",
    "Deadline tidak cocok",
    "Customer hanya tanya tanya",
    "Desain belum selesai",
    "Produk tidak ada di perusahaan",
)
NO_ORDER_REASON_LAINNYA = "Lainnya"

COMPLAINT_CATEGORY_OPTIONS = (
    "CUP CACAT",
    "SABLON CACAT",
    "SALAH DESIGN",
    "SALAH CUP",
    "KESALAHAN DEALING HARGA",
    "Lainnya",
)

SALES_SOURCE_EXISTING = "existing"
SALES_SOURCE_MANUAL = "manual"
SALES_SOURCE_LEGACY = "legacy"
MANUAL_SALES_MAX_LEN = 80

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
    "sales_label",
    "sales_user_id",
    "sales_source",
    "sales_note",
    "status_order",
    "no_order_reason",
    "has_complaint",
    "complaint_category",
    "complaint_description",
    "has_issue",
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
    "sales_label",
    "sales_user_id",
    "sales_source",
    "status_order",
    "no_order_reason",
    "has_complaint",
    "complaint_category",
    "complaint_description",
    "has_issue",
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
    out = {k: doc.get(k) for k in _PUBLIC_FIELDS if k in doc or k in (
        "id", "report_date", "created_by_id", "created_by_name", "status_customer",
        "status_order", "has_complaint", "has_issue", "created_at", "updated_at",
    )}
    # Legacy rows may omit complaint_category — expose empty string for readers.
    if "complaint_category" not in out:
        out["complaint_category"] = _text(doc.get("complaint_category"))
    # Legacy free-text sales are not treated as verified existing users.
    if "sales_source" not in out or not _text(out.get("sales_source")):
        if _text(doc.get("sales_user_id")):
            out["sales_source"] = SALES_SOURCE_EXISTING
        elif _text(doc.get("sales_label")):
            out["sales_source"] = SALES_SOURCE_LEGACY
        else:
            out["sales_source"] = ""
    if "sales_note" not in out:
        out["sales_note"] = _text(doc.get("sales_note"))
    return out


def excel_dropdown_meta() -> dict:
    """Static Excel-aligned option lists for CRM Daily Report forms/filters."""
    return {
        "status_order_options": list(STATUS_ORDER_OPTIONS),
        "no_order_reason_presets": list(NO_ORDER_REASON_PRESETS),
        "no_order_reason_lainnya": NO_ORDER_REASON_LAINNYA,
        "complaint_category_options": list(COMPLAINT_CATEGORY_OPTIONS),
    }


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
    aliases = {
        "ORDER": STATUS_ORDER_ORDER,
        "YA ORDER": STATUS_ORDER_ORDER,
        "TIDAK ORDER": STATUS_ORDER_NO,
        "NO ORDER": STATUS_ORDER_NO,
        "TIDAK": STATUS_ORDER_NO,
        "PENGAJUAN PO": STATUS_ORDER_PENGAJUAN_PO,
        "PENGAJUAN DITOLAK": STATUS_ORDER_PENGAJUAN_DITOLAK,
        "FOLLOW UP": STATUS_ORDER_FOLLOW_UP,
        "FOLLOWUP": STATUS_ORDER_FOLLOW_UP,
    }
    if raw in aliases:
        return aliases[raw]
    raise HTTPException(
        400,
        "Status Order harus salah satu dari: ORDER, TIDAK ORDER, PENGAJUAN PO, "
        "PENGAJUAN DITOLAK, FOLLOW UP",
    )


def _normalize_no_order_reason(value: Any, *, status_order: str) -> str:
    """Store preset Excel text as-is, or free text for Lainnya / legacy."""
    if status_order != STATUS_ORDER_NO:
        return ""
    reason = _text(value)
    if not reason:
        raise HTTPException(400, "Keterangan Tidak Order wajib diisi")
    if reason == NO_ORDER_REASON_LAINNYA:
        raise HTTPException(400, "Isi keterangan manual untuk pilihan Lainnya")
    # Presets and free-text (Lainnya / legacy) are both accepted when non-empty.
    return reason


def _normalize_complaint_category(
    value: Any,
    *,
    has_complaint: bool,
    require_category: bool,
) -> str:
    if not has_complaint:
        return ""
    category = _text(value)
    if not category:
        if require_category:
            raise HTTPException(400, "Jenis Komplain wajib dipilih")
        return ""
    # Exact match first; allow case-insensitive match to canonical labels.
    if category in COMPLAINT_CATEGORY_OPTIONS:
        return category
    upper_map = {opt.upper(): opt for opt in COMPLAINT_CATEGORY_OPTIONS}
    hit = upper_map.get(category.upper())
    if hit:
        return hit
    raise HTTPException(400, "Jenis Komplain tidak valid")


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


async def list_sales_for_report(db, q: str = "") -> List[dict]:
    """Canonical Sales options from sales_user_mapping → active sales_eternal users.

    Falls back to active sales_eternal users when mapping is empty.
    Does not invent labels or create users.
    """
    from services.sales_user_mapping import SALES_PWA_ROLE, exact_sales_label, load_mapping_doc

    mapping = await load_mapping_doc(db)
    entries = [row for row in (mapping.get("entries") or []) if isinstance(row, dict)]
    uids = [
        str(row.get("user_id") or "").strip()
        for row in entries
        if str(row.get("user_id") or "").strip()
    ]
    users_by_id: Dict[str, dict] = {}
    if uids:
        rows = await db.users.find(
            {"id": {"$in": uids}},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
        ).to_list(len(uids))
        users_by_id = {str(row.get("id") or "").strip(): row for row in rows}

    out: List[dict] = []
    seen: set = set()
    for row in entries:
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if not label or not uid or uid in seen:
            continue
        user = users_by_id.get(uid)
        if not user:
            continue
        if _text(user.get("status") or "active").lower() == "inactive":
            continue
        if _text(user.get("role")) != SALES_PWA_ROLE:
            continue
        seen.add(uid)
        out.append({
            "user_id": uid,
            "label": label,
            "name": _text(user.get("name")) or label,
            "email": _text(user.get("email")),
        })

    if not out:
        rows = await db.users.find(
            {"role": SALES_PWA_ROLE, "status": {"$ne": "inactive"}},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
        ).sort("name", 1).to_list(100)
        for user in rows:
            uid = str(user.get("id") or "").strip()
            if not uid or uid in seen:
                continue
            name = _text(user.get("name")) or _text(user.get("email"))
            if not name:
                continue
            seen.add(uid)
            out.append({
                "user_id": uid,
                "label": name,
                "name": name,
                "email": _text(user.get("email")),
            })

    needle = _text(q).casefold()
    if needle:
        out = [
            row for row in out
            if needle in row["label"].casefold()
            or needle in row["name"].casefold()
            or needle in row["email"].casefold()
        ]
    out.sort(key=lambda row: row["label"].casefold())
    return out


async def _canonical_sales_map(db) -> Dict[str, dict]:
    rows = await list_sales_for_report(db)
    return {row["user_id"]: row for row in rows}


async def _resolve_po(
    db,
    *,
    order_id: str,
    po_number: str,
    customer_id: str,
    user: dict,
    perms: set,
) -> Optional[dict]:
    oid = _text(order_id)
    po = _text(po_number)
    if not oid and not po:
        return None
    if "po.view" not in perms and "sales_order.view" not in perms:
        raise HTTPException(403, "Tidak berwenang mereferensikan PO")
    query: Dict[str, Any] = {"cancelled": {"$ne": True}}
    if oid:
        query["id"] = oid
    elif po:
        query["po_number"] = po
    order = await db.orders.find_one(
        query,
        {
            "_id": 0,
            "id": 1,
            "po_number": 1,
            "customer_id": 1,
            "customer": 1,
            "sales": 1,
            "sales_user_id": 1,
        },
    )
    if not order:
        raise HTTPException(400, "PO tidak ditemukan atau tidak valid")
    order_cid = _text(order.get("customer_id"))
    if customer_id and order_cid and order_cid != customer_id:
        raise HTTPException(400, "PO tidak sesuai dengan customer laporan")
    return order


async def _po_verified_sales(db, order: Optional[dict], canon: Dict[str, dict]) -> Tuple[Optional[str], Optional[str]]:
    """Return (sales_user_id, sales_label) when PO sales can be verified canonically."""
    if not order:
        return None, None
    uid = _text(order.get("sales_user_id"))
    if uid and uid in canon:
        return uid, canon[uid]["label"]
    from services.sales_user_mapping import resolve_sales_user_id

    mapped_uid, status = await resolve_sales_user_id(db, order.get("sales"))
    if status == "matched" and mapped_uid and mapped_uid in canon:
        return mapped_uid, canon[mapped_uid]["label"]
    return None, None


async def _resolve_sales_assignment(
    db,
    *,
    status_order: str,
    order: Optional[dict],
    sales_user_id: Any,
    sales_label: Any,
    sales_source: Any,
    existing: Optional[dict] = None,
) -> Tuple[str, Optional[str], str, str]:
    """Validate and normalize Sales for a CRM daily report.

    Returns (sales_label, sales_user_id, sales_source, sales_note).
    """
    canon = await _canonical_sales_map(db)
    payload_uid = _text(sales_user_id) or None
    payload_label = _text(sales_label or "")
    source = _text(sales_source).lower()
    has_po = bool(order)
    po_uid, po_label = await _po_verified_sales(db, order, canon)

    if has_po and po_uid:
        if source == SALES_SOURCE_MANUAL:
            raise HTTPException(400, "Input Manual Sales tidak diizinkan ketika PO dipilih")
        if payload_uid and payload_uid != po_uid:
            raise HTTPException(400, "Sales laporan harus sesuai Sales pada PO yang dipilih")
        return po_label or canon[po_uid]["label"], po_uid, SALES_SOURCE_EXISTING, ""

    if has_po and not po_uid:
        if source == SALES_SOURCE_MANUAL:
            raise HTTPException(400, "Input Manual Sales tidak diizinkan ketika PO dipilih")
        if not payload_uid or payload_uid not in canon:
            raise HTTPException(
                400,
                "PO tidak memiliki Sales terverifikasi; pilih Sales dari daftar Eternal",
            )
        return (
            canon[payload_uid]["label"],
            payload_uid,
            SALES_SOURCE_EXISTING,
            "PO tidak memiliki Sales terverifikasi; Sales dipilih dari daftar",
        )

    # No PO — explicit manual only for TIDAK ORDER.
    if source == SALES_SOURCE_MANUAL:
        if status_order != STATUS_ORDER_NO:
            raise HTTPException(400, "Input Manual Sales hanya untuk status TIDAK ORDER tanpa PO")
        if payload_uid:
            raise HTTPException(400, "Sales manual tidak boleh menyertakan sales_user_id")
        if not payload_label:
            raise HTTPException(400, "Nama Sales wajib diisi")
        if len(payload_label) > MANUAL_SALES_MAX_LEN:
            raise HTTPException(400, f"Nama Sales maksimal {MANUAL_SALES_MAX_LEN} karakter")
        return payload_label, None, SALES_SOURCE_MANUAL, ""

    if payload_uid:
        if payload_uid not in canon:
            raise HTTPException(400, "Sales tidak valid atau tidak ada dalam daftar Eternal")
        return canon[payload_uid]["label"], payload_uid, SALES_SOURCE_EXISTING, ""

    # Legacy preserve on edit: free-text snapshot without verified user id.
    if existing is not None:
        prev_label = _text(existing.get("sales_label"))
        prev_uid = _text(existing.get("sales_user_id")) or None
        prev_source = _text(existing.get("sales_source")).lower()
        if not prev_source:
            prev_source = SALES_SOURCE_LEGACY if prev_label and not prev_uid else (
                SALES_SOURCE_EXISTING if prev_uid else ""
            )
        same_label = payload_label == prev_label or (not payload_label and prev_label)
        if prev_label and not prev_uid and same_label and prev_source in (
            SALES_SOURCE_LEGACY, SALES_SOURCE_MANUAL, "",
        ):
            if status_order != STATUS_ORDER_NO and prev_source == SALES_SOURCE_MANUAL:
                raise HTTPException(
                    400,
                    "Sales manual lama tidak berlaku untuk status ini; pilih Sales dari daftar",
                )
            kept_source = prev_source or SALES_SOURCE_LEGACY
            if status_order != STATUS_ORDER_NO and kept_source == SALES_SOURCE_MANUAL:
                kept_source = SALES_SOURCE_LEGACY
            return prev_label, None, kept_source, ""

    if status_order == STATUS_ORDER_NO and payload_label and not payload_uid:
        # Treat as manual when UI forgot sales_source but label is free text.
        if len(payload_label) > MANUAL_SALES_MAX_LEN:
            raise HTTPException(400, f"Nama Sales maksimal {MANUAL_SALES_MAX_LEN} karakter")
        # Reject if label coincidentally matches a canonical label without user_id —
        # force picking from dropdown to get sales_user_id.
        matched = next((row for row in canon.values() if row["label"] == payload_label), None)
        if matched:
            raise HTTPException(400, "Pilih Sales dari daftar agar terverifikasi")
        return payload_label, None, SALES_SOURCE_MANUAL, ""

    raise HTTPException(400, "Sales / Siapa yang Menjual wajib dipilih dari daftar")


def _assert_payload_core(payload: dict, *, require_complaint_category: bool = True) -> dict:
    status_customer = _normalize_status_customer(payload.get("status_customer"))
    status_order = _normalize_status_order(payload.get("status_order"))
    report_date = _normalize_report_date(payload.get("report_date"))
    has_complaint = _boolish(payload.get("has_complaint"))
    has_issue = _boolish(payload.get("has_issue"))
    complaint_description = _text(payload.get("complaint_description"))
    issue_description = _text(payload.get("issue_description"))
    notes = _text(payload.get("notes"))

    no_order_reason = _normalize_no_order_reason(
        payload.get("no_order_reason"),
        status_order=status_order,
    )
    complaint_category = _normalize_complaint_category(
        payload.get("complaint_category"),
        has_complaint=has_complaint,
        require_category=require_complaint_category,
    )

    if has_complaint:
        if not complaint_description:
            raise HTTPException(400, "Deskripsi Komplain wajib diisi")
    else:
        complaint_description = ""

    if has_issue:
        if not issue_description:
            raise HTTPException(400, "Deskripsi Kendala wajib diisi")
    else:
        issue_description = ""

    return {
        "report_date": report_date,
        "status_customer": status_customer,
        "status_order": status_order,
        "sales_label": _text(payload.get("sales_label") or payload.get("sales")),
        "sales_user_id": _text(payload.get("sales_user_id")) or None,
        "sales_source": _text(payload.get("sales_source")).lower(),
        "has_complaint": has_complaint,
        "complaint_category": complaint_category,
        "complaint_description": complaint_description,
        "has_issue": has_issue,
        "issue_description": issue_description,
        "no_order_reason": no_order_reason,
        "notes": notes,
        "order_id": _text(payload.get("order_id")),
        "po_number": _text(payload.get("po_number")),
        "customer_id": _text(payload.get("customer_id")),
        "outlet_name": _text(payload.get("outlet_name")),
        "whatsapp": _text(payload.get("whatsapp")),
        "client_submit_id": _text(payload.get("client_submit_id")),
    }


async def build_normalized_doc(
    db,
    payload: dict,
    user: dict,
    perms: set,
    *,
    require_complaint_category: bool = True,
    existing: Optional[dict] = None,
) -> dict:
    core = _assert_payload_core(payload, require_complaint_category=require_complaint_category)
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

    order = await _resolve_po(
        db,
        order_id=core["order_id"],
        po_number=core["po_number"],
        customer_id=customer_id or "",
        user=user,
        perms=perms,
    )
    sales_label, sales_user_id, sales_source, sales_note = await _resolve_sales_assignment(
        db,
        status_order=core["status_order"],
        order=order,
        sales_user_id=core["sales_user_id"],
        sales_label=core["sales_label"],
        sales_source=core["sales_source"],
        existing=existing,
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
        "order_id": _text((order or {}).get("id")) or None,
        "po_number": _text((order or {}).get("po_number")) or "",
        "sales_label": sales_label,
        "sales_user_id": sales_user_id,
        "sales_source": sales_source,
        "sales_note": sales_note,
        "status_order": core["status_order"],
        "no_order_reason": core["no_order_reason"],
        "has_complaint": core["has_complaint"],
        "complaint_category": core["complaint_category"],
        "complaint_description": core["complaint_description"],
        "has_issue": core["has_issue"],
        "issue_description": core["issue_description"],
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

    # Summary: ORDER / TIDAK ORDER only for those literals; other statuses counted separately.
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "order_count": {"$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_ORDER]}, 1, 0]}},
            "no_order_count": {"$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_NO]}, 1, 0]}},
            "pengajuan_po_count": {
                "$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_PENGAJUAN_PO]}, 1, 0]}
            },
            "pengajuan_ditolak_count": {
                "$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_PENGAJUAN_DITOLAK]}, 1, 0]}
            },
            "follow_up_count": {
                "$sum": {"$cond": [{"$eq": ["$status_order", STATUS_ORDER_FOLLOW_UP]}, 1, 0]}
            },
            "issue_count": {"$sum": {"$cond": ["$has_issue", 1, 0]}},
            "complaint_count": {"$sum": {"$cond": ["$has_complaint", 1, 0]}},
        }},
    ]
    agg = await db[COLLECTION].aggregate(pipeline).to_list(1)
    summary = agg[0] if agg else {
        "total": 0,
        "order_count": 0,
        "no_order_count": 0,
        "pengajuan_po_count": 0,
        "pengajuan_ditolak_count": 0,
        "follow_up_count": 0,
        "issue_count": 0,
        "complaint_count": 0,
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
    for key in ("status_customer", "customer_id", "outlet_name", "whatsapp", "order_id", "po_number",
                "sales_label", "sales", "sales_user_id", "sales_source", "status_order", "no_order_reason",
                "has_complaint", "complaint_category", "complaint_description",
                "has_issue", "issue_description", "notes",
                "report_date"):
        if key in payload:
            merged[key] = payload.get(key)

    # Same-day edit must keep report_date == today (or allow changing only within today)
    new_date = _normalize_report_date(merged.get("report_date"))
    if new_date != today:
        raise HTTPException(403, "Tanggal laporan tidak boleh diubah ke hari lain saat edit same-day")
    merged["report_date"] = new_date

    # Legacy complaint rows without category remain editable without forcing a category
    # until the CRM supplies one. New complaints still require a category.
    legacy_complaint = bool(doc.get("has_complaint")) and not _text(doc.get("complaint_category"))
    will_complaint = _boolish(merged.get("has_complaint"))
    require_cat = True
    if will_complaint and legacy_complaint and not _text(merged.get("complaint_category")):
        require_cat = False

    fields = await build_normalized_doc(
        db, merged, user, perms,
        require_complaint_category=require_cat,
        existing=doc,
    )
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
        {
            "_id": 0,
            "id": 1,
            "po_number": 1,
            "customer": 1,
            "customer_id": 1,
            "sales": 1,
            "sales_user_id": 1,
            "order_date": 1,
        },
    ).sort([("order_date", -1), ("created_at", -1)]).to_list(max(1, min(int(limit or 20), 50)))
    canon = await _canonical_sales_map(db)
    out = []
    for row in rows:
        uid, label = await _po_verified_sales(db, row, canon)
        item = {
            "id": row.get("id"),
            "po_number": row.get("po_number"),
            "customer": row.get("customer"),
            "customer_id": row.get("customer_id"),
            "sales": row.get("sales") or "",
            "sales_user_id": row.get("sales_user_id"),
            "order_date": row.get("order_date"),
            "sales_verified": bool(uid),
            "sales_label_resolved": label or "",
        }
        out.append(item)
    return out
