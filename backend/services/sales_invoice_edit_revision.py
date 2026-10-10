"""Posted Sales Invoice edit: reverse the active journal and repost.

Does not cancel the invoice and does not call reverse-to-cancel.
Flag `sales_invoice_edit_revision_enabled` defaults OFF.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

FLAG_KEY = "sales_invoice_edit_revision_enabled"
COLLECTION = "sales_invoice_edit_revisions"
EDIT_CLAIM_FIELD = "sales_invoice_edit_revision_claim"
EDIT_CLAIM_AT_FIELD = "sales_invoice_edit_revision_claim_at"
EDIT_CLAIM_TTL_SECONDS = 120
FENCE_FIELD = "sales_invoice_edit_revision_fence"
FENCE_KEY_FIELD = "sales_invoice_edit_revision_key"
APPLIED_FIELD = "edit_revision_applied_id"

PHASE_PENDING = "pending"
PHASE_REVERSED = "reversed"
PHASE_INVOICE = "invoice_saved"
PHASE_REPOSTED = "reposted"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"


class RevisionPaused(Exception):
    """Deterministic stop for crash-window tests. Not an API error."""

    def __init__(self, phase: str):
        self.phase = phase
        super().__init__(phase)


class RevisionCompleted(Exception):
    """Another worker already verified financial completion."""


async def is_sales_invoice_edit_revision_enabled(db) -> bool:
    try:
        doc = await db.app_settings.find_one({"key": FLAG_KEY}, {"_id": 0, "enabled": 1, "value": 1})
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


def revision_fence_is_active(invoice: Optional[dict]) -> bool:
    return bool(str((invoice or {}).get(FENCE_FIELD) or "").strip())


def revision_fence_absent_clause() -> Dict[str, Any]:
    return {
        "$or": [
            {FENCE_FIELD: {"$exists": False}},
            {FENCE_FIELD: None},
            {FENCE_FIELD: ""},
        ]
    }


def edit_revision_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """Active for the life of the fence. A claim timestamp does not release it."""
    del now
    return revision_fence_is_active(invoice)


def edit_revision_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Durable fence absence. Callers must not treat an expired timestamp as free."""
    del now
    return revision_fence_absent_clause()


async def raise_if_revision_in_progress(db, invoice_id: str) -> None:
    invoice = await db.sales_invoices.find_one(
        {"id": str(invoice_id)},
        {"_id": 0, FENCE_FIELD: 1},
    )
    if revision_fence_is_active(invoice) or await incomplete_edit_revision(db, invoice_id):
        raise HTTPException(409, "Revisi faktur belum selesai.")


async def incomplete_edit_revision(db, invoice_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {
            "invoice_id": str(invoice_id),
            "financial_complete": {"$ne": True},
            "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
        },
        {"_id": 0},
    )


async def _require_edit_permission(user: dict) -> None:
    from permissions import effective_permissions

    if str((user or {}).get("role") or "") == "admin":
        return
    perms = effective_permissions(
        str((user or {}).get("role") or ""),
        (user or {}).get("permissions_override") or {},
    )
    if "sales.invoice.edit" not in perms:
        raise HTTPException(403, "Anda tidak punya permission 'sales.invoice.edit'")


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


async def _activation_blockers(db) -> list:
    from services.financial_journal_foundation import (
        is_financial_journal_revision_enabled,
        revision_source_allowlist,
    )
    from services.sales_invoice_posting import SOURCE_TYPE

    blockers = []
    if not await is_sales_invoice_edit_revision_enabled(db):
        blockers.append("edit_revision_flag_off")
    if not await is_financial_journal_revision_enabled(db):
        blockers.append("global_flag_off")
    if SOURCE_TYPE not in await revision_source_allowlist(db):
        blockers.append("sales_invoice_not_allowlisted")
    if not await _primary_index_ready(db):
        blockers.append("primary_index_not_ready")
    return blockers


async def _ensure_revision_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("invoice_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="uniq_sales_invoice_edit_revision_key",
    )


def _fingerprint(proposed: dict) -> str:
    body = {
        "invoice_date": proposed.get("invoice_date"),
        "payment_term_days": proposed.get("payment_term_days"),
        "discount_amount": proposed.get("discount_amount"),
        "ppn_percent": proposed.get("ppn_percent"),
        "notes": proposed.get("notes") or "",
        "lines": [
            {
                "line_idx": line.get("line_idx"),
                "quantity": line.get("quantity"),
                "unit_price": line.get("unit_price"),
            }
            for line in proposed.get("lines") or []
        ],
        "other_charges": proposed.get("other_charges") or [],
    }
    raw = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _snapshot(invoice: dict) -> dict:
    keep = (
        "id", "invoice_number", "po_id", "financial_status", "invoice_date", "due_date",
        "payment_term_days", "lines", "subtotal", "discount_amount", "after_discount",
        "ppn_percent", "ppn_amount", "other_charges", "other_charges_total", "grand_total",
        "paid_total", "outstanding_amount", "notes", "updated_at",
    )
    return {key: invoice.get(key) for key in keep}


async def _proposed_fields(db, invoice: dict, payload: dict) -> dict:
    from decimal import Decimal

    from services.sales_invoice_dates import SalesInvoiceDateError, persist_sales_invoice_date
    from services.sales_invoices import (
        FINANCIAL_FINALIZED,
        _explicit_draft_qty_by_line,
        _parse_non_negative_price,
        assert_invoice_qty_within_po,
        normalize_other_charges,
        now_iso,
        other_charges_total,
    )

    existing_lines = invoice.get("lines") or []
    input_lines = payload.get("lines") or payload.get("line_items") or []
    explicit_qty = _explicit_draft_qty_by_line(existing_lines, input_lines)
    input_by_idx = {}
    seen = set()
    for fallback, inp in enumerate(input_lines):
        line_idx = int(inp.get("line_idx", fallback))
        if line_idx in seen:
            raise HTTPException(400, "Baris faktur duplikat.")
        seen.add(line_idx)
        input_by_idx[line_idx] = inp

    updated_lines = []
    subtotal = 0.0
    total_qty = 0
    for idx, el in enumerate(existing_lines):
        line_idx = int(el.get("line_idx", idx))
        qty = explicit_qty[line_idx] if line_idx in explicit_qty else int(el.get("quantity") or 0)
        if qty <= 0:
            raise HTTPException(400, "Qty faktur harus lebih besar dari 0.")
        if line_idx in input_by_idx and input_by_idx[line_idx].get("unit_price") not in (None, ""):
            unit_price = _parse_non_negative_price(input_by_idx[line_idx].get("unit_price"), "Harga satuan")
        else:
            unit_price = _parse_non_negative_price(el.get("unit_price", 0), "Harga satuan")
        line_subtotal = round(float(Decimal(str(qty)) * Decimal(str(unit_price))), 2)
        subtotal += line_subtotal
        total_qty += qty
        row = dict(el)
        row["quantity"] = qty
        row["unit_price"] = unit_price
        row["line_subtotal"] = line_subtotal
        row["line_total"] = line_subtotal
        for derived in ("current_qty", "po_qty", "other_active_invoiced_qty", "max_qty"):
            row.pop(derived, None)
        updated_lines.append(row)

    subtotal = round(subtotal, 2)
    proposed_invoice = {**invoice, "lines": updated_lines}
    await assert_invoice_qty_within_po(db, proposed_invoice, finalize=True)

    raw_disc = payload.get("discount_amount") if "discount_amount" in payload else invoice.get("discount_amount", 0)
    discount_amount = _parse_non_negative_price(raw_disc, "Diskon")
    if discount_amount > subtotal:
        raise HTTPException(400, "Diskon tidak boleh melebihi subtotal.")
    after_discount = round(float(Decimal(str(subtotal)) - Decimal(str(discount_amount))), 2)
    raw_ppn = payload.get("ppn_percent") if "ppn_percent" in payload else invoice.get("ppn_percent", 0)
    ppn_percent = _parse_non_negative_price(raw_ppn, "Persentase PPN")
    ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)
    if payload.get("other_charges") is None:
        other_charges = list(invoice.get("other_charges") or [])
    else:
        other_charges = await normalize_other_charges(db, payload.get("other_charges"))
    charges_total = float(other_charges_total({"other_charges": other_charges}))
    grand = round(float(Decimal(str(after_discount)) + Decimal(str(ppn_amount)) + Decimal(str(charges_total))), 2)
    paid = round(float(invoice.get("paid_total") or 0), 2)
    if paid - grand > 0.009:
        raise HTTPException(409, "Total baru lebih kecil dari jumlah yang sudah dibayar.")
    raw_term = payload.get("payment_term_days") if "payment_term_days" in payload else invoice.get("payment_term_days", 0)
    try:
        payment_term_days = int(raw_term if raw_term is not None and str(raw_term).strip() != "" else 0)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Termin pembayaran harus berupa angka hari.") from exc
    if payment_term_days < 0:
        raise HTTPException(400, "Termin pembayaran tidak boleh negatif.")
    try:
        inv_dt, invoice_date_str = persist_sales_invoice_date(
            invoice.get("invoice_date"),
            payload.get("invoice_date") if "invoice_date" in payload else None,
        )
    except SalesInvoiceDateError as exc:
        raise HTTPException(400, "Tanggal faktur tidak valid.") from exc
    from datetime import timedelta as _td

    fields = {
        "lines": updated_lines,
        "total_qty": total_qty,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "after_discount": after_discount,
        "ppn_percent": ppn_percent,
        "ppn_amount": ppn_amount,
        "other_charges": other_charges,
        "other_charges_total": charges_total,
        "grand_total": grand,
        "paid_total": paid,
        "outstanding_amount": round(max(grand - paid, 0), 2),
        "payment_term_days": payment_term_days,
        "invoice_date": invoice_date_str,
        "due_date": (inv_dt + _td(days=payment_term_days)).strftime("%Y-%m-%d"),
        "notes": str(payload.get("notes") if "notes" in payload else invoice.get("notes") or "").strip(),
        "financial_status": FINANCIAL_FINALIZED,
        "updated_at": now_iso(),
    }
    return fields


async def _period_closed(db, canonical_date: str) -> bool:
    from services.accounting_periods import get_period_raw, period_key_from_date

    key = period_key_from_date(canonical_date)
    doc = await get_period_raw(db, key)
    return bool(doc and str(doc.get("status") or "") == "closed")


async def _assert_periods_open(db, original_date: str, new_date: str) -> None:
    if await _period_closed(db, original_date):
        raise HTTPException(409, "Periode jurnal asli tertutup. Revisi faktur tidak dijalankan.")
    if await _period_closed(db, new_date):
        raise HTTPException(409, "Periode jurnal baru tertutup. Revisi faktur tidak dijalankan.")


async def _assert_dependencies(
    db,
    invoice: dict,
    new_date: str,
    original_date: str,
    *,
    include_periods: bool = True,
    include_journal: bool = True,
) -> None:
    from services.sales_customer_credit import COLLECTION as CREDIT_COLLECTION
    from services.sales_customer_credit import claim_is_active
    from services.sales_invoice_auto_lifecycle import (
        AUTO_STATUS_FAILED,
        AUTO_STATUS_FIELD,
        AUTO_STATUS_PENDING,
        AUTO_STATUS_POSTING,
        AUTO_STATUS_RETRY_PENDING,
    )
    from services.sales_invoice_posting import (
        SOURCE_REVERSAL,
        SOURCE_TYPE,
        collect_invoice_reverse_receipt_blockers,
        posting_claim_is_active,
        raise_active_receipt_blockers,
        receipt_create_claim_is_active,
        reverse_claim_is_active,
        select_sales_invoice_primary,
    )
    from services.journal_core import STATUS_INCOMPLETE

    if posting_claim_is_active(invoice) or reverse_claim_is_active(invoice) or receipt_create_claim_is_active(invoice):
        raise HTTPException(409, "Faktur sedang dikunci oleh proses akuntansi lain.")
    auto = str(invoice.get(AUTO_STATUS_FIELD) or "")
    if auto in {AUTO_STATUS_PENDING, AUTO_STATUS_POSTING, AUTO_STATUS_FAILED, AUTO_STATUS_RETRY_PENDING}:
        raise HTTPException(409, "Posting otomatis faktur belum selesai.")
    raise_active_receipt_blockers(await collect_invoice_reverse_receipt_blockers(db, invoice["id"]))
    receipts = await db.sales_receipts.find({"invoice_id": invoice["id"]}, {"_id": 0}).to_list(200)
    for rec in receipts:
        if claim_is_active(rec):
            raise HTTPException(409, "Alokasi saldo pelanggan sedang berjalan.")
    credit = await db[CREDIT_COLLECTION].count_documents({
        "invoice_id": invoice["id"],
        "financial_status": "active",
        "status": {"$in": ["posted", "reserving", "posting"]},
    })
    if credit:
        raise HTTPException(409, "Faktur masih memiliki pemakaian saldo pelanggan.")
    if include_journal:
        docs = await db.journal_entries.find(
            {"source_type": SOURCE_TYPE, "source_id": str(invoice["id"]), "reversal_of": None},
            {"_id": 0},
        ).to_list(20)
        chosen = select_sales_invoice_primary(docs)
        if not chosen or chosen.get("ambiguous"):
            raise HTTPException(409, "Jurnal faktur tidak tunggal.")
        incomplete = await db.journal_entries.find_one({
            "source_id": str(invoice["id"]),
            "source_type": {"$in": [SOURCE_TYPE, SOURCE_REVERSAL]},
            "$or": [
                {"status": STATUS_INCOMPLETE},
                {"reversal_intent.phase": {"$nin": [None, "", "complete"]}},
            ],
        })
        if incomplete:
            raise HTTPException(409, "Reversal jurnal belum selesai.")
    if include_periods:
        await _assert_periods_open(db, original_date, new_date)


async def _acquire_fence(db, invoice_id: str, revision_id: str, token: str, key: str) -> Optional[dict]:
    from services.sales_invoice_posting import (
        posting_claim_absent_or_stale_clause,
        receipt_create_claim_absent_or_stale_clause,
        reverse_claim_absent_or_stale_clause,
    )
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    now = now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": FINANCIAL_FINALIZED,
            "$and": [
                posting_claim_absent_or_stale_clause(),
                reverse_claim_absent_or_stale_clause(),
                receipt_create_claim_absent_or_stale_clause(),
                revision_fence_absent_clause(),
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


async def _touch_owned_fence(db, invoice_id: str, revision_id: str, token: str) -> Optional[dict]:
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    now = now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": FINANCIAL_FINALIZED,
            FENCE_FIELD: revision_id,
            EDIT_CLAIM_FIELD: token,
        },
        {"$set": {EDIT_CLAIM_AT_FIELD: now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_fence(db, invoice_id: str, revision_id: str, token: str) -> None:
    from services.sales_invoices import now_iso

    await db.sales_invoices.update_one(
        {"id": str(invoice_id), FENCE_FIELD: revision_id, EDIT_CLAIM_FIELD: token},
        {"$unset": {
            FENCE_FIELD: "",
            FENCE_KEY_FIELD: "",
            EDIT_CLAIM_FIELD: "",
            EDIT_CLAIM_AT_FIELD: "",
        }, "$set": {"updated_at": now_iso()}},
    )


async def _load_revision(db, invoice_id: str, key: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {"invoice_id": str(invoice_id), "idempotency_key": key},
        {"_id": 0},
    )


_PHASE_FROM = {
    PHASE_REVERSED: (PHASE_PENDING, PHASE_REVERSED),
    PHASE_INVOICE: (PHASE_REVERSED, PHASE_INVOICE),
    PHASE_REPOSTED: (PHASE_INVOICE, PHASE_REPOSTED),
    PHASE_COMPLETE: (PHASE_REPOSTED,),
    PHASE_FAILED: (PHASE_PENDING,),
}
_PHASE_RANK = {
    PHASE_PENDING: 0,
    PHASE_REVERSED: 1,
    PHASE_INVOICE: 2,
    PHASE_REPOSTED: 3,
    PHASE_COMPLETE: 4,
}


def _verified_complete(row: Optional[dict]) -> bool:
    return bool(row) and row.get("phase") == PHASE_COMPLETE and row.get("financial_complete") is True


async def _advance_phase(db, revision_id: str, target: str, patch: Optional[dict] = None) -> dict:
    """Move forward only. A completed row is never rewritten into an earlier phase."""
    from services.sales_invoices import now_iso

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
    if result.matched_count:
        return current or {"id": revision_id, "phase": target}
    if _verified_complete(current):
        raise RevisionCompleted()
    if current and current.get("phase") == target and current.get("financial_complete") is not True:
        return current
    current_rank = _PHASE_RANK.get(str((current or {}).get("phase") or ""), -1)
    target_rank = _PHASE_RANK.get(target, -1)
    if current and current.get("phase") != PHASE_FAILED and current_rank > target_rank:
        return current
    raise HTTPException(409, {"code": "revision_pending", "message": "Revisi faktur belum selesai."})


def _pause(stop_after: Optional[str], phase: str) -> None:
    if stop_after == phase:
        raise RevisionPaused(phase)


async def revise_posted_sales_invoice(
    db,
    invoice_id: str,
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
    from services.sales_invoice_dates import canonical_sales_invoice_date
    from services.sales_invoice_posting import (
        SOURCE_REVERSAL,
        SOURCE_TYPE,
        _build_journal_lines,
        _require_posting_amounts,
        _require_posting_settings,
        find_sales_invoice_reversal_journal,
        find_sales_invoice_source_journal,
        journal_date_from_invoice,
    )
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        FINANCIAL_FINALIZED,
        effective_financial_status,
        get_sales_invoice_detail,
        normalize_other_charges,
        now_iso,
    )
    from services.sales_user_mapping import ensure_owned_po_id
    from services.journal_core import post_balanced_journal as _post

    blockers = await _activation_blockers(db)
    if blockers:
        raise HTTPException(409, {"code": "revision_not_ready", "blockers": blockers})
    await _require_edit_permission(user)
    await _ensure_revision_indexes(db)

    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        invoice = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")
    await ensure_owned_po_id(db, user, invoice.get("po_id"))
    status = effective_financial_status(invoice)
    if status == FINANCIAL_CANCELLED:
        raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diubah.")
    if status != FINANCIAL_FINALIZED:
        raise HTTPException(409, "Hanya faktur finalized yang dapat direvisi.")

    key = str((payload or {}).get("idempotency_key") or "").strip()
    if not key:
        raise HTTPException(400, "idempotency_key wajib diisi untuk revisi faktur yang sudah diposting.")
    proposed = await _proposed_fields(db, invoice, payload or {})
    fingerprint = _fingerprint(proposed)
    stored = await _load_revision(db, invoice["id"], key)
    if stored and stored.get("fingerprint") != fingerprint:
        raise HTTPException(409, "Idempotency key sudah dipakai untuk payload lain.")
    if stored and stored.get("phase") == PHASE_COMPLETE and stored.get("financial_complete") is True:
        await _release_fence(
            db,
            invoice["id"],
            str(stored.get("id") or ""),
            str(stored.get("claim_token") or ""),
        )
        return await get_sales_invoice_detail(db, invoice["id"])
    if stored and stored.get("phase") == PHASE_FAILED:
        raise HTTPException(409, "Revisi faktur gagal dan tidak dapat dilanjutkan dengan key yang sama.")
    other = await db[COLLECTION].find_one({
        "invoice_id": invoice["id"],
        "idempotency_key": {"$ne": key},
        "financial_complete": {"$ne": True},
        "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
    })
    if other:
        raise HTTPException(409, "Revisi faktur lain belum selesai.")

    current_invoice = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
    fence_id = str(current_invoice.get(FENCE_FIELD) or "")
    fence_key = str(current_invoice.get(FENCE_KEY_FIELD) or "")
    if fence_id and fence_key != key:
        raise HTTPException(409, "Revisi faktur lain belum selesai.")
    revision_id = ""
    token = ""

    original_journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if stored and stored.get("original_journal_date"):
        original_date = stored["original_journal_date"]
    else:
        original_date = str((original_journal or {}).get("date") or journal_date_from_invoice(invoice))
    new_date = canonical_sales_invoice_date(proposed["invoice_date"])

    async def _own(owned_revision_id: str, owned_token: str) -> dict:
        owned = await _touch_owned_fence(db, invoice["id"], owned_revision_id, owned_token)
        current_row = await db[COLLECTION].find_one({"id": str(owned_revision_id)}, {"_id": 0})
        if _verified_complete(current_row):
            raise RevisionCompleted()
        if not owned:
            raise HTTPException(409, {"code": "revision_pending", "message": "Kepemilikan revisi faktur sudah berubah."})
        return owned

    try:
        if stored:
            revision_id = str(stored["id"])
            token = str(stored.get("claim_token") or "")
            if not token:
                raise HTTPException(409, {"code": "revision_pending", "message": "Revisi faktur belum selesai."})
            invoice = await _own(revision_id, token)
            proposed = stored.get("proposed") or proposed
        elif fence_id and fence_key == key:
            revision_id = fence_id
            token = str(current_invoice.get(EDIT_CLAIM_FIELD) or "")
            invoice = await _own(revision_id, token)
        else:
            if (
                not original_journal
                or original_journal.get("ambiguous")
                or original_journal.get("status") != STATUS_POSTED
            ):
                raise HTTPException(409, "Jurnal faktur tidak tunggal.")
            await _assert_dependencies(db, invoice, new_date, original_date)
            revision_id = str(uuid.uuid4())
            token = str(uuid.uuid4())
            claimed = await _acquire_fence(db, invoice["id"], revision_id, token, key)
            if not claimed:
                raced = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
                if str(raced.get(FENCE_KEY_FIELD) or "") == key and str(raced.get(FENCE_FIELD) or ""):
                    revision_id = str(raced.get(FENCE_FIELD))
                    token = str(raced.get(EDIT_CLAIM_FIELD) or "")
                    invoice = await _own(revision_id, token)
                else:
                    raise HTTPException(409, "Faktur sedang direvisi atau dikunci proses lain.")
            else:
                invoice = claimed

        if not stored:
            from pymongo.errors import DuplicateKeyError

            try:
                await db[COLLECTION].insert_one({
                    "id": revision_id,
                    "invoice_id": invoice["id"],
                    "idempotency_key": key,
                    "fingerprint": fingerprint,
                    "phase": PHASE_PENDING,
                    "financial_complete": False,
                    "claim_token": token,
                    "original_snapshot": _snapshot(invoice),
                    "proposed": proposed,
                    "original_journal_id": (original_journal or {}).get("id"),
                    "original_journal_date": original_date,
                    "created_at": now_iso(),
                })
            except DuplicateKeyError:
                pass
            stored = await _load_revision(db, invoice["id"], key)
            if not stored:
                raise HTTPException(409, {"code": "revision_pending", "message": "Revisi faktur belum selesai."})
            revision_id = str(stored["id"])
            token = str(stored.get("claim_token") or token)
            proposed = stored.get("proposed") or proposed
            if stored.get("fingerprint") != fingerprint:
                raise HTTPException(409, "Idempotency key sudah dipakai untuk payload lain.")

        original_id = str(stored.get("original_journal_id") or (original_journal or {}).get("id") or "")
        original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0}) if original_id else None
        already_reversed = bool(original and original.get("status") == STATUS_REVERSED)

        if stored.get("phase") == PHASE_PENDING and not already_reversed:
            try:
                await _assert_dependencies(db, invoice, new_date, original_date)
            except HTTPException:
                original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0}) if original_id else None
                if original and original.get("status") == STATUS_REVERSED:
                    raise HTTPException(409, {"code": "revision_pending", "message": "Revisi faktur belum selesai."})
                failed = await _advance_phase(db, revision_id, PHASE_FAILED)
                if failed and failed.get("phase") == PHASE_FAILED:
                    await _release_fence(db, invoice["id"], revision_id, token)
                raise

        if already_reversed or stored.get("phase") in {PHASE_REVERSED, PHASE_INVOICE, PHASE_REPOSTED}:
            try:
                await _assert_dependencies(
                    db, invoice, new_date, original_date, include_periods=False, include_journal=False,
                )
            except HTTPException as exc:
                message = exc.detail if isinstance(exc.detail, str) else "Revisi faktur belum selesai."
                raise HTTPException(409, {"code": "revision_pending", "message": message}) from exc

        invoice = await _own(revision_id, token)
        _pause(stop_after, "before_reverse")

        original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0}) if original_id else None
        if original and original.get("status") == STATUS_POSTED:
            await _assert_periods_open(db, original_date, new_date)
            invoice = await _own(revision_id, token)
            try:
                reversal = await reverse_journal(
                    db,
                    original_id,
                    source_type=SOURCE_REVERSAL,
                    source_id=str(invoice["id"]),
                    posted_by=user,
                    description=f"Revisi Faktur Penjualan {invoice.get('invoice_number') or invoice['id']}",
                )
            except PeriodLockedError as exc:
                original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0})
                if original and original.get("status") == STATUS_REVERSED:
                    raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
                raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
            except (JournalValidationError, JournalConflictError) as exc:
                original = await db.journal_entries.find_one({"id": original_id}, {"_id": 0})
                if not original or original.get("status") != STATUS_REVERSED:
                    raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
                reversal = await find_sales_invoice_reversal_journal(db, original_id)
            _pause(stop_after, "after_reverse_journal")
            await _advance_phase(db, revision_id, PHASE_REVERSED, {
                "original_journal_id": original_id,
                "reversal_journal_id": (reversal or {}).get("id"),
            })
        elif original and original.get("status") == STATUS_REVERSED:
            reversal = await find_sales_invoice_reversal_journal(db, original_id)
            await _advance_phase(db, revision_id, PHASE_REVERSED, {
                "original_journal_id": original_id,
                "reversal_journal_id": (reversal or {}).get("id"),
            })
        else:
            raise HTTPException(409, {"code": "revision_pending", "message": "Jurnal asli tidak dapat di-reverse."})
        _pause(stop_after, "after_reverse")

        invoice = await _own(revision_id, token)
        fresh = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
        if _verified_complete(await db[COLLECTION].find_one({"id": revision_id}, {"_id": 0})):
            raise RevisionCompleted()
        if str(fresh.get(APPLIED_FIELD) or "") != revision_id:
            applied = await db.sales_invoices.find_one_and_update(
                {
                    "id": invoice["id"],
                    "financial_status": FINANCIAL_FINALIZED,
                    EDIT_CLAIM_FIELD: token,
                    FENCE_FIELD: revision_id,
                },
                {"$set": {
                    **proposed,
                    "financial_status": FINANCIAL_FINALIZED,
                    APPLIED_FIELD: revision_id,
                    "updated_at": now_iso(),
                }},
                return_document=ReturnDocument.AFTER,
            )
            if not applied:
                raced_row = await db[COLLECTION].find_one({"id": revision_id}, {"_id": 0})
                if _verified_complete(raced_row):
                    raise RevisionCompleted()
                raise HTTPException(409, {"code": "revision_pending", "message": "Perubahan faktur belum tersimpan."})
        await _advance_phase(db, revision_id, PHASE_INVOICE)
        _pause(stop_after, "after_invoice")

        invoice = await _own(revision_id, token)
        active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, invoice["id"])
        if not active:
            saved = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or fresh
            try:
                grand, after, ppn, _charges = _require_posting_amounts(saved)
                charges = await normalize_other_charges(db, saved.get("other_charges") or [])
                mapped = await _require_posting_settings(db)
                lines = _build_journal_lines(
                    receivable_account_id=mapped["receivable_account_id"],
                    revenue_account_id=mapped["revenue_account_id"],
                    output_tax_account_id=mapped["output_tax_account_id"],
                    grand_total=grand,
                    after_discount=after,
                    ppn_amount=ppn,
                    other_charges=charges,
                    invoice_number=str(saved.get("invoice_number") or ""),
                )
                await _post(
                    db,
                    date=journal_date_from_invoice(saved),
                    source_type=SOURCE_TYPE,
                    source_id=str(saved["id"]),
                    description=f"Faktur Penjualan {saved.get('invoice_number') or saved['id']}",
                    lines=lines,
                    posted_by=user,
                )
            except PeriodLockedError as exc:
                raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
            except JournalValidationError as exc:
                raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
            except JournalConflictError as exc:
                active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, invoice["id"])
                if not active:
                    raise HTTPException(409, {"code": "revision_pending", "message": str(exc)}) from exc
        await _advance_phase(db, revision_id, PHASE_REPOSTED)
        _pause(stop_after, "after_repost")

        active = await find_active_posted_primary_for_source(db, SOURCE_TYPE, invoice["id"])
        raw_active = None
        if active and active.get("id"):
            raw_active = await db.journal_entries.find_one(
                {"id": active["id"]},
                {"_id": 0, "status": 1, "supersedes_journal_id": 1, "journal_revision": 1},
            )
        posted_count = await db.journal_entries.count_documents({
            "source_type": SOURCE_TYPE,
            "source_id": str(invoice["id"]),
            "reversal_of": None,
            "status": STATUS_POSTED,
        })
        reversal = await find_sales_invoice_reversal_journal(db, original_id)
        current = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
        supersedes = str((raw_active or {}).get("supersedes_journal_id") or "")
        verified = (
            posted_count == 1
            and raw_active
            and str(active.get("id") or "") != original_id
            and str(raw_active.get("status") or "") == STATUS_POSTED
            and int(raw_active.get("journal_revision") or 0) >= 2
            and supersedes == original_id
            and reversal
            and str(reversal.get("reversal_of") or "") == original_id
            and effective_financial_status(current) == FINANCIAL_FINALIZED
        )
        if not verified:
            raise HTTPException(409, {"code": "revision_pending", "message": "Akuntansi revisi belum lengkap."})
        await _advance_phase(db, revision_id, PHASE_COMPLETE, {
            "new_journal_id": active.get("id"),
            "reversal_journal_id": reversal.get("id"),
        })
        await _release_fence(db, invoice["id"], revision_id, token)
        return await get_sales_invoice_detail(db, invoice["id"])
    except RevisionCompleted:
        row = await _load_revision(db, invoice["id"], key)
        if row and row.get("claim_token"):
            await _release_fence(
                db,
                invoice["id"],
                str(row.get("id") or ""),
                str(row.get("claim_token") or ""),
            )
        return await get_sales_invoice_detail(db, invoice["id"])
