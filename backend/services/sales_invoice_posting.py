"""Sales invoice → journal_core posting (Phase 1).

Explicit POST only. Does not auto-post on finalize, write receipts/HPP, or
mutate production workflow. Reuses journal_core.post_balanced_journal.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.journal_core import (
    STATUS_POSTED,
    STATUS_REVERSED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.sales_accounting_settings import (
    _load_account,
    _validate_output_tax,
    _validate_receivable,
    _validate_revenue,
    get_sales_accounting_settings,
)

SOURCE_TYPE = "sales_invoice"
SOURCE_REVERSAL = "sales_invoice_reversal"
PERM_POST = "sales_accounting.post"
PERM_REVERSE = "sales_accounting.reverse"
CLAIM_FIELD = "accounting_posting_claim"
CLAIM_AT_FIELD = "accounting_posting_claim_at"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"
REVERSE_CLAIM_AT_FIELD = "accounting_reverse_claim_at"
RECEIPT_CREATE_CLAIM_FIELD = "receipt_creation_claim"
RECEIPT_CREATE_CLAIM_AT_FIELD = "receipt_creation_claim_at"
RECEIPT_CREATE_CLAIM_TTL_SECONDS = 600
ACCOUNTING_POST_CLAIM_TTL_SECONDS = 600
ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS = 600
REVERSAL_REASON_MIN_LEN = 3

CANCEL_CLAIM_MESSAGE = (
    "Faktur sedang diposting ke accounting dan tidak dapat dibatalkan."
)

ACCOUNTING_NOT_POSTED = "not_posted"
ACCOUNTING_POSTED = "posted"
ACCOUNTING_REVERSED = "reversed"

CANCEL_POSTED_MESSAGE = (
    "Invoice sudah diposting ke accounting dan belum dapat dibatalkan sebelum "
    "proses reversal/credit note tersedia."
)
UNPOSTED_REVERSE_MESSAGE = (
    "Hanya faktur yang sudah diposting ke accounting yang dapat di-reverse."
)
REVERSE_CLAIM_MESSAGE = (
    "Faktur sedang di-reverse dari accounting."
)
POSTING_CLAIM_REVERSE_MESSAGE = (
    "Faktur sedang diposting ke accounting dan tidak dapat di-reverse."
)
ACTIVE_RECEIPT_REVERSE_MESSAGE = (
    "Faktur tidak dapat di-reverse karena masih memiliki penerimaan aktif."
)
INVOICE_REVERSE_IN_PROGRESS_RECEIPT_MESSAGE = (
    "Faktur sedang di-reverse dari accounting dan tidak dapat diposting penerimaan."
)
INVOICE_REVERSE_IN_PROGRESS_RECEIPT_CREATE_MESSAGE = (
    "Faktur sedang di-reverse dari accounting dan tidak dapat menerima Penerimaan Penjualan."
)
RECEIPT_CREATE_CLAIM_REVERSE_MESSAGE = (
    "Faktur sedang menerima Penerimaan Penjualan dan tidak dapat di-reverse."
)
RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE = (
    "Faktur sedang diproses untuk Penerimaan Penjualan."
)
REASON_REQUIRED_MESSAGE = "Alasan reversal wajib diisi minimal 3 karakter."


def _money(raw: Any, field: str) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(400, f"{field} tidak valid.") from exc


def journal_date_from_invoice(invoice: dict) -> str:
    raw = str((invoice or {}).get("invoice_date") or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal faktur tidak valid.")
    return raw[:10]


def accounting_status_from_journal(journal: Optional[dict]) -> str:
    if not journal:
        return ACCOUNTING_NOT_POSTED
    status = str(journal.get("status") or "").strip().lower()
    if status == STATUS_REVERSED:
        return ACCOUNTING_REVERSED
    if status == STATUS_POSTED:
        return ACCOUNTING_POSTED
    return ACCOUNTING_NOT_POSTED


async def find_sales_invoice_source_journal(db, invoice_id: str) -> Optional[dict]:
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(invoice_id))


async def load_source_journals_by_invoice_ids(db, invoice_ids: List[str]) -> Dict[str, dict]:
    ids = [str(i) for i in invoice_ids if i]
    if not ids:
        return {}
    docs = await db.journal_entries.find(
        {
            "source_type": SOURCE_TYPE,
            "source_id": {"$in": ids},
            "reversal_of": None,
        },
        {"_id": 0, "id": 1, "source_id": 1, "status": 1},
    ).to_list(len(ids) + 50)
    return {str(doc.get("source_id")): doc for doc in docs}


def raise_if_journal_blocks_cancel(journal: Optional[dict]) -> None:
    if journal:
        raise HTTPException(409, CANCEL_POSTED_MESSAGE)


def _parse_claim_at(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
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


def _claim_absent_clause(field: str) -> Dict[str, Any]:
    return {"$or": [{field: {"$exists": False}}, {field: None}, {field: ""}]}


def _claim_lease_is_active(
    doc: Optional[dict],
    token_field: str,
    at_field: str,
    ttl_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Fresh lease only. Token without a valid timestamp is stale/recoverable."""
    token = str((doc or {}).get(token_field) or "").strip()
    if not token:
        return False
    claimed_at = _parse_claim_at((doc or {}).get(at_field))
    if claimed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < ttl_seconds


def _claim_absent_or_stale_clause(
    token_field: str,
    at_field: str,
    ttl_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=ttl_seconds)
    return {
        "$or": [
            {token_field: {"$exists": False}},
            {token_field: None},
            {token_field: ""},
            {at_field: {"$exists": False}},
            {at_field: None},
            {at_field: ""},
            {at_field: {"$lte": cutoff.isoformat()}},
        ]
    }


def posting_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        invoice, CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        invoice, REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def posting_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def receipt_create_claim_cutoff_iso(*, now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(seconds=RECEIPT_CREATE_CLAIM_TTL_SECONDS)).isoformat()


def receipt_create_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """Fresh lease only. Token without timestamp is stale/recoverable."""
    token = str((invoice or {}).get(RECEIPT_CREATE_CLAIM_FIELD) or "").strip()
    if not token:
        return False
    claimed_at = _parse_claim_at((invoice or {}).get(RECEIPT_CREATE_CLAIM_AT_FIELD))
    if claimed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < RECEIPT_CREATE_CLAIM_TTL_SECONDS


def receipt_create_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    cutoff = receipt_create_claim_cutoff_iso(now=now)
    return {
        "$or": [
            {RECEIPT_CREATE_CLAIM_FIELD: {"$exists": False}},
            {RECEIPT_CREATE_CLAIM_FIELD: None},
            {RECEIPT_CREATE_CLAIM_FIELD: ""},
            {RECEIPT_CREATE_CLAIM_AT_FIELD: {"$exists": False}},
            {RECEIPT_CREATE_CLAIM_AT_FIELD: None},
            {RECEIPT_CREATE_CLAIM_AT_FIELD: ""},
            {RECEIPT_CREATE_CLAIM_AT_FIELD: {"$lte": cutoff}},
        ]
    }


def normalize_reversal_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < REVERSAL_REASON_MIN_LEN:
        raise HTTPException(400, REASON_REQUIRED_MESSAGE)
    return reason


def _receipt_blocker_status(receipt: dict, journal: Optional[dict]) -> str:
    return accounting_status_from_journal(journal)


def public_receipt_reverse_blocker(receipt: dict, journal: Optional[dict]) -> Dict[str, Any]:
    return {
        "id": receipt.get("id"),
        "number": receipt.get("receipt_number") or receipt.get("id"),
        "accounting_status": _receipt_blocker_status(receipt, journal),
        "amount": receipt.get("amount"),
    }


def cancel_unclaimed_filter(invoice_id: str) -> Dict[str, Any]:
    from services.sales_invoices import FINANCIAL_FINALIZED

    return {
        "id": invoice_id,
        "financial_status": FINANCIAL_FINALIZED,
        **posting_claim_absent_or_stale_clause(),
    }


async def clear_posting_claim(db, invoice_id: str) -> None:
    from services.sales_invoices import now_iso

    await db.sales_invoices.update_one(
        {"id": str(invoice_id)},
        {
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_posting_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Atomically claim a finalized invoice. Fresh reverse claim blocks it."""
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": FINANCIAL_FINALIZED,
            **reverse_claim_absent_or_stale_clause(),
        },
        {
            "$set": {CLAIM_FIELD: claim_token, CLAIM_AT_FIELD: now, "updated_at": now},
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_reverse_claim(db, invoice_id: str) -> None:
    from services.sales_invoices import now_iso

    await db.sales_invoices.update_one(
        {"id": str(invoice_id)},
        {
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_reverse_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Atomically claim a finalized invoice for reverse. Fresh post/create claims block it."""
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": FINANCIAL_FINALIZED,
            "$and": [
                posting_claim_absent_or_stale_clause(),
                receipt_create_claim_absent_or_stale_clause(),
            ],
        },
        {
            "$set": {
                REVERSE_CLAIM_FIELD: claim_token,
                REVERSE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            },
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_receipt_create_claim(db, invoice_id: str, *, token: Optional[str] = None) -> None:
    from services.sales_invoices import now_iso

    filt: Dict[str, Any] = {"id": str(invoice_id)}
    if token:
        filt[RECEIPT_CREATE_CLAIM_FIELD] = str(token)
    await db.sales_invoices.update_one(
        filt,
        {
            "$unset": {RECEIPT_CREATE_CLAIM_FIELD: "", RECEIPT_CREATE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_receipt_create_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Lease a finalized invoice for receipt create. Fresh reverse claim blocks it."""
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    create_claim_ok = receipt_create_claim_absent_or_stale_clause()
    create_claim_ok.setdefault("$or", []).append({RECEIPT_CREATE_CLAIM_FIELD: claim_token})
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "financial_status": FINANCIAL_FINALIZED,
            "$and": [
                reverse_claim_absent_or_stale_clause(),
                create_claim_ok,
            ],
        },
        {
            "$set": {
                RECEIPT_CREATE_CLAIM_FIELD: claim_token,
                RECEIPT_CREATE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def find_sales_invoice_reversal_journal(db, original_journal_id: str) -> Optional[dict]:
    oid = str(original_journal_id or "").strip()
    if not oid:
        return None
    return await db.journal_entries.find_one({"reversal_of": oid}, {"_id": 0})


async def collect_invoice_reverse_receipt_blockers(db, invoice_id: str) -> List[dict]:
    from services.sales_receipt_posting import (
        load_source_journals_by_receipt_ids,
        posting_claim_is_active as receipt_posting_claim_is_active,
        receipt_is_cancelled,
    )

    receipts = await db.sales_receipts.find({"invoice_id": str(invoice_id)}, {"_id": 0}).to_list(500)
    journals = await load_source_journals_by_receipt_ids(
        db, [rec.get("id") for rec in receipts if rec.get("id")]
    )
    blockers: List[dict] = []
    seen: set = set()
    for rec in receipts:
        rid = str(rec.get("id") or "")
        journal = journals.get(rid)
        acc = accounting_status_from_journal(journal)
        active_doc = not receipt_is_cancelled(rec)
        active_claim = receipt_posting_claim_is_active(rec)
        posted_journal = acc == ACCOUNTING_POSTED
        if not (active_doc or active_claim or posted_journal):
            continue
        if rid in seen:
            continue
        seen.add(rid)
        blockers.append(public_receipt_reverse_blocker(rec, journal))
    return blockers


def raise_active_receipt_blockers(blockers: List[dict]) -> None:
    if blockers:
        raise HTTPException(409, {
            "message": ACTIVE_RECEIPT_REVERSE_MESSAGE,
            "blockers": blockers,
        })


async def _finalize_invoice_reversal_state(
    db,
    invoice: dict,
    user: dict,
    reason: str,
    reversal_journal: Optional[dict] = None,
) -> None:
    from services.sales_invoices import FINANCIAL_CANCELLED, _actor_public, now_iso

    now = now_iso()
    stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {
        "financial_status": FINANCIAL_CANCELLED,
        "paid_total": 0.0,
        "outstanding_amount": 0.0,
        "updated_at": now,
    }
    if not stored.get("cancelled_at"):
        patch["cancelled_at"] = now
        patch["cancelled_by"] = _actor_public(user)
    if not stored.get("accounting_reversed_at"):
        patch["accounting_reversed_at"] = now
        patch["accounting_reversed_by"] = _actor_public(user)
    if reason and not str(stored.get("accounting_reversal_reason") or "").strip():
        patch["accounting_reversal_reason"] = reason
    rev_id = None
    if reversal_journal and reversal_journal.get("id"):
        rev_id = reversal_journal.get("id")
    elif stored.get("accounting_reversal_journal_id"):
        rev_id = stored.get("accounting_reversal_journal_id")
    if rev_id:
        patch["accounting_reversal_journal_id"] = rev_id
    await db.sales_invoices.update_one(
        {"id": invoice["id"]},
        {
            "$set": patch,
            "$unset": {
                REVERSE_CLAIM_FIELD: "",
                REVERSE_CLAIM_AT_FIELD: "",
                CLAIM_FIELD: "",
                CLAIM_AT_FIELD: "",
                RECEIPT_CREATE_CLAIM_FIELD: "",
                RECEIPT_CREATE_CLAIM_AT_FIELD: "",
            },
        },
    )


@asynccontextmanager
async def _optional_invoice_lock(enabled: bool):
    from services.sales_invoices import _get_invoice_lock

    if not enabled:
        yield
        return
    async with _get_invoice_lock():
        yield


async def restore_finalized_after_cancel_race(db, invoice_id: str) -> None:
    """Restore operational cancel vs post. Never undo a completed accounting reversal."""
    from services.sales_invoices import FINANCIAL_CANCELLED, FINANCIAL_FINALIZED, now_iso

    journal = await find_sales_invoice_source_journal(db, invoice_id)
    if accounting_status_from_journal(journal) == ACCOUNTING_REVERSED:
        return
    stored = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0}) or {}
    if stored.get("accounting_reversed_at") or stored.get("accounting_reversal_journal_id"):
        return
    await db.sales_invoices.update_one(
        {
            "id": invoice_id,
            "financial_status": FINANCIAL_CANCELLED,
            "$and": [
                _claim_absent_clause("accounting_reversed_at"),
                _claim_absent_clause("accounting_reversal_journal_id"),
            ],
        },
        {
            "$set": {"financial_status": FINANCIAL_FINALIZED, "updated_at": now_iso()},
            "$unset": {"cancelled_at": "", "cancelled_by": ""},
        },
    )


def _build_journal_lines(
    *,
    receivable_account_id: str,
    revenue_account_id: str,
    output_tax_account_id: str,
    grand_total: Decimal,
    after_discount: Decimal,
    ppn_amount: Decimal,
    invoice_number: str,
) -> List[dict]:
    ref = invoice_number or "faktur"
    lines: List[dict] = []
    if grand_total > 0:
        lines.append({
            "account_id": receivable_account_id,
            "debit": grand_total,
            "credit": Decimal("0.00"),
            "description": f"Piutang {ref}",
        })
    if after_discount > 0:
        lines.append({
            "account_id": revenue_account_id,
            "debit": Decimal("0.00"),
            "credit": after_discount,
            "description": f"Pendapatan {ref}",
        })
    if ppn_amount > 0:
        lines.append({
            "account_id": output_tax_account_id,
            "debit": Decimal("0.00"),
            "credit": ppn_amount,
            "description": f"PPN Keluaran {ref}",
        })
    return lines


async def _require_posting_settings(db) -> dict:
    settings = await get_sales_accounting_settings(db)
    receivable_id = str(settings.get("receivable_account_id") or "").strip()
    revenue_id = str(settings.get("revenue_account_id") or "").strip()
    tax_id = str(settings.get("output_tax_account_id") or "").strip()
    if not (receivable_id and revenue_id and tax_id):
        raise HTTPException(400, "Pengaturan Akuntansi Penjualan belum dikonfigurasi.")

    receivable = _validate_receivable(await _load_account(db, receivable_id))
    revenue = _validate_revenue(await _load_account(db, revenue_id))
    output_tax = _validate_output_tax(await _load_account(db, tax_id))
    return {
        "receivable_account_id": str(receivable["id"]),
        "revenue_account_id": str(revenue["id"]),
        "output_tax_account_id": str(output_tax["id"]),
    }


def _require_posting_amounts(invoice: dict) -> tuple[Decimal, Decimal, Decimal]:
    from services.sales_invoices import monetary_invariant_valid

    grand = _money(invoice.get("grand_total"), "grand_total")
    after = _money(invoice.get("after_discount"), "after_discount")
    ppn = _money(invoice.get("ppn_amount"), "ppn_amount")
    if grand <= 0:
        raise HTTPException(400, "grand_total harus lebih besar dari 0 untuk posting accounting.")
    if after < 0:
        raise HTTPException(400, "after_discount tidak boleh negatif.")
    if ppn < 0:
        raise HTTPException(400, "ppn_amount tidak boleh negatif.")
    if not monetary_invariant_valid(invoice):
        raise HTTPException(
            400,
            "Total faktur tidak valid: grand_total harus sama dengan after_discount + ppn_amount.",
        )
    return grand, after, ppn


async def _load_invoice(db, invoice_id: str) -> dict:
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")
    return inv


async def _write_source_journal(db, inv: dict, user: dict) -> None:
    grand, after, ppn = _require_posting_amounts(inv)
    mapped = await _require_posting_settings(db)
    lines = _build_journal_lines(
        receivable_account_id=mapped["receivable_account_id"],
        revenue_account_id=mapped["revenue_account_id"],
        output_tax_account_id=mapped["output_tax_account_id"],
        grand_total=grand,
        after_discount=after,
        ppn_amount=ppn,
        invoice_number=str(inv.get("invoice_number") or ""),
    )
    if len(lines) < 2:
        raise HTTPException(400, "Baris jurnal posting tidak valid.")
    try:
        await post_balanced_journal(
            db,
            date=journal_date_from_invoice(inv),
            source_type=SOURCE_TYPE,
            source_id=str(inv["id"]),
            description=f"Faktur Penjualan {inv.get('invoice_number') or inv['id']}",
            lines=lines,
            posted_by=user,
        )
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_sales_invoice_source_journal(db, inv["id"])
        if existing:
            return
        raise HTTPException(409, "Jurnal untuk faktur ini sudah ada.")


async def post_sales_invoice_accounting(
    db, invoice_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Post one finalized invoice. Idempotent. Safety is the Mongo claim, not the lock."""
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        FINANCIAL_FINALIZED,
        effective_financial_status,
        get_sales_invoice_detail,
    )

    async with _optional_invoice_lock(serialize_in_process):
        inv = await _load_invoice(db, invoice_id)
        status = effective_financial_status(inv)
        if status == FINANCIAL_CANCELLED:
            raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diposting ke accounting.")
        if status != FINANCIAL_FINALIZED:
            raise HTTPException(409, "Hanya faktur finalized yang dapat diposting ke accounting.")

        existing = await find_sales_invoice_source_journal(db, inv["id"])
        if existing:
            if inv.get(CLAIM_FIELD):
                await clear_posting_claim(db, inv["id"])
            return await get_sales_invoice_detail(db, inv["id"])

        # Validate before claim so a failed eligibility check does not hold cancel.
        _require_posting_amounts(inv)
        await _require_posting_settings(db)

        claimed = await acquire_posting_claim(db, inv["id"])
        if claimed is None:
            current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            if current and effective_financial_status(current) == FINANCIAL_CANCELLED:
                raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diposting ke accounting.")
            if current and reverse_claim_is_active(current):
                raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            existing = await find_sales_invoice_source_journal(db, inv["id"])
            if existing:
                return await get_sales_invoice_detail(db, inv["id"])
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_invoice_source_journal(db, inv["id"])
        if existing:
            await clear_posting_claim(db, inv["id"])
            return await get_sales_invoice_detail(db, inv["id"])

        current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or claimed
        if (
            reverse_claim_is_active(current)
            or effective_financial_status(current) != FINANCIAL_FINALIZED
        ):
            await clear_posting_claim(db, inv["id"])
            if reverse_claim_is_active(current):
                raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            if effective_financial_status(current) == FINANCIAL_CANCELLED:
                raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diposting ke accounting.")
            raise HTTPException(409, "Hanya faktur finalized yang dapat diposting ke accounting.")

        try:
            await _write_source_journal(db, claimed, user)
        except Exception:
            still = await find_sales_invoice_source_journal(db, inv["id"])
            if not still:
                await clear_posting_claim(db, inv["id"])
            raise

        current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        if current and effective_financial_status(current) == FINANCIAL_CANCELLED:
            await restore_finalized_after_cancel_race(db, inv["id"])

        await clear_posting_claim(db, inv["id"])
        return await get_sales_invoice_detail(db, inv["id"])


async def reverse_sales_invoice_accounting(
    db, invoice_id: str, user: dict, reason: Any, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse a posted invoice journal. No receipt cascade. Mongo claim is the lock."""
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        FINANCIAL_FINALIZED,
        effective_financial_status,
        get_sales_invoice_detail,
    )

    reason = normalize_reversal_reason(reason)

    async with _optional_invoice_lock(serialize_in_process):
        inv = await _load_invoice(db, invoice_id)
        existing = await find_sales_invoice_source_journal(db, inv["id"])
        status = accounting_status_from_journal(existing)

        if status == ACCOUNTING_REVERSED:
            rev = await find_sales_invoice_reversal_journal(db, existing.get("id") if existing else "")
            await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
            return await get_sales_invoice_detail(db, inv["id"])

        fin_status = effective_financial_status(inv)
        if fin_status == FINANCIAL_CANCELLED:
            if reverse_claim_is_active(inv):
                await clear_reverse_claim(db, inv["id"])
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if fin_status != FINANCIAL_FINALIZED:
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if posting_claim_is_active(inv):
            raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)

        raise_active_receipt_blockers(await collect_invoice_reverse_receipt_blockers(db, inv["id"]))
        if receipt_create_claim_is_active(inv):
            raise HTTPException(409, RECEIPT_CREATE_CLAIM_REVERSE_MESSAGE)

        claimed = await acquire_reverse_claim(db, inv["id"])
        if claimed is None:
            current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            still = await find_sales_invoice_source_journal(db, inv["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_sales_invoice_reversal_journal(db, still.get("id") if still else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await get_sales_invoice_detail(db, inv["id"])
            if current and receipt_create_claim_is_active(current):
                raise HTTPException(409, RECEIPT_CREATE_CLAIM_REVERSE_MESSAGE)
            if current and posting_claim_is_active(current):
                raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        try:
            if receipt_create_claim_is_active(claimed):
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(409, RECEIPT_CREATE_CLAIM_REVERSE_MESSAGE)
            blockers = await collect_invoice_reverse_receipt_blockers(db, inv["id"])
            if blockers:
                await clear_reverse_claim(db, inv["id"])
                raise_active_receipt_blockers(blockers)

            existing = await find_sales_invoice_source_journal(db, inv["id"])
            status = accounting_status_from_journal(existing)
            if status == ACCOUNTING_REVERSED:
                rev = await find_sales_invoice_reversal_journal(db, existing.get("id") if existing else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await get_sales_invoice_detail(db, inv["id"])
            if status != ACCOUNTING_POSTED or not existing:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)

            try:
                rev_doc = await reverse_journal(
                    db,
                    existing["id"],
                    source_type=SOURCE_REVERSAL,
                    source_id=str(inv["id"]),
                    posted_by=user,
                    description=(
                        f"Reversal Faktur Penjualan {inv.get('invoice_number') or inv['id']} — {reason}"
                    ),
                )
            except KeyError as exc:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(404, "Jurnal faktur tidak ditemukan.") from exc
            except JournalValidationError as exc:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(400, str(exc)) from exc
            except JournalConflictError:
                still = await find_sales_invoice_source_journal(db, inv["id"])
                if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                    await clear_reverse_claim(db, inv["id"])
                    raise HTTPException(409, "Reversal jurnal untuk faktur ini sudah ada.")
                rev_doc = await find_sales_invoice_reversal_journal(db, still.get("id") if still else "")

            await _finalize_invoice_reversal_state(db, inv, user, reason, rev_doc)
            return await get_sales_invoice_detail(db, inv["id"])
        except Exception:
            still = await find_sales_invoice_source_journal(db, inv["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_sales_invoice_reversal_journal(db, still.get("id") if still else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await get_sales_invoice_detail(db, inv["id"])
            raise
