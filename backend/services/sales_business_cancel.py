"""Unified Sales Invoice & Receipt business soft-cancellation (Phase 2.2D).

Hapus = business soft-cancel, not physical deletion.
Posted documents reverse their own journal once via existing reverse_journal.
Unposted documents soft-cancel without fabricating a reversal.

Feature flags default OFF. Permissions sales.invoice.cancel / sales.receipt.cancel
are OPT_IN_ONLY — never implied by create/edit/delete or admin bypass.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

INVOICE_FLAG_KEY = "sales_invoice_business_cancel_enabled"
RECEIPT_FLAG_KEY = "sales_receipt_business_cancel_enabled"
COLLECTION = "sales_business_cancellations"

PERM_INVOICE_CANCEL = "sales.invoice.cancel"
PERM_RECEIPT_CANCEL = "sales.receipt.cancel"

REQUIRED_CANCEL_INDEX_NAME = "uniq_sales_business_cancel_key"
REQUIRED_CANCEL_INDEX_KEYS = ("document_type", "document_id", "idempotency_key")
ERR_CANCEL_INDEX_NOT_READY = (
    "Indeks pembatalan bisnis belum siap (uniq_sales_business_cancel_key). "
    "Provisioning indeks memerlukan persetujuan Owner; Hapus tidak membuat indeks runtime."
)

DOC_INVOICE = "invoice"
DOC_RECEIPT = "receipt"

# Durable document-scoped fence (no TTL release). Timestamp is audit only.
INVOICE_FENCE_FIELD = "sales_business_cancel_fence"
INVOICE_FENCE_KEY_FIELD = "sales_business_cancel_key"
INVOICE_CLAIM_FIELD = "sales_business_cancel_claim"
INVOICE_CLAIM_AT_FIELD = "sales_business_cancel_claim_at"

RECEIPT_FENCE_FIELD = "sales_business_cancel_fence"
RECEIPT_FENCE_KEY_FIELD = "sales_business_cancel_key"
RECEIPT_CLAIM_FIELD = "sales_business_cancel_claim"
RECEIPT_CLAIM_AT_FIELD = "sales_business_cancel_claim_at"

PHASE_PENDING = "pending"
PHASE_REVERSING = "reversing"
PHASE_REVERSED = "reversed"
PHASE_CANCELLED = "cancelled"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"

REASON_MIN_LEN = 3

# List / detail classification contract (backend authoritative).
# Tab membership uses verified financial_status / receipt_is_cancelled only.
# Pending and failed intents stay on Aktif / Penerimaan Aktif with an explicit
# recoverable progress field — never as Batal.
LIST_CLASS_ACTIVE = "active"
LIST_CLASS_CANCELLED = "cancelled"
CANCEL_PROGRESS_NONE = "none"
CANCEL_PROGRESS_PENDING = "pending"
CANCEL_PROGRESS_FAILED = "failed"
CANCEL_PROGRESS_COMPLETE = "complete"


class CancelPaused(Exception):
    """Deterministic stop for crash-window tests. Not an API error."""

    def __init__(self, phase: str):
        self.phase = phase
        super().__init__(phase)


def _now_iso() -> str:
    from services.sales_invoices import now_iso

    return now_iso()


def _actor_public(user: dict) -> dict:
    from services.sales_invoices import _actor_public as _inv_actor

    return _inv_actor(user)


async def is_sales_invoice_business_cancel_enabled(db) -> bool:
    try:
        doc = await db.app_settings.find_one(
            {"key": INVOICE_FLAG_KEY}, {"_id": 0, "enabled": 1, "value": 1}
        )
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


async def is_sales_receipt_business_cancel_enabled(db) -> bool:
    try:
        doc = await db.app_settings.find_one(
            {"key": RECEIPT_FLAG_KEY}, {"_id": 0, "enabled": 1, "value": 1}
        )
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


def business_cancel_fence_is_active(doc: Optional[dict], *, fence_field: str = INVOICE_FENCE_FIELD) -> bool:
    return bool(str((doc or {}).get(fence_field) or "").strip())


def business_cancel_fence_absent_clause(*, fence_field: str = INVOICE_FENCE_FIELD) -> Dict[str, Any]:
    return {
        "$or": [
            {fence_field: {"$exists": False}},
            {fence_field: None},
            {fence_field: ""},
        ]
    }


def invoice_cancel_fence_is_active(invoice: Optional[dict]) -> bool:
    return business_cancel_fence_is_active(invoice, fence_field=INVOICE_FENCE_FIELD)


def invoice_cancel_fence_absent_clause() -> Dict[str, Any]:
    return business_cancel_fence_absent_clause(fence_field=INVOICE_FENCE_FIELD)


def receipt_cancel_fence_is_active(receipt: Optional[dict]) -> bool:
    return business_cancel_fence_is_active(receipt, fence_field=RECEIPT_FENCE_FIELD)


def receipt_cancel_fence_absent_clause() -> Dict[str, Any]:
    return business_cancel_fence_absent_clause(fence_field=RECEIPT_FENCE_FIELD)


async def raise_if_invoice_cancel_in_progress(db, invoice_id: str) -> None:
    invoice = await db.sales_invoices.find_one(
        {"id": str(invoice_id)},
        {"_id": 0, INVOICE_FENCE_FIELD: 1},
    )
    if invoice_cancel_fence_is_active(invoice) or await incomplete_cancellation(
        db, DOC_INVOICE, invoice_id
    ):
        raise HTTPException(409, "Pembatalan faktur belum selesai.")


async def raise_if_receipt_cancel_in_progress(db, receipt_id: str) -> None:
    receipt = await db.sales_receipts.find_one(
        {"id": str(receipt_id)},
        {"_id": 0, RECEIPT_FENCE_FIELD: 1},
    )
    if receipt_cancel_fence_is_active(receipt) or await incomplete_cancellation(
        db, DOC_RECEIPT, receipt_id
    ):
        raise HTTPException(409, "Pembatalan penerimaan belum selesai.")


async def incomplete_cancellation(
    db, document_type: str, document_id: str
) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {
            "document_type": document_type,
            "document_id": str(document_id),
            "complete": {"$ne": True},
            "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
        },
        {"_id": 0},
    )


def cancel_progress_from_intent(intent: Optional[dict]) -> str:
    if not intent:
        return CANCEL_PROGRESS_NONE
    phase = str(intent.get("phase") or "").strip().lower()
    if intent.get("complete") is True or phase == PHASE_COMPLETE:
        return CANCEL_PROGRESS_COMPLETE
    if phase == PHASE_FAILED:
        return CANCEL_PROGRESS_FAILED
    if phase in {
        PHASE_PENDING,
        PHASE_REVERSING,
        PHASE_REVERSED,
        PHASE_CANCELLED,
    }:
        return CANCEL_PROGRESS_PENDING
    return CANCEL_PROGRESS_NONE


def invoice_list_classification(
    invoice: Optional[dict], intent: Optional[dict] = None
) -> str:
    """Batal only when cancellation is financially verified complete.

    Incomplete (pending/reversing/reversed) and recoverable failed intents stay
    Aktif even if financial_status was already written during reverse finalize.
    Historic cancelled rows with no cancel intent remain Batal.
    """
    from services.sales_invoices import FINANCIAL_CANCELLED, effective_financial_status

    progress = cancel_progress_from_intent(intent)
    if progress in {CANCEL_PROGRESS_PENDING, CANCEL_PROGRESS_FAILED}:
        return LIST_CLASS_ACTIVE
    if effective_financial_status(invoice) == FINANCIAL_CANCELLED:
        return LIST_CLASS_CANCELLED
    return LIST_CLASS_ACTIVE


def receipt_list_classification(
    receipt: Optional[dict], intent: Optional[dict] = None
) -> str:
    """Equivalent Batal rule for Penerimaan Penjualan."""
    from services.sales_receipt_posting import receipt_is_cancelled

    progress = cancel_progress_from_intent(intent)
    if progress in {CANCEL_PROGRESS_PENDING, CANCEL_PROGRESS_FAILED}:
        return LIST_CLASS_ACTIVE
    if receipt_is_cancelled(receipt):
        return LIST_CLASS_CANCELLED
    return LIST_CLASS_ACTIVE


async def _latest_cancel_intents_by_document(
    db, document_type: str, document_ids: list
) -> Dict[str, dict]:
    ids = [str(i) for i in document_ids if i]
    if not ids:
        return {}
    rows = await db[COLLECTION].find(
        {"document_type": document_type, "document_id": {"$in": ids}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(max(len(ids) * 4, 20))
    out: Dict[str, dict] = {}
    for row in rows:
        did = str(row.get("document_id") or "")
        if did and did not in out:
            out[did] = row
    return out


async def incomplete_or_failed_cancel_document_ids(db, document_type: str) -> set:
    """Document ids whose cancel intent is not yet verified complete."""
    rows = await db[COLLECTION].find(
        {
            "document_type": document_type,
            "complete": {"$ne": True},
        },
        {"_id": 0, "document_id": 1},
    ).to_list(5000)
    return {str(r.get("document_id") or "") for r in rows if r.get("document_id")}


async def attach_invoice_business_cancel_fields(db, invoices: list) -> list:
    """Attach list_class + cancel_progress from durable cancel evidence."""
    intents = await _latest_cancel_intents_by_document(
        db, DOC_INVOICE, [inv.get("id") for inv in (invoices or []) if inv]
    )
    for inv in invoices or []:
        if not isinstance(inv, dict):
            continue
        intent = intents.get(str(inv.get("id") or ""))
        progress = cancel_progress_from_intent(intent)
        inv["business_cancel_progress"] = progress
        inv["list_class"] = invoice_list_classification(inv, intent)
        if intent and progress != CANCEL_PROGRESS_NONE:
            inv["business_cancel_phase"] = intent.get("phase")
            inv["business_cancel_intent_id"] = intent.get("id")
    return invoices


async def attach_receipt_business_cancel_fields(db, receipts: list) -> list:
    intents = await _latest_cancel_intents_by_document(
        db, DOC_RECEIPT, [rec.get("id") for rec in (receipts or []) if rec]
    )
    for rec in receipts or []:
        if not isinstance(rec, dict):
            continue
        intent = intents.get(str(rec.get("id") or ""))
        progress = cancel_progress_from_intent(intent)
        rec["business_cancel_progress"] = progress
        rec["list_class"] = receipt_list_classification(rec, intent)
        if intent and progress != CANCEL_PROGRESS_NONE:
            rec["business_cancel_phase"] = intent.get("phase")
            rec["business_cancel_intent_id"] = intent.get("id")
    return receipts


def _index_key_fields(meta: dict) -> list:
    keys = (meta or {}).get("key")
    if keys is None:
        return []
    out = []
    if isinstance(keys, dict):
        out.extend(str(k) for k in keys.keys())
        return out
    try:
        items = list(keys)
    except TypeError:
        return []
    for item in items:
        if isinstance(item, (list, tuple)) and item:
            out.append(str(item[0]))
        elif isinstance(item, dict):
            out.extend(str(k) for k in item.keys())
    return out


async def sales_business_cancel_index_ready(db) -> bool:
    """True only when the required unique compound cancel intent index exists.

    Read-only. Does not create, drop, or modify indexes.
    Accepts the required name or an equivalent unique key-order match.
    """
    try:
        info = await db[COLLECTION].index_information()
    except Exception:
        return False
    required = list(REQUIRED_CANCEL_INDEX_KEYS)
    named = info.get(REQUIRED_CANCEL_INDEX_NAME)
    if named and named.get("unique") and _index_key_fields(named) == required:
        return True
    for meta in info.values():
        if not meta.get("unique"):
            continue
        if _index_key_fields(meta) == required:
            return True
    return False


async def require_cancel_index_ready(db) -> None:
    """Fail closed before fence acquire or financial mutation."""
    if not await sales_business_cancel_index_ready(db):
        raise HTTPException(409, ERR_CANCEL_INDEX_NOT_READY)


async def prepare_cancel_indexes_for_tests(db) -> None:
    """Synthetic-test helper only. Never call from Hapus or production runtime."""
    await db[COLLECTION].create_index(
        [("document_type", 1), ("document_id", 1), ("idempotency_key", 1)],
        unique=True,
        name=REQUIRED_CANCEL_INDEX_NAME,
    )


def _normalize_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < REASON_MIN_LEN:
        raise HTTPException(400, "Alasan pembatalan wajib diisi (minimal 3 karakter).")
    return reason


def _normalize_idempotency_key(raw: Any) -> str:
    key = str(raw or "").strip()
    if not key or len(key) > 128:
        raise HTTPException(400, "Idempotency key wajib diisi (maksimal 128 karakter).")
    return key


def _payload_hash(*, document_type: str, document_id: str, reason: str) -> str:
    body = {
        "document_type": document_type,
        "document_id": str(document_id),
        "reason": reason,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _role_defaults_from_db(db) -> Optional[Dict[str, list]]:
    """Hak Akses role_permissions documents. Empty dict means no overrides stored."""
    try:
        docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(
            200
        )
    except Exception:
        return None
    out: Dict[str, list] = {}
    for doc in docs or []:
        role = str(doc.get("role") or "").strip()
        if not role:
            continue
        out[role] = list(doc.get("permissions") or [])
    return out or None


async def require_cancel_permission(db, user: dict, perm: str) -> None:
    """Service-boundary opt-in enforcement aligned with Role & Hak Akses.

    No admin/super-admin bypass. Explicit user override True wins. Explicit False
    denies. Otherwise effective_permissions(role, override, role_permissions).
    """
    from permissions import OPT_IN_ONLY_PERMISSIONS, effective_permissions

    if perm not in OPT_IN_ONLY_PERMISSIONS:
        raise HTTPException(500, f"Permission '{perm}' bukan opt-in cancel.")
    override = (user or {}).get("permissions_override") or {}
    if override.get(perm) is True:
        return
    if override.get(perm) is False:
        raise HTTPException(403, f"Anda tidak punya permission '{perm}'")
    role_override = await _role_defaults_from_db(db)
    perms = effective_permissions(
        str((user or {}).get("role") or ""),
        override,
        role_override,
    )
    if perm not in perms:
        raise HTTPException(403, f"Anda tidak punya permission '{perm}'")


async def _load_intent(db, document_type: str, document_id: str, key: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {
            "document_type": document_type,
            "document_id": str(document_id),
            "idempotency_key": key,
        },
        {"_id": 0},
    )


async def _advance_phase(
    db, intent_id: str, from_phases: tuple, to_phase: str, extra: Optional[dict] = None
) -> Optional[dict]:
    patch = {"phase": to_phase, "updated_at": _now_iso()}
    if extra:
        patch.update(extra)
    return await db[COLLECTION].find_one_and_update(
        {"id": str(intent_id), "phase": {"$in": list(from_phases)}},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )


async def _mark_complete(db, intent_id: str, extra: Optional[dict] = None) -> None:
    patch = {
        "phase": PHASE_COMPLETE,
        "complete": True,
        "completed_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if extra:
        patch.update(extra)
    await db[COLLECTION].update_one(
        {"id": str(intent_id)},
        {"$set": patch},
    )


async def _mark_failed(db, intent_id: str, error: str) -> None:
    await db[COLLECTION].update_one(
        {"id": str(intent_id), "phase": {"$nin": [PHASE_COMPLETE]}},
        {
            "$set": {
                "phase": PHASE_FAILED,
                "complete": False,
                "error": str(error)[:500],
                "updated_at": _now_iso(),
            }
        },
    )


# ---------------------------------------------------------------------------
# Invoice fence acquire / release
# ---------------------------------------------------------------------------

async def _acquire_invoice_cancel_fence(
    db, invoice_id: str, cancel_id: str, token: str, key: str, *, allow_draft: bool
) -> Optional[dict]:
    from services.sales_invoice_edit_revision import revision_fence_absent_clause
    from services.sales_invoice_posting import (
        posting_claim_absent_or_stale_clause,
        receipt_create_claim_absent_or_stale_clause,
        reverse_claim_absent_or_stale_clause,
    )
    from services.sales_invoices import FINANCIAL_DRAFT, FINANCIAL_FINALIZED

    now = _now_iso()
    statuses = [FINANCIAL_FINALIZED]
    if allow_draft:
        statuses = [FINANCIAL_DRAFT, FINANCIAL_FINALIZED]
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": {"$in": statuses},
            "$and": [
                posting_claim_absent_or_stale_clause(),
                reverse_claim_absent_or_stale_clause(),
                receipt_create_claim_absent_or_stale_clause(),
                revision_fence_absent_clause(),
                invoice_cancel_fence_absent_clause(),
            ],
        },
        {
            "$set": {
                INVOICE_FENCE_FIELD: cancel_id,
                INVOICE_FENCE_KEY_FIELD: key,
                INVOICE_CLAIM_FIELD: token,
                INVOICE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _acquire_invoice_cancel_fence_legacy_draft(
    db, invoice_id: str, cancel_id: str, token: str, key: str
) -> Optional[dict]:
    """Historical draft rows may omit financial_status (treated as draft)."""
    from services.sales_invoice_edit_revision import revision_fence_absent_clause
    from services.sales_invoice_posting import (
        posting_claim_absent_or_stale_clause,
        receipt_create_claim_absent_or_stale_clause,
        reverse_claim_absent_or_stale_clause,
    )

    now = _now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "$or": [
                {"financial_status": {"$exists": False}},
                {"financial_status": None},
                {"financial_status": ""},
                {"financial_status": "draft"},
            ],
            "$and": [
                posting_claim_absent_or_stale_clause(),
                reverse_claim_absent_or_stale_clause(),
                receipt_create_claim_absent_or_stale_clause(),
                revision_fence_absent_clause(),
                invoice_cancel_fence_absent_clause(),
            ],
        },
        {
            "$set": {
                INVOICE_FENCE_FIELD: cancel_id,
                INVOICE_FENCE_KEY_FIELD: key,
                INVOICE_CLAIM_FIELD: token,
                INVOICE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _touch_owned_invoice_fence(
    db, invoice_id: str, cancel_id: str, token: str
) -> Optional[dict]:
    now = _now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            INVOICE_FENCE_FIELD: cancel_id,
            INVOICE_CLAIM_FIELD: token,
        },
        {"$set": {INVOICE_CLAIM_AT_FIELD: now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_invoice_cancel_fence(
    db, invoice_id: str, cancel_id: str, token: str
) -> None:
    await db.sales_invoices.update_one(
        {
            "id": str(invoice_id),
            INVOICE_FENCE_FIELD: cancel_id,
            INVOICE_CLAIM_FIELD: token,
        },
        {
            "$unset": {
                INVOICE_FENCE_FIELD: "",
                INVOICE_FENCE_KEY_FIELD: "",
                INVOICE_CLAIM_FIELD: "",
                INVOICE_CLAIM_AT_FIELD: "",
            },
            "$set": {"updated_at": _now_iso()},
        },
    )


def _customer_credit_claim_absent_clause() -> Dict[str, Any]:
    """Credit claim token must be absent. TTL alone must not free the receipt."""
    from services.sales_customer_credit import CLAIM_FIELD as CREDIT_CLAIM

    return {
        "$or": [
            {CREDIT_CLAIM: {"$exists": False}},
            {CREDIT_CLAIM: None},
            {CREDIT_CLAIM: ""},
        ]
    }


async def _acquire_receipt_cancel_fence(
    db, receipt_id: str, cancel_id: str, token: str, key: str
) -> Optional[dict]:
    from services.sales_receipt_posting import CLAIM_FIELD, REVERSE_CLAIM_FIELD, _claim_absent_clause

    now = _now_iso()
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "$nor": [
                {"financial_status": {"$in": ["cancelled", "reversed"]}},
                {"cancelled": True},
            ],
            "$and": [
                _claim_absent_clause(CLAIM_FIELD),
                _claim_absent_clause(REVERSE_CLAIM_FIELD),
                _customer_credit_claim_absent_clause(),
                receipt_cancel_fence_absent_clause(),
            ],
        },
        {
            "$set": {
                RECEIPT_FENCE_FIELD: cancel_id,
                RECEIPT_FENCE_KEY_FIELD: key,
                RECEIPT_CLAIM_FIELD: token,
                RECEIPT_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _touch_owned_receipt_fence(
    db, receipt_id: str, cancel_id: str, token: str
) -> Optional[dict]:
    now = _now_iso()
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            RECEIPT_FENCE_FIELD: cancel_id,
            RECEIPT_CLAIM_FIELD: token,
        },
        {"$set": {RECEIPT_CLAIM_AT_FIELD: now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_receipt_cancel_fence(
    db, receipt_id: str, cancel_id: str, token: str
) -> None:
    await db.sales_receipts.update_one(
        {
            "id": str(receipt_id),
            RECEIPT_FENCE_FIELD: cancel_id,
            RECEIPT_CLAIM_FIELD: token,
        },
        {
            "$unset": {
                RECEIPT_FENCE_FIELD: "",
                RECEIPT_FENCE_KEY_FIELD: "",
                RECEIPT_CLAIM_FIELD: "",
                RECEIPT_CLAIM_AT_FIELD: "",
            },
            "$set": {"updated_at": _now_iso()},
        },
    )


# ---------------------------------------------------------------------------
# Soft-cancel business state
# ---------------------------------------------------------------------------

async def _soft_cancel_invoice_business(
    db, invoice: dict, user: dict, reason: str, *, cancel_id: str, token: str
) -> dict:
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        effective_financial_status,
        get_sales_invoice_detail,
    )

    now = _now_iso()
    stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
    if effective_financial_status(stored) == FINANCIAL_CANCELLED:
        return await get_sales_invoice_detail(db, invoice["id"])

    patch: Dict[str, Any] = {
        "financial_status": FINANCIAL_CANCELLED,
        "paid_total": 0.0,
        "outstanding_amount": 0.0,
        "updated_at": now,
        "business_cancel_reason": reason,
    }
    if not stored.get("cancelled_at"):
        patch["cancelled_at"] = now
        patch["cancelled_by"] = _actor_public(user)

    result = await db.sales_invoices.update_one(
        {
            "id": invoice["id"],
            INVOICE_FENCE_FIELD: cancel_id,
            INVOICE_CLAIM_FIELD: token,
            "financial_status": {"$ne": FINANCIAL_CANCELLED},
        },
        {"$set": patch},
    )
    if result.matched_count:
        return await get_sales_invoice_detail(db, invoice["id"])

    current = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
    if effective_financial_status(current) == FINANCIAL_CANCELLED:
        return await get_sales_invoice_detail(db, invoice["id"])
    if str(current.get(INVOICE_CLAIM_FIELD) or "") != token:
        raise HTTPException(409, "Kepemilikan pembatalan faktur sudah berubah.")
    raise HTTPException(409, "Pembatalan faktur gagal karena perubahan bersamaan.")


async def _soft_cancel_receipt_business(
    db, receipt: dict, user: dict, reason: str, *, cancel_id: str, token: str
) -> dict:
    from services.sales_receipt_posting import receipt_is_cancelled
    from services.sales_receipts import _recalc_invoice_payment_from_receipts, sync_order_payment_projections

    now = _now_iso()
    stored = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    if receipt_is_cancelled(stored):
        return await _public_receipt(db, receipt["id"])

    patch: Dict[str, Any] = {
        "financial_status": "cancelled",
        "cancelled": True,
        "settlement_effective": False,
        "updated_at": now,
        "business_cancel_reason": reason,
    }
    if not stored.get("cancelled_at"):
        patch["cancelled_at"] = now
        patch["cancelled_by"] = _actor_public(user)

    result = await db.sales_receipts.update_one(
        {
            "id": receipt["id"],
            RECEIPT_FENCE_FIELD: cancel_id,
            RECEIPT_CLAIM_FIELD: token,
            "$nor": [
                {"financial_status": {"$in": ["cancelled", "reversed"]}},
                {"cancelled": True},
            ],
        },
        {"$set": patch},
    )
    invoice_id = stored.get("invoice_id") or receipt.get("invoice_id")
    po_id = stored.get("po_id") or receipt.get("po_id")
    if result.matched_count:
        await _recalc_invoice_payment_from_receipts(db, invoice_id)
        if po_id:
            await sync_order_payment_projections(db, po_id)
        return await _public_receipt(db, receipt["id"])

    current = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    if receipt_is_cancelled(current):
        await _recalc_invoice_payment_from_receipts(db, invoice_id)
        if po_id:
            await sync_order_payment_projections(db, po_id)
        return await _public_receipt(db, receipt["id"])
    if str(current.get(RECEIPT_CLAIM_FIELD) or "") != token:
        raise HTTPException(409, "Kepemilikan pembatalan penerimaan sudah berubah.")
    raise HTTPException(409, "Pembatalan penerimaan gagal karena perubahan bersamaan.")


async def _public_receipt(db, receipt_id: str) -> dict:
    from services.sales_receipt_posting import _public_receipt_detail

    return await _public_receipt_detail(db, receipt_id)


# ---------------------------------------------------------------------------
# Posted reverse under cancel fence (reuse reverse_journal; no HTTP hop)
# ---------------------------------------------------------------------------

async def _reverse_posted_invoice_under_fence(
    db, invoice: dict, user: dict, reason: str, *, cancel_id: str, token: str
) -> Optional[dict]:
    from services.accounting_periods import PeriodLockedError
    from services.journal_core import JournalConflictError, JournalValidationError, reverse_journal
    from services.sales_invoice_posting import (
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
        SOURCE_REVERSAL,
        accounting_status_from_journal,
        collect_invoice_reverse_receipt_blockers,
        find_sales_invoice_reversal_journal,
        find_sales_invoice_source_journal,
        raise_active_receipt_blockers,
        _finalize_invoice_reversal_state,
    )

    owned = await _touch_owned_invoice_fence(db, invoice["id"], cancel_id, token)
    if not owned:
        raise HTTPException(409, "Kepemilikan pembatalan faktur sudah berubah.")

    existing = await find_sales_invoice_source_journal(db, invoice["id"])
    status = accounting_status_from_journal(existing)
    if status == ACCOUNTING_REVERSED:
        rev = await find_sales_invoice_reversal_journal(
            db, existing.get("id") if existing else ""
        )
        await _finalize_invoice_reversal_state(db, invoice, user, reason, rev)
        return rev
    if existing and existing.get("ambiguous"):
        raise HTTPException(409, "Jurnal faktur tidak tunggal.")
    if status != ACCOUNTING_POSTED or not existing:
        raise HTTPException(409, "Jurnal faktur yang diharapkan tidak ditemukan.")

    raise_active_receipt_blockers(
        await collect_invoice_reverse_receipt_blockers(db, invoice["id"])
    )

    try:
        rev_doc = await reverse_journal(
            db,
            existing["id"],
            source_type=SOURCE_REVERSAL,
            source_id=str(invoice["id"]),
            posted_by=user,
            description=(
                f"Reversal Faktur Penjualan {invoice.get('invoice_number') or invoice['id']} — {reason}"
            ),
        )
    except KeyError as exc:
        raise HTTPException(404, "Jurnal faktur tidak ditemukan.") from exc
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        still = await find_sales_invoice_source_journal(db, invoice["id"])
        if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
            raise HTTPException(409, "Reversal jurnal untuk faktur ini sudah ada.")
        rev_doc = await find_sales_invoice_reversal_journal(
            db, still.get("id") if still else ""
        )

    await _finalize_invoice_reversal_state(db, invoice, user, reason, rev_doc)
    return rev_doc


async def _reverse_posted_receipt_under_fence(
    db, receipt: dict, user: dict, reason: str, *, cancel_id: str, token: str
) -> None:
    from services.accounting_periods import PeriodLockedError
    from services.journal_core import JournalConflictError, JournalValidationError, reverse_journal
    from services.sales_customer_credit import (
        assert_source_receipt_reversible,
        hold_source_credit_claim,
        release_source_credit_claim,
        release_unfenced_credit_reservations,
    )
    from services.sales_receipt_posting import (
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
        SOURCE_REVERSAL,
        accounting_status_from_journal,
        find_sales_receipt_source_journal,
        _finalize_reversal_operational_state,
        _journal_description,
    )

    owned = await _touch_owned_receipt_fence(db, receipt["id"], cancel_id, token)
    if not owned:
        raise HTTPException(409, "Kepemilikan pembatalan penerimaan sudah berubah.")

    existing = await find_sales_receipt_source_journal(db, receipt["id"])
    status = accounting_status_from_journal(existing)
    if status == ACCOUNTING_REVERSED:
        await _finalize_reversal_operational_state(db, receipt, user)
        return
    if status != ACCOUNTING_POSTED or not existing:
        raise HTTPException(409, "Jurnal penerimaan yang diharapkan tidak ditemukan.")

    await assert_source_receipt_reversible(db, receipt["id"])

    credit_token = str(uuid.uuid4())
    credit_held = await hold_source_credit_claim(
        db, receipt["id"], credit_token, owned_cancel_fence=cancel_id
    )
    if not credit_held:
        raise HTTPException(409, "Saldo pelanggan sedang dipakai. Ulangi permintaan.")
    try:
        await release_unfenced_credit_reservations(db, receipt["id"], credit_token)
        await assert_source_receipt_reversible(db, receipt["id"])
        try:
            await reverse_journal(
                db,
                existing["id"],
                source_type=SOURCE_REVERSAL,
                source_id=str(receipt["id"]),
                posted_by=user,
                description=f"Reversal {_journal_description(receipt)} — {reason}",
            )
        except KeyError as exc:
            raise HTTPException(404, "Jurnal penerimaan tidak ditemukan.") from exc
        except PeriodLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            still = await find_sales_receipt_source_journal(db, receipt["id"])
            if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                raise HTTPException(409, "Reversal jurnal untuk penerimaan ini sudah ada.")
        await _finalize_reversal_operational_state(db, receipt, user)
    finally:
        await release_source_credit_claim(db, receipt["id"], credit_token)


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

async def cancel_sales_invoice_business(
    db,
    invoice_id: str,
    user: dict,
    *,
    reason: Any,
    idempotency_key: Any,
    pause_after: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified Hapus for Sales Invoice. Soft-cancel; reverse only when posted."""
    from services.sales_invoice_edit_revision import raise_if_revision_in_progress
    from services.sales_invoice_posting import (
        ACCOUNTING_NOT_POSTED,
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
        accounting_status_from_journal,
        collect_invoice_reverse_receipt_blockers,
        find_sales_invoice_source_journal,
        posting_claim_is_active,
        raise_active_receipt_blockers,
        receipt_create_claim_is_active,
        reverse_claim_is_active,
    )
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        FINANCIAL_DRAFT,
        FINANCIAL_FINALIZED,
        effective_financial_status,
        get_sales_invoice_detail,
    )

    if not await is_sales_invoice_business_cancel_enabled(db):
        raise HTTPException(409, "Pembatalan bisnis faktur belum diaktifkan.")

    await require_cancel_permission(db, user, PERM_INVOICE_CANCEL)
    reason = _normalize_reason(reason)
    key = _normalize_idempotency_key(idempotency_key)
    payload_hash = _payload_hash(
        document_type=DOC_INVOICE, document_id=str(invoice_id), reason=reason
    )
    await require_cancel_index_ready(db)

    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one(
            {"invoice_number": str(invoice_id)}, {"_id": 0}
        )
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    status = effective_financial_status(inv)
    stored = await _load_intent(db, DOC_INVOICE, inv["id"], key)
    if stored and stored.get("payload_hash") and stored["payload_hash"] != payload_hash:
        raise HTTPException(409, "Idempotency key dipakai dengan payload berbeda.")
    if stored and stored.get("phase") == PHASE_COMPLETE and stored.get("complete") is True:
        detail = await get_sales_invoice_detail(db, inv["id"])
        await _release_invoice_cancel_fence(
            db, inv["id"], str(stored.get("id") or ""), str(stored.get("claim_token") or "")
        )
        await attach_invoice_business_cancel_fields(db, [detail])
        return {
            "invoice": detail,
            "outcome": "idempotent_replay",
            "message": "Faktur Penjualan dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }
    if stored and stored.get("phase") == PHASE_FAILED:
        raise HTTPException(409, "Pembatalan faktur gagal dan tidak dapat dilanjutkan dengan key yang sama.")

    other = await incomplete_cancellation(db, DOC_INVOICE, inv["id"])
    if other and str(other.get("idempotency_key") or "") != key:
        raise HTTPException(409, "Pembatalan faktur lain belum selesai.")

    # Already cancelled with no incomplete intent for this key → idempotent terminal.
    # Incomplete intent for this key must continue recovery even when financial_status
    # was already set by reverse finalize.
    if status == FINANCIAL_CANCELLED and not (
        stored and stored.get("complete") is not True
        and str(stored.get("phase") or "") not in {PHASE_COMPLETE, PHASE_FAILED}
    ):
        detail = await get_sales_invoice_detail(db, inv["id"])
        await attach_invoice_business_cancel_fields(db, [detail])
        return {
            "invoice": detail,
            "outcome": "already_cancelled",
            "message": "Faktur Penjualan sudah dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }

    await raise_if_revision_in_progress(db, inv["id"])
    if posting_claim_is_active(inv) or reverse_claim_is_active(inv) or receipt_create_claim_is_active(inv):
        raise HTTPException(409, "Operasi accounting faktur sedang berjalan.")

    journal = await find_sales_invoice_source_journal(db, inv["id"])
    acc = accounting_status_from_journal(journal)
    if journal and journal.get("ambiguous"):
        raise HTTPException(409, "Jurnal faktur tidak tunggal.")

    needs_reverse = acc == ACCOUNTING_POSTED
    if needs_reverse:
        raise_active_receipt_blockers(
            await collect_invoice_reverse_receipt_blockers(db, inv["id"])
        )
    elif acc not in (ACCOUNTING_NOT_POSTED, ACCOUNTING_REVERSED, "ambiguous"):
        # unknown — fail closed when journal row exists but status unexpected
        if journal:
            raise HTTPException(409, "Status jurnal faktur tidak dapat diverifikasi.")

    # Unposted finalized/draft: block if any receipt documents remain unresolved
    if not needs_reverse and acc != ACCOUNTING_REVERSED and status != FINANCIAL_CANCELLED:
        pay_count = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
        if pay_count > 0:
            from services.sales_receipt_posting import receipt_is_cancelled

            active = 0
            async for rec in db.sales_receipts.find({"invoice_id": inv["id"]}, {"_id": 0}):
                if not receipt_is_cancelled(rec):
                    active += 1
            if active > 0:
                raise HTTPException(
                    409,
                    "Faktur tidak dapat dibatalkan karena masih memiliki Penerimaan Penjualan.",
                )

    current_invoice = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or inv
    fence_id = str(current_invoice.get(INVOICE_FENCE_FIELD) or "")
    fence_key = str(current_invoice.get(INVOICE_FENCE_KEY_FIELD) or "")
    if fence_id and fence_key != key:
        raise HTTPException(409, "Pembatalan faktur lain belum selesai.")

    cancel_id = ""
    token = ""

    async def _own(owned_id: str, owned_token: str) -> dict:
        owned = await _touch_owned_invoice_fence(db, inv["id"], owned_id, owned_token)
        if not owned:
            raise HTTPException(409, "Kepemilikan pembatalan faktur sudah berubah.")
        return owned

    if stored:
        cancel_id = str(stored["id"])
        token = str(stored.get("claim_token") or "")
        if not token:
            raise HTTPException(409, "Pembatalan faktur belum selesai.")
        await _own(cancel_id, token)
    elif fence_id and fence_key == key:
        cancel_id = fence_id
        token = str(current_invoice.get(INVOICE_CLAIM_FIELD) or "")
        await _own(cancel_id, token)
    else:
        cancel_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        allow_draft = status == FINANCIAL_DRAFT
        claimed = await _acquire_invoice_cancel_fence(
            db, inv["id"], cancel_id, token, key, allow_draft=allow_draft
        )
        if not claimed and allow_draft:
            claimed = await _acquire_invoice_cancel_fence_legacy_draft(
                db, inv["id"], cancel_id, token, key
            )
        if not claimed:
            raced = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or {}
            if (
                str(raced.get(INVOICE_FENCE_KEY_FIELD) or "") == key
                and str(raced.get(INVOICE_FENCE_FIELD) or "")
            ):
                cancel_id = str(raced.get(INVOICE_FENCE_FIELD))
                token = str(raced.get(INVOICE_CLAIM_FIELD) or "")
                await _own(cancel_id, token)
            else:
                raise HTTPException(
                    409, "Faktur sedang dibatalkan atau dikunci proses lain."
                )

    if not stored:
        from pymongo.errors import DuplicateKeyError

        try:
            await db[COLLECTION].insert_one(
                {
                    "id": cancel_id,
                    "document_type": DOC_INVOICE,
                    "document_id": inv["id"],
                    "idempotency_key": key,
                    "payload_hash": payload_hash,
                    "reason": reason,
                    "phase": PHASE_PENDING,
                    "complete": False,
                    "needs_reverse": needs_reverse,
                    "claim_token": token,
                    "created_by": _actor_public(user),
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            )
        except DuplicateKeyError:
            pass
        stored = await _load_intent(db, DOC_INVOICE, inv["id"], key)
        if not stored:
            raise HTTPException(409, "Pembatalan faktur belum selesai.")
        if stored.get("payload_hash") != payload_hash:
            raise HTTPException(409, "Idempotency key dipakai dengan payload berbeda.")
        cancel_id = str(stored["id"])
        token = str(stored.get("claim_token") or token)

    try:
        phase = str(stored.get("phase") or PHASE_PENDING)
        rev_doc = None

        if needs_reverse or phase in (PHASE_REVERSING, PHASE_REVERSED, PHASE_CANCELLED):
            # Re-read journal evidence as authority for financial completion.
            live_journal = await find_sales_invoice_source_journal(db, inv["id"])
            live_acc = accounting_status_from_journal(live_journal)
            if live_acc == ACCOUNTING_POSTED or phase in (PHASE_PENDING, PHASE_REVERSING):
                if phase == PHASE_PENDING:
                    await _advance_phase(
                        db, cancel_id, (PHASE_PENDING,), PHASE_REVERSING
                    )
                if pause_after == PHASE_REVERSING:
                    raise CancelPaused(PHASE_REVERSING)
                rev_doc = await _reverse_posted_invoice_under_fence(
                    db, inv, user, reason, cancel_id=cancel_id, token=token
                )
                await _advance_phase(
                    db,
                    cancel_id,
                    (PHASE_PENDING, PHASE_REVERSING),
                    PHASE_REVERSED,
                    {
                        "reversal_journal_id": (rev_doc or {}).get("id"),
                        "financial_complete": True,
                    },
                )
                if pause_after == PHASE_REVERSED:
                    raise CancelPaused(PHASE_REVERSED)
            elif live_acc == ACCOUNTING_REVERSED:
                from services.sales_invoice_posting import (
                    find_sales_invoice_reversal_journal,
                    _finalize_invoice_reversal_state,
                )

                rev = await find_sales_invoice_reversal_journal(
                    db, live_journal.get("id") if live_journal else ""
                )
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                await _advance_phase(
                    db,
                    cancel_id,
                    (PHASE_PENDING, PHASE_REVERSING, PHASE_REVERSED),
                    PHASE_REVERSED,
                    {
                        "reversal_journal_id": (rev or {}).get("id"),
                        "financial_complete": True,
                    },
                )
            elif needs_reverse:
                raise HTTPException(409, "Jurnal faktur yang diharapkan tidak ditemukan.")

        # Soft-cancel business state (also covers unposted path)
        live = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or {}
        if effective_financial_status(live) != FINANCIAL_CANCELLED:
            if pause_after == "before_business_cancel":
                raise CancelPaused("before_business_cancel")
            await _soft_cancel_invoice_business(
                db, inv, user, reason, cancel_id=cancel_id, token=token
            )
        await _advance_phase(
            db,
            cancel_id,
            (PHASE_PENDING, PHASE_REVERSING, PHASE_REVERSED, PHASE_CANCELLED),
            PHASE_CANCELLED,
            {"business_complete": True},
        )
        if pause_after == PHASE_CANCELLED:
            raise CancelPaused(PHASE_CANCELLED)

        detail = await get_sales_invoice_detail(db, inv["id"])
        if effective_financial_status(detail) != FINANCIAL_CANCELLED:
            raise HTTPException(409, "Status bisnis faktur belum dibatalkan.")
        # Fail closed: posted path must show reversed journal evidence before success.
        if needs_reverse:
            live_journal = await find_sales_invoice_source_journal(db, inv["id"])
            if accounting_status_from_journal(live_journal) != ACCOUNTING_REVERSED:
                raise HTTPException(
                    409,
                    "Pembatalan belum lengkap: jurnal faktur belum ter-reverse.",
                )

        await _mark_complete(db, cancel_id)
        await _release_invoice_cancel_fence(db, inv["id"], cancel_id, token)
        await attach_invoice_business_cancel_fields(db, [detail])
        return {
            "invoice": detail,
            "outcome": "cancelled",
            "message": "Faktur Penjualan dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }
    except CancelPaused:
        raise
    except HTTPException as exc:
        # Retain recoverable state when financial mutation may have started.
        live_journal = await find_sales_invoice_source_journal(db, inv["id"])
        if accounting_status_from_journal(live_journal) == ACCOUNTING_REVERSED:
            from services.sales_invoice_posting import (
                find_sales_invoice_reversal_journal,
                _finalize_invoice_reversal_state,
            )

            rev = await find_sales_invoice_reversal_journal(
                db, live_journal.get("id") if live_journal else ""
            )
            try:
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                detail = await get_sales_invoice_detail(db, inv["id"])
                if effective_financial_status(detail) == FINANCIAL_CANCELLED:
                    await _mark_complete(
                        db,
                        cancel_id,
                        {
                            "reversal_journal_id": (rev or {}).get("id"),
                            "financial_complete": True,
                            "business_complete": True,
                        },
                    )
                    await _release_invoice_cancel_fence(db, inv["id"], cancel_id, token)
                    return {
                        "invoice": detail,
                        "outcome": "recovered",
                        "message": "Faktur Penjualan dibatalkan",
                    }
            except HTTPException:
                pass
        # Before financial mutation: release fence and mark failed.
        row = await _load_intent(db, DOC_INVOICE, inv["id"], key)
        if row and row.get("phase") == PHASE_PENDING and not row.get("financial_complete"):
            await _mark_failed(db, cancel_id, getattr(exc, "detail", str(exc)))
            await _release_invoice_cancel_fence(db, inv["id"], cancel_id, token)
        raise


async def cancel_sales_receipt_business(
    db,
    receipt_id: str,
    user: dict,
    *,
    reason: Any,
    idempotency_key: Any,
    pause_after: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified Hapus for Sales Receipt. Soft-cancel; reverse only when posted."""
    from services.sales_customer_credit import claim_is_active
    from services.sales_receipt_posting import (
        ACCOUNTING_NOT_POSTED,
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
        accounting_status_from_journal,
        find_sales_receipt_source_journal,
        posting_claim_is_active,
        receipt_is_cancelled,
        reverse_claim_is_active,
    )

    if not await is_sales_receipt_business_cancel_enabled(db):
        raise HTTPException(409, "Pembatalan bisnis penerimaan belum diaktifkan.")

    await require_cancel_permission(db, user, PERM_RECEIPT_CANCEL)
    reason = _normalize_reason(reason)
    key = _normalize_idempotency_key(idempotency_key)
    payload_hash = _payload_hash(
        document_type=DOC_RECEIPT, document_id=str(receipt_id), reason=reason
    )
    await require_cancel_index_ready(db)

    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one(
            {"receipt_number": str(receipt_id)}, {"_id": 0}
        )
    if not rec:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")

    stored = await _load_intent(db, DOC_RECEIPT, rec["id"], key)
    if stored and stored.get("payload_hash") and stored["payload_hash"] != payload_hash:
        raise HTTPException(409, "Idempotency key dipakai dengan payload berbeda.")
    if stored and stored.get("phase") == PHASE_COMPLETE and stored.get("complete") is True:
        detail = await _public_receipt(db, rec["id"])
        await _release_receipt_cancel_fence(
            db, rec["id"], str(stored.get("id") or ""), str(stored.get("claim_token") or "")
        )
        await attach_receipt_business_cancel_fields(db, [detail])
        return {
            "receipt": detail,
            "outcome": "idempotent_replay",
            "message": "Penerimaan Penjualan dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }
    if stored and stored.get("phase") == PHASE_FAILED:
        raise HTTPException(
            409, "Pembatalan penerimaan gagal dan tidak dapat dilanjutkan dengan key yang sama."
        )

    other = await incomplete_cancellation(db, DOC_RECEIPT, rec["id"])
    if other and str(other.get("idempotency_key") or "") != key:
        raise HTTPException(409, "Pembatalan penerimaan lain belum selesai.")

    if receipt_is_cancelled(rec) and not (
        stored and stored.get("complete") is not True
        and str(stored.get("phase") or "") not in {PHASE_COMPLETE, PHASE_FAILED}
    ):
        detail = await _public_receipt(db, rec["id"])
        await attach_receipt_business_cancel_fields(db, [detail])
        return {
            "receipt": detail,
            "outcome": "already_cancelled",
            "message": "Penerimaan Penjualan sudah dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }

    if posting_claim_is_active(rec) or reverse_claim_is_active(rec):
        raise HTTPException(409, "Operasi accounting penerimaan sedang berjalan.")
    if claim_is_active(rec):
        raise HTTPException(409, "Saldo pelanggan sedang dipakai. Ulangi permintaan.")

    # Block cancel while invoice-level cancel/revision/post is active
    invoice_id = str(rec.get("invoice_id") or "").strip()
    if invoice_id:
        await raise_if_invoice_cancel_in_progress(db, invoice_id)
        from services.sales_invoice_edit_revision import raise_if_revision_in_progress

        await raise_if_revision_in_progress(db, invoice_id)

    journal = await find_sales_receipt_source_journal(db, rec["id"])
    acc = accounting_status_from_journal(journal)
    needs_reverse = acc == ACCOUNTING_POSTED
    if not needs_reverse and acc not in (ACCOUNTING_NOT_POSTED, ACCOUNTING_REVERSED):
        if journal:
            raise HTTPException(409, "Status jurnal penerimaan tidak dapat diverifikasi.")

    current_receipt = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or rec
    fence_id = str(current_receipt.get(RECEIPT_FENCE_FIELD) or "")
    fence_key = str(current_receipt.get(RECEIPT_FENCE_KEY_FIELD) or "")
    if fence_id and fence_key != key:
        raise HTTPException(409, "Pembatalan penerimaan lain belum selesai.")

    cancel_id = ""
    token = ""

    async def _own(owned_id: str, owned_token: str) -> dict:
        owned = await _touch_owned_receipt_fence(db, rec["id"], owned_id, owned_token)
        if not owned:
            raise HTTPException(409, "Kepemilikan pembatalan penerimaan sudah berubah.")
        return owned

    if stored:
        cancel_id = str(stored["id"])
        token = str(stored.get("claim_token") or "")
        if not token:
            raise HTTPException(409, "Pembatalan penerimaan belum selesai.")
        await _own(cancel_id, token)
    elif fence_id and fence_key == key:
        cancel_id = fence_id
        token = str(current_receipt.get(RECEIPT_CLAIM_FIELD) or "")
        await _own(cancel_id, token)
    else:
        cancel_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        claimed = await _acquire_receipt_cancel_fence(
            db, rec["id"], cancel_id, token, key
        )
        if not claimed:
            raced = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or {}
            if (
                str(raced.get(RECEIPT_FENCE_KEY_FIELD) or "") == key
                and str(raced.get(RECEIPT_FENCE_FIELD) or "")
            ):
                cancel_id = str(raced.get(RECEIPT_FENCE_FIELD))
                token = str(raced.get(RECEIPT_CLAIM_FIELD) or "")
                await _own(cancel_id, token)
            else:
                raise HTTPException(
                    409, "Penerimaan sedang dibatalkan atau dikunci proses lain."
                )

    if not stored:
        from pymongo.errors import DuplicateKeyError

        try:
            await db[COLLECTION].insert_one(
                {
                    "id": cancel_id,
                    "document_type": DOC_RECEIPT,
                    "document_id": rec["id"],
                    "idempotency_key": key,
                    "payload_hash": payload_hash,
                    "reason": reason,
                    "phase": PHASE_PENDING,
                    "complete": False,
                    "needs_reverse": needs_reverse,
                    "claim_token": token,
                    "created_by": _actor_public(user),
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            )
        except DuplicateKeyError:
            pass
        stored = await _load_intent(db, DOC_RECEIPT, rec["id"], key)
        if not stored:
            raise HTTPException(409, "Pembatalan penerimaan belum selesai.")
        if stored.get("payload_hash") != payload_hash:
            raise HTTPException(409, "Idempotency key dipakai dengan payload berbeda.")
        cancel_id = str(stored["id"])
        token = str(stored.get("claim_token") or token)

    try:
        phase = str(stored.get("phase") or PHASE_PENDING)

        if needs_reverse or phase in (PHASE_REVERSING, PHASE_REVERSED, PHASE_CANCELLED):
            live_journal = await find_sales_receipt_source_journal(db, rec["id"])
            live_acc = accounting_status_from_journal(live_journal)
            if live_acc == ACCOUNTING_POSTED or phase in (PHASE_PENDING, PHASE_REVERSING):
                if phase == PHASE_PENDING:
                    await _advance_phase(
                        db, cancel_id, (PHASE_PENDING,), PHASE_REVERSING
                    )
                if pause_after == PHASE_REVERSING:
                    raise CancelPaused(PHASE_REVERSING)
                await _reverse_posted_receipt_under_fence(
                    db, rec, user, reason, cancel_id=cancel_id, token=token
                )
                await _advance_phase(
                    db,
                    cancel_id,
                    (PHASE_PENDING, PHASE_REVERSING),
                    PHASE_REVERSED,
                    {"financial_complete": True},
                )
                if pause_after == PHASE_REVERSED:
                    raise CancelPaused(PHASE_REVERSED)
            elif live_acc == ACCOUNTING_REVERSED:
                from services.sales_receipt_posting import _finalize_reversal_operational_state

                await _finalize_reversal_operational_state(db, rec, user)
                await _advance_phase(
                    db,
                    cancel_id,
                    (PHASE_PENDING, PHASE_REVERSING, PHASE_REVERSED),
                    PHASE_REVERSED,
                    {"financial_complete": True},
                )
            elif needs_reverse:
                raise HTTPException(
                    409, "Jurnal penerimaan yang diharapkan tidak ditemukan."
                )

        live = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or {}
        if not receipt_is_cancelled(live):
            if pause_after == "before_business_cancel":
                raise CancelPaused("before_business_cancel")
            await _soft_cancel_receipt_business(
                db, rec, user, reason, cancel_id=cancel_id, token=token
            )
        await _advance_phase(
            db,
            cancel_id,
            (PHASE_PENDING, PHASE_REVERSING, PHASE_REVERSED, PHASE_CANCELLED),
            PHASE_CANCELLED,
            {"business_complete": True},
        )
        if pause_after == PHASE_CANCELLED:
            raise CancelPaused(PHASE_CANCELLED)

        detail = await _public_receipt(db, rec["id"])
        if not receipt_is_cancelled(detail):
            raise HTTPException(409, "Status bisnis penerimaan belum dibatalkan.")
        if needs_reverse:
            live_journal = await find_sales_receipt_source_journal(db, rec["id"])
            if accounting_status_from_journal(live_journal) != ACCOUNTING_REVERSED:
                raise HTTPException(
                    409,
                    "Pembatalan belum lengkap: jurnal penerimaan belum ter-reverse.",
                )

        await _mark_complete(db, cancel_id)
        await _release_receipt_cancel_fence(db, rec["id"], cancel_id, token)
        await attach_receipt_business_cancel_fields(db, [detail])
        return {
            "receipt": detail,
            "outcome": "cancelled",
            "message": "Penerimaan Penjualan dibatalkan",
            "list_class": LIST_CLASS_CANCELLED,
        }
    except CancelPaused:
        raise
    except HTTPException as exc:
        live_journal = await find_sales_receipt_source_journal(db, rec["id"])
        if accounting_status_from_journal(live_journal) == ACCOUNTING_REVERSED:
            from services.sales_receipt_posting import _finalize_reversal_operational_state

            try:
                await _finalize_reversal_operational_state(db, rec, user)
                detail = await _public_receipt(db, rec["id"])
                if receipt_is_cancelled(detail):
                    await _mark_complete(
                        db, cancel_id, {"financial_complete": True, "business_complete": True}
                    )
                    await _release_receipt_cancel_fence(db, rec["id"], cancel_id, token)
                    return {
                        "receipt": detail,
                        "outcome": "recovered",
                        "message": "Penerimaan Penjualan dibatalkan",
                    }
            except HTTPException:
                pass
        row = await _load_intent(db, DOC_RECEIPT, rec["id"], key)
        if row and row.get("phase") == PHASE_PENDING and not row.get("financial_complete"):
            await _mark_failed(db, cancel_id, getattr(exc, "detail", str(exc)))
            await _release_receipt_cancel_fence(db, rec["id"], cancel_id, token)
        raise
