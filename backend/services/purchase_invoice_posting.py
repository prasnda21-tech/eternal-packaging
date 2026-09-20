"""Purchase invoice → journal_core posting (Phase 4A).

Explicit POST only. Does not auto-post on create/cancel, write inventory,
or mutate goods-receipt stock. Reuses journal_core.post_balanced_journal.
Journal is Dr Purchase Clearing / GRNI, Cr Accounts Payable.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.accounting_periods import PeriodLockedError
from services.journal_core import (
    STATUS_POSTED,
    STATUS_REVERSED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.purchase_accounting_settings import (
    _load_account,
    _validate_payable,
    _validate_purchase_clearing,
    get_purchase_accounting_settings,
)
from services.purchase_common import cancellation_fields, is_cancelled, now_iso

SOURCE_TYPE = "purchase_invoice"
SOURCE_REVERSAL = "purchase_invoice_reversal"
PERM_POST = "purchase_accounting.post"
PERM_REVERSE = "purchase_accounting.reverse"
CLAIM_FIELD = "accounting_posting_claim"
CLAIM_AT_FIELD = "accounting_posting_claim_at"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"
REVERSE_CLAIM_AT_FIELD = "accounting_reverse_claim_at"
PAYMENT_CREATE_CLAIM_FIELD = "payment_creation_claim"
PAYMENT_CREATE_CLAIM_AT_FIELD = "payment_creation_claim_at"
PAYMENT_CREATE_CLAIM_TTL_SECONDS = 600
INVOICE_CANCEL_CLAIM_FIELD = "invoice_cancel_claim"
INVOICE_CANCEL_CLAIM_AT_FIELD = "invoice_cancel_claim_at"
INVOICE_CANCEL_CLAIM_TTL_SECONDS = 600
ACCOUNTING_POST_CLAIM_TTL_SECONDS = 600
ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS = 600
REVERSAL_REASON_MIN_LEN = 3

ACCOUNTING_NOT_POSTED = "not_posted"
ACCOUNTING_POSTED = "posted"
ACCOUNTING_REVERSED = "reversed"

CANCEL_POSTED_MESSAGE = (
    "Faktur sudah diposting ke accounting. Gunakan Reverse Accounting."
)
CANCEL_CLAIM_MESSAGE = (
    "Faktur sedang diposting ke accounting. Gunakan Reverse Accounting."
)
UNPOSTED_REVERSE_MESSAGE = (
    "Hanya faktur yang sudah diposting ke accounting yang dapat di-reverse."
)
REVERSE_CLAIM_MESSAGE = "Faktur sedang di-reverse dari accounting."
POSTING_CLAIM_REVERSE_MESSAGE = (
    "Faktur sedang diposting ke accounting dan tidak dapat di-reverse."
)
ACTIVE_PAYMENT_REVERSE_MESSAGE = (
    "Faktur tidak dapat di-reverse karena masih memiliki pembayaran aktif."
)
INVOICE_REVERSE_IN_PROGRESS_PAYMENT_CREATE_MESSAGE = (
    "Faktur sedang di-reverse dari accounting dan tidak dapat menerima Pembayaran Pembelian."
)
PAYMENT_CREATE_CLAIM_REVERSE_MESSAGE = (
    "Faktur sedang menerima Pembayaran Pembelian dan tidak dapat di-reverse."
)
PAYMENT_CREATE_CLAIM_CONFLICT_MESSAGE = (
    "Faktur sedang diproses untuk Pembayaran Pembelian."
)
INVOICE_CANCEL_IN_PROGRESS_PAYMENT_CREATE_MESSAGE = (
    "Faktur sedang dibatalkan dan tidak dapat menerima Pembayaran Pembelian."
)
PAYMENT_CREATE_CLAIM_CANCEL_MESSAGE = (
    "Faktur sedang menerima Pembayaran Pembelian dan tidak dapat dibatalkan."
)
INVOICE_CANCEL_CLAIM_CONFLICT_MESSAGE = "Faktur sedang dibatalkan."
ACTIVE_PAYMENT_CANCEL_MESSAGE = (
    "Faktur tidak dapat dihapus karena sudah memiliki pembayaran. Batalkan pembayaran terlebih dahulu."
)
CANCELLED_POST_MESSAGE = "Faktur yang dibatalkan tidak dapat diposting ke accounting."
REASON_REQUIRED_MESSAGE = "Alasan reversal wajib diisi minimal 3 karakter."
SETTINGS_NOT_READY_MESSAGE = "Pengaturan Akuntansi Pembelian belum dikonfigurasi."


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


async def find_purchase_invoice_source_journal(db, invoice_id: str) -> Optional[dict]:
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(invoice_id))


async def load_source_journals_by_invoice_ids(db, invoice_ids: List[str]) -> Dict[str, dict]:
    ids = [str(i) for i in invoice_ids if i]
    if not ids:
        return {}
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        cursor = db.journal_entries.find(
            {
                "source_type": SOURCE_TYPE,
                "source_id": {"$in": chunk},
                "reversal_of": None,
            },
            {"_id": 0, "id": 1, "source_id": 1, "status": 1},
        )
        async for doc in cursor:
            out[str(doc.get("source_id"))] = doc
    return out


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


def payment_create_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        invoice,
        PAYMENT_CREATE_CLAIM_FIELD,
        PAYMENT_CREATE_CLAIM_AT_FIELD,
        PAYMENT_CREATE_CLAIM_TTL_SECONDS,
        now=now,
    )


def payment_create_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        PAYMENT_CREATE_CLAIM_FIELD,
        PAYMENT_CREATE_CLAIM_AT_FIELD,
        PAYMENT_CREATE_CLAIM_TTL_SECONDS,
        now=now,
    )


def invoice_cancel_claim_is_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        invoice,
        INVOICE_CANCEL_CLAIM_FIELD,
        INVOICE_CANCEL_CLAIM_AT_FIELD,
        INVOICE_CANCEL_CLAIM_TTL_SECONDS,
        now=now,
    )


def invoice_cancel_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        INVOICE_CANCEL_CLAIM_FIELD,
        INVOICE_CANCEL_CLAIM_AT_FIELD,
        INVOICE_CANCEL_CLAIM_TTL_SECONDS,
        now=now,
    )


def cancel_unclaimed_filter(invoice_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Atomic cancel only when the invoice is active and exclusion claims are absent/stale."""
    return {
        "id": str(invoice_id),
        "cancelled": {"$ne": True},
        "$and": [
            posting_claim_absent_or_stale_clause(now=now),
            payment_create_claim_absent_or_stale_clause(now=now),
            invoice_cancel_claim_absent_or_stale_clause(now=now),
        ],
    }


def normalize_reversal_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < REVERSAL_REASON_MIN_LEN:
        raise HTTPException(400, REASON_REQUIRED_MESSAGE)
    return reason


async def clear_posting_claim(db, invoice_id: str) -> None:
    await db.purchase_invoices.update_one(
        {"id": str(invoice_id)},
        {
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_posting_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.purchase_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "cancelled": {"$ne": True},
            **reverse_claim_absent_or_stale_clause(),
        },
        {
            "$set": {CLAIM_FIELD: claim_token, CLAIM_AT_FIELD: now, "updated_at": now},
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_reverse_claim(db, invoice_id: str) -> None:
    await db.purchase_invoices.update_one(
        {"id": str(invoice_id)},
        {
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_reverse_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.purchase_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "cancelled": {"$ne": True},
            "$and": [
                posting_claim_absent_or_stale_clause(),
                payment_create_claim_absent_or_stale_clause(),
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


async def clear_payment_create_claim(db, invoice_id: str, *, token: Optional[str] = None) -> None:
    filt: Dict[str, Any] = {"id": str(invoice_id)}
    if token:
        filt[PAYMENT_CREATE_CLAIM_FIELD] = str(token)
    await db.purchase_invoices.update_one(
        filt,
        {
            "$unset": {PAYMENT_CREATE_CLAIM_FIELD: "", PAYMENT_CREATE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_payment_create_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Lease an active invoice for payment create. Fresh reverse/cancel claims block it."""
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    create_claim_ok = payment_create_claim_absent_or_stale_clause()
    create_claim_ok.setdefault("$or", []).append({PAYMENT_CREATE_CLAIM_FIELD: claim_token})
    return await db.purchase_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "cancelled": {"$ne": True},
            "$and": [
                reverse_claim_absent_or_stale_clause(),
                invoice_cancel_claim_absent_or_stale_clause(),
                create_claim_ok,
            ],
        },
        {
            "$set": {
                PAYMENT_CREATE_CLAIM_FIELD: claim_token,
                PAYMENT_CREATE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_invoice_cancel_claim(db, invoice_id: str, *, token: Optional[str] = None) -> None:
    """Token-scoped release. Never clears another worker's cancel claim."""
    filt: Dict[str, Any] = {"id": str(invoice_id)}
    if token:
        filt[INVOICE_CANCEL_CLAIM_FIELD] = str(token)
    await db.purchase_invoices.update_one(
        filt,
        {
            "$unset": {INVOICE_CANCEL_CLAIM_FIELD: "", INVOICE_CANCEL_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_invoice_cancel_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Lease an active invoice for cancel. Fresh post/create/reverse claims block it."""
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    cancel_ok = invoice_cancel_claim_absent_or_stale_clause()
    cancel_ok.setdefault("$or", []).append({INVOICE_CANCEL_CLAIM_FIELD: claim_token})
    return await db.purchase_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "cancelled": {"$ne": True},
            "$and": [
                posting_claim_absent_or_stale_clause(),
                payment_create_claim_absent_or_stale_clause(),
                reverse_claim_absent_or_stale_clause(),
                cancel_ok,
            ],
        },
        {
            "$set": {
                INVOICE_CANCEL_CLAIM_FIELD: claim_token,
                INVOICE_CANCEL_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def find_purchase_invoice_reversal_journal(db, original_journal_id: str) -> Optional[dict]:
    oid = str(original_journal_id or "").strip()
    if not oid:
        return None
    return await db.journal_entries.find_one({"reversal_of": oid}, {"_id": 0})


async def collect_active_payment_blockers(db, invoice_id: str) -> List[dict]:
    from services.purchase_payment_posting import posting_claim_is_active as payment_posting_claim_is_active

    rows = []
    cursor = db.purchase_payments.find({"purchase_invoice_id": str(invoice_id)}, {"_id": 0})
    async for rec in cursor:
        if is_cancelled(rec) and not payment_posting_claim_is_active(rec):
            continue
        if is_cancelled(rec) and payment_posting_claim_is_active(rec):
            rows.append({
                "id": rec.get("id"),
                "number": rec.get("payment_number") or rec.get("id"),
                "settlement_status": rec.get("settlement_status"),
                "amount": rec.get("amount"),
            })
            continue
        if is_cancelled(rec):
            continue
        rows.append({
            "id": rec.get("id"),
            "number": rec.get("payment_number") or rec.get("id"),
            "settlement_status": rec.get("settlement_status"),
            "amount": rec.get("amount"),
        })
    return rows


def raise_active_payment_blockers(blockers: List[dict]) -> None:
    if blockers:
        raise HTTPException(409, {
            "message": ACTIVE_PAYMENT_REVERSE_MESSAGE,
            "blockers": blockers,
        })


async def _count_active_payments_by_invoice(db, invoice_ids: List[str]) -> Dict[str, int]:
    ids = [str(i) for i in invoice_ids if i]
    counts: Dict[str, int] = {iid: 0 for iid in ids}
    if not ids:
        return counts
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        cursor = db.purchase_payments.aggregate([
            {"$match": {"purchase_invoice_id": {"$in": chunk}, "cancelled": {"$ne": True}}},
            {"$group": {"_id": "$purchase_invoice_id", "n": {"$sum": 1}}},
        ])
        async for row in cursor:
            counts[str(row.get("_id") or "")] = int(row.get("n") or 0)
    return counts


async def attach_invoice_accounting_fields(db, invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derived journal status + posting readiness. Never writes invoices or journals."""
    if not invoices:
        return invoices
    from services.purchase_accounting_settings import mappings_configured

    configured = await mappings_configured(db)
    journals = await load_source_journals_by_invoice_ids(
        db, [inv.get("id") for inv in invoices if inv.get("id")]
    )
    payment_counts = await _count_active_payments_by_invoice(
        db, [inv.get("id") for inv in invoices if inv.get("id")]
    )
    for inv in invoices:
        journal = journals.get(str(inv.get("id")))
        accounting_status = accounting_status_from_journal(journal)
        cancelled = is_cancelled(inv)
        amount_ok = False
        try:
            amount_ok = _money(inv.get("grand_total"), "grand_total") > 0
        except HTTPException:
            amount_ok = False
        inv["accounting_status"] = accounting_status
        inv["accounting_posted"] = accounting_status == ACCOUNTING_POSTED
        inv["accounting_journal_id"] = journal.get("id") if journal else None
        inv["accounting_ready"] = (not cancelled) and configured and amount_ok
        if journal:
            inv["can_cancel"] = False
            inv["cancel_block_reason"] = CANCEL_POSTED_MESSAGE
        inv["can_post_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_NOT_POSTED
            and configured
            and amount_ok
            and not posting_claim_is_active(inv)
            and not reverse_claim_is_active(inv)
        )
        inv["can_reverse_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_POSTED
            and payment_counts.get(str(inv.get("id") or ""), 0) <= 0
            and not posting_claim_is_active(inv)
            and not reverse_claim_is_active(inv)
            and not payment_create_claim_is_active(inv)
        )
        inv.pop(CLAIM_FIELD, None)
        inv.pop(CLAIM_AT_FIELD, None)
        inv.pop(REVERSE_CLAIM_FIELD, None)
        inv.pop(REVERSE_CLAIM_AT_FIELD, None)
        inv.pop(PAYMENT_CREATE_CLAIM_FIELD, None)
        inv.pop(PAYMENT_CREATE_CLAIM_AT_FIELD, None)
        inv.pop(INVOICE_CANCEL_CLAIM_FIELD, None)
        inv.pop(INVOICE_CANCEL_CLAIM_AT_FIELD, None)
    return invoices


async def _finalize_invoice_reversal_state(
    db,
    invoice: dict,
    user: dict,
    reason: str,
    reversal_journal: Optional[dict] = None,
) -> None:
    now = now_iso()
    stored = await db.purchase_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {"updated_at": now}
    if not is_cancelled(stored):
        patch.update(cancellation_fields(user, reason))
    if not stored.get("accounting_reversed_at"):
        patch["accounting_reversed_at"] = now
        patch["accounting_reversed_by"] = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": str(user.get("name") or user.get("email") or ""),
            "role": str(user.get("role") or ""),
        }
    if reason and not str(stored.get("accounting_reversal_reason") or "").strip():
        patch["accounting_reversal_reason"] = reason
    rev_id = None
    if reversal_journal and reversal_journal.get("id"):
        rev_id = reversal_journal.get("id")
    elif stored.get("accounting_reversal_journal_id"):
        rev_id = stored.get("accounting_reversal_journal_id")
    if rev_id:
        patch["accounting_reversal_journal_id"] = rev_id
    await db.purchase_invoices.update_one(
        {"id": invoice["id"]},
        {
            "$set": patch,
            "$unset": {
                REVERSE_CLAIM_FIELD: "",
                REVERSE_CLAIM_AT_FIELD: "",
                CLAIM_FIELD: "",
                CLAIM_AT_FIELD: "",
                PAYMENT_CREATE_CLAIM_FIELD: "",
                PAYMENT_CREATE_CLAIM_AT_FIELD: "",
                INVOICE_CANCEL_CLAIM_FIELD: "",
                INVOICE_CANCEL_CLAIM_AT_FIELD: "",
            },
        },
    )


async def restore_active_after_cancel_race(db, invoice_id: str) -> None:
    """Restore operational cancel vs post. Never undo a completed accounting reversal."""
    journal = await find_purchase_invoice_source_journal(db, invoice_id)
    if accounting_status_from_journal(journal) == ACCOUNTING_REVERSED:
        return
    stored = await db.purchase_invoices.find_one({"id": invoice_id}, {"_id": 0}) or {}
    if stored.get("accounting_reversed_at") or stored.get("accounting_reversal_journal_id"):
        return
    await db.purchase_invoices.update_one(
        {
            "id": invoice_id,
            "cancelled": True,
            "$and": [
                _claim_absent_clause("accounting_reversed_at"),
                _claim_absent_clause("accounting_reversal_journal_id"),
            ],
        },
        {
            "$set": {"updated_at": now_iso()},
            "$unset": {
                "cancelled": "",
                "cancelled_at": "",
                "cancelled_by": "",
                "cancel_reason": "",
            },
        },
    )


def _build_journal_lines(
    *,
    clearing_account_id: str,
    payable_account_id: str,
    grand_total: Decimal,
    invoice_number: str,
) -> List[dict]:
    ref = invoice_number or "faktur"
    return [
        {
            "account_id": clearing_account_id,
            "debit": grand_total,
            "credit": Decimal("0.00"),
            "description": f"Purchase Clearing {ref}",
        },
        {
            "account_id": payable_account_id,
            "debit": Decimal("0.00"),
            "credit": grand_total,
            "description": f"Hutang Usaha {ref}",
        },
    ]


async def _require_posting_settings(db) -> dict:
    settings = await get_purchase_accounting_settings(db)
    payable_id = str(settings.get("payable_account_id") or "").strip()
    clearing_id = str(settings.get("purchase_clearing_account_id") or "").strip()
    if not (payable_id and clearing_id) or not settings.get("configured"):
        raise HTTPException(400, SETTINGS_NOT_READY_MESSAGE)
    payable = _validate_payable(await _load_account(db, payable_id))
    clearing = _validate_purchase_clearing(await _load_account(db, clearing_id))
    return {
        "payable_account_id": str(payable["id"]),
        "purchase_clearing_account_id": str(clearing["id"]),
    }


def _require_posting_amount(invoice: dict) -> Decimal:
    grand = _money(invoice.get("grand_total"), "grand_total")
    if grand <= 0:
        raise HTTPException(400, "grand_total harus lebih besar dari 0 untuk posting accounting.")
    return grand


async def _load_invoice(db, invoice_id: str) -> dict:
    inv = await db.purchase_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.purchase_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")
    return inv


async def _write_source_journal(db, inv: dict, user: dict) -> None:
    grand = _require_posting_amount(inv)
    mapped = await _require_posting_settings(db)
    lines = _build_journal_lines(
        clearing_account_id=mapped["purchase_clearing_account_id"],
        payable_account_id=mapped["payable_account_id"],
        grand_total=grand,
        invoice_number=str(inv.get("invoice_number") or ""),
    )
    try:
        await post_balanced_journal(
            db,
            date=journal_date_from_invoice(inv),
            source_type=SOURCE_TYPE,
            source_id=str(inv["id"]),
            description=f"Faktur Pembelian {inv.get('invoice_number') or inv['id']}",
            lines=lines,
            posted_by=user,
        )
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_purchase_invoice_source_journal(db, inv["id"])
        if existing:
            return
        raise HTTPException(409, "Jurnal untuk faktur ini sudah ada.")


async def _public_invoice_detail(db, invoice_id: str) -> Dict[str, Any]:
    from services.purchase_invoices import get_purchase_invoice

    return await get_purchase_invoice(db, invoice_id)


async def post_purchase_invoice_accounting(
    db, invoice_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Post one active invoice. Idempotent. Safety is the Mongo claim, not the lock."""
    async with _optional_invoice_lock(serialize_in_process):
        inv = await _load_invoice(db, invoice_id)
        if is_cancelled(inv):
            raise HTTPException(409, CANCELLED_POST_MESSAGE)

        existing = await find_purchase_invoice_source_journal(db, inv["id"])
        if existing:
            if inv.get(CLAIM_FIELD):
                await clear_posting_claim(db, inv["id"])
            return await _public_invoice_detail(db, inv["id"])

        _require_posting_amount(inv)
        await _require_posting_settings(db)

        claimed = await acquire_posting_claim(db, inv["id"])
        if claimed is None:
            current = await db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            if current and is_cancelled(current):
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            if current and reverse_claim_is_active(current):
                raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            existing = await find_purchase_invoice_source_journal(db, inv["id"])
            if existing:
                return await _public_invoice_detail(db, inv["id"])
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_purchase_invoice_source_journal(db, inv["id"])
        if existing:
            await clear_posting_claim(db, inv["id"])
            return await _public_invoice_detail(db, inv["id"])

        try:
            # Recheck authoritative invoice immediately before a NEW journal write.
            # Do not rely only on the in-process named lock or the earlier claim snapshot.
            fresh = await db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or claimed
            if reverse_claim_is_active(fresh) or is_cancelled(fresh):
                await clear_posting_claim(db, inv["id"])
                if reverse_claim_is_active(fresh):
                    raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            await _write_source_journal(db, claimed, user)
        except Exception:
            still = await find_purchase_invoice_source_journal(db, inv["id"])
            if not still:
                await clear_posting_claim(db, inv["id"])
            raise

        current = await db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        if current and is_cancelled(current):
            await restore_active_after_cancel_race(db, inv["id"])

        await clear_posting_claim(db, inv["id"])
        return await _public_invoice_detail(db, inv["id"])


async def reverse_purchase_invoice_accounting(
    db, invoice_id: str, user: dict, reason: Any, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse a posted invoice journal. No payment cascade. Mongo claim is the lock."""
    reason = normalize_reversal_reason(reason)

    async with _optional_invoice_lock(serialize_in_process):
        inv = await _load_invoice(db, invoice_id)
        existing = await find_purchase_invoice_source_journal(db, inv["id"])
        status = accounting_status_from_journal(existing)

        if status == ACCOUNTING_REVERSED:
            rev = await find_purchase_invoice_reversal_journal(db, existing.get("id") if existing else "")
            await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
            return await _public_invoice_detail(db, inv["id"])

        if is_cancelled(inv):
            if reverse_claim_is_active(inv):
                await clear_reverse_claim(db, inv["id"])
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if posting_claim_is_active(inv):
            raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
        if payment_create_claim_is_active(inv):
            raise HTTPException(409, PAYMENT_CREATE_CLAIM_REVERSE_MESSAGE)

        raise_active_payment_blockers(await collect_active_payment_blockers(db, inv["id"]))

        claimed = await acquire_reverse_claim(db, inv["id"])
        if claimed is None:
            current = await db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            still = await find_purchase_invoice_source_journal(db, inv["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_purchase_invoice_reversal_journal(db, still.get("id") if still else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await _public_invoice_detail(db, inv["id"])
            if current and posting_claim_is_active(current):
                raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
            if current and payment_create_claim_is_active(current):
                raise HTTPException(409, PAYMENT_CREATE_CLAIM_REVERSE_MESSAGE)
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        try:
            blockers = await collect_active_payment_blockers(db, inv["id"])
            if blockers:
                await clear_reverse_claim(db, inv["id"])
                raise_active_payment_blockers(blockers)

            current = await db.purchase_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or claimed
            if payment_create_claim_is_active(current):
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(409, PAYMENT_CREATE_CLAIM_REVERSE_MESSAGE)

            existing = await find_purchase_invoice_source_journal(db, inv["id"])
            status = accounting_status_from_journal(existing)
            if status == ACCOUNTING_REVERSED:
                rev = await find_purchase_invoice_reversal_journal(db, existing.get("id") if existing else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await _public_invoice_detail(db, inv["id"])
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
                        f"Reversal Faktur Pembelian {inv.get('invoice_number') or inv['id']} — {reason}"
                    ),
                )
            except KeyError as exc:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(404, "Jurnal faktur tidak ditemukan.") from exc
            except PeriodLockedError as exc:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(409, str(exc)) from exc
            except JournalValidationError as exc:
                await clear_reverse_claim(db, inv["id"])
                raise HTTPException(400, str(exc)) from exc
            except JournalConflictError:
                still = await find_purchase_invoice_source_journal(db, inv["id"])
                if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                    await clear_reverse_claim(db, inv["id"])
                    raise HTTPException(409, "Reversal jurnal untuk faktur ini sudah ada.")
                rev_doc = await find_purchase_invoice_reversal_journal(db, still.get("id") if still else "")

            await _finalize_invoice_reversal_state(db, inv, user, reason, rev_doc)
            return await _public_invoice_detail(db, inv["id"])
        except Exception:
            still = await find_purchase_invoice_source_journal(db, inv["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_purchase_invoice_reversal_journal(db, still.get("id") if still else "")
                await _finalize_invoice_reversal_state(db, inv, user, reason, rev)
                return await _public_invoice_detail(db, inv["id"])
            raise


@asynccontextmanager
async def _optional_invoice_lock(enabled: bool):
    from services.purchase_common import get_named_lock

    if not enabled:
        yield
        return
    async with get_named_lock("purchase_invoice_accounting"):
        yield
