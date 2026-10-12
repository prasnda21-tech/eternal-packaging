"""Posted/unposted Sales Receipt edit revision (Package C).

Preserves receipt id / number / invoice linkage. Posted path reverse/reposts
without cancelling the receipt. Flag `sales_receipt_edit_revision_enabled`
defaults OFF. Does NOT create indexes at runtime — fail closed if missing.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

FLAG_KEY = "sales_receipt_edit_revision_enabled"
COLLECTION = "sales_receipt_edit_revisions"
REQUIRED_INTENT_INDEX_NAME = "uniq_sales_receipt_edit_revision_key"
PERM_EDIT = "sales.receipt.edit"

FENCE_FIELD = "sales_receipt_edit_revision_fence"
FENCE_KEY_FIELD = "sales_receipt_edit_revision_key"
EDIT_CLAIM_FIELD = "sales_receipt_edit_revision_claim"
EDIT_CLAIM_AT_FIELD = "sales_receipt_edit_revision_claim_at"
APPLIED_FIELD = "edit_revision_applied_id"

PHASE_PREPARED = "prepared"
PHASE_OLD_POSTING_REVERSED = "old_posting_reversed"
PHASE_OLD_SETTLEMENT_RELEASED = "old_settlement_released"
PHASE_RECEIPT_UPDATED = "receipt_updated"
PHASE_NEW_POSTING_CREATED = "new_posting_created"
PHASE_NEW_SETTLEMENT_VERIFIED = "new_settlement_verified"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"

_PHASE_FROM = {
    PHASE_OLD_POSTING_REVERSED: (PHASE_PREPARED, PHASE_OLD_POSTING_REVERSED),
    PHASE_OLD_SETTLEMENT_RELEASED: (PHASE_OLD_POSTING_REVERSED, PHASE_OLD_SETTLEMENT_RELEASED),
    PHASE_RECEIPT_UPDATED: (PHASE_OLD_SETTLEMENT_RELEASED, PHASE_RECEIPT_UPDATED),
    PHASE_NEW_POSTING_CREATED: (PHASE_RECEIPT_UPDATED, PHASE_NEW_POSTING_CREATED),
    PHASE_NEW_SETTLEMENT_VERIFIED: (PHASE_NEW_POSTING_CREATED, PHASE_NEW_SETTLEMENT_VERIFIED),
    PHASE_COMPLETE: (PHASE_NEW_SETTLEMENT_VERIFIED,),
    PHASE_FAILED: (PHASE_PREPARED,),
}
_PHASE_RANK = {
    PHASE_PREPARED: 0,
    PHASE_OLD_POSTING_REVERSED: 1,
    PHASE_OLD_SETTLEMENT_RELEASED: 2,
    PHASE_RECEIPT_UPDATED: 3,
    PHASE_NEW_POSTING_CREATED: 4,
    PHASE_NEW_SETTLEMENT_VERIFIED: 5,
    PHASE_COMPLETE: 6,
}


class RevisionPaused(Exception):
    """Deterministic stop for crash-window tests. Not an API error."""

    def __init__(self, phase: str):
        self.phase = phase
        super().__init__(phase)


class RevisionCompleted(Exception):
    """Another worker already verified financial completion."""


def _pause(stop_after: Optional[str], marker: str) -> None:
    if stop_after and stop_after == marker:
        raise RevisionPaused(marker)


async def is_sales_receipt_edit_revision_enabled(db) -> bool:
    try:
        doc = await db.app_settings.find_one({"key": FLAG_KEY}, {"_id": 0, "enabled": 1, "value": 1})
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


def revision_fence_is_active(receipt: Optional[dict]) -> bool:
    return bool(str((receipt or {}).get(FENCE_FIELD) or "").strip())


def revision_fence_absent_clause() -> Dict[str, Any]:
    return {
        "$or": [
            {FENCE_FIELD: {"$exists": False}},
            {FENCE_FIELD: None},
            {FENCE_FIELD: ""},
        ]
    }


async def incomplete_edit_revision(db, receipt_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {
            "receipt_id": str(receipt_id),
            "financial_complete": {"$ne": True},
            "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
        },
        {"_id": 0},
    )


async def raise_if_receipt_revision_in_progress(db, receipt_id: str) -> None:
    receipt = await db.sales_receipts.find_one(
        {"id": str(receipt_id)},
        {"_id": 0, FENCE_FIELD: 1},
    )
    if revision_fence_is_active(receipt) or await incomplete_edit_revision(db, receipt_id):
        raise HTTPException(409, "Revisi penerimaan belum selesai.")


async def _role_defaults_from_db(db) -> Optional[Dict[str, list]]:
    """Hak Akses role_permissions — same source as require_cancel_permission."""
    try:
        docs = await db.role_permissions.find(
            {}, {"_id": 0, "role": 1, "permissions": 1}
        ).to_list(200)
    except Exception:
        return None
    out: Dict[str, list] = {}
    for doc in docs or []:
        role = str(doc.get("role") or "").strip()
        if not role:
            continue
        out[role] = list(doc.get("permissions") or [])
    return out or None


async def _require_edit_permission(db, user: dict) -> None:
    """Opt-in only — aligned with require_cancel_permission / Role & Hak Akses.

    No admin/super-admin bypass. Explicit override True wins. Explicit False
    denies. Otherwise effective_permissions(role, override, role_permissions).
    """
    from permissions import OPT_IN_ONLY_PERMISSIONS, effective_permissions

    if PERM_EDIT not in OPT_IN_ONLY_PERMISSIONS:
        raise HTTPException(500, f"Permission '{PERM_EDIT}' bukan opt-in.")
    override = (user or {}).get("permissions_override") or {}
    if override.get(PERM_EDIT) is True:
        return
    if override.get(PERM_EDIT) is False:
        raise HTTPException(403, f"Anda tidak punya permission '{PERM_EDIT}'")
    role_override = await _role_defaults_from_db(db)
    perms = effective_permissions(
        str((user or {}).get("role") or ""),
        override,
        role_override,
    )
    if PERM_EDIT not in perms:
        raise HTTPException(403, f"Anda tidak punya permission '{PERM_EDIT}'")


async def _primary_index_ready(db) -> bool:
    from services.financial_journal_foundation import (
        REQUIRED_PRIMARY_INDEX_NAME,
        REQUIRED_PRIMARY_INDEX_PARTIAL,
    )

    try:
        info = await db.journal_entries.index_information()
    except Exception:
        return False
    spec = info.get(REQUIRED_PRIMARY_INDEX_NAME) or {}
    if not spec.get("unique"):
        return False
    partial = spec.get("partialFilterExpression") or {}
    return (
        partial.get("status") == REQUIRED_PRIMARY_INDEX_PARTIAL["status"]
        and "reversal_of" in partial
        and partial.get("reversal_of") is None
    )


async def _intent_index_ready(db) -> bool:
    """Fail closed when unique (receipt_id, idempotency_key) index is absent. No DDL."""
    try:
        info = await db[COLLECTION].index_information()
    except Exception:
        return False
    spec = info.get(REQUIRED_INTENT_INDEX_NAME) or {}
    if not spec.get("unique"):
        return False
    key = list(spec.get("key") or [])
    expected = [("receipt_id", 1), ("idempotency_key", 1)]
    return key == expected


async def _activation_blockers(db) -> list:
    from services.financial_journal_foundation import (
        is_financial_journal_revision_enabled,
        revision_source_allowlist,
    )
    from services.sales_receipt_posting import SOURCE_TYPE

    blockers = []
    if not await is_sales_receipt_edit_revision_enabled(db):
        blockers.append("edit_revision_flag_off")
    if not await is_financial_journal_revision_enabled(db):
        blockers.append("global_flag_off")
    if SOURCE_TYPE not in await revision_source_allowlist(db):
        blockers.append("sales_receipt_not_allowlisted")
    if not await _primary_index_ready(db):
        blockers.append("primary_index_not_ready")
    if not await _intent_index_ready(db):
        blockers.append("intent_index_not_ready")
    return blockers


async def posted_revision_ready(db) -> bool:
    return not await _activation_blockers(db)


def _as_money(raw: Any) -> Optional[Decimal]:
    from services.financial_statement_common import MONEY_Q

    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw)).quantize(MONEY_Q)
    except Exception:
        return None


def _fingerprint(proposed: dict) -> str:
    payload = {
        "receipt_date": str(proposed.get("receipt_date") or ""),
        "amount": str(proposed.get("amount") or ""),
        "allocated_amount": str(proposed.get("allocated_amount") or ""),
        "cash_bank_id": str(proposed.get("cash_bank_id") or ""),
        "notes": str(proposed.get("notes") or ""),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _period_closed(db, canonical_date: str) -> bool:
    from services.accounting_periods import get_period_raw, period_key_from_date

    key = period_key_from_date(canonical_date)
    doc = await get_period_raw(db, key)
    return bool(doc and str(doc.get("status") or "") == "closed")


async def _assert_periods_open(db, original_date: str, new_date: str) -> None:
    if await _period_closed(db, original_date):
        raise HTTPException(409, "Periode jurnal asli tertutup. Revisi penerimaan tidak dijalankan.")
    if await _period_closed(db, new_date):
        raise HTTPException(409, "Periode jurnal baru tertutup. Revisi penerimaan tidak dijalankan.")


async def _load_revision(db, receipt_id: str, key: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {"receipt_id": str(receipt_id), "idempotency_key": key},
        {"_id": 0},
    )


def _verified_complete(row: Optional[dict]) -> bool:
    return bool(row) and row.get("phase") == PHASE_COMPLETE and row.get("financial_complete") is True


async def _advance_phase(db, revision_id: str, target: str, patch: Optional[dict] = None) -> dict:
    from services.sales_receipts import now_iso

    extra = dict(patch or {})
    extra.pop("phase", None)
    if target != PHASE_COMPLETE:
        extra.pop("financial_complete", None)
    fields = {**extra, "phase": target, "updated_at": now_iso()}
    if target == PHASE_FAILED:
        fields["financial_complete"] = False
    if target == PHASE_COMPLETE:
        fields["financial_complete"] = True
    result = await db[COLLECTION].update_one(
        {
            "id": str(revision_id),
            "phase": {"$in": list(_PHASE_FROM[target])},
            "financial_complete": {"$ne": True},
        },
        {"$set": fields},
    )
    current = await db[COLLECTION].find_one({"id": str(revision_id)}, {"_id": 0})
    if result.modified_count or result.matched_count:
        return current or {}
    if current and current.get("phase") == target and current.get("financial_complete") is not True:
        return current
    current_rank = _PHASE_RANK.get(str((current or {}).get("phase") or ""), -1)
    target_rank = _PHASE_RANK.get(target, -1)
    if current and current.get("phase") != PHASE_FAILED and current_rank > target_rank:
        return current
    if _verified_complete(current):
        return current or {}
    raise HTTPException(409, {"code": "revision_pending", "message": "Revisi penerimaan belum selesai."})


async def _acquire_fence(db, receipt_id: str, revision_id: str, token: str, key: str) -> Optional[dict]:
    from services.sales_business_cancel import receipt_cancel_fence_absent_clause
    from services.sales_receipt_posting import (
        CLAIM_FIELD,
        REVERSE_CLAIM_FIELD,
        delete_claim_absent_or_stale_clause,
    )
    from services.sales_receipts import now_iso

    now = now_iso()
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "financial_status": {"$nin": ["cancelled", "reversed"]},
            "cancelled": {"$ne": True},
            "$and": [
                revision_fence_absent_clause(),
                receipt_cancel_fence_absent_clause(),
                delete_claim_absent_or_stale_clause(),
                {"$or": [
                    {CLAIM_FIELD: {"$exists": False}},
                    {CLAIM_FIELD: None},
                    {CLAIM_FIELD: ""},
                ]},
                {"$or": [
                    {REVERSE_CLAIM_FIELD: {"$exists": False}},
                    {REVERSE_CLAIM_FIELD: None},
                    {REVERSE_CLAIM_FIELD: ""},
                ]},
            ],
        },
        {"$set": {
            FENCE_FIELD: revision_id,
            FENCE_KEY_FIELD: key,
            EDIT_CLAIM_FIELD: token,
            EDIT_CLAIM_AT_FIELD: now,
            "updated_at": now,
        }},
        return_document=ReturnDocument.AFTER,
    )


async def _touch_owned_fence(db, receipt_id: str, revision_id: str, token: str) -> Optional[dict]:
    from services.sales_receipts import now_iso

    now = now_iso()
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            FENCE_FIELD: revision_id,
            EDIT_CLAIM_FIELD: token,
            "cancelled": {"$ne": True},
        },
        {"$set": {EDIT_CLAIM_AT_FIELD: now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_fence(db, receipt_id: str, revision_id: str, token: str) -> None:
    from services.sales_receipts import now_iso

    await db.sales_receipts.update_one(
        {"id": str(receipt_id), FENCE_FIELD: revision_id, EDIT_CLAIM_FIELD: token},
        {"$unset": {
            FENCE_FIELD: "",
            FENCE_KEY_FIELD: "",
            EDIT_CLAIM_FIELD: "",
            EDIT_CLAIM_AT_FIELD: "",
        }, "$set": {"updated_at": now_iso()}},
    )


async def _outstanding_excluding_self(db, invoice_id: str, receipt_id: str) -> Dict[str, Any]:
    """Invoice outstanding as if this receipt did not count."""
    from services.financial_statement_common import MONEY_Q, money_float
    from services.sales_customer_credit import invoice_credit_applied_total
    from services.sales_receipt_posting import receipt_allocated_amount, receipt_counts_toward_outstanding

    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")
    rows = await db.sales_receipts.find({"invoice_id": str(invoice_id)}, {"_id": 0}).to_list(500)
    paid = Decimal("0.00")
    self_allocated = Decimal("0.00")
    for row in rows:
        allocated = receipt_allocated_amount(row)
        if str(row.get("id") or "") == str(receipt_id):
            if receipt_counts_toward_outstanding(row):
                self_allocated = allocated
            continue
        if receipt_counts_toward_outstanding(row):
            paid += allocated
    paid += await invoice_credit_applied_total(db, invoice_id)
    paid = paid.quantize(MONEY_Q)
    grand = Decimal(str(invoice.get("grand_total") or 0)).quantize(MONEY_Q)
    outstanding = max(grand - paid, Decimal("0.00")).quantize(MONEY_Q)
    return {
        "invoice": invoice,
        "outstanding_excluding_self": money_float(outstanding),
        "self_allocated": money_float(self_allocated),
        "grand_total": money_float(grand),
    }


async def _proposed_fields(db, receipt: dict, payload: dict) -> dict:
    from services.financial_statement_common import MONEY_Q, money_float
    from services.sales_customer_credit import split_receipt_amounts
    from services.sales_receipt_posting import resolve_cash_bank_for_create

    receipt_date = str(payload.get("receipt_date") or receipt.get("receipt_date") or "").strip()
    if not receipt_date:
        raise HTTPException(400, "Tanggal penerimaan wajib diisi.")
    amount_dec = _as_money(payload.get("amount") if "amount" in payload else receipt.get("amount"))
    if amount_dec is None or amount_dec <= 0:
        raise HTTPException(400, "Jumlah penerimaan harus lebih besar dari 0.")
    facts = await _outstanding_excluding_self(db, receipt["invoice_id"], receipt["id"])
    outstanding = Decimal(str(facts["outstanding_excluding_self"])).quantize(MONEY_Q)
    allocated_raw = payload.get("allocated_amount") if "allocated_amount" in payload else receipt.get("allocated_amount")
    allocated_dec, credit_dec = split_receipt_amounts(
        amount_dec,
        outstanding,
        allocated_raw if allocated_raw is not None else None,
    )
    if allocated_dec > outstanding:
        raise HTTPException(
            400,
            f"Alokasi ({money_float(allocated_dec)}) melebihi sisa tagihan ({money_float(outstanding)}).",
        )
    cash_bank_id = str(payload.get("cash_bank_id") or receipt.get("cash_bank_id") or "").strip()
    if not cash_bank_id:
        raise HTTPException(400, "Kas/Bank wajib dipilih.")
    cash_snap = await resolve_cash_bank_for_create(db, cash_bank_id)
    notes = str(payload.get("notes") if "notes" in payload else receipt.get("notes") or "").strip()
    return {
        "receipt_date": receipt_date,
        "amount": money_float(amount_dec),
        "allocated_amount": money_float(allocated_dec),
        "customer_credit_amount": money_float(credit_dec),
        "notes": notes,
        **cash_snap,
    }


async def _assert_credit_reversible(db, receipt_id: str) -> None:
    from services.sales_customer_credit import assert_source_receipt_reversible

    await assert_source_receipt_reversible(db, receipt_id)


async def _release_settlement(db, receipt_id: str) -> None:
    """Stop counting this receipt toward outstanding without cancelling it."""
    from services.sales_receipt_posting import SETTLEMENT_EFFECTIVE_FIELD
    from services.sales_receipts import _recalc_invoice_payment_from_receipts, now_iso, sync_order_payment_projections

    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    await db.sales_receipts.update_one(
        {"id": str(receipt_id)},
        {"$set": {
            SETTLEMENT_EFFECTIVE_FIELD: False,
            "updated_at": now_iso(),
        }},
    )
    await _recalc_invoice_payment_from_receipts(db, rec.get("invoice_id"))
    if rec.get("po_id"):
        await sync_order_payment_projections(db, rec.get("po_id"))


async def receipt_edit_capability(db, receipt: dict) -> Dict[str, Any]:
    """Safe read-only Edit capability (no secrets / allowlist dump)."""
    from services.sales_receipt_posting import (
        accounting_status_from_journal,
        find_sales_receipt_source_journal,
        receipt_is_cancelled,
        ACCOUNTING_POSTED,
        ACCOUNTING_NOT_POSTED,
    )

    if receipt_is_cancelled(receipt):
        return {
            "editable": False,
            "mode": None,
            "posted_revision_ready": False,
            "reason": "Penerimaan yang dibatalkan tidak dapat diubah.",
        }
    if revision_fence_is_active(receipt) or await incomplete_edit_revision(db, receipt.get("id")):
        return {
            "editable": False,
            "mode": None,
            "posted_revision_ready": False,
            "reason": "Revisi penerimaan belum selesai.",
        }
    journal = await find_sales_receipt_source_journal(db, receipt.get("id"))
    if journal and journal.get("ambiguous"):
        return {
            "editable": False,
            "mode": None,
            "posted_revision_ready": False,
            "reason": "Jurnal penerimaan tidak tunggal.",
        }
    status = accounting_status_from_journal(journal)
    ready = await posted_revision_ready(db)
    if status == ACCOUNTING_POSTED:
        return {
            "editable": ready,
            "mode": "posted_revision" if ready else None,
            "posted_revision_ready": ready,
            "reason": None if ready else "Koreksi keuangan penerimaan belum diaktifkan.",
        }
    if status in (ACCOUNTING_NOT_POSTED, "", None) or journal is None:
        return {
            "editable": True,
            "mode": "unposted",
            "posted_revision_ready": ready,
            "reason": None,
        }
    return {
        "editable": False,
        "mode": None,
        "posted_revision_ready": ready,
        "reason": "Status accounting penerimaan tidak memungkinkan edit.",
    }


async def update_unposted_sales_receipt(db, receipt_id: str, payload: dict, user: dict) -> dict:
    """Safe field update for receipts without an active posted primary journal."""
    from services.sales_receipt_posting import (
        ACCOUNTING_POSTED,
        accounting_status_from_journal,
        find_sales_receipt_source_journal,
        receipt_is_cancelled,
    )
    from services.sales_receipts import (
        _recalc_invoice_payment_from_receipts,
        get_sales_receipt_detail,
        now_iso,
        sync_order_payment_projections,
    )

    await _require_edit_permission(db, user)
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    if receipt_is_cancelled(rec):
        raise HTTPException(409, "Penerimaan yang dibatalkan tidak dapat diubah.")
    await raise_if_receipt_revision_in_progress(db, rec["id"])
    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if journal and journal.get("ambiguous"):
        raise HTTPException(409, "Jurnal penerimaan tidak tunggal.")
    if accounting_status_from_journal(journal) == ACCOUNTING_POSTED:
        raise HTTPException(409, "Penerimaan sudah diposting. Aktifkan revisi keuangan untuk mengubahnya.")
    await _assert_credit_reversible(db, rec["id"])
    proposed = await _proposed_fields(db, rec, payload or {})
    # Locked identity fields never written.
    patch = {
        "receipt_date": proposed["receipt_date"],
        "amount": proposed["amount"],
        "allocated_amount": proposed["allocated_amount"],
        "customer_credit_amount": proposed["customer_credit_amount"],
        "notes": proposed["notes"],
        "cash_bank_id": proposed["cash_bank_id"],
        "cash_bank_name": proposed["cash_bank_name"],
        "cash_bank_account_id": proposed["cash_bank_account_id"],
        "cash_bank_account_code": proposed.get("cash_bank_account_code"),
        "cash_bank_account_name": proposed.get("cash_bank_account_name"),
        "updated_at": now_iso(),
        "updated_by": {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": str(user.get("name") or user.get("email") or ""),
        },
    }
    result = await db.sales_receipts.find_one_and_update(
        {
            "id": rec["id"],
            "cancelled": {"$ne": True},
            "financial_status": {"$nin": ["cancelled", "reversed"]},
            **revision_fence_absent_clause(),
        },
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(409, "Penerimaan tidak dapat diubah karena perubahan bersamaan.")
    await _recalc_invoice_payment_from_receipts(db, rec.get("invoice_id"))
    if rec.get("po_id"):
        await sync_order_payment_projections(db, rec.get("po_id"))
    detail = await get_sales_receipt_detail(db, rec["id"])
    return {
        "receipt": detail,
        "message": "Penerimaan Penjualan berhasil diperbarui",
        "financial_complete": True,
        "mode": "unposted",
    }


async def revise_posted_sales_receipt(
    db,
    receipt_id: str,
    payload: dict,
    user: dict,
    *,
    stop_after: Optional[str] = None,
) -> dict:
    from services.accounting_periods import PeriodLockedError
    from services.journal_core import (
        JournalConflictError,
        JournalValidationError,
        STATUS_POSTED,
        STATUS_REVERSED,
        find_active_posted_primary_for_source,
        reverse_journal,
    )
    from services.sales_receipt_posting import (
        SOURCE_REVERSAL,
        SOURCE_TYPE,
        find_sales_receipt_source_journal,
        journal_date_from_receipt,
        post_sales_receipt_accounting,
        receipt_is_cancelled,
    )
    from services.sales_receipts import (
        apply_operational_settlement_for_receipt,
        get_sales_receipt_detail,
        now_iso,
        verify_settlement_projections,
    )

    await _require_edit_permission(db, user)
    blockers = await _activation_blockers(db)
    if blockers:
        raise HTTPException(409, {"code": "revision_not_ready", "blockers": blockers})

    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    if receipt_is_cancelled(rec):
        raise HTTPException(409, "Penerimaan yang dibatalkan tidak dapat diubah.")

    key = str((payload or {}).get("idempotency_key") or "").strip()
    if not key:
        raise HTTPException(400, "idempotency_key wajib diisi untuk revisi penerimaan yang sudah diposting.")

    await _assert_credit_reversible(db, rec["id"])
    proposed = await _proposed_fields(db, rec, payload or {})
    fingerprint = _fingerprint(proposed)
    stored = await _load_revision(db, rec["id"], key)
    if stored and stored.get("fingerprint") != fingerprint:
        raise HTTPException(409, "Idempotency key sudah dipakai untuk payload lain.")
    if stored and _verified_complete(stored):
        await _release_fence(
            db, rec["id"], str(stored.get("id") or ""), str(stored.get("claim_token") or ""),
        )
        detail = await get_sales_receipt_detail(db, rec["id"])
        return {
            "receipt": detail,
            "message": "Penerimaan Penjualan berhasil direvisi",
            "financial_complete": True,
            "mode": "posted_revision",
        }
    if stored and stored.get("phase") == PHASE_FAILED:
        raise HTTPException(409, "Revisi penerimaan gagal dan tidak dapat dilanjutkan dengan key yang sama.")

    other = await db[COLLECTION].find_one({
        "receipt_id": rec["id"],
        "idempotency_key": {"$ne": key},
        "financial_complete": {"$ne": True},
        "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
    })
    if other:
        raise HTTPException(409, "Revisi penerimaan lain belum selesai.")

    current = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or rec
    fence_id = str(current.get(FENCE_FIELD) or "")
    fence_key = str(current.get(FENCE_KEY_FIELD) or "")
    if fence_id and fence_key != key:
        raise HTTPException(409, "Revisi penerimaan lain belum selesai.")

    original_journal = await find_sales_receipt_source_journal(db, rec["id"])
    if original_journal and original_journal.get("ambiguous"):
        raise HTTPException(409, "Jurnal penerimaan tidak tunggal.")
    if not original_journal or original_journal.get("status") not in {STATUS_POSTED, STATUS_REVERSED}:
        if not stored:
            raise HTTPException(409, "Hanya penerimaan yang sudah diposting yang dapat direvisi keuangan.")

    original_date = str(
        (stored or {}).get("original_journal_date")
        or (original_journal or {}).get("date")
        or journal_date_from_receipt(rec)
    )
    new_date = str(proposed["receipt_date"])

    revision_id = ""
    token = ""

    async def _own(owned_revision_id: str, owned_token: str) -> dict:
        owned = await _touch_owned_fence(db, rec["id"], owned_revision_id, owned_token)
        current_row = await db[COLLECTION].find_one({"id": str(owned_revision_id)}, {"_id": 0})
        if _verified_complete(current_row):
            raise RevisionCompleted()
        if not owned:
            raise HTTPException(409, {"code": "revision_pending", "message": "Kepemilikan revisi penerimaan sudah berubah."})
        return owned

    try:
        if stored:
            revision_id = str(stored["id"])
            token = str(stored.get("claim_token") or "")
            if not token:
                raise HTTPException(409, {"code": "revision_pending", "message": "Revisi penerimaan belum selesai."})
            await _own(revision_id, token)
            proposed = stored.get("proposed") or proposed
        elif fence_id and fence_key == key:
            revision_id = fence_id
            token = str(current.get(EDIT_CLAIM_FIELD) or "")
            await _own(revision_id, token)
        else:
            await _assert_periods_open(db, original_date, new_date)
            revision_id = str(uuid.uuid4())
            token = str(uuid.uuid4())
            claimed = await _acquire_fence(db, rec["id"], revision_id, token, key)
            if not claimed:
                raced = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or {}
                if str(raced.get(FENCE_KEY_FIELD) or "") == key and str(raced.get(FENCE_FIELD) or ""):
                    revision_id = str(raced.get(FENCE_FIELD))
                    token = str(raced.get(EDIT_CLAIM_FIELD) or "")
                    await _own(revision_id, token)
                else:
                    raise HTTPException(409, "Penerimaan sedang direvisi atau dikunci proses lain.")

        if not stored:
            from pymongo.errors import DuplicateKeyError

            try:
                await db[COLLECTION].insert_one({
                    "id": revision_id,
                    "receipt_id": rec["id"],
                    "idempotency_key": key,
                    "fingerprint": fingerprint,
                    "phase": PHASE_PREPARED,
                    "financial_complete": False,
                    "claim_token": token,
                    "proposed": proposed,
                    "original_journal_id": (original_journal or {}).get("id"),
                    "original_journal_date": original_date,
                    "created_at": now_iso(),
                })
            except DuplicateKeyError:
                pass
            stored = await _load_revision(db, rec["id"], key)
            if not stored:
                raise HTTPException(409, {"code": "revision_pending", "message": "Revisi penerimaan belum selesai."})
            revision_id = str(stored["id"])
            token = str(stored.get("claim_token") or token)
            proposed = stored.get("proposed") or proposed
            if stored.get("fingerprint") != fingerprint:
                raise HTTPException(409, "Idempotency key sudah dipakai untuk payload lain.")

        original_id = str(stored.get("original_journal_id") or (original_journal or {}).get("id") or "")
        original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0}) if original_id else None
        already_reversed = bool(original and original.get("status") == STATUS_REVERSED)

        await _own(revision_id, token)
        _pause(stop_after, "before_reverse")

        # PREPARED → OLD_POSTING_REVERSED (never cancel receipt)
        if original and original.get("status") == STATUS_POSTED:
            await _assert_periods_open(db, original_date, new_date)
            await _own(revision_id, token)
            try:
                reversal = await reverse_journal(
                    db,
                    original_id,
                    source_type=SOURCE_REVERSAL,
                    source_id=str(rec["id"]),
                    posted_by=user,
                    description=f"Revisi Penerimaan Penjualan {rec.get('receipt_number') or rec['id']}",
                )
            except PeriodLockedError as exc:
                original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0})
                if not original or original.get("status") != STATUS_REVERSED:
                    raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
                reversal = await db.journal_entries.find_one({"reversal_of": original_id}, {"_id": 0})
            except (JournalValidationError, JournalConflictError) as exc:
                original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0})
                if not original or original.get("status") != STATUS_REVERSED:
                    raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
                reversal = await db.journal_entries.find_one({"reversal_of": original_id}, {"_id": 0})
            _pause(stop_after, "after_reverse_journal")
            await _advance_phase(db, revision_id, PHASE_OLD_POSTING_REVERSED, {
                "original_journal_id": original_id,
                "reversal_journal_id": (reversal or {}).get("id"),
            })
        elif already_reversed or (original and original.get("status") == STATUS_REVERSED):
            reversal = await db.journal_entries.find_one({"reversal_of": original_id}, {"_id": 0})
            await _advance_phase(db, revision_id, PHASE_OLD_POSTING_REVERSED, {
                "original_journal_id": original_id,
                "reversal_journal_id": (reversal or {}).get("id"),
            })
        else:
            raise HTTPException(409, {"code": "revision_pending", "message": "Jurnal asli tidak dapat di-reverse."})
        _pause(stop_after, "after_reverse")

        # OLD_SETTLEMENT_RELEASED
        await _own(revision_id, token)
        phase_row = await db[COLLECTION].find_one({"id": revision_id}, {"_id": 0, "phase": 1})
        phase = str((phase_row or {}).get("phase") or "")
        if phase == PHASE_OLD_POSTING_REVERSED:
            await _release_settlement(db, rec["id"])
            await _advance_phase(db, revision_id, PHASE_OLD_SETTLEMENT_RELEASED)
        _pause(stop_after, "after_settlement_release")

        # RECEIPT_UPDATED — same id/number/invoice_id; never cancelled
        await _own(revision_id, token)
        fresh = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or rec
        if receipt_is_cancelled(fresh):
            raise HTTPException(409, "Penerimaan dibatalkan selama revisi.")
        if str(fresh.get(APPLIED_FIELD) or "") != revision_id:
            applied = await db.sales_receipts.find_one_and_update(
                {
                    "id": rec["id"],
                    EDIT_CLAIM_FIELD: token,
                    FENCE_FIELD: revision_id,
                    "cancelled": {"$ne": True},
                },
                {"$set": {
                    "receipt_date": proposed["receipt_date"],
                    "amount": proposed["amount"],
                    "allocated_amount": proposed["allocated_amount"],
                    "customer_credit_amount": proposed["customer_credit_amount"],
                    "notes": proposed["notes"],
                    "cash_bank_id": proposed["cash_bank_id"],
                    "cash_bank_name": proposed["cash_bank_name"],
                    "cash_bank_account_id": proposed["cash_bank_account_id"],
                    "cash_bank_account_code": proposed.get("cash_bank_account_code"),
                    "cash_bank_account_name": proposed.get("cash_bank_account_name"),
                    "financial_status": "active",
                    APPLIED_FIELD: revision_id,
                    "updated_at": now_iso(),
                }},
                return_document=ReturnDocument.AFTER,
            )
            if not applied:
                raced_row = await db[COLLECTION].find_one({"id": revision_id}, {"_id": 0})
                if _verified_complete(raced_row):
                    raise RevisionCompleted()
                raise HTTPException(409, {"code": "revision_pending", "message": "Perubahan penerimaan belum tersimpan."})
            # Identity lock verification
            if (
                str(applied.get("id")) != str(rec["id"])
                or str(applied.get("receipt_number")) != str(rec.get("receipt_number"))
                or str(applied.get("invoice_id")) != str(rec.get("invoice_id"))
            ):
                raise HTTPException(500, "Identitas penerimaan berubah selama revisi.")
        await _advance_phase(db, revision_id, PHASE_RECEIPT_UPDATED)
        _pause(stop_after, "after_receipt_update")

        # NEW_POSTING_CREATED
        await _own(revision_id, token)
        phase_row = await db[COLLECTION].find_one({"id": revision_id}, {"_id": 0, "phase": 1})
        already_reposted = str((phase_row or {}).get("phase") or "") in {
            PHASE_NEW_POSTING_CREATED,
            PHASE_NEW_SETTLEMENT_VERIFIED,
            PHASE_COMPLETE,
        }
        active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, rec["id"])
        if not active and not already_reposted:
            try:
                await post_sales_receipt_accounting(
                    db,
                    rec["id"],
                    user,
                    cash_bank_id=proposed["cash_bank_id"],
                    serialize_in_process=False,
                    bypass_revision_guard=True,
                )
            except HTTPException as exc:
                active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, rec["id"])
                if not active:
                    raise HTTPException(
                        409,
                        {"code": "revision_pending", "message": str(exc.detail if isinstance(exc.detail, str) else exc.detail)},
                    ) from exc
        active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, rec["id"])
        if not active:
            raise HTTPException(409, {"code": "revision_pending", "message": "Jurnal baru belum tersedia."})
        await _advance_phase(db, revision_id, PHASE_NEW_POSTING_CREATED, {
            "new_journal_id": active.get("id"),
        })
        _pause(stop_after, "after_repost")

        # NEW_SETTLEMENT_VERIFIED → COMPLETE
        await _own(revision_id, token)
        await apply_operational_settlement_for_receipt(db, rec["id"], user)
        verified = await verify_settlement_projections(db, rec["id"])
        if not verified.get("ok"):
            raise HTTPException(
                409,
                {
                    "code": "revision_pending",
                    "message": "Settlement penerimaan belum terverifikasi.",
                    "reason": verified.get("reason"),
                },
            )
        await _advance_phase(db, revision_id, PHASE_NEW_SETTLEMENT_VERIFIED)
        _pause(stop_after, "after_settlement")

        # Confirm receipt not cancelled by reverse path
        final_rec = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or {}
        if receipt_is_cancelled(final_rec):
            raise HTTPException(500, "Revisi tidak boleh membatalkan penerimaan.")

        await _advance_phase(db, revision_id, PHASE_COMPLETE, {
            "new_journal_id": active.get("id"),
            "reversal_journal_id": (await db.journal_entries.find_one({"reversal_of": original_id}, {"_id": 0}) or {}).get("id"),
        })
        await _release_fence(db, rec["id"], revision_id, token)
        detail = await get_sales_receipt_detail(db, rec["id"])
        return {
            "receipt": detail,
            "message": "Penerimaan Penjualan berhasil direvisi",
            "financial_complete": True,
            "mode": "posted_revision",
        }
    except RevisionCompleted:
        row = await _load_revision(db, rec["id"], key)
        if row and row.get("claim_token"):
            await _release_fence(db, rec["id"], str(row.get("id") or ""), str(row.get("claim_token") or ""))
        detail = await get_sales_receipt_detail(db, rec["id"])
        return {
            "receipt": detail,
            "message": "Penerimaan Penjualan berhasil direvisi",
            "financial_complete": True,
            "mode": "posted_revision",
        }
    except RevisionPaused:
        raise
    except HTTPException:
        raise


async def update_sales_receipt(db, receipt_id: str, payload: dict, user: dict) -> dict:
    """Canonical PUT handler: unposted field edit or posted financial revision."""
    from services.sales_receipt_posting import (
        ACCOUNTING_POSTED,
        accounting_status_from_journal,
        find_sales_receipt_source_journal,
        receipt_is_cancelled,
    )

    await _require_edit_permission(db, user)
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    if receipt_is_cancelled(rec):
        raise HTTPException(409, "Penerimaan yang dibatalkan tidak dapat diubah.")

    # Reject attempts to reassign invoice / identity via payload (ignore silently for safety).
    if payload:
        for locked in ("id", "receipt_number", "invoice_id", "po_id", "po_number", "customer", "customer_id"):
            if locked in payload and str(payload.get(locked) or "") not in ("", str(rec.get(locked) or "")):
                if str(payload.get(locked)) != str(rec.get(locked)):
                    raise HTTPException(400, f"Field '{locked}' tidak dapat diubah.")

    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if journal and journal.get("ambiguous"):
        raise HTTPException(409, "Jurnal penerimaan tidak tunggal.")
    status = accounting_status_from_journal(journal)
    if status == ACCOUNTING_POSTED:
        return await revise_posted_sales_receipt(db, rec["id"], payload or {}, user)
    return await update_unposted_sales_receipt(db, rec["id"], payload or {}, user)
