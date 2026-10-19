"""Sentralisasi Cat V1 — standalone paint stock ledger (grams).

Standalone-safe atomicity (no multi-document transaction / no replica-set assumption):
claim pending_event on paint_balances → unique ledger insert → CAS finalize.

Does NOT touch R2, HPP, Accurate, finance, or operational purchasing.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple  # noqa: F401 — Sequence used by list filters

from fastapi import HTTPException
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from permissions import ROLE_LABELS
from services.branch_access import (
    branch_id_allowed,
    require_branch_access,
    resolve_branch_access,
)
from services.hr_payroll import EMPLOYEES as HR_EMPLOYEES
from services.paint_table_query import (
    apply_filters,
    mongo_filters,
    parse_header_filters,
    parse_sort,
    sort_rows,
)

PAINT_MASTER = "paint_master"
PAINT_LEDGER = "paint_ledger"
PAINT_BALANCES = "paint_balances"
PAINT_EMPLOYEE_BALANCES = "paint_employee_balances"

UNIT_GRAMS = "gr"

TX_OPENING = "OPENING"
TX_ADD = "ADD"
TX_TAKE = "TAKE"
TX_RETURN = "RETURN"
TX_ADJUST = "ADJUST"

ALL_TX_TYPES = frozenset({TX_OPENING, TX_ADD, TX_TAKE, TX_RETURN, TX_ADJUST})
EMPLOYEE_TX_TYPES = frozenset({TX_TAKE, TX_RETURN})

EXCLUDED_SUBJECT_ROLES = frozenset({"holding_team", "direktur_utama"})

CLAIM_LEASE_SECONDS = 45

STATUS_AMAN = "AMAN"
STATUS_MENIPIS = "MENIPIS"
STATUS_HABIS = "HABIS"


class PaintValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PaintConflictError(PaintValidationError):
    def __init__(self, message: str):
        super().__init__(message, status_code=409)


class PaintRetryError(PaintValidationError):
    def __init__(self, message: str):
        super().__init__(message, status_code=409)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PaintValidationError(f"{field} wajib diisi")
    return text


def _as_grams(value: Any, *, allow_zero: bool = False, allow_negative: bool = False) -> int:
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise PaintValidationError("quantity_grams harus bilangan bulat")
    if not allow_negative and qty < 0:
        raise PaintValidationError("quantity_grams tidak boleh negatif")
    if not allow_zero and qty == 0:
        raise PaintValidationError("quantity_grams harus lebih dari 0")
    return qty


def _actor_id(user: Optional[dict]) -> str:
    return str((user or {}).get("id") or "").strip() or "system"


def _actor_name(user: Optional[dict]) -> str:
    return str((user or {}).get("name") or (user or {}).get("email") or "").strip()


def _collect_roles(doc: Optional[dict]) -> Set[str]:
    """Canonical role slugs from a user/employee document (single + optional multi)."""
    doc = doc or {}
    roles: Set[str] = set()
    primary = str(doc.get("role") or "").strip().lower()
    if primary:
        roles.add(primary)
    raw = doc.get("roles")
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            slug = str(item or "").strip().lower()
            if slug:
                roles.add(slug)
    return roles


def subject_roles_excluded(roles: Set[str]) -> bool:
    return bool(roles & EXCLUDED_SUBJECT_ROLES)


def stock_status(quantity_grams: int, minimum_stock_grams: int) -> str:
    qty = int(quantity_grams or 0)
    minimum = max(0, int(minimum_stock_grams or 0))
    if qty <= 0:
        return STATUS_HABIS
    if minimum > 0 and qty <= minimum:
        return STATUS_MENIPIS
    return STATUS_AMAN


async def ensure_indexes(db) -> None:
    await db[PAINT_MASTER].create_index("id", unique=True)
    await db[PAINT_MASTER].create_index("code", unique=True)
    await db[PAINT_MASTER].create_index([("active", ASCENDING), ("name", ASCENDING)])

    await db[PAINT_LEDGER].create_index("idempotency_key", unique=True)
    await db[PAINT_LEDGER].create_index("transaction_id", unique=True)
    await db[PAINT_LEDGER].create_index(
        [("branch_id", ASCENDING), ("paint_id", ASCENDING), ("created_at", ASCENDING)]
    )
    await db[PAINT_LEDGER].create_index(
        [("employee_user_id", ASCENDING), ("paint_id", ASCENDING), ("branch_id", ASCENDING), ("type", ASCENDING)]
    )
    await db[PAINT_LEDGER].create_index(
        [("branch_id", ASCENDING), ("type", ASCENDING), ("created_at", ASCENDING)]
    )
    # At most one OPENING per paint+branch scope.
    await db[PAINT_LEDGER].create_index(
        [("paint_id", ASCENDING), ("branch_id", ASCENDING), ("type", ASCENDING)],
        unique=True,
        partialFilterExpression={"type": TX_OPENING},
        name="paint_opening_once_per_scope",
    )

    await db[PAINT_BALANCES].create_index(
        [("paint_id", ASCENDING), ("branch_id", ASCENDING)],
        unique=True,
    )
    await db[PAINT_EMPLOYEE_BALANCES].create_index(
        [("employee_user_id", ASCENDING), ("paint_id", ASCENDING), ("branch_id", ASCENDING)],
        unique=True,
    )


def _public_master(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "paint_id": doc.get("id") or doc.get("paint_id"),
        "id": doc.get("id") or doc.get("paint_id"),
        "code": doc.get("code"),
        "name": doc.get("name"),
        "color": doc.get("name"),
        "active": bool(doc.get("active", True)),
        "minimum_stock_grams": int(doc.get("minimum_stock_grams") or 0),
        "unit": UNIT_GRAMS,
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
    }


def _public_tx(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["quantity_grams"] = int(out.get("quantity_grams") or 0)
    out["signed_quantity_grams"] = int(out.get("signed_quantity_grams") or 0)
    return out


def _signed_qty(tx_type: str, quantity_grams: int) -> int:
    if tx_type in (TX_OPENING, TX_ADD, TX_RETURN):
        return abs(quantity_grams)
    if tx_type == TX_TAKE:
        return -abs(quantity_grams)
    # ADJUST: signed as provided
    return int(quantity_grams)


async def get_paint(db, paint_id: str, *, require_active: bool = False) -> dict:
    pid = str(paint_id or "").strip()
    if not pid:
        raise PaintValidationError("paint_id wajib diisi")
    doc = await db[PAINT_MASTER].find_one({"id": pid}, {"_id": 0})
    if not doc:
        raise PaintValidationError("Master cat tidak ditemukan", status_code=404)
    if require_active and not bool(doc.get("active", True)):
        raise PaintValidationError("Master cat nonaktif")
    return doc


async def list_paint_master(
    db,
    *,
    q: str = "",
    active: Optional[bool] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    filt: Dict[str, Any] = {}
    if active is not None:
        filt["active"] = bool(active)
    query = str(q or "").strip()
    if query:
        filt["$or"] = [
            {"code": {"$regex": query, "$options": "i"}},
            {"name": {"$regex": query, "$options": "i"}},
        ]
    total = await db[PAINT_MASTER].count_documents(filt)
    rows = (
        await db[PAINT_MASTER]
        .find(filt, {"_id": 0})
        .sort([("code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return {
        "items": [_public_master(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def create_paint_master(db, payload: dict, user: Optional[dict]) -> dict:
    code = _as_text(payload.get("code"), "code").upper()
    name = _as_text(payload.get("name") or payload.get("color"), "name")
    minimum = _as_grams(payload.get("minimum_stock_grams") or 0, allow_zero=True)
    paint_id = str(payload.get("paint_id") or payload.get("id") or "").strip() or str(uuid.uuid4())
    stamp = now_iso()
    doc = {
        "id": paint_id,
        "code": code,
        "name": name,
        "active": True if payload.get("active") is None else bool(payload.get("active")),
        "minimum_stock_grams": minimum,
        "unit": UNIT_GRAMS,
        "created_at": stamp,
        "updated_at": stamp,
        "created_by": _actor_id(user),
        "updated_by": _actor_id(user),
    }
    try:
        await db[PAINT_MASTER].insert_one(doc)
    except DuplicateKeyError:
        raise PaintConflictError("Kode cat sudah digunakan")
    return _public_master(doc)


async def update_paint_master(db, paint_id: str, payload: dict, user: Optional[dict]) -> dict:
    existing = await get_paint(db, paint_id)
    updates: Dict[str, Any] = {"updated_at": now_iso(), "updated_by": _actor_id(user)}
    if "code" in payload and payload.get("code") is not None:
        updates["code"] = _as_text(payload.get("code"), "code").upper()
    if "name" in payload or "color" in payload:
        updates["name"] = _as_text(payload.get("name") or payload.get("color"), "name")
    if "minimum_stock_grams" in payload and payload.get("minimum_stock_grams") is not None:
        updates["minimum_stock_grams"] = _as_grams(payload.get("minimum_stock_grams"), allow_zero=True)
    if "active" in payload and payload.get("active") is not None:
        updates["active"] = bool(payload.get("active"))
    try:
        doc = await db[PAINT_MASTER].find_one_and_update(
            {"id": existing["id"]},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
    except DuplicateKeyError:
        raise PaintConflictError("Kode cat sudah digunakan")
    return _public_master(doc)


async def deactivate_paint_master(db, paint_id: str, user: Optional[dict]) -> dict:
    """Soft-delete only. Hard delete blocked when ledger history exists."""
    existing = await get_paint(db, paint_id)
    history = await db[PAINT_LEDGER].find_one({"paint_id": existing["id"]}, {"_id": 1})
    if history:
        return await update_paint_master(db, paint_id, {"active": False}, user)
    # Still prefer deactivate over delete for audit safety in V1.
    return await update_paint_master(db, paint_id, {"active": False}, user)


def _is_active_user(user: Optional[dict]) -> bool:
    return str((user or {}).get("status") or "active").lower() != "inactive"


async def _load_user(db, user_id: str) -> Optional[dict]:
    uid = str(user_id or "").strip()
    if not uid:
        return None
    return await db.users.find_one(
        {"id": uid},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "roles": 1, "status": 1, "branch_ids": 1, "branch_id": 1},
    )


async def resolve_eligible_subject(db, employee_user_id: str) -> dict:
    """Validate TAKE/RETURN subject. Uses users as role authority."""
    uid = str(employee_user_id or "").strip()
    if not uid:
        raise PaintValidationError("employee_user_id wajib diisi")
    user = await _load_user(db, uid)
    if not user:
        raise PaintValidationError("Karyawan tidak ditemukan", status_code=404)
    if not _is_active_user(user):
        raise PaintValidationError("Karyawan nonaktif tidak eligible")
    roles = _collect_roles(user)
    if subject_roles_excluded(roles):
        raise PaintValidationError("Role Holding/Direktur tidak eligible sebagai pengambil/pengembali cat")
    hr = await db[HR_EMPLOYEES].find_one({"user_id": uid}, {"_id": 0, "id": 1, "division": 1, "name": 1})
    return {
        "employee_user_id": uid,
        "employee_id": (hr or {}).get("id"),
        "employee_name": str(user.get("name") or (hr or {}).get("name") or "").strip(),
        "employee_email": str(user.get("email") or "").strip(),
        "role": str(user.get("role") or "").strip(),
        "roles": sorted(roles),
        "division": (hr or {}).get("division"),
        "active": True,
    }


async def list_eligible_employees(
    db,
    *,
    q: str = "",
    page: int = 1,
    page_size: int = 30,
) -> dict:
    """Active employees/users excluding Holding + Direktur (incl. multi-role)."""
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 30)))
    query = str(q or "").strip()
    filt: Dict[str, Any] = {
        "status": {"$ne": "inactive"},
        "role": {"$nin": list(EXCLUDED_SUBJECT_ROLES)},
    }
    if query:
        filt["$or"] = [
            {"name": {"$regex": query, "$options": "i"}},
            {"email": {"$regex": query, "$options": "i"}},
        ]
    # Fetch a wider window then filter multi-role / roles[] exclusions in Python
    # so multi-role Holding/Direktur cannot slip through on secondary roles.
    cursor = (
        db.users.find(
            filt,
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "roles": 1, "status": 1, "branch_ids": 1},
        )
        .sort([("name", ASCENDING)])
        .limit(2000)
    )
    users = await cursor.to_list(2000)
    hr_rows = await db[HR_EMPLOYEES].find({}, {"_id": 0, "id": 1, "user_id": 1, "division": 1}).to_list(5000)
    hr_by_user = {str(r.get("user_id") or ""): r for r in hr_rows if r.get("user_id")}

    items: List[dict] = []
    for user in users:
        roles = _collect_roles(user)
        if subject_roles_excluded(roles):
            continue
        if not _is_active_user(user):
            continue
        uid = str(user.get("id") or "").strip()
        if not uid:
            continue
        hr = hr_by_user.get(uid) or {}
        role_slug = str(user.get("role") or "").strip()
        items.append(
            {
                "employee_user_id": uid,
                "employee_id": hr.get("id"),
                "name": str(user.get("name") or "").strip(),
                "email": str(user.get("email") or "").strip(),
                "role": role_slug,
                "role_label": ROLE_LABELS.get(role_slug) or role_slug,
                "division": hr.get("division"),
                "branch_ids": user.get("branch_ids") or [],
            }
        )

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {"items": page_items, "page": page, "page_size": page_size, "total": total}


def _identity(paint_id: str, branch_id: str) -> dict:
    return {"paint_id": paint_id, "branch_id": branch_id}


def _new_claim(*, idempotency_key: str, transaction_id: str, signed_qty: int, tx_type: str, actor_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "event_id": f"paint:{idempotency_key}",
        "idempotency_key": idempotency_key,
        "transaction_id": transaction_id,
        "signed_quantity_grams": signed_qty,
        "type": tx_type,
        "stage": "CLAIMED",
        "claimed_at": now.isoformat(),
        "lease_expires_at": (now + timedelta(seconds=CLAIM_LEASE_SECONDS)).isoformat(),
        "actor_id": actor_id,
        "fence_token": str(uuid.uuid4()),
    }


def _claim_is_stale(pending: dict) -> bool:
    raw = str(pending.get("lease_expires_at") or "").strip()
    if not raw:
        return True
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) >= expires


async def _existing_by_key(db, idempotency_key: str) -> Optional[dict]:
    return await db[PAINT_LEDGER].find_one({"idempotency_key": idempotency_key}, {"_id": 0})


async def _finalize_balance(db, identity: dict, pending: dict) -> dict:
    signed = int(pending.get("signed_quantity_grams") or 0)
    stamp = now_iso()
    updated = await db[PAINT_BALANCES].find_one_and_update(
        {**identity, "pending_event.event_id": pending.get("event_id")},
        {
            "$inc": {"quantity_grams": signed, "version": 1},
            "$set": {
                "updated_at": stamp,
                "last_transaction_id": pending.get("transaction_id"),
            },
            "$unset": {"pending_event": ""},
        },
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated is None:
        return await db[PAINT_BALANCES].find_one(identity, {"_id": 0}) or {}
    return updated


async def _apply_employee_outstanding(
    db,
    *,
    employee_user_id: str,
    paint_id: str,
    branch_id: str,
    delta: int,
) -> None:
    if not employee_user_id or delta == 0:
        return
    identity = {
        "employee_user_id": employee_user_id,
        "paint_id": paint_id,
        "branch_id": branch_id,
    }
    stamp = now_iso()
    if delta > 0:
        await db[PAINT_EMPLOYEE_BALANCES].update_one(
            identity,
            {
                "$inc": {"outstanding_take_grams": delta, "version": 1},
                "$set": {"updated_at": stamp},
                "$setOnInsert": {
                    "employee_user_id": employee_user_id,
                    "paint_id": paint_id,
                    "branch_id": branch_id,
                    "created_at": stamp,
                },
            },
            upsert=True,
        )
        return
    need = abs(delta)
    updated = await db[PAINT_EMPLOYEE_BALANCES].find_one_and_update(
        {**identity, "outstanding_take_grams": {"$gte": need}},
        {
            "$inc": {"outstanding_take_grams": -need, "version": 1},
            "$set": {"updated_at": stamp},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise PaintValidationError(
            "Pengembalian melebihi outstanding TAKE karyawan untuk cat/cabang yang sama"
        )


async def recover_pending_balance(
    db,
    identity: dict,
    *,
    for_idempotency_key: Optional[str] = None,
) -> Optional[dict]:
    bal = await db[PAINT_BALANCES].find_one(identity, {"_id": 0})
    if not bal:
        return None
    pending = bal.get("pending_event")
    if not isinstance(pending, dict):
        return bal
    key = str(pending.get("idempotency_key") or "").strip()
    ev_id = str(pending.get("event_id") or "").strip()
    if not key or not ev_id:
        await db[PAINT_BALANCES].update_one(identity, {"$unset": {"pending_event": ""}})
        return await db[PAINT_BALANCES].find_one(identity, {"_id": 0})

    existing = await _existing_by_key(db, key)
    if existing:
        await _finalize_balance(db, identity, pending)
        return await db[PAINT_BALANCES].find_one(identity, {"_id": 0})

    same_owner = bool(for_idempotency_key) and for_idempotency_key == key
    if same_owner or _claim_is_stale(pending):
        await db[PAINT_BALANCES].update_one(
            {**identity, "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return await db[PAINT_BALANCES].find_one(identity, {"_id": 0})

    raise PaintRetryError("Saldo cat sedang diklaim proses lain (lease masih aktif)")


async def _ensure_balance_doc(db, paint_id: str, branch_id: str) -> dict:
    identity = _identity(paint_id, branch_id)
    existing = await db[PAINT_BALANCES].find_one(identity, {"_id": 0})
    if existing:
        return existing
    stamp = now_iso()
    doc = {
        "paint_id": paint_id,
        "branch_id": branch_id,
        "quantity_grams": 0,
        "opening_posted": False,
        "version": 0,
        "created_at": stamp,
        "updated_at": stamp,
    }
    try:
        await db[PAINT_BALANCES].insert_one(doc)
    except DuplicateKeyError:
        pass
    return await db[PAINT_BALANCES].find_one(identity, {"_id": 0}) or doc


async def post_transaction(
    db,
    payload: dict,
    user: Optional[dict],
) -> dict:
    tx_type = _as_text(payload.get("type"), "type").upper()
    if tx_type not in ALL_TX_TYPES:
        raise PaintValidationError(f"Jenis transaksi tidak valid: {tx_type}")

    paint_id = _as_text(payload.get("paint_id"), "paint_id")
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    await require_branch_access(db, user, branch_id)
    paint = await get_paint(db, paint_id, require_active=True)

    idempotency_key = _as_text(payload.get("idempotency_key"), "idempotency_key")
    existing = await _existing_by_key(db, idempotency_key)
    if existing:
        return _public_tx(existing)

    if tx_type == TX_ADJUST:
        qty = _as_grams(payload.get("quantity_grams"), allow_zero=False, allow_negative=True)
        notes = _as_text(payload.get("notes") or payload.get("reason"), "notes")
    else:
        qty = _as_grams(payload.get("quantity_grams"))
        notes = str(payload.get("notes") or "").strip()

    signed = _signed_qty(tx_type, qty)
    subject = None
    if tx_type in EMPLOYEE_TX_TYPES:
        subject = await resolve_eligible_subject(
            db,
            payload.get("employee_user_id") or payload.get("employee_id"),
        )
    elif payload.get("employee_user_id") or payload.get("employee_id"):
        raise PaintValidationError(f"{tx_type} tidak memerlukan karyawan penerima")

    if tx_type == TX_OPENING:
        prior = await db[PAINT_LEDGER].find_one(
            {"paint_id": paint_id, "branch_id": branch_id, "type": TX_OPENING},
            {"_id": 1},
        )
        if prior:
            raise PaintConflictError("Stok awal sudah diinput untuk cat dan cabang ini")

    if tx_type == TX_RETURN and subject:
        emp_bal = await db[PAINT_EMPLOYEE_BALANCES].find_one(
            {
                "employee_user_id": subject["employee_user_id"],
                "paint_id": paint_id,
                "branch_id": branch_id,
            },
            {"_id": 0},
        )
        outstanding = int((emp_bal or {}).get("outstanding_take_grams") or 0)
        if qty > outstanding:
            raise PaintValidationError(
                "Pengembalian melebihi outstanding TAKE karyawan untuk cat/cabang yang sama"
            )

    actor_id = _actor_id(user)
    transaction_id = str(uuid.uuid4())
    stamp = now_iso()
    identity = _identity(paint_id, branch_id)
    await _ensure_balance_doc(db, paint_id, branch_id)
    await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)

    claim = _new_claim(
        idempotency_key=idempotency_key,
        transaction_id=transaction_id,
        signed_qty=signed,
        tx_type=tx_type,
        actor_id=actor_id,
    )

    # Claim balance row. For reducing stock, require sufficient qty.
    if signed < 0:
        claimed = await db[PAINT_BALANCES].find_one_and_update(
            {
                **identity,
                "pending_event": {"$exists": False},
                "quantity_grams": {"$gte": abs(signed)},
            },
            {"$set": {"pending_event": claim, "updated_at": stamp}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if claimed is None:
            # Distinguish insufficient vs busy claim
            bal = await db[PAINT_BALANCES].find_one(identity, {"_id": 0})
            if bal and isinstance(bal.get("pending_event"), dict) and not _claim_is_stale(bal["pending_event"]):
                raise PaintRetryError("Saldo cat sedang diklaim proses lain")
            if bal and int(bal.get("quantity_grams") or 0) < abs(signed):
                raise PaintValidationError("Stok cat tidak mencukupi")
            # retry once after recover
            await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)
            claimed = await db[PAINT_BALANCES].find_one_and_update(
                {
                    **identity,
                    "pending_event": {"$exists": False},
                    "quantity_grams": {"$gte": abs(signed)},
                },
                {"$set": {"pending_event": claim, "updated_at": stamp}},
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if claimed is None:
                raise PaintValidationError("Stok cat tidak mencukupi")
    else:
        claimed = await db[PAINT_BALANCES].find_one_and_update(
            {**identity, "pending_event": {"$exists": False}},
            {"$set": {"pending_event": claim, "updated_at": stamp}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if claimed is None:
            await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)
            claimed = await db[PAINT_BALANCES].find_one_and_update(
                {**identity, "pending_event": {"$exists": False}},
                {"$set": {"pending_event": claim, "updated_at": stamp}},
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if claimed is None:
                raise PaintRetryError("Saldo cat sedang diklaim proses lain")

    if tx_type == TX_RETURN and subject:
        try:
            await _apply_employee_outstanding(
                db,
                employee_user_id=subject["employee_user_id"],
                paint_id=paint_id,
                branch_id=branch_id,
                delta=-qty,
            )
        except PaintValidationError:
            await db[PAINT_BALANCES].update_one(
                {**identity, "pending_event.event_id": claim["event_id"]},
                {"$unset": {"pending_event": ""}},
            )
            raise

    ledger_doc = {
        "transaction_id": transaction_id,
        "idempotency_key": idempotency_key,
        "type": tx_type,
        "paint_id": paint_id,
        "paint_code": paint.get("code"),
        "paint_name": paint.get("name"),
        "branch_id": branch_id,
        "quantity_grams": abs(qty) if tx_type != TX_ADJUST else abs(qty),
        "signed_quantity_grams": signed,
        "employee_user_id": (subject or {}).get("employee_user_id"),
        "employee_id": (subject or {}).get("employee_id"),
        "employee_name_snapshot": (subject or {}).get("employee_name"),
        "actor_user_id": actor_id,
        "actor_name_snapshot": _actor_name(user),
        "notes": notes,
        "reference": str(payload.get("reference") or payload.get("production_ref") or payload.get("po_ref") or "").strip() or None,
        "correction_of": str(payload.get("correction_of") or "").strip() or None,
        "created_at": stamp,
        "unit": UNIT_GRAMS,
    }

    try:
        await db[PAINT_LEDGER].insert_one(ledger_doc)
    except DuplicateKeyError:
        # Idempotent retry or OPENING unique race
        raced = await _existing_by_key(db, idempotency_key)
        if raced:
            await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)
            return _public_tx(raced)
        await db[PAINT_BALANCES].update_one(
            {**identity, "pending_event.event_id": claim["event_id"]},
            {"$unset": {"pending_event": ""}},
        )
        if tx_type == TX_RETURN and subject:
            # Undo pre-ledger outstanding reservation.
            await _apply_employee_outstanding(
                db,
                employee_user_id=subject["employee_user_id"],
                paint_id=paint_id,
                branch_id=branch_id,
                delta=qty,
            )
        if tx_type == TX_OPENING:
            raise PaintConflictError("Stok awal sudah diinput untuk cat dan cabang ini")
        raise PaintConflictError("Konflik transaksi cat")

    if tx_type == TX_TAKE and subject:
        await _apply_employee_outstanding(
            db,
            employee_user_id=subject["employee_user_id"],
            paint_id=paint_id,
            branch_id=branch_id,
            delta=qty,
        )

    if tx_type == TX_OPENING:
        await db[PAINT_BALANCES].update_one(
            {**identity, "pending_event.event_id": claim["event_id"]},
            {"$set": {"opening_posted": True}},
        )

    await _finalize_balance(db, identity, claim)
    stored = await db[PAINT_LEDGER].find_one({"transaction_id": transaction_id}, {"_id": 0})
    return _public_tx(stored)


async def _accessible_branch_filter(db, user: Optional[dict], branch_id: Optional[str]) -> Dict[str, Any]:
    access = await resolve_branch_access(db, user)
    if branch_id:
        await require_branch_access(db, user, branch_id)
        return {"branch_id": str(branch_id).strip()}
    if access.get("scope") == "ALL":
        return {}
    ids = list(access.get("branch_ids") or [])
    if not ids:
        return {"branch_id": "__none__"}
    return {"branch_id": {"$in": ids}}


async def list_paint_branches(db, user: Optional[dict]) -> dict:
    """Read-only branch options for Sentralisasi Cat (paint.* scope, not org.branches.view)."""
    access = await resolve_branch_access(db, user)
    filt: Dict[str, Any] = {"active": {"$ne": False}}
    if access.get("scope") != "ALL":
        ids = [str(x) for x in (access.get("branch_ids") or []) if x]
        if not ids:
            return {"items": []}
        filt["id"] = {"$in": ids}
    rows = await db.branches.find(filt, {"_id": 0}).sort([("code", ASCENDING), ("name", ASCENDING)]).to_list(500)
    items = []
    for row in rows:
        bid = str(row.get("id") or "").strip()
        if not bid:
            continue
        items.append(
            {
                "id": bid,
                "code": row.get("code") or bid,
                "name": row.get("name") or row.get("code") or bid,
                "active": row.get("active", True) is not False,
            }
        )
    return {"items": items}


TX_SORT_FIELDS = (
    "created_at",
    "transaction_id",
    "type",
    "employee_name_snapshot",
    "paint_code",
    "signed_quantity_grams",
    "quantity_grams",
    "actor_name_snapshot",
    "branch_id",
    "notes",
)
TX_FILTER_FIELDS = TX_SORT_FIELDS + ("paint_name", "reference")


async def list_transactions(
    db,
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    paint_id: Optional[str] = None,
    employee_user_id: Optional[str] = None,
    tx_type: Optional[str] = None,
    q: str = "",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hf: Optional[Sequence[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    filt = await _accessible_branch_filter(db, user, branch_id)
    if paint_id:
        filt["paint_id"] = str(paint_id).strip()
    if employee_user_id:
        filt["employee_user_id"] = str(employee_user_id).strip()
    if tx_type:
        filt["type"] = str(tx_type).strip().upper()
    if date_from or date_to:
        created: Dict[str, Any] = {}
        if date_from:
            created["$gte"] = str(date_from).strip()
        if date_to:
            raw_to = str(date_to).strip()
            created["$lte"] = raw_to if "T" in raw_to else raw_to + "T23:59:59.999999"
        filt["created_at"] = created
    query = str(q or "").strip()
    if query:
        filt["$or"] = [
            {"transaction_id": {"$regex": query, "$options": "i"}},
            {"paint_code": {"$regex": query, "$options": "i"}},
            {"paint_name": {"$regex": query, "$options": "i"}},
            {"employee_name_snapshot": {"$regex": query, "$options": "i"}},
            {"actor_name_snapshot": {"$regex": query, "$options": "i"}},
            {"notes": {"$regex": query, "$options": "i"}},
            {"reference": {"$regex": query, "$options": "i"}},
        ]

    header_filters = parse_header_filters(hf, TX_FILTER_FIELDS)
    extra = mongo_filters(header_filters, numeric_fields=("quantity_grams", "signed_quantity_grams"))
    if extra:
        filt = {"$and": [filt, extra]} if filt else extra

    sort_field, sort_direction = parse_sort(sort_by, sort_dir, TX_SORT_FIELDS, "created_at")
    total = await db[PAINT_LEDGER].count_documents(filt)
    rows = (
        await db[PAINT_LEDGER]
        .find(filt, {"_id": 0})
        .sort([(sort_field, sort_direction), ("transaction_id", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return {
        "items": [_public_tx(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "sort_by": sort_field,
        "sort_dir": "asc" if sort_direction > 0 else "desc",
    }


STOCK_SORT_FIELDS = (
    "branch_label",
    "code",
    "name",
    "opening",
    "add",
    "take",
    "return",
    "adjustment",
    "quantity_grams",
    "minimum_stock_grams",
    "status",
)
STOCK_FILTER_FIELDS = STOCK_SORT_FIELDS + ("branch_id",)


async def list_stock_balances(
    db,
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    q: str = "",
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hf: Optional[Sequence[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    branch_filt = await _accessible_branch_filter(db, user, branch_id)

    masters = await db[PAINT_MASTER].find({}, {"_id": 0}).sort([("code", ASCENDING)]).to_list(2000)
    query = str(q or "").strip().lower()
    if query:
        masters = [
            m for m in masters
            if query in str(m.get("code") or "").lower() or query in str(m.get("name") or "").lower()
        ]

    bal_filt = dict(branch_filt)
    balances = await db[PAINT_BALANCES].find(bal_filt, {"_id": 0}).to_list(5000)
    bal_map = {(b.get("paint_id"), b.get("branch_id")): b for b in balances}

    # Aggregate ledger buckets for display columns
    match = dict(branch_filt)
    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {
            "$group": {
                "_id": {"paint_id": "$paint_id", "branch_id": "$branch_id", "type": "$type"},
                "total": {"$sum": "$quantity_grams"},
                "signed": {"$sum": "$signed_quantity_grams"},
            }
        },
    ]
    if not match:
        pipeline[0] = {"$match": {}}
    agg = await db[PAINT_LEDGER].aggregate(pipeline).to_list(10000)
    buckets: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in agg:
        key = (row["_id"]["paint_id"], row["_id"]["branch_id"])
        buckets.setdefault(key, {"OPENING": 0, "ADD": 0, "TAKE": 0, "RETURN": 0, "ADJUST": 0})
        t = row["_id"]["type"]
        if t == TX_ADJUST:
            buckets[key]["ADJUST"] = int(row.get("signed") or 0)
        else:
            buckets[key][t] = int(row.get("total") or 0)

    # Determine branch list for cartesian expansion when no balances yet
    branch_ids: List[str] = []
    if branch_id:
        branch_ids = [str(branch_id).strip()]
    else:
        access = await resolve_branch_access(db, user)
        if access.get("scope") == "ALL":
            branch_docs = await db.branches.find({"active": {"$ne": False}}, {"_id": 0, "id": 1}).to_list(200)
            branch_ids = [str(b["id"]) for b in branch_docs if b.get("id")]
            # also include branches that only appear on balances
            for b in balances:
                bid = str(b.get("branch_id") or "")
                if bid and bid not in branch_ids:
                    branch_ids.append(bid)
        else:
            branch_ids = list(access.get("branch_ids") or [])

    branch_docs = await db.branches.find({}, {"_id": 0, "id": 1, "code": 1, "name": 1}).to_list(500)
    branch_label = {
        str(b["id"]): str(b.get("code") or b.get("name") or b["id"])
        for b in branch_docs
        if b.get("id")
    }

    items: List[dict] = []
    for master in masters:
        pid = master["id"]
        for bid in branch_ids or [None]:
            if not bid:
                continue
            key = (pid, bid)
            bal = bal_map.get(key) or {}
            bucket = buckets.get(key) or {"OPENING": 0, "ADD": 0, "TAKE": 0, "RETURN": 0, "ADJUST": 0}
            qty = int(bal.get("quantity_grams") or 0)
            if not bal and not any(bucket.values()):
                # Still show active paints with zero stock for selected branch
                if not bool(master.get("active", True)):
                    continue
            minimum = int(master.get("minimum_stock_grams") or 0)
            items.append(
                {
                    "paint_id": pid,
                    "branch_id": bid,
                    "branch_label": branch_label.get(bid, bid),
                    "code": master.get("code"),
                    "name": master.get("name"),
                    "color": master.get("name"),
                    "opening": bucket["OPENING"],
                    "add": bucket["ADD"],
                    "take": bucket["TAKE"],
                    "return": bucket["RETURN"],
                    "adjustment": bucket["ADJUST"],
                    "quantity_grams": qty,
                    "minimum_stock_grams": minimum,
                    "unit": UNIT_GRAMS,
                    "active": bool(master.get("active", True)),
                    "status": stock_status(qty, minimum),
                }
            )

    status_filter = str(status or "").strip().upper()
    if status_filter in {STATUS_AMAN, STATUS_MENIPIS, STATUS_HABIS}:
        items = [row for row in items if row.get("status") == status_filter]

    header_filters = parse_header_filters(hf, STOCK_FILTER_FIELDS)
    items = apply_filters(items, header_filters)
    sort_field, sort_direction = parse_sort(sort_by, sort_dir, STOCK_SORT_FIELDS, "code")
    items = sort_rows(items, sort_by=sort_field, sort_dir=sort_direction, tie_break="paint_id")

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "sort_by": sort_field,
        "sort_dir": "asc" if sort_direction > 0 else "desc",
    }


async def summary_kpis(
    db,
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    branch_filt = await _accessible_branch_filter(db, user, branch_id)
    balances = await db[PAINT_BALANCES].find(branch_filt, {"_id": 0}).to_list(5000)
    masters = {
        m["id"]: m
        for m in await db[PAINT_MASTER].find({}, {"_id": 0}).to_list(2000)
    }
    total_stock = sum(int(b.get("quantity_grams") or 0) for b in balances)
    active_colors = sum(1 for m in masters.values() if bool(m.get("active", True)))
    low_stock = 0
    for b in balances:
        m = masters.get(b.get("paint_id")) or {}
        if stock_status(int(b.get("quantity_grams") or 0), int(m.get("minimum_stock_grams") or 0)) in (
            STATUS_MENIPIS,
            STATUS_HABIS,
        ):
            if int(b.get("quantity_grams") or 0) > 0 or stock_status(0, int(m.get("minimum_stock_grams") or 0)) == STATUS_HABIS:
                if stock_status(int(b.get("quantity_grams") or 0), int(m.get("minimum_stock_grams") or 0)) != STATUS_AMAN:
                    low_stock += 1

    tx_filt = dict(branch_filt)
    created: Dict[str, Any] = {}
    if date_from:
        created["$gte"] = str(date_from).strip()
    if date_to:
        created["$lte"] = str(date_to).strip() + ("T23:59:59.999999" if "T" not in str(date_to) else "")
    if created:
        tx_filt["created_at"] = created

    take_total = 0
    return_total = 0
    async for row in db[PAINT_LEDGER].find(
        {**tx_filt, "type": {"$in": [TX_TAKE, TX_RETURN]}},
        {"_id": 0, "type": 1, "quantity_grams": 1},
    ):
        if row.get("type") == TX_TAKE:
            take_total += int(row.get("quantity_grams") or 0)
        else:
            return_total += int(row.get("quantity_grams") or 0)

    recent = (
        await db[PAINT_LEDGER]
        .find(branch_filt, {"_id": 0})
        .sort([("created_at", -1)])
        .limit(10)
        .to_list(10)
    )

    alerts = []
    for b in balances:
        m = masters.get(b.get("paint_id")) or {}
        st = stock_status(int(b.get("quantity_grams") or 0), int(m.get("minimum_stock_grams") or 0))
        if st in (STATUS_MENIPIS, STATUS_HABIS):
            alerts.append(
                {
                    "paint_id": b.get("paint_id"),
                    "branch_id": b.get("branch_id"),
                    "code": m.get("code"),
                    "name": m.get("name"),
                    "quantity_grams": int(b.get("quantity_grams") or 0),
                    "minimum_stock_grams": int(m.get("minimum_stock_grams") or 0),
                    "status": st,
                }
            )

    return {
        "total_stock_grams": total_stock,
        "active_colors": active_colors,
        "low_stock_count": low_stock,
        "net_usage_grams": take_total - return_total,
        "take_grams": take_total,
        "return_grams": return_total,
        "recent_transactions": [_public_tx(r) for r in recent],
        "low_stock_alerts": alerts[:50],
        "unit": UNIT_GRAMS,
    }


USAGE_SORT_FIELDS = (
    "employee_name",
    "division",
    "branch_label",
    "paint_code",
    "take_grams",
    "return_grams",
    "net_usage_grams",
    "transaction_count",
)
USAGE_FILTER_FIELDS = USAGE_SORT_FIELDS + ("paint_name", "branch_id")


async def usage_report(
    db,
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    employee_user_id: Optional[str] = None,
    paint_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hf: Optional[Sequence[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    filt = await _accessible_branch_filter(db, user, branch_id)
    filt["type"] = {"$in": [TX_TAKE, TX_RETURN]}
    if employee_user_id:
        filt["employee_user_id"] = str(employee_user_id).strip()
    if paint_id:
        filt["paint_id"] = str(paint_id).strip()
    created: Dict[str, Any] = {}
    if date_from:
        created["$gte"] = str(date_from).strip()
    if date_to:
        created["$lte"] = str(date_to).strip() + ("T23:59:59.999999" if "T" not in str(date_to) else "")
    if created:
        filt["created_at"] = created

    pipeline = [
        {"$match": filt},
        {
            "$group": {
                "_id": {
                    "employee_user_id": "$employee_user_id",
                    "paint_id": "$paint_id",
                    "branch_id": "$branch_id",
                },
                "employee_name": {"$last": "$employee_name_snapshot"},
                "paint_code": {"$last": "$paint_code"},
                "paint_name": {"$last": "$paint_name"},
                "references": {"$addToSet": "$reference"},
                "tx_count": {"$sum": 1},
                "take_grams": {
                    "$sum": {
                        "$cond": [{"$eq": ["$type", TX_TAKE]}, "$quantity_grams", 0]
                    }
                },
                "return_grams": {
                    "$sum": {
                        "$cond": [{"$eq": ["$type", TX_RETURN]}, "$quantity_grams", 0]
                    }
                },
            }
        },
        {"$sort": {"employee_name": 1, "paint_code": 1}},
    ]
    grouped = await db[PAINT_LEDGER].aggregate(pipeline).to_list(5000)

    # Enrich division from HR
    user_ids = [str(g["_id"].get("employee_user_id") or "") for g in grouped]
    hr_rows = await db[HR_EMPLOYEES].find(
        {"user_id": {"$in": [u for u in user_ids if u]}},
        {"_id": 0, "user_id": 1, "division": 1},
    ).to_list(5000)
    div_map = {str(r.get("user_id")): r.get("division") for r in hr_rows}
    branch_docs = await db.branches.find({}, {"_id": 0, "id": 1, "code": 1, "name": 1}).to_list(500)
    branch_label = {
        str(b["id"]): str(b.get("code") or b.get("name") or b["id"])
        for b in branch_docs
        if b.get("id")
    }

    items = []
    total_take = 0
    total_return = 0
    for g in grouped:
        take = int(g.get("take_grams") or 0)
        ret = int(g.get("return_grams") or 0)
        total_take += take
        total_return += ret
        refs = [r for r in (g.get("references") or []) if r]
        uid = str(g["_id"].get("employee_user_id") or "")
        items.append(
            {
                "employee_user_id": uid,
                "employee_name": g.get("employee_name") or uid,
                "division": div_map.get(uid),
                "branch_id": g["_id"].get("branch_id"),
                "branch_label": branch_label.get(str(g["_id"].get("branch_id") or ""), g["_id"].get("branch_id")),
                "paint_id": g["_id"].get("paint_id"),
                "paint_code": g.get("paint_code"),
                "paint_name": g.get("paint_name"),
                "period_from": date_from,
                "period_to": date_to,
                "take_grams": take,
                "return_grams": ret,
                "net_usage_grams": take - ret,
                "transaction_count": int(g.get("tx_count") or 0),
                "references": refs,
                "unit": UNIT_GRAMS,
            }
        )

    header_filters = parse_header_filters(hf, USAGE_FILTER_FIELDS)
    items = apply_filters(items, header_filters)
    sort_field, sort_direction = parse_sort(sort_by, sort_dir, USAGE_SORT_FIELDS, "employee_name")
    items = sort_rows(items, sort_by=sort_field, sort_dir=sort_direction, tie_break="paint_id")

    # Totals remain canonical full-scope aggregates (not reduced by header-only table filters).
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "sort_by": sort_field,
        "sort_dir": "asc" if sort_direction > 0 else "desc",
        "totals": {
            "take_grams": total_take,
            "return_grams": total_return,
            "net_usage_grams": total_take - total_return,
            "row_count": total,
        },
        "unit": UNIT_GRAMS,
    }


async def export_usage_report_csv(
    db,
    user: Optional[dict],
    **filters,
) -> str:
    report = await usage_report(db, user, page=1, page_size=10000, **filters)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Nama Karyawan",
            "Divisi",
            "Cabang",
            "Kode Cat",
            "Warna",
            "Periode Mulai",
            "Periode Akhir",
            "Total Diambil (gr)",
            "Total Dikembalikan (gr)",
            "Pemakaian Bersih (gr)",
            "Jumlah Transaksi",
            "Referensi",
        ]
    )
    for row in report["items"]:
        writer.writerow(
            [
                row.get("employee_name"),
                row.get("division") or "",
                row.get("branch_label") or "",
                row.get("paint_code") or "",
                row.get("paint_name") or "",
                row.get("period_from") or "",
                row.get("period_to") or "",
                row.get("take_grams") or 0,
                row.get("return_grams") or 0,
                row.get("net_usage_grams") or 0,
                row.get("transaction_count") or 0,
                "; ".join(row.get("references") or []),
            ]
        )
    return buf.getvalue()


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PaintValidationError):
        return HTTPException(exc.status_code, exc.message)
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(500, str(exc) or "Paint stock error")
