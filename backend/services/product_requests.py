"""Customer product requests — CRM draft/submit, shared submitted read, Holding/Direktur feedback/status.

Does not create sales_customers or master_items. Does not call Accurate.
Terbeli does not create PO, stock movements, or journal entries.
Actor, branch, and timestamps always come from the authenticated user / UTC.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    insert_with_unique_number,
    next_dated_seq,
)
from services.sales_activity.util import normalize_whatsapp
from services.sales_customers import get_sales_customer
from services.master_items import list_master_items_picker, public_master_item

COLLECTION = "product_requests"
# Fields safe for CRM product-request picker — never HPP / cost / stock / supplier.
PRODUCT_PICKER_SAFE_FIELDS = (
    "id",
    "accurate_item_id",
    "item_code",
    "item_name",
    "unit",
    "is_active",
    # Needed by Pesanan Penjualan master picker; never stock/HPP/cost.
    "production_process",
    # SERVICE vs INVENTORY label for Add On (Warna) selection — not stock/HPP.
    "item_type",
)
ACTIVITY_COLLECTION = "product_request_activities"
PREFIX_REQUEST = "PPR"
NOTE_MAX_LEN = 2000
FEEDBACK_MAX_LEN = 2000
FREQUENCY_MAX_LEN = 80
CHANNEL_MAX_LEN = 80
TEXT_MAX_LEN = 160

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_REVIEWED = "reviewed"
STATUS_PENDING = "pending"
STATUS_PURCHASED = "purchased"
STATUS_REJECTED = "rejected"
STATUS_COMPLETED = "completed"

STATUS_LABELS = {
    STATUS_DRAFT: "Draft",
    STATUS_SUBMITTED: "Terkirim",
    STATUS_REVIEWED: "Ditinjau",
    STATUS_PENDING: "Pending",
    STATUS_PURCHASED: "Terbeli",
    STATUS_REJECTED: "Ditolak",
    STATUS_COMPLETED: "Selesai",
}

# Follow-up statuses selectable after submit (no forced sequence).
POST_SUBMIT_STATUSES = frozenset({
    STATUS_SUBMITTED,
    STATUS_REVIEWED,
    STATUS_PENDING,
    STATUS_PURCHASED,
    STATUS_REJECTED,
    STATUS_COMPLETED,
})
NOTE_REQUIRED_STATUSES = frozenset({STATUS_PENDING, STATUS_REJECTED})

ACTIVITY_FEEDBACK = "feedback"
ACTIVITY_STATUS = "status_change"
ACTIVITY_SUBMITTED = "submitted"

CUSTOMER_MASTER = "master_crm"
CUSTOMER_NEW = "new_prospect"
PRODUCT_MASTER = "product_master"
PRODUCT_REQUESTED = "requested_product"
VERSION_FIELD = "doc_version"

EVENT_SUBMITTED = "product_request_submitted"
EVENT_FEEDBACK = "product_request_feedback"
EVENT_STATUS = "product_request_status"

# Tests assign an awaitable to synchronize concurrent draft writes. Production stays None.
after_draft_load = None

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PROTECTED_PAYLOAD_KEYS = frozenset({
    "id",
    "request_number",
    "status",
    VERSION_FIELD,
    "created_by_id",
    "created_by_snapshot",
    "branch_id",
    "branch_snapshot",
    "created_at",
    "updated_at",
    "submitted_at",
    "customer_snapshot",
    "product_snapshot",
    "purchase_actual_qty",
    "purchase_actual_price",
})

OVERLAY_KEYS = frozenset({
    "customer_source",
    "customer_id",
    "customer_name",
    "pic",
    "whatsapp",
    "phone",
    "city",
    "product_source",
    "product_id",
    "product_name",
    "category",
    "size",
    "ukuran",
    "material",
    "model",
    "specification",
    "potential_qty",
    "frequency",
    "target_price",
    "needed_date",
    "request_note",
    "note",
    "request_channel",
})
CUSTOMER_IDENTITY_KEYS = ("customer_id", "customer_name", "pic", "whatsapp", "phone", "city")
PRODUCT_IDENTITY_KEYS = (
    "product_id",
    "product_name",
    "category",
    "size",
    "ukuran",
    "material",
    "model",
    "specification",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_user_id(user: Optional[dict]) -> str:
    return str(actor_snapshot(user).get("id") or "").strip()


def _clean_text(value: Any, *, max_len: int = TEXT_MAX_LEN) -> str:
    return str(value or "").strip()[:max_len]


def _parse_positive_int(raw: Any, field_name: str) -> int:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    if isinstance(raw, bool):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.")
    if isinstance(raw, float) and raw != int(raw):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.") from None
    if n <= 0:
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")
    return n


def _parse_non_negative_int(raw: Any, field_name: str) -> int:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    if isinstance(raw, bool):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.")
    if isinstance(raw, float) and raw != int(raw):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field_name} harus berupa angka bulat.") from None
    if n < 0:
        raise HTTPException(400, f"{field_name} tidak boleh negatif.")
    return n


def _parse_date(raw: Any, field_name: str) -> str:
    value = str(raw or "").strip()[:10]
    if not _ISO_DATE.match(value):
        raise HTTPException(400, f"{field_name} wajib berformat YYYY-MM-DD.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, f"{field_name} tidak valid.") from exc
    return value


async def load_actor_branch(db, user: Optional[dict]) -> Dict[str, str]:
    """Branch comes from the authenticated users document, never from payload."""
    actor = actor_snapshot(user)
    query: Dict[str, Any] = {}
    email = str((user or {}).get("email") or "").strip()
    uid = actor.get("id") or ""
    if email:
        query = {"email": email}
    elif uid:
        query = {"id": uid}
    doc = await db.users.find_one(query, {"_id": 0}) if query else None
    doc = doc or {}
    branch_id = str(doc.get("branch_id") or doc.get("cabang_id") or "").strip()
    branch_name = str(
        doc.get("branch")
        or doc.get("cabang")
        or doc.get("branch_name")
        or ""
    ).strip()
    return {
        "id": branch_id,
        "name": branch_name,
    }


async def _resolve_master_item(db, product_id: str) -> Optional[dict]:
    mid = str(product_id or "").strip()
    if not mid:
        return None
    doc = await db.master_items.find_one(
        {"$or": [{"id": mid}, {"accurate_item_id": mid}]},
        {"_id": 0},
    )
    return public_master_item(doc) if doc else None


async def search_master_products_for_request(
    db,
    *,
    q: str = "",
    status: str = "active",
    page_size: int = 40,
) -> Dict[str, Any]:
    """Autocomplete for product-request form. Requires create perm at HTTP layer only."""
    raw = await list_master_items_picker(db, q=q or "", status=status or "", page_size=page_size)
    items: List[Dict[str, Any]] = []
    for row in raw.get("items") or []:
        if not row:
            continue
        items.append({key: row.get(key) for key in PRODUCT_PICKER_SAFE_FIELDS})
    return {"items": items}


def _customer_snapshot_from_master(master: dict) -> dict:
    phone = str(master.get("phone") or "").strip()
    wa = normalize_whatsapp(phone) or phone
    return {
        "id": master.get("id"),
        "name": str(master.get("name") or "").strip(),
        "pic": str(master.get("pic") or "").strip(),
        "whatsapp": wa,
        "phone": phone,
        "city": str(master.get("city") or "").strip(),
        "address": str(master.get("address") or "").strip(),
    }


def _customer_snapshot_from_input(payload: dict) -> dict:
    phone = str(payload.get("whatsapp") or payload.get("phone") or "").strip()
    wa = normalize_whatsapp(phone) or phone
    name = _clean_text(payload.get("customer_name") or payload.get("name"), max_len=TEXT_MAX_LEN)
    return {
        "id": None,
        "name": name,
        "pic": _clean_text(payload.get("pic"), max_len=TEXT_MAX_LEN),
        "whatsapp": wa,
        "phone": phone,
        "city": _clean_text(payload.get("city"), max_len=TEXT_MAX_LEN),
    }


def _product_snapshot_from_master(master: dict) -> dict:
    return {
        "id": master.get("id"),
        "accurate_item_id": master.get("accurate_item_id"),
        "item_code": str(master.get("item_code") or "").strip(),
        "item_name": str(master.get("item_name") or "").strip(),
        "unit": str(master.get("unit") or "").strip(),
    }


def _product_snapshot_from_input(payload: dict) -> dict:
    return {
        "id": None,
        "name": _clean_text(payload.get("product_name") or payload.get("name"), max_len=TEXT_MAX_LEN),
        "category": _clean_text(payload.get("category"), max_len=TEXT_MAX_LEN),
        "size": _clean_text(payload.get("size") or payload.get("ukuran"), max_len=TEXT_MAX_LEN),
        "material": _clean_text(payload.get("material"), max_len=TEXT_MAX_LEN),
        "model": _clean_text(payload.get("model") or payload.get("specification"), max_len=TEXT_MAX_LEN),
        "label": "Requested Product",
    }


async def _build_customer(db, payload: dict, *, require_complete: bool) -> Dict[str, Any]:
    source = str(payload.get("customer_source") or "").strip()
    if source not in (CUSTOMER_MASTER, CUSTOMER_NEW):
        if require_complete:
            raise HTTPException(400, "Sumber customer wajib dipilih.")
        return {"customer_source": source or None, "customer_id": None, "customer_snapshot": {}}

    if source == CUSTOMER_MASTER:
        customer_id = str(payload.get("customer_id") or "").strip()
        if not customer_id:
            if require_complete:
                raise HTTPException(400, "Customer master wajib dipilih.")
            return {"customer_source": source, "customer_id": None, "customer_snapshot": {}}
        master = await get_sales_customer(db, customer_id)
        if not master:
            raise HTTPException(400, "Customer master tidak ditemukan.")
        return {
            "customer_source": CUSTOMER_MASTER,
            "customer_id": master.get("id"),
            "customer_snapshot": _customer_snapshot_from_master(master),
        }

    snapshot = _customer_snapshot_from_input(payload)
    if require_complete and not snapshot["name"]:
        raise HTTPException(400, "Nama customer baru wajib diisi.")
    return {
        "customer_source": CUSTOMER_NEW,
        "customer_id": None,
        "customer_snapshot": snapshot,
    }


async def _build_product(db, payload: dict, *, require_complete: bool) -> Dict[str, Any]:
    source = str(payload.get("product_source") or "").strip()
    if source not in (PRODUCT_MASTER, PRODUCT_REQUESTED):
        if require_complete:
            raise HTTPException(400, "Sumber produk wajib dipilih.")
        return {"product_source": source or None, "product_id": None, "product_snapshot": {}}

    if source == PRODUCT_MASTER:
        product_id = str(payload.get("product_id") or "").strip()
        if not product_id:
            if require_complete:
                raise HTTPException(400, "Produk master wajib dipilih.")
            return {"product_source": source, "product_id": None, "product_snapshot": {}}
        master = await _resolve_master_item(db, product_id)
        if not master:
            raise HTTPException(400, "Produk master tidak ditemukan.")
        return {
            "product_source": PRODUCT_MASTER,
            "product_id": master.get("id") or master.get("accurate_item_id"),
            "product_snapshot": _product_snapshot_from_master(master),
        }

    snapshot = _product_snapshot_from_input(payload)
    if require_complete and not snapshot["name"]:
        raise HTTPException(400, "Nama requested product wajib diisi.")
    return {
        "product_source": PRODUCT_REQUESTED,
        "product_id": None,
        "product_snapshot": snapshot,
    }


def payload_from_stored(doc: dict) -> Dict[str, Any]:
    """Rebuild validator input from stored snapshots so omitted PATCH keys keep values."""
    customer = doc.get("customer_snapshot") or {}
    product = doc.get("product_snapshot") or {}
    return {
        "customer_source": doc.get("customer_source"),
        "customer_id": doc.get("customer_id"),
        "customer_name": customer.get("name") or "",
        "pic": customer.get("pic") or "",
        "whatsapp": customer.get("whatsapp") or customer.get("phone") or "",
        "phone": customer.get("phone") or "",
        "city": customer.get("city") or "",
        "product_source": doc.get("product_source"),
        "product_id": doc.get("product_id"),
        "product_name": product.get("name") or product.get("item_name") or "",
        "category": product.get("category") or "",
        "size": product.get("size") or "",
        "material": product.get("material") or "",
        "model": product.get("model") or product.get("specification") or "",
        "potential_qty": doc.get("potential_qty"),
        "frequency": doc.get("frequency") or "",
        "target_price": doc.get("target_price"),
        "needed_date": doc.get("needed_date") or "",
        "request_note": doc.get("request_note") or "",
        "request_channel": doc.get("request_channel") or "",
    }


def merge_draft_payload(doc: dict, payload: Optional[dict]) -> Dict[str, Any]:
    incoming = {
        key: value
        for key, value in (payload or {}).items()
        if key in OVERLAY_KEYS and key not in PROTECTED_PAYLOAD_KEYS
    }
    if "request_note" not in incoming and "note" in incoming:
        incoming["request_note"] = incoming["note"]
    if "size" not in incoming and "ukuran" in incoming:
        incoming["size"] = incoming["ukuran"]
    if "model" not in incoming and "specification" in incoming:
        incoming["model"] = incoming["specification"]

    base = payload_from_stored(doc)
    stored_customer_source = doc.get("customer_source")
    stored_product_source = doc.get("product_source")
    if "customer_source" in incoming and incoming.get("customer_source") != stored_customer_source:
        for key in CUSTOMER_IDENTITY_KEYS:
            if key not in incoming:
                base[key] = None if key == "customer_id" else ""
    if "product_source" in incoming and incoming.get("product_source") != stored_product_source:
        for key in PRODUCT_IDENTITY_KEYS:
            if key not in incoming:
                base[key] = None if key == "product_id" else ""
    base.update(incoming)
    return base


_VERSION_OMITTED = object()


def _authoritative_version(doc: dict):
    """Stored draft version. Missing or null is the legacy versionless row."""
    stored = doc.get(VERSION_FIELD)
    if isinstance(stored, bool) or not isinstance(stored, int) or stored < 0:
        return None
    return stored


def _expected_version(payload: Optional[dict]):
    """Optional client precondition.

    Omitted means the caller did not send one. That is not version 0.
    Explicit null is the public version of a legacy versionless row.
    """
    if not isinstance(payload, dict) or VERSION_FIELD not in payload:
        return _VERSION_OMITTED
    raw = payload.get(VERSION_FIELD)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise HTTPException(400, "doc_version tidak valid.")
    return raw


def _reject_client_precondition(doc: dict, expected) -> None:
    """Additional gate when the client supplies doc_version.

    Omitted doc_version does not enter this gate. The atomic CAS below still
    uses the version loaded for this request, so overlapping writes stay safe,
    while a later stale retry that omits the version can still commit.
    """
    if expected is _VERSION_OMITTED:
        return
    if _authoritative_version(doc) != expected:
        raise HTTPException(409, "Permintaan berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")


def _predicate_version(doc: dict, expected):
    if expected is _VERSION_OMITTED:
        return _authoritative_version(doc)
    return expected


def _draft_write_filter(doc: dict, user: dict, expected) -> Dict[str, Any]:
    filt: Dict[str, Any] = {
        "id": doc["id"],
        "created_by_id": actor_user_id(user),
        "status": STATUS_DRAFT,
    }
    version = _predicate_version(doc, expected)
    if version is None:
        filt["$or"] = [
            {VERSION_FIELD: {"$exists": False}},
            {VERSION_FIELD: None},
        ]
    else:
        filt[VERSION_FIELD] = version
    return filt


def _next_version(doc: dict, expected) -> int:
    version = _predicate_version(doc, expected)
    if version is None:
        return 1
    return int(version) + 1


async def _sync_after_draft_load() -> None:
    hook = after_draft_load
    if hook is not None:
        await hook()


async def _load_owned_draft(db, request_id: str, user: dict, *, on_submitted: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Permintaan produk tidak ditemukan.")
    if not _owned_by(doc, user):
        raise HTTPException(403, "Anda tidak dapat mengubah draft milik pengguna lain.")
    if doc.get("status") != STATUS_DRAFT:
        raise HTTPException(409, on_submitted)
    await _sync_after_draft_load()
    return doc


async def _apply_draft_write(
    db,
    doc: dict,
    user: dict,
    update_fields: Dict[str, Any],
    *,
    submitted: bool,
    expected,
) -> dict:
    ts = now_iso()
    set_fields = {
        **update_fields,
        VERSION_FIELD: _next_version(doc, expected),
        "updated_at": ts,
        "created_by_id": doc.get("created_by_id"),
        "created_by_snapshot": doc.get("created_by_snapshot"),
        "branch_id": doc.get("branch_id"),
        "branch_snapshot": doc.get("branch_snapshot"),
        "created_at": doc.get("created_at"),
        "request_number": doc.get("request_number"),
        "id": doc.get("id"),
    }
    if submitted:
        set_fields["status"] = STATUS_SUBMITTED
        set_fields["submitted_at"] = ts
    else:
        set_fields["status"] = STATUS_DRAFT

    saved = await db[COLLECTION].find_one_and_update(
        _draft_write_filter(doc, user, expected),
        {"$set": set_fields},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if saved:
        return saved

    current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Permintaan produk tidak ditemukan.")
    if not _owned_by(current, user):
        raise HTTPException(403, "Anda tidak dapat mengubah draft milik pengguna lain.")
    if current.get("status") != STATUS_DRAFT:
        raise HTTPException(
            409,
            "Permintaan ini sudah dikirim." if submitted else "Permintaan yang sudah dikirim tidak dapat diubah.",
        )
    raise HTTPException(409, "Permintaan berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")


def _build_potential(payload: dict, *, require_complete: bool) -> Dict[str, Any]:
    qty_raw = payload.get("potential_qty")
    freq_raw = payload.get("frequency")
    price_raw = payload.get("target_price")
    date_raw = payload.get("needed_date")
    note = str(payload.get("request_note") or payload.get("note") or "").strip()
    if len(note) > NOTE_MAX_LEN:
        raise HTTPException(400, f"Catatan maksimal {NOTE_MAX_LEN} karakter.")
    channel = _clean_text(payload.get("request_channel"), max_len=CHANNEL_MAX_LEN)
    frequency = _clean_text(freq_raw, max_len=FREQUENCY_MAX_LEN)

    out: Dict[str, Any] = {
        "request_note": note,
        "request_channel": channel,
        "frequency": frequency,
    }
    if require_complete or qty_raw not in (None, ""):
        out["potential_qty"] = _parse_positive_int(qty_raw, "Qty potensi") if require_complete or qty_raw not in (None, "") else None
    else:
        out["potential_qty"] = None
    if require_complete:
        if not frequency:
            raise HTTPException(400, "Frekuensi wajib diisi.")
        out["target_price"] = _parse_non_negative_int(price_raw, "Target harga")
        out["needed_date"] = _parse_date(date_raw, "Tanggal dibutuhkan")
    else:
        if price_raw not in (None, ""):
            out["target_price"] = _parse_non_negative_int(price_raw, "Target harga")
        else:
            out["target_price"] = None
        if date_raw not in (None, ""):
            out["needed_date"] = _parse_date(date_raw, "Tanggal dibutuhkan")
        else:
            out["needed_date"] = None
    return out


def receipt_view(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "request_number": doc.get("request_number"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "receipt_only": True,
    }


def status_label(status: Any) -> str:
    key = str(status or "").strip()
    return STATUS_LABELS.get(key, key or "—")


def is_post_submit_status(status: Any) -> bool:
    return str(status or "").strip() in POST_SUBMIT_STATUSES


def public_view(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "request_number": doc.get("request_number"),
        "status": doc.get("status"),
        "status_label": status_label(doc.get("status")),
        "customer_source": doc.get("customer_source"),
        "customer_id": doc.get("customer_id"),
        "customer_snapshot": dict(doc.get("customer_snapshot") or {}),
        "product_source": doc.get("product_source"),
        "product_id": doc.get("product_id"),
        "product_snapshot": dict(doc.get("product_snapshot") or {}),
        "potential_qty": doc.get("potential_qty"),
        "frequency": doc.get("frequency"),
        "target_price": doc.get("target_price"),
        "needed_date": doc.get("needed_date"),
        "request_note": doc.get("request_note") or "",
        "request_channel": doc.get("request_channel") or "",
        "purchase_actual_qty": doc.get("purchase_actual_qty"),
        "purchase_actual_price": doc.get("purchase_actual_price"),
        "created_by_id": doc.get("created_by_id"),
        "created_by_snapshot": dict(doc.get("created_by_snapshot") or {}),
        "branch_id": doc.get("branch_id"),
        "branch_snapshot": dict(doc.get("branch_snapshot") or {}),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "submitted_at": doc.get("submitted_at"),
        VERSION_FIELD: doc.get(VERSION_FIELD),
    }


def list_row(doc: dict) -> dict:
    customer = doc.get("customer_snapshot") or {}
    product = doc.get("product_snapshot") or {}
    creator = doc.get("created_by_snapshot") or {}
    branch = doc.get("branch_snapshot") or {}
    return {
        "id": doc.get("id"),
        "request_number": doc.get("request_number"),
        "status": doc.get("status"),
        "status_label": status_label(doc.get("status")),
        "submitted_at": doc.get("submitted_at"),
        "created_at": doc.get("created_at"),
        "customer_source": doc.get("customer_source"),
        "customer_name": customer.get("name") or "",
        "product_source": doc.get("product_source"),
        "product_name": product.get("item_name") or product.get("name") or "",
        "potential_qty": doc.get("potential_qty"),
        "created_by_name": creator.get("name") or "",
        "branch_name": branch.get("name") or "",
        "branch_id": doc.get("branch_id") or "",
    }


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _whole_number(raw: Any) -> Optional[int]:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and raw != int(raw):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _is_iso_date(value: Any) -> bool:
    text = str(value or "")
    if not _ISO_DATE.match(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def draft_completeness(doc: dict) -> dict:
    """Display-only kelengkapan. Mirrors the form required checks; does not write."""
    customer_source = doc.get("customer_source")
    product_source = doc.get("product_source")
    customer = doc.get("customer_snapshot") or {}
    product = doc.get("product_snapshot") or {}
    qty = _whole_number(doc.get("potential_qty"))
    price = _whole_number(doc.get("target_price"))
    if customer_source == CUSTOMER_MASTER:
        customer_identity = bool(doc.get("customer_id"))
    elif customer_source == CUSTOMER_NEW:
        customer_identity = bool(str(customer.get("name") or "").strip())
    else:
        customer_identity = False
    if product_source == PRODUCT_MASTER:
        product_identity = bool(doc.get("product_id"))
    elif product_source == PRODUCT_REQUESTED:
        product_identity = bool(str(product.get("name") or product.get("item_name") or "").strip())
    else:
        product_identity = False
    checks = (
        bool(customer_source),
        customer_identity,
        bool(product_source),
        product_identity,
        qty is not None and qty > 0,
        bool(str(doc.get("frequency") or "").strip()),
        price is not None and price >= 0,
        _is_iso_date(doc.get("needed_date")),
        len(str(doc.get("request_note") or "")) <= NOTE_MAX_LEN,
    )
    return {"done": sum(1 for ok in checks if ok), "total": len(checks)}


def draft_list_row(doc: dict) -> dict:
    row = list_row(doc)
    row["updated_at"] = doc.get("updated_at")
    row["completeness"] = draft_completeness(doc)
    return row


def context_view(user: dict, branch: dict) -> dict:
    actor = actor_snapshot(user)
    return {
        "created_by": actor,
        "branch": {
            "id": branch.get("id") or "",
            "name": branch.get("name") or "",
        },
    }


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("request_number", unique=True)
    await db[COLLECTION].create_index("created_by_id")
    await db[COLLECTION].create_index("status")
    await db[COLLECTION].create_index("created_at")
    await db[ACTIVITY_COLLECTION].create_index("id", unique=True)
    await db[ACTIVITY_COLLECTION].create_index([("request_id", 1), ("created_at", 1)])


def _owned_by(doc: dict, user: dict) -> bool:
    return str(doc.get("created_by_id") or "") == actor_user_id(user)


async def get_form_context(db, user: dict) -> dict:
    branch = await load_actor_branch(db, user)
    return context_view(user, branch)


async def create_draft(db, payload: Dict[str, Any], user: dict) -> dict:
    async with get_named_lock("product_request"):
        actor = actor_snapshot(user)
        uid = actor["id"]
        if not uid:
            raise HTTPException(400, "Identitas pengguna tidak valid.")
        branch = await load_actor_branch(db, user)
        customer = await _build_customer(db, payload or {}, require_complete=False)
        product = await _build_product(db, payload or {}, require_complete=False)
        potential = _build_potential(payload or {}, require_complete=False)
        ts = now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            "status": STATUS_DRAFT,
            **customer,
            **product,
            **potential,
            "created_by_id": uid,
            "created_by_snapshot": actor,
            "branch_id": branch["id"],
            "branch_snapshot": branch,
            "created_at": ts,
            "updated_at": ts,
            "submitted_at": None,
            VERSION_FIELD: 0,
        }

        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "request_number", PREFIX_REQUEST)

        saved = await insert_with_unique_number(db, COLLECTION, "request_number", doc, _alloc)
        return public_view(saved)


async def update_draft(db, request_id: str, payload: Dict[str, Any], user: dict) -> dict:
    doc = await _load_owned_draft(
        db,
        request_id,
        user,
        on_submitted="Permintaan yang sudah dikirim tidak dapat diubah.",
    )
    expected = _expected_version(payload)
    _reject_client_precondition(doc, expected)
    merged = merge_draft_payload(doc, payload)
    customer = await _build_customer(db, merged, require_complete=False)
    product = await _build_product(db, merged, require_complete=False)
    potential = _build_potential(merged, require_complete=False)
    saved = await _apply_draft_write(
        db,
        doc,
        user,
        {**customer, **product, **potential},
        submitted=False,
        expected=expected,
    )
    return public_view(saved)


async def submit_draft(db, request_id: str, payload: Optional[Dict[str, Any]], user: dict) -> dict:
    doc = await _load_owned_draft(
        db,
        request_id,
        user,
        on_submitted="Permintaan ini sudah dikirim.",
    )
    expected = _expected_version(payload)
    _reject_client_precondition(doc, expected)
    merged = merge_draft_payload(doc, payload)
    customer = await _build_customer(db, merged, require_complete=True)
    product = await _build_product(db, merged, require_complete=True)
    potential = _build_potential(merged, require_complete=True)
    saved = await _apply_draft_write(
        db,
        doc,
        user,
        {**customer, **product, **potential},
        submitted=True,
        expected=expected,
    )
    await _append_activity(
        db,
        request_id=saved["id"],
        activity_type=ACTIVITY_SUBMITTED,
        user=user,
        note="",
        from_status=STATUS_DRAFT,
        to_status=STATUS_SUBMITTED,
    )
    await _emit_submitted_notifications(db, saved, user)
    return receipt_view(saved)


def can_read_all(perms: set) -> bool:
    return "product_request.read_all" in perms


def can_create(perms: set) -> bool:
    return "product_request.create" in perms


def can_read_own_draft(perms: set) -> bool:
    return "product_request.read_own_draft" in perms or "product_request.create" in perms


def can_feedback(perms: set) -> bool:
    return "product_request.feedback" in perms


def can_update_status(perms: set) -> bool:
    return "product_request.update_status" in perms


async def get_request(db, request_id: str, user: dict, perms: set) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"request_number": str(request_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Permintaan produk tidak ditemukan.")
    owned = _owned_by(doc, user)
    status = doc.get("status")
    if status == STATUS_DRAFT:
        if owned and can_read_own_draft(perms):
            return public_view(doc)
        raise HTTPException(403, "Anda tidak dapat membaca draft ini.")
    if is_post_submit_status(status):
        # Any authenticated caller may read submitted/follow-up requests.
        return public_view(doc)
    raise HTTPException(403, "Akses ditolak.")


VIEW_SUBMITTED = "submitted"
VIEW_OWN_DRAFTS = "own_drafts"


async def _submitted_rows(db) -> List[dict]:
    rows = await db[COLLECTION].find(
        {"status": {"$in": list(POST_SUBMIT_STATUSES)}},
        {"_id": 0},
    ).sort([("submitted_at", -1), ("updated_at", -1)]).to_list(500)
    return [list_row(r) for r in rows]


async def _own_draft_rows(db, user: dict) -> List[dict]:
    rows = await db[COLLECTION].find(
        {"created_by_id": actor_user_id(user), "status": STATUS_DRAFT},
        {"_id": 0},
    ).sort("updated_at", -1).to_list(200)
    return [draft_list_row(r) for r in rows]


async def list_requests(db, user: dict, perms: set, view: Optional[str] = None) -> List[dict]:
    """Submitted/follow-up list is open to any authenticated user.

    Own drafts remain a separate scope (created_by_id + status=draft).
    Drafts of other users are never included.
    """
    scope = (view or "").strip() or None
    if scope == VIEW_SUBMITTED:
        return await _submitted_rows(db)
    if scope == VIEW_OWN_DRAFTS:
        if not (can_create(perms) or can_read_own_draft(perms)):
            raise HTTPException(403, "Draft sendiri memerlukan product_request.create atau read_own_draft.")
        return await _own_draft_rows(db, user)
    if scope is not None:
        raise HTTPException(400, "Parameter view tidak dikenal.")
    # Default: submitted/follow-up for everyone; creators still use view=own_drafts for drafts.
    return await _submitted_rows(db)


def public_activity(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "request_id": doc.get("request_id"),
        "type": doc.get("type"),
        "note": doc.get("note") or "",
        "from_status": doc.get("from_status"),
        "to_status": doc.get("to_status"),
        "from_status_label": status_label(doc.get("from_status")) if doc.get("from_status") else None,
        "to_status_label": status_label(doc.get("to_status")) if doc.get("to_status") else None,
        "actual_qty": doc.get("actual_qty"),
        "actual_price": doc.get("actual_price"),
        "actor_id": doc.get("actor_id"),
        "actor_snapshot": dict(doc.get("actor_snapshot") or {}),
        "created_at": doc.get("created_at"),
    }


async def _append_activity(
    db,
    *,
    request_id: str,
    activity_type: str,
    user: dict,
    note: str = "",
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    actual_qty: Any = None,
    actual_price: Any = None,
) -> dict:
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "request_id": str(request_id),
        "type": activity_type,
        "note": str(note or "")[:FEEDBACK_MAX_LEN],
        "from_status": from_status,
        "to_status": to_status,
        "actual_qty": actual_qty,
        "actual_price": actual_price,
        "actor_id": actor.get("id") or "",
        "actor_snapshot": actor,
        "created_at": now_iso(),
    }
    await db[ACTIVITY_COLLECTION].insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def deep_link_for_request(request_id: str) -> str:
    # Detail route shares Status & Feedback with the list drawer.
    return f"/penjualan/permintaan-produk/{request_id}"


async def _load_role_permission_map(db) -> Dict[str, List[str]]:
    rows = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(200)
    out: Dict[str, List[str]] = {}
    for row in rows:
        role = str((row or {}).get("role") or "").strip()
        if not role:
            continue
        perms = (row or {}).get("permissions")
        if isinstance(perms, list):
            out[role] = [str(p) for p in perms if p]
    return out


async def resolve_read_all_recipient_user_ids(db, *, exclude_user_id: str = "") -> List[str]:
    """Active users whose effective permissions include product_request.read_all."""
    from permissions import effective_permissions

    role_map = await _load_role_permission_map(db)
    role_defaults_override = role_map or None
    cursor = db.users.find(
        {"status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1, "role": 1, "permissions_override": 1},
    )
    users = await cursor.to_list(500)
    skip = str(exclude_user_id or "").strip()
    seen = set()
    out: List[str] = []
    for row in users:
        uid = str((row or {}).get("id") or "").strip()
        if not uid or uid == skip or uid in seen:
            continue
        role = str((row or {}).get("role") or "").strip()
        override = (row or {}).get("permissions_override") or {}
        if not isinstance(override, dict):
            override = {}
        perms = effective_permissions(role, override, role_defaults_override)
        if "product_request.read_all" not in perms:
            continue
        seen.add(uid)
        out.append(uid)
    return out


async def _notify_users(
    db,
    *,
    user_ids: Sequence[str],
    event_type: str,
    dedupe_id: str,
    request: dict,
    title: str,
    body: str,
    decision: str,
) -> None:
    from services.notifications import create_notification

    link = deep_link_for_request(str(request.get("id") or ""))
    order_id = str(request.get("id") or "")
    po_number = str(request.get("request_number") or "") or None
    seen = set()
    for uid in user_ids:
        key = str(uid or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            await create_notification(
                db,
                user_id=key,
                event_type=event_type,
                approval_id=dedupe_id,
                order_id=order_id,
                po_number=po_number,
                decision=decision,
                title=title,
                body=body,
                deep_link=link,
            )
        except Exception:
            logging.getLogger("product_requests").exception(
                "product_request_notify_failed event=%s user=%s", event_type, key
            )


async def _emit_submitted_notifications(db, request: dict, user: dict) -> None:
    try:
        recipients = await resolve_read_all_recipient_user_ids(
            db, exclude_user_id=actor_user_id(user)
        )
        if not recipients:
            return
        number = request.get("request_number") or request.get("id")
        customer = (request.get("customer_snapshot") or {}).get("name") or ""
        title = "Permintaan produk baru"
        body = f"{number} dikirim"
        if customer:
            body = f"{number} — {customer} dikirim"
        await _notify_users(
            db,
            user_ids=recipients,
            event_type=EVENT_SUBMITTED,
            dedupe_id=f"submit:{request.get('id')}",
            request=request,
            title=title,
            body=body,
            decision=STATUS_SUBMITTED,
        )
    except Exception:
        logging.getLogger("product_requests").exception(
            "product_request_submit_notify_failed id=%s", (request or {}).get("id")
        )


async def _emit_creator_notification(
    db,
    request: dict,
    *,
    event_type: str,
    dedupe_id: str,
    title: str,
    body: str,
    decision: str,
    actor_id: str,
) -> None:
    try:
        creator = str(request.get("created_by_id") or "").strip()
        if not creator or creator == actor_id:
            return
        await _notify_users(
            db,
            user_ids=[creator],
            event_type=event_type,
            dedupe_id=dedupe_id,
            request=request,
            title=title,
            body=body,
            decision=decision,
        )
    except Exception:
        logging.getLogger("product_requests").exception(
            "product_request_creator_notify_failed id=%s", (request or {}).get("id")
        )


def _post_submit_write_filter(doc: dict, expected) -> Dict[str, Any]:
    filt: Dict[str, Any] = {
        "id": doc["id"],
        "status": {"$in": list(POST_SUBMIT_STATUSES)},
    }
    version = _predicate_version(doc, expected)
    if version is None:
        filt["$or"] = [
            {VERSION_FIELD: {"$exists": False}},
            {VERSION_FIELD: None},
        ]
    else:
        filt[VERSION_FIELD] = version
    return filt


async def _load_post_submit(db, request_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Permintaan produk tidak ditemukan.")
    if doc.get("status") == STATUS_DRAFT:
        raise HTTPException(409, "Draft harus dikirim sebelum feedback atau perubahan status.")
    if not is_post_submit_status(doc.get("status")):
        raise HTTPException(409, "Status permintaan tidak memungkinkan perubahan ini.")
    await _sync_after_draft_load()
    return doc


async def _apply_post_submit_write(
    db,
    doc: dict,
    update_fields: Dict[str, Any],
    expected,
) -> dict:
    ts = now_iso()
    set_fields = {
        **update_fields,
        VERSION_FIELD: _next_version(doc, expected),
        "updated_at": ts,
    }
    saved = await db[COLLECTION].find_one_and_update(
        _post_submit_write_filter(doc, expected),
        {"$set": set_fields},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if saved:
        return saved
    current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Permintaan produk tidak ditemukan.")
    if current.get("status") == STATUS_DRAFT:
        raise HTTPException(409, "Draft harus dikirim sebelum feedback atau perubahan status.")
    raise HTTPException(409, "Permintaan berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")


async def list_activities(db, request_id: str, user: dict, perms: set) -> List[dict]:
    # Reuse get_request access rules (draft owner-only; submitted open).
    await get_request(db, request_id, user, perms)
    rows = await db[ACTIVITY_COLLECTION].find(
        {"request_id": str(request_id)},
        {"_id": 0},
    ).sort("created_at", 1).to_list(500)
    return [public_activity(r) for r in rows]


async def add_feedback(
    db,
    request_id: str,
    payload: Optional[Dict[str, Any]],
    user: dict,
    perms: set,
) -> dict:
    if not can_feedback(perms):
        raise HTTPException(403, "Permission product_request.feedback diperlukan")
    doc = await _load_post_submit(db, request_id)
    expected = _expected_version(payload)
    _reject_client_precondition(doc, expected)
    note = str((payload or {}).get("note") or (payload or {}).get("feedback") or "").strip()
    if not note:
        raise HTTPException(400, "Feedback wajib diisi.")
    if len(note) > FEEDBACK_MAX_LEN:
        raise HTTPException(400, f"Feedback maksimal {FEEDBACK_MAX_LEN} karakter.")
    # Feedback must not change status — only bump version / updated_at.
    saved = await _apply_post_submit_write(db, doc, {}, expected)
    activity = await _append_activity(
        db,
        request_id=saved["id"],
        activity_type=ACTIVITY_FEEDBACK,
        user=user,
        note=note,
        from_status=saved.get("status"),
        to_status=saved.get("status"),
    )
    number = saved.get("request_number") or saved.get("id")
    await _emit_creator_notification(
        db,
        saved,
        event_type=EVENT_FEEDBACK,
        dedupe_id=f"feedback:{activity['id']}",
        title="Feedback permintaan produk",
        body=f"{number}: {note[:160]}",
        decision=ACTIVITY_FEEDBACK,
        actor_id=actor_user_id(user),
    )
    return {
        "request": public_view(saved),
        "activity": public_activity(activity),
    }


def _optional_non_negative_int(raw: Any, field_name: str) -> Optional[int]:
    if raw is None or raw == "":
        return None
    return _parse_non_negative_int(raw, field_name)


async def update_status(
    db,
    request_id: str,
    payload: Optional[Dict[str, Any]],
    user: dict,
    perms: set,
) -> dict:
    if not can_update_status(perms):
        raise HTTPException(403, "Permission product_request.update_status diperlukan")
    doc = await _load_post_submit(db, request_id)
    expected = _expected_version(payload)
    _reject_client_precondition(doc, expected)
    data = payload or {}
    new_status = str(data.get("status") or "").strip()
    if new_status not in POST_SUBMIT_STATUSES:
        raise HTTPException(400, "Status tidak valid.")
    if new_status == STATUS_DRAFT:
        raise HTTPException(400, "Tidak dapat mengembalikan ke draft.")
    note = str(data.get("note") or data.get("status_note") or "").strip()
    if new_status in NOTE_REQUIRED_STATUSES and not note:
        raise HTTPException(400, "Catatan wajib untuk status Pending atau Ditolak.")
    if len(note) > FEEDBACK_MAX_LEN:
        raise HTTPException(400, f"Catatan maksimal {FEEDBACK_MAX_LEN} karakter.")

    update_fields: Dict[str, Any] = {"status": new_status}
    actual_qty = None
    actual_price = None
    if new_status == STATUS_PURCHASED:
        if "actual_qty" in data or "purchase_actual_qty" in data:
            actual_qty = _optional_non_negative_int(
                data.get("actual_qty", data.get("purchase_actual_qty")),
                "Jumlah aktual",
            )
            update_fields["purchase_actual_qty"] = actual_qty
        if "actual_price" in data or "purchase_actual_price" in data:
            actual_price = _optional_non_negative_int(
                data.get("actual_price", data.get("purchase_actual_price")),
                "Harga pembelian",
            )
            update_fields["purchase_actual_price"] = actual_price

    from_status = doc.get("status")
    saved = await _apply_post_submit_write(db, doc, update_fields, expected)
    # Guard: potential snapshot fields must remain intact.
    if saved.get("potential_qty") != doc.get("potential_qty"):
        raise HTTPException(500, "Integritas potential_qty gagal dijaga.")
    if saved.get("target_price") != doc.get("target_price"):
        raise HTTPException(500, "Integritas target_price gagal dijaga.")

    activity = await _append_activity(
        db,
        request_id=saved["id"],
        activity_type=ACTIVITY_STATUS,
        user=user,
        note=note,
        from_status=from_status,
        to_status=new_status,
        actual_qty=actual_qty,
        actual_price=actual_price,
    )
    number = saved.get("request_number") or saved.get("id")
    label = status_label(new_status)
    body = f"{number} status menjadi {label}"
    if note:
        body = f"{body}. {note[:120]}"
    await _emit_creator_notification(
        db,
        saved,
        event_type=EVENT_STATUS,
        dedupe_id=f"status:{activity['id']}",
        title="Status permintaan produk diperbarui",
        body=body,
        decision=new_status,
        actor_id=actor_user_id(user),
    )
    return {
        "request": public_view(saved),
        "activity": public_activity(activity),
    }
