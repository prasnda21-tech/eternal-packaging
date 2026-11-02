"""Pra PO — CRM draft/submit/return/verify gate before operational Pesanan Penjualan.

Pra PO documents live in collection ``pra_pos`` and never enter production, delivery,
invoice, stock/HPP, or Accurate until Admin verifies and an ``orders`` document is created.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.purchase_common import actor_snapshot, actor_user_id, get_named_lock, now_iso

COLLECTION = "pra_pos"
HISTORY_COLLECTION = "pra_po_history"
PREFIX = "PRA"
VERSION_FIELD = "doc_version"

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_RETURNED = "returned"
STATUS_RESUBMITTED = "resubmitted"
STATUS_VERIFYING = "verifying"
STATUS_VERIFIED = "verified"

STATUS_LABELS = {
    STATUS_DRAFT: "Draft",
    STATUS_SUBMITTED: "Diajukan",
    STATUS_RETURNED: "Dikembalikan",
    STATUS_RESUBMITTED: "Diajukan Ulang",
    STATUS_VERIFYING: "Sedang diverifikasi",
    STATUS_VERIFIED: "Disetujui",
}

EDITABLE_STATUSES = frozenset({STATUS_DRAFT, STATUS_RETURNED})
SUBMITTABLE_STATUSES = frozenset({STATUS_DRAFT, STATUS_RETURNED})
VERIFYABLE_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_RESUBMITTED})
# Claimable includes in-flight verifying so a crashed worker can resume safely.
CLAIMABLE_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_RESUBMITTED, STATUS_VERIFYING})

PERM_CREATE = "pra_po.create"
PERM_EDIT_OWN = "pra_po.edit_own"
PERM_VIEW_OWN = "pra_po.view_own"
PERM_VIEW_ALL = "pra_po.view_all"
PERM_SUBMIT = "pra_po.submit"
PERM_VERIFY = "pra_po.verify"
PERM_RETURN = "pra_po.return"
PERM_AUDIT = "pra_po.audit.view"

logger = logging.getLogger("pra_po")


def can_create(perms: set) -> bool:
    return PERM_CREATE in perms


def can_edit_own(perms: set) -> bool:
    return PERM_EDIT_OWN in perms or PERM_CREATE in perms


def can_view_own(perms: set) -> bool:
    return PERM_VIEW_OWN in perms or PERM_CREATE in perms or PERM_EDIT_OWN in perms


def can_view_all(perms: set) -> bool:
    return PERM_VIEW_ALL in perms or PERM_VERIFY in perms or PERM_RETURN in perms


def can_submit(perms: set) -> bool:
    return PERM_SUBMIT in perms or PERM_CREATE in perms


def can_verify(perms: set) -> bool:
    return PERM_VERIFY in perms


def can_return(perms: set) -> bool:
    return PERM_RETURN in perms


def can_audit(perms: set) -> bool:
    return PERM_AUDIT in perms or can_view_all(perms) or can_view_own(perms)


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
LIST_SORT = [("updated_at", -1), ("id", -1)]


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("number", unique=True)
    await db[COLLECTION].create_index([("created_by_user_id", 1), ("updated_at", -1)])
    await db[COLLECTION].create_index([("status", 1), ("updated_at", -1)])
    await db[COLLECTION].create_index([("updated_at", -1), ("id", -1)])
    await db[COLLECTION].create_index("customer")
    await db[COLLECTION].create_index("sales_order_id", sparse=True)
    await db[HISTORY_COLLECTION].create_index([("pra_po_id", 1), ("at", -1)])
    # One operational SO per Pra PO — DB-level guard against concurrent verify races.
    await db.orders.create_index("pra_po_id", unique=True, sparse=True)


def _today_str_po() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%y")


async def _next_pra_number(db) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    stem = f"{PREFIX}-{day}-"
    cursor = db[COLLECTION].find({"number": {"$regex": f"^{re.escape(stem)}\\d+$"}}, {"number": 1, "_id": 0})
    rows = await cursor.to_list(2000)
    max_seq = 0
    for row in rows:
        suffix = str(row.get("number") or "")[len(stem):]
        try:
            max_seq = max(max_seq, int(suffix))
        except ValueError:
            continue
    return f"{stem}{max_seq + 1:03d}"


async def _next_po_number(db) -> str:
    today_str = _today_str_po()
    prefix = f"PO/{today_str}/"
    regex = f"^{re.escape(prefix)}\\d+"
    cursor = db.orders.find({"po_number": {"$regex": regex}}, {"po_number": 1, "_id": 0})
    existing = await cursor.to_list(1000)
    max_seq = 0
    for doc in existing:
        pn = str(doc.get("po_number") or "")
        suffix = pn[len(prefix):]
        m = re.match(r"^(\d+)", suffix)
        if m:
            try:
                max_seq = max(max_seq, int(m.group(1)))
            except ValueError:
                pass
    return f"{prefix}{max_seq + 1:03d}"


def _history_entry(*, action: str, user: dict, note: str = "", before: Any = None, after: Any = None) -> dict:
    actor = actor_snapshot(user)
    return {
        "id": str(uuid.uuid4()),
        "action": action,
        "at": now_iso(),
        "by_id": actor.get("id"),
        "by_name": actor.get("name"),
        "by_email": actor.get("email"),
        "by_role": actor.get("role"),
        "note": note or "",
        "before": before,
        "after": after,
    }


async def _append_history(db, pra_po_id: str, entry: dict) -> None:
    entry = dict(entry)
    entry["pra_po_id"] = pra_po_id
    await db[HISTORY_COLLECTION].insert_one(entry)


def public_view(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["status_label"] = STATUS_LABELS.get(str(out.get("status") or ""), out.get("status"))
    return out


def _require_owner(doc: dict, user: dict) -> None:
    uid = actor_user_id(user)
    if str(doc.get("created_by_user_id") or "") != uid:
        raise HTTPException(403, "Pra PO ini bukan milik Anda.")


def _parse_unit_price(raw: Any) -> Optional[float]:
    """Parse selling price; blank is not zero. Returns None when missing/invalid."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        amount = float(raw)
        if not (amount == amount) or amount == float("inf") or amount == float("-inf"):  # NaN/inf
            return None
        return round(amount * 100) / 100
    text = str(raw).strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return None
    if not (amount == amount) or amount == float("inf") or amount == float("-inf"):
        return None
    return round(amount * 100) / 100


def _normalize_lines(raw_lines: Any) -> List[dict]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(400, "Minimal 1 line item.")
    out = []
    seen = set()
    for i, li in enumerate(raw_lines):
        if not isinstance(li, dict):
            raise HTTPException(400, f"Line item #{i + 1} tidak valid.")
        name = str(li.get("name") or "").strip()
        if not name:
            raise HTTPException(400, f"Line item #{i + 1}: Nama barang/jasa wajib diisi.")
        try:
            qty = int(li.get("quantity"))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            raise HTTPException(400, f"Line item #{i + 1}: Quantity harus > 0.")
        line_id = str(li.get("line_id") or "").strip() or f"ln-{uuid.uuid4().hex[:12]}"
        if line_id in seen:
            raise HTTPException(400, f"Line item #{i + 1}: line_id duplikat.")
        seen.add(line_id)
        # Draft remains permissive: store parsed price when valid, else keep None.
        parsed_price = _parse_unit_price(li.get("unit_price"))
        proc = li.get("production_process")
        if proc not in ("print", "non_print", None):
            proc = str(proc or "").strip() or None
        row = {
            "line_id": line_id,
            "name": name,
            "quantity": qty,
            "unit": str(li.get("unit") or "pcs").strip() or "pcs",
            "item_code": str(li.get("item_code") or "").strip() or None,
            "color": str(li.get("color") or "").strip() or None,
            "notes": str(li.get("notes") or "").strip() or None,
            "master_item_id": str(li.get("master_item_id") or "").strip() or None,
            "accurate_item_id": str(li.get("accurate_item_id") or "").strip() or None,
            "production_process": proc if proc in ("print", "non_print") else (proc or "print"),
            "unit_price": parsed_price,
            "price_source": li.get("price_source"),
            "price_review": bool(li.get("price_review")) if li.get("price_review") is not None else None,
            "item_type": str(li.get("item_type") or "").strip() or None,
        }
        parent = str(li.get("parent_line_id") or "").strip()
        if parent:
            row["parent_line_id"] = parent
        if li.get("parent_line_idx") is not None:
            try:
                row["parent_line_idx"] = int(li.get("parent_line_idx"))
            except (TypeError, ValueError):
                pass
        out.append(row)
    return out


async def _validate_for_submit(db, doc: dict) -> None:
    """Strict checks before submit / resubmit. Draft create/update stays permissive."""
    customer = str(doc.get("customer") or "").strip()
    if not customer:
        raise HTTPException(400, "Customer wajib diisi.")

    customer_id = str(doc.get("customer_id") or "").strip()
    if customer_id:
        master = await db.sales_customers.find_one({"id": customer_id}, {"_id": 1, "id": 1})
        if not master:
            raise HTTPException(400, "Customer master tidak valid.")
    else:
        if not str(doc.get("customer_phone") or "").strip():
            raise HTTPException(400, "Pelanggan baru: No. HP / WhatsApp wajib diisi.")
        if not str(doc.get("address") or "").strip():
            raise HTTPException(400, "Pelanggan baru: Alamat lengkap pengiriman wajib diisi.")

    lines = doc.get("line_items") or []
    if not isinstance(lines, list) or not lines:
        raise HTTPException(400, "Minimal harus ada 1 line item.")

    for i, li in enumerate(lines):
        if not isinstance(li, dict):
            raise HTTPException(400, f"Line item #{i + 1} tidak valid.")
        if not str(li.get("name") or "").strip():
            raise HTTPException(400, f"Line item #{i + 1}: Nama barang/jasa wajib diisi.")
        mid = str(li.get("master_item_id") or "").strip()
        aid = str(li.get("accurate_item_id") or "").strip()
        if not mid and not aid:
            raise HTTPException(
                400,
                f"Line item #{i + 1}: Master Produk wajib dipilih "
                "(master_item_id atau accurate_item_id).",
            )
        try:
            qty = int(li.get("quantity"))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            raise HTTPException(400, f"Line item #{i + 1}: Quantity harus > 0.")
        unit = str(li.get("unit") or "").strip()
        if not unit:
            raise HTTPException(400, f"Line item #{i + 1}: Satuan wajib diisi.")
        price = _parse_unit_price(li.get("unit_price"))
        if price is None or price <= 0:
            raise HTTPException(
                400,
                f"Line item #{i + 1}: Harga jual wajib angka lebih besar dari 0.",
            )


async def _coerce_sales_payload(db, payload: dict, user: dict, existing: Optional[dict] = None) -> None:
    """Keep stored Sales unchanged. A new choice must be an active catalog row.

    Omitting both keys leaves the document alone. Resending the stored id and
    label, including an inactive or free-text historical value, does not
    revalidate or rename it. A different master id is checked against the
    caller's active branch catalog and stored with the canonical sales_name.
    """
    if "sales" not in payload and "sales_master_id" not in payload:
        return
    existing = existing or {}
    stored_id = str(existing.get("sales_master_id") or "").strip()
    stored_label = str(existing.get("sales") or "").strip()

    if "sales_master_id" in payload:
        raw_id = payload.get("sales_master_id")
        sent_id = "" if raw_id is None else str(raw_id).strip()
    else:
        sent_id = stored_id
    if "sales" in payload:
        raw_label = payload.get("sales")
        sent_label = "" if raw_label is None else str(raw_label).strip()
    else:
        sent_label = stored_label

    if sent_id == stored_id and sent_label == stored_label:
        return
    if not sent_id and not sent_label:
        payload["sales"] = None
        payload["sales_master_id"] = None
        return
    if sent_id and sent_id == stored_id:
        payload["sales"] = stored_label or None
        payload["sales_master_id"] = stored_id
        return
    if sent_id:
        from services.sales_master import require_active_sales
        master = await require_active_sales(db, sent_id, user=user)
        payload["sales_master_id"] = master["id"]
        payload["sales"] = str(master.get("sales_name") or "").strip() or None
        return
    raise HTTPException(400, "Sales harus dipilih dari Master Sales.")


def _apply_payload(doc: dict, payload: dict) -> None:
    if "customer" in payload:
        cust = str(payload.get("customer") or "").strip()
        if not cust:
            raise HTTPException(400, "Customer wajib diisi.")
        doc["customer"] = cust
    for key in (
        "customer_id", "customer_phone", "customer_pic", "customer_email", "customer_city",
        "outlet", "recipient", "address", "delivery_zone", "payment_terms",
        "sales", "sales_master_id", "expedition", "estimated_delivery", "deadline",
        "order_date", "notes", "po_number_hint",
    ):
        if key in payload:
            val = payload.get(key)
            if val is None:
                doc[key] = None
            else:
                doc[key] = str(val).strip() if not isinstance(val, (int, float, bool)) else val
    if "line_items" in payload:
        doc["line_items"] = _normalize_lines(payload.get("line_items"))
        doc["total_quantity"] = sum(int(x.get("quantity") or 0) for x in doc["line_items"])
        doc["item_count"] = len(doc["line_items"])


async def create_draft(db, payload: dict, user: dict) -> dict:
    from services.operational_branch import resolve_write_branch
    operational_branch_id = await resolve_write_branch(db, user, payload.get("operational_branch_id"))
    actor = actor_snapshot(user)
    uid = actor.get("id")
    if not uid:
        raise HTTPException(401, "Sesi tidak valid")
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "number": await _next_pra_number(db),
        "status": STATUS_DRAFT,
        "created_by_user_id": uid,
        "created_by_snapshot": actor,
        "operational_branch_id": operational_branch_id,
        "branch_id": str(user.get("branch_id") or user.get("cabang_id") or "").strip() or None,
        "customer": "",
        "line_items": [],
        "notes": "",
        "return_reason": None,
        "returned_at": None,
        "sales_order_id": None,
        "sales_order_po_number": None,
        "verified_at": None,
        "verified_by": None,
        "submitted_at": None,
        VERSION_FIELD: 1,
        "created_at": now,
        "updated_at": now,
    }
    payload = dict(payload or {})
    await _coerce_sales_payload(db, payload, user)
    _apply_payload(doc, payload)
    if not doc.get("customer"):
        raise HTTPException(400, "Customer wajib diisi.")
    if not doc.get("line_items"):
        raise HTTPException(400, "Minimal 1 line item.")
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError:
        doc["number"] = await _next_pra_number(db)
        await db[COLLECTION].insert_one(dict(doc))
    doc.pop("_id", None)
    await _append_history(db, doc["id"], _history_entry(action="create", user=user, after={"status": STATUS_DRAFT}))
    return public_view(doc)


async def get_pra_po(db, pra_po_id: str, user: dict, perms: set) -> dict:
    doc = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pra PO tidak ditemukan")
    from services.operational_branch import require_document_branch
    await require_document_branch(db, user, doc)
    if can_view_all(perms):
        return public_view(doc)
    if can_view_own(perms):
        _require_owner(doc, user)
        return public_view(doc)
    raise HTTPException(403, "Tidak berwenang melihat Pra PO")


def _list_visibility_query(user: dict, perms: set, *, view: Optional[str] = None) -> Dict[str, Any]:
    """Permission-scoped base query (no search/status/pagination)."""
    query: Dict[str, Any] = {}
    view_norm = str(view or "").strip().lower()
    if view_norm in ("queue", "verification"):
        if not can_view_all(perms):
            raise HTTPException(403, "Tidak berwenang melihat antrean verifikasi")
        # Visibility for queue view still unrestricted among view_all; status applied separately.
        return query
    if view_norm in ("own", "mine"):
        if not can_view_own(perms) and not can_view_all(perms):
            raise HTTPException(403, "Tidak berwenang melihat Pra PO")
        # own/mine always restricts to actor even for admins who request own.
        query["created_by_user_id"] = actor_user_id(user)
        return query
    # Default / empty view: admins see all; CRM sees own.
    if can_view_all(perms):
        return query
    if can_view_own(perms):
        query["created_by_user_id"] = actor_user_id(user)
        return query
    raise HTTPException(403, "Tidak berwenang melihat Pra PO")


def _status_clause(status: Optional[str], *, view: Optional[str] = None) -> Dict[str, Any]:
    view_norm = str(view or "").strip().lower()
    st = str(status or "").strip().lower()
    if view_norm in ("queue", "verification") and not st:
        return {"status": {"$in": [STATUS_SUBMITTED, STATUS_RESUBMITTED]}}
    if not st or st in ("all", "*"):
        return {}
    if st in ("queue", "verification", "antrean"):
        return {"status": {"$in": [STATUS_SUBMITTED, STATUS_RESUBMITTED]}}
    return {"status": st}


def _search_clause(q: Optional[str]) -> Dict[str, Any]:
    text = str(q or "").strip()
    if not text:
        return {}
    # Escape user input for regex; case-insensitive match on key display fields.
    pat = re.escape(text)
    rx = {"$regex": pat, "$options": "i"}
    return {
        "$or": [
            {"number": rx},
            {"customer": rx},
            {"sales_order_po_number": rx},
            {"notes": rx},
        ]
    }


def _merge_query(*parts: Dict[str, Any]) -> Dict[str, Any]:
    clauses = [p for p in parts if p]
    if not clauses:
        return {}
    if len(clauses) == 1:
        return dict(clauses[0])
    return {"$and": clauses}


def _clamp_page(page: Optional[int], page_size: Optional[int]) -> tuple[int, int]:
    try:
        p = int(page) if page is not None else 1
    except (TypeError, ValueError):
        p = 1
    try:
        ps = int(page_size) if page_size is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        ps = DEFAULT_PAGE_SIZE
    if p < 1:
        p = 1
    if ps < 1:
        ps = DEFAULT_PAGE_SIZE
    if ps > MAX_PAGE_SIZE:
        ps = MAX_PAGE_SIZE
    return p, ps


async def list_pra_pos(
    db,
    user: dict,
    perms: set,
    *,
    view: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Paginated Pra PO list. Search/status applied in Mongo before skip/limit.

    Response shape is additive/compatible: always includes ``items``. Also returns
    ``total``, ``page``, ``page_size``, and scoped ``counts`` (all/draft/queue/
    returned/verified) under the same visibility + search filter.
    """
    page_n, size_n = _clamp_page(page, page_size)
    visibility = _list_visibility_query(user, perms, view=view)
    search = _search_clause(q)
    status_filter = _status_clause(status, view=view)

    from services.operational_branch import operational_query
    scoped = _merge_query(_merge_query(visibility, search), await operational_query(db, user))
    item_query = _merge_query(scoped, status_filter)

    total = int(await db[COLLECTION].count_documents(item_query))
    skip = (page_n - 1) * size_n
    cursor = db[COLLECTION].find(item_query, {"_id": 0}).sort(LIST_SORT).skip(skip).limit(size_n)
    rows = await cursor.to_list(size_n)
    items = [public_view(r) for r in rows]

    counts = {
        "all": int(await db[COLLECTION].count_documents(scoped)),
        "draft": int(await db[COLLECTION].count_documents(_merge_query(scoped, {"status": STATUS_DRAFT}))),
        "queue": int(await db[COLLECTION].count_documents(
            _merge_query(scoped, {"status": {"$in": [STATUS_SUBMITTED, STATUS_RESUBMITTED]}})
        )),
        "returned": int(await db[COLLECTION].count_documents(_merge_query(scoped, {"status": STATUS_RETURNED}))),
        "verified": int(await db[COLLECTION].count_documents(_merge_query(scoped, {"status": STATUS_VERIFIED}))),
    }

    return {
        "items": items,
        "total": total,
        "page": page_n,
        "page_size": size_n,
        "counts": counts,
    }


async def update_draft(db, pra_po_id: str, payload: dict, user: dict, perms: set) -> dict:
    if not can_edit_own(perms):
        raise HTTPException(403, "Tidak berwenang mengubah Pra PO")
    doc = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pra PO tidak ditemukan")
    from services.operational_branch import require_document_branch
    await require_document_branch(db, user, doc)
    _require_owner(doc, user)
    if doc.get("status") not in EDITABLE_STATUSES:
        raise HTTPException(400, "Pra PO hanya dapat diubah saat Draft atau Dikembalikan.")
    before = {"status": doc.get("status"), "customer": doc.get("customer")}
    payload = dict(payload or {})
    await _coerce_sales_payload(db, payload, user, existing=doc)
    _apply_payload(doc, payload)
    doc["updated_at"] = now_iso()
    doc[VERSION_FIELD] = int(doc.get(VERSION_FIELD) or 1) + 1
    await db[COLLECTION].replace_one({"id": pra_po_id}, doc)
    await _append_history(
        db, pra_po_id,
        _history_entry(action="update", user=user, before=before, after={"status": doc.get("status")}),
    )
    return public_view(doc)


async def submit_pra_po(db, pra_po_id: str, user: dict, perms: set) -> dict:
    if not can_submit(perms):
        raise HTTPException(403, "Tidak berwenang mengajukan Pra PO")
    async with get_named_lock(f"pra_po:{pra_po_id}"):
        doc = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Pra PO tidak ditemukan")
        from services.operational_branch import require_document_branch
        await require_document_branch(db, user, doc)
        _require_owner(doc, user)
        status = doc.get("status")
        if status not in SUBMITTABLE_STATUSES:
            raise HTTPException(400, "Pra PO tidak dapat diajukan dari status saat ini.")
        await _validate_for_submit(db, doc)
        new_status = STATUS_RESUBMITTED if status == STATUS_RETURNED else STATUS_SUBMITTED
        now = now_iso()
        updated = await db[COLLECTION].find_one_and_update(
            {"id": pra_po_id, "status": status, VERSION_FIELD: doc.get(VERSION_FIELD)},
            {"$set": {
                "status": new_status,
                "submitted_at": now,
                "updated_at": now,
                "return_reason": None if new_status == STATUS_RESUBMITTED else doc.get("return_reason"),
                VERSION_FIELD: int(doc.get(VERSION_FIELD) or 1) + 1,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not updated:
            raise HTTPException(409, "Pra PO berubah — muat ulang dan coba lagi.")
        await _append_history(
            db, pra_po_id,
            _history_entry(
                action="submit" if new_status == STATUS_SUBMITTED else "resubmit",
                user=user,
                before={"status": status},
                after={"status": new_status},
            ),
        )
        return public_view(updated)


async def return_pra_po(db, pra_po_id: str, payload: dict, user: dict, perms: set) -> dict:
    if not can_return(perms):
        raise HTTPException(403, "Tidak berwenang mengembalikan Pra PO")
    reason = str((payload or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan pengembalian wajib diisi.")
    async with get_named_lock(f"pra_po:{pra_po_id}"):
        doc = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Pra PO tidak ditemukan")
        from services.operational_branch import require_document_branch
        await require_document_branch(db, user, doc)
        status = doc.get("status")
        if status not in VERIFYABLE_STATUSES:
            raise HTTPException(400, "Hanya Pra PO yang diajukan yang dapat dikembalikan.")
        now = now_iso()
        updated = await db[COLLECTION].find_one_and_update(
            {"id": pra_po_id, "status": status, VERSION_FIELD: doc.get(VERSION_FIELD)},
            {"$set": {
                "status": STATUS_RETURNED,
                "return_reason": reason,
                "returned_at": now,
                "updated_at": now,
                VERSION_FIELD: int(doc.get(VERSION_FIELD) or 1) + 1,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not updated:
            raise HTTPException(409, "Pra PO berubah — muat ulang dan coba lagi.")
        await _append_history(
            db, pra_po_id,
            _history_entry(
                action="return",
                user=user,
                note=reason,
                before={"status": status},
                after={"status": STATUS_RETURNED},
            ),
        )
        try:
            from services.notifications import emit_pra_po_returned_to_creator
            await emit_pra_po_returned_to_creator(db, pra_po=updated, reason=reason)
        except Exception:
            logger.exception("pra_po return notification failed")
        return public_view(updated)


async def _materialize_sales_order(
    db,
    pra: dict,
    user: dict,
    *,
    allocation_payload: Optional[dict] = None,
) -> dict:
    """Create one operational orders document from a verified Pra PO."""
    from services.line_product_replacement import require_po_create_line_master
    from services.master_items import resolve_line_production_process
    from services.order_line_addon import validate_service_print_requires_parent
    from services.physical_stock_engine import attach_order_stock_snapshot
    from services.stock_engine_v2 import validate_sales_order_availability
    from services.sales_user_mapping import attach_sales_ownership
    from services.stock_allocation import (
        attach_allocations_to_lines,
        load_allowed_warehouses_for_branch,
        summarize_order_allocation_warnings,
    )

    # Local import of stage labels to avoid circular module load at import time.
    try:
        from server import STAGES  # type: ignore
    except Exception:
        STAGES = ["Order Masuk"]

    creator_id = str(pra.get("created_by_user_id") or "").strip()
    creator_snap = pra.get("created_by_snapshot") or {}
    now = now_iso()

    normalized_lines = []
    seen_line_ids = set()
    for i, li in enumerate(pra.get("line_items") or []):
        master, mapped = await require_po_create_line_master(
            db,
            master_item_id=li.get("master_item_id"),
            accurate_item_id=li.get("accurate_item_id"),
            line_no=i + 1,
        )
        mid = mapped.get("master_item_id")
        aid = mapped.get("accurate_item_id")
        proc = await resolve_line_production_process(
            db,
            master_item_id=mid,
            accurate_item_id=aid,
            explicit=li.get("production_process"),
        )
        line_id = str(li.get("line_id") or "").strip() or f"ln-{uuid.uuid4().hex[:12]}"
        if line_id in seen_line_ids:
            raise HTTPException(400, f"Line item #{i + 1}: line_id duplikat.")
        seen_line_ids.add(line_id)
        unit = str(li.get("unit") or mapped.get("unit") or "pcs").strip() or "pcs"
        row = {
            "line_id": line_id,
            "item_code": li.get("item_code") or mapped.get("item_code") or None,
            "name": li.get("name"),
            "quantity": int(li.get("quantity") or 0),
            "unit": unit,
            "color": li.get("color"),
            "production_process": proc,
            "notes": li.get("notes"),
            "accurate_item_id": aid,
            "master_item_id": mid,
            "unit_price": li.get("unit_price"),
            "price_source": li.get("price_source"),
            "price_review": bool(li.get("price_review")) if li.get("price_review") is not None else None,
        }
        stamped_type = str(master.get("item_type") or li.get("item_type") or "").strip().upper()
        if stamped_type:
            row["item_type"] = stamped_type
        if li.get("parent_line_id"):
            row["parent_line_id"] = li.get("parent_line_id")
        if li.get("parent_line_idx") is not None:
            row["parent_line_idx"] = li.get("parent_line_idx")
        normalized_lines.append(row)

    parent_err = validate_service_print_requires_parent(normalized_lines)
    if parent_err:
        raise HTTPException(400, parent_err)

    order_date = str(pra.get("order_date") or "").strip() or datetime.now(timezone.utc).date().isoformat()
    estimated = str(pra.get("estimated_delivery") or pra.get("deadline") or "").strip() or None
    deadline = str(pra.get("deadline") or pra.get("estimated_delivery") or "").strip() or None

    from services.sales_master import resolve_for_po_write
    sales_master_id, sales_label, _hint = await resolve_for_po_write(
        db,
        sales_master_id=pra.get("sales_master_id"),
        sales_label=pra.get("sales"),
        user=user,
    )

    total_qty = sum(int(x.get("quantity") or 0) for x in normalized_lines)
    order_id = str(uuid.uuid4())
    po_num = str(pra.get("po_number_hint") or "").strip()
    if not po_num:
        for _ in range(5):
            candidate = await _next_po_number(db)
            if not await db.orders.find_one({"po_number": candidate}, {"_id": 1}):
                po_num = candidate
                break
        if not po_num:
            po_num = await _next_po_number(db)

    new_order = {
        "id": order_id,
        "po_number": po_num,
        "accurate_id": None,
        "accurate_synced_at": None,
        "accurate_source": "manual",
        "import_source": "pra_po",
        "pra_po_id": pra.get("id"),
        "pra_po_number": pra.get("number"),
        "import_filename": None,
        "source_pdf": None,
        "order_date": order_date,
        "estimated_delivery": estimated,
        "deadline": deadline,
        "priority": "normal",
        "urgency_approved_by": None,
        "customer": pra.get("customer"),
        "customer_id": pra.get("customer_id"),
        "customer_phone": pra.get("customer_phone"),
        "outlet": pra.get("outlet"),
        "recipient": pra.get("recipient"),
        "address": pra.get("address"),
        "payment_terms": pra.get("payment_terms"),
        "payment_status": "Waiting",
        "design_status": "Waiting",
        "sales": sales_label,
        "sales_master_id": sales_master_id,
        "created_by_user": (
            (creator_snap or {}).get("name")
            or (creator_snap or {}).get("email")
            or user.get("name")
            or user.get("email")
        ),
        "created_by_user_id": creator_id,
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now,
        "expedition": pra.get("expedition"),
        "delivery_zone": pra.get("delivery_zone"),
        "seller": sales_label,
        "line_items": normalized_lines,
        "total_quantity": total_qty,
        "item_count": len(normalized_lines),
        "operator_splits": [],
        "design_files": [],
        "sample_files": [],
        "delivery_proofs": [],
        "notes": pra.get("notes") or "",
        "current_stage": 0,
        "status_label": STAGES[0] if STAGES else "Order Masuk",
        "packing_status": "Belum",
        "delivery_status": "Belum Terkirim",
        "delivery_note_number": "",
        "loading_checklist": None,
        "branch_id": pra.get("branch_id"),
        "operational_branch_id": pra.get("operational_branch_id"),
        "timeline": [{
            "stage": 0,
            "stage_label": STAGES[0] if STAGES else "Order Masuk",
            "at": now,
            "by_email": user.get("email"),
            "by_role": user.get("role"),
            "by_name": user.get("name"),
            "note": f"Diverifikasi dari Pra PO {pra.get('number')}",
        }],
        "created_at": now,
        "updated_at": now,
    }

    # Admin may supply warehouse allocations at verify time. Empty/unbalanced
    # never blocks materialize — warnings only (Owner policy).
    alloc_payload = allocation_payload if isinstance(allocation_payload, dict) else {}
    raw_by_line_id = {}
    raw_by_index = None
    if isinstance(alloc_payload.get("line_allocations"), dict):
        raw_by_line_id = alloc_payload.get("line_allocations") or {}
    elif isinstance(alloc_payload.get("line_items"), list):
        raw_by_index = [
            (li.get("stock_allocations") if isinstance(li, dict) else None)
            for li in alloc_payload.get("line_items") or []
        ]
    branch_for_alloc = str(new_order.get("operational_branch_id") or new_order.get("branch_id") or "")
    allowed_wh = await load_allowed_warehouses_for_branch(db, branch_for_alloc)
    lines_with_alloc, allocation_warnings, _ = attach_allocations_to_lines(
        new_order.get("line_items") or [],
        branch_id=branch_for_alloc,
        allowed_warehouses=allowed_wh,
        raw_by_line_id=raw_by_line_id,
        raw_by_index=raw_by_index,
    )
    new_order["line_items"] = lines_with_alloc
    new_order["stock_allocation_summary"] = summarize_order_allocation_warnings(lines_with_alloc)

    await validate_sales_order_availability(db, new_order.get("line_items") or [])
    await attach_order_stock_snapshot(db, new_order)
    await attach_sales_ownership(db, new_order)

    try:
        await db.orders.insert_one(dict(new_order))
    except DuplicateKeyError:
        # Unique pra_po_id race → caller recovers the winning SO.
        existing = await db.orders.find_one({"pra_po_id": new_order.get("pra_po_id")}, {"_id": 0})
        if existing:
            raise
        # Rare race on po_number — retry once with a fresh number.
        new_order["po_number"] = await _next_po_number(db)
        await db.orders.insert_one(dict(new_order))
    new_order.pop("_id", None)
    # Transient (not in Mongo): consumed by verify_pra_po response.
    new_order["_allocation_warnings_out"] = list(allocation_warnings)
    return new_order


def _normalized_name_absent(doc: dict) -> bool:
    if "normalized_name" not in doc or doc.get("normalized_name") is None:
        return True
    return not str(doc.get("normalized_name") or "").strip()


async def _find_master_by_customer_name(db, name: str) -> Optional[dict]:
    """Find one master by normalized_name, then by a legacy row that lacks that field.

    Does not write normalized_name onto the legacy row. Several legacy rows with
    the same normalized display name are refused instead of picking one.
    """
    from services.sales_customers import (
        COLLECTION as CUSTOMERS,
        find_customer_by_normalized_name,
        normalize_customer_name,
    )

    existing = await find_customer_by_normalized_name(db, name)
    if existing and str(existing.get("id") or "").strip():
        return existing
    norm = normalize_customer_name(name)
    if not norm:
        return None
    rows = await db[CUSTOMERS].find(
        {"$or": [
            {"normalized_name": {"$exists": False}},
            {"normalized_name": None},
            {"normalized_name": ""},
        ]},
        {"_id": 0, "id": 1, "name": 1, "normalized_name": 1},
    ).to_list(5000)
    if len(rows) >= 5000:
        raise HTTPException(
            409,
            "Pencarian Master Customer lama tidak lengkap. Pilih pelanggan dari katalog.",
        )
    matches = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not _normalized_name_absent(row):
            continue
        if normalize_customer_name(row.get("name")) != norm:
            continue
        cid = str(row.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        matches.append(row)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(
            409,
            "Beberapa Master Customer lama memiliki nama yang sama. Pilih pelanggan dari katalog.",
        )
    return None


async def _resolve_customer_for_verify(db, pra: dict, user: dict) -> str:
    """Resolve the master used by this verification. Does not edit an existing profile.

    A stored customer_id must already exist. A missing id reuses the one master
    with the same normalized_name, including a legacy row that has no
    normalized_name field, or inserts one row from the Pra-PO snapshot.
    A unique-name race returns the winning row. The master document is not updated.
    """
    from services.sales_customers import (
        COLLECTION as CUSTOMERS,
        normalize_customer_name,
        now_iso as customer_now_iso,
    )

    stored_id = str(pra.get("customer_id") or "").strip()
    if stored_id:
        master = await db[CUSTOMERS].find_one({"id": stored_id}, {"_id": 0, "id": 1})
        if not master:
            raise HTTPException(400, "Customer master tidak valid.")
        return stored_id

    name = str(pra.get("customer") or "").strip()
    if not name:
        raise HTTPException(400, "Customer wajib diisi.")
    existing = await _find_master_by_customer_name(db, name)
    if existing and str(existing.get("id") or "").strip():
        return str(existing["id"])

    phone = str(pra.get("customer_phone") or "").strip()
    address = str(pra.get("address") or "").strip()
    if not phone:
        raise HTTPException(400, "Pelanggan baru: No. HP / WhatsApp wajib diisi.")
    if not address:
        raise HTTPException(400, "Pelanggan baru: Alamat lengkap pengiriman wajib diisi.")

    # Prefer explicit customer_pic; fall back to transaction recipient for new masters
    # (same semantics as prepare_customer_for_po: customer_pic or recipient).
    pic = str(pra.get("customer_pic") or "").strip() or str(pra.get("recipient") or "").strip()

    cid = str(uuid.uuid4())
    now = customer_now_iso()
    doc = {
        "id": cid,
        "name": name,
        "normalized_name": normalize_customer_name(name),
        "phone": phone,
        "address": address,
        "pic": pic,
        "email": str(pra.get("customer_email") or "").strip(),
        "city": str(pra.get("customer_city") or "").strip(),
        "notes": "",
        "created_at": now,
        "updated_at": now,
        "created_by": str((user or {}).get("email") or "").strip() or None,
    }
    try:
        await db[CUSTOMERS].insert_one(doc)
    except DuplicateKeyError:
        winner = await _find_master_by_customer_name(db, name)
        if winner and str(winner.get("id") or "").strip():
            return str(winner["id"])
        raise
    return cid


async def _link_existing_order_if_any(db, pra: dict, user: dict) -> Optional[dict]:
    """If an SO already exists for this Pra PO, finalize Pra PO link and return it."""
    pra_po_id = str(pra.get("id") or "")
    existing = await db.orders.find_one({"pra_po_id": pra_po_id}, {"_id": 0})
    if not existing:
        linked_id = str(pra.get("sales_order_id") or "").strip()
        if linked_id:
            existing = await db.orders.find_one({"id": linked_id}, {"_id": 0})
    if not existing:
        return None
    now = now_iso()
    actor = actor_snapshot(user)
    link_set = {
        "status": STATUS_VERIFIED,
        "sales_order_id": existing["id"],
        "sales_order_po_number": existing.get("po_number"),
        "verified_at": pra.get("verified_at") or now,
        "verified_by": pra.get("verified_by") or actor,
        "updated_at": now,
        "return_reason": None,
        "verify_prior_status": None,
        VERSION_FIELD: int(pra.get(VERSION_FIELD) or 1) + 1,
    }
    linked_customer_id = str(existing.get("customer_id") or pra.get("customer_id") or "").strip()
    if linked_customer_id:
        link_set["customer_id"] = linked_customer_id
    updated = await db[COLLECTION].find_one_and_update(
        {"id": pra_po_id},
        {"$set": link_set},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    return {"pra_po": public_view(updated or pra), "order": existing, "idempotent": True}


async def verify_pra_po(
    db,
    pra_po_id: str,
    user: dict,
    perms: set,
    *,
    allocation_payload: Optional[dict] = None,
) -> dict:
    if not can_verify(perms):
        raise HTTPException(403, "Tidak berwenang menyetujui Pra PO")
    async with get_named_lock(f"pra_po:{pra_po_id}"):
        doc = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Pra PO tidak ditemukan")
        from services.operational_branch import require_document_branch
        await require_document_branch(db, user, doc)

        # Idempotent: already verified with linked SO.
        if doc.get("status") == STATUS_VERIFIED and doc.get("sales_order_id"):
            order = await db.orders.find_one({"id": doc["sales_order_id"]}, {"_id": 0})
            return {"pra_po": public_view(doc), "order": order, "idempotent": True}

        # Partial success recovery: SO exists (or sales_order_id set) but status not finalized.
        recovered = await _link_existing_order_if_any(db, doc, user)
        if recovered:
            return recovered

        status = doc.get("status")
        if status not in CLAIMABLE_STATUSES:
            raise HTTPException(400, "Hanya Pra PO yang diajukan yang dapat disetujui.")

        # Atomic claim BEFORE materialize — only one verifier proceeds to create SO.
        prior_status = status if status in VERIFYABLE_STATUSES else (doc.get("verify_prior_status") or STATUS_SUBMITTED)
        now = now_iso()
        claimed = await db[COLLECTION].find_one_and_update(
            {
                "id": pra_po_id,
                "status": {"$in": list(CLAIMABLE_STATUSES)},
                "sales_order_id": None,
            },
            {"$set": {
                "status": STATUS_VERIFYING,
                "verify_prior_status": prior_status if prior_status in VERIFYABLE_STATUSES else STATUS_SUBMITTED,
                "updated_at": now,
                VERSION_FIELD: int(doc.get(VERSION_FIELD) or 1) + 1,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not claimed:
            # Another worker claimed or finished — re-read and recover.
            latest = await db[COLLECTION].find_one({"id": pra_po_id}, {"_id": 0})
            if latest and latest.get("status") == STATUS_VERIFIED and latest.get("sales_order_id"):
                order = await db.orders.find_one({"id": latest["sales_order_id"]}, {"_id": 0})
                return {"pra_po": public_view(latest), "order": order, "idempotent": True}
            recovered = await _link_existing_order_if_any(db, latest or doc, user)
            if recovered:
                return recovered
            raise HTTPException(409, "Verifikasi sedang diproses — coba lagi.")

        try:
            resolved_id = await _resolve_customer_for_verify(db, claimed, user)
            if str(claimed.get("customer_id") or "").strip() != resolved_id:
                await db[COLLECTION].update_one(
                    {"id": pra_po_id, "status": STATUS_VERIFYING, "sales_order_id": None},
                    {"$set": {"customer_id": resolved_id, "updated_at": now_iso()}},
                )
                claimed = {**claimed, "customer_id": resolved_id}
            order = await _materialize_sales_order(
                db, claimed, user, allocation_payload=allocation_payload,
            )
        except DuplicateKeyError:
            # Concurrent insert hit unique pra_po_id — adopt the winner's SO.
            recovered = await _link_existing_order_if_any(db, claimed, user)
            if recovered:
                return recovered
            raise HTTPException(409, "Verifikasi bentrok — coba lagi.")
        except HTTPException:
            # Validation failure (e.g. master item): revert claim, keep submission editable via return/resubmit path.
            revert_to = claimed.get("verify_prior_status") or STATUS_SUBMITTED
            if revert_to not in VERIFYABLE_STATUSES:
                revert_to = STATUS_SUBMITTED
            await db[COLLECTION].update_one(
                {"id": pra_po_id, "status": STATUS_VERIFYING, "sales_order_id": None},
                {"$set": {
                    "status": revert_to,
                    "updated_at": now_iso(),
                    "verify_prior_status": None,
                }},
            )
            raise
        except Exception:
            revert_to = claimed.get("verify_prior_status") or STATUS_SUBMITTED
            if revert_to not in VERIFYABLE_STATUSES:
                revert_to = STATUS_SUBMITTED
            await db[COLLECTION].update_one(
                {"id": pra_po_id, "status": STATUS_VERIFYING, "sales_order_id": None},
                {"$set": {
                    "status": revert_to,
                    "updated_at": now_iso(),
                    "verify_prior_status": None,
                }},
            )
            raise

        actor = actor_snapshot(user)
        verified_customer_id = str(order.get("customer_id") or claimed.get("customer_id") or "").strip()
        verified_set = {
            "status": STATUS_VERIFIED,
            "sales_order_id": order["id"],
            "sales_order_po_number": order.get("po_number"),
            "verified_at": now_iso(),
            "verified_by": actor,
            "updated_at": now_iso(),
            "return_reason": None,
            "verify_prior_status": None,
            VERSION_FIELD: int(claimed.get(VERSION_FIELD) or 1) + 1,
        }
        if verified_customer_id:
            verified_set["customer_id"] = verified_customer_id
        updated = await db[COLLECTION].find_one_and_update(
            {
                "id": pra_po_id,
                "status": STATUS_VERIFYING,
                "sales_order_id": None,
            },
            {"$set": verified_set},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not updated:
            # SO created but Pra PO link lost the race — adopt whatever is linked now.
            recovered = await _link_existing_order_if_any(
                db, {**claimed, "sales_order_id": order.get("id")}, user,
            )
            if recovered:
                # Drop our orphan only if another SO won the unique pra_po_id.
                if recovered["order"]["id"] != order.get("id"):
                    await db.orders.delete_one({
                        "id": order["id"],
                        "pra_po_id": pra_po_id,
                        "import_source": "pra_po",
                    })
                return recovered
            # Force-link our SO (SO exists, Pra PO must reflect it).
            forced_set = {
                "status": STATUS_VERIFIED,
                "sales_order_id": order["id"],
                "sales_order_po_number": order.get("po_number"),
                "verified_at": now_iso(),
                "verified_by": actor,
                "updated_at": now_iso(),
                "return_reason": None,
                "verify_prior_status": None,
            }
            if verified_customer_id:
                forced_set["customer_id"] = verified_customer_id
            forced = await db[COLLECTION].find_one_and_update(
                {"id": pra_po_id},
                {"$set": forced_set},
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            updated = forced

        await _append_history(
            db, pra_po_id,
            _history_entry(
                action="verify",
                user=user,
                before={"status": prior_status},
                after={"status": STATUS_VERIFIED, "sales_order_id": order["id"]},
            ),
        )
        try:
            from services.notifications import emit_sales_order_verified_to_creator
            await emit_sales_order_verified_to_creator(db, order=order)
        except Exception:
            logger.exception("sales_order_verified notification failed")
        allocation_summary = (order or {}).get("stock_allocation_summary") or {}
        allocation_warnings = []
        if isinstance(order, dict):
            allocation_warnings = list(order.pop("_allocation_warnings_out", None) or [])
        warnings = list(allocation_warnings)
        if allocation_summary.get("message"):
            warnings.append({
                "code": "ALLOCATION_INCOMPLETE",
                "message": allocation_summary["message"],
                "incomplete_line_count": allocation_summary.get("incomplete_line_count"),
            })
        return {
            "pra_po": public_view(updated),
            "order": order,
            "idempotent": False,
            "warnings": warnings,
            "allocation_warnings": allocation_warnings,
            "stock_allocation_summary": allocation_summary,
        }


async def list_history(db, pra_po_id: str, user: dict, perms: set) -> List[dict]:
    # Access check via get
    await get_pra_po(db, pra_po_id, user, perms)
    if not can_audit(perms):
        raise HTTPException(403, "Tidak berwenang melihat audit Pra PO")
    rows = await db[HISTORY_COLLECTION].find(
        {"pra_po_id": pra_po_id}, {"_id": 0},
    ).sort("at", -1).to_list(200)
    return rows
