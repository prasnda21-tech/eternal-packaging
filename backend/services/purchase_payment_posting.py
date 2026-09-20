"""Purchase payment → journal_core posting (Phase 4A).

Explicit POST only. Does not auto-post on create/submit/confirm-paid.
Journal is Dr actual invoice AP account, Cr selected BANK account.
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
from services.cash_bank_master import (
    COLLECTION as CASH_BANK_COLLECTION,
    CashBankValidationError,
    get_cash_bank_raw,
    require_bank_coa,
)
from services.chart_of_accounts import get_account
from services.journal_core import (
    STATUS_POSTED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.purchase_accounting_settings import _validate_payable
from services.purchase_common import cancellation_fields, is_cancelled, now_iso
from services.purchase_invoice_posting import (
    ACCOUNTING_NOT_POSTED,
    ACCOUNTING_POSTED,
    ACCOUNTING_REVERSED,
    accounting_status_from_journal,
    find_purchase_invoice_source_journal,
    reverse_claim_is_active as invoice_reverse_claim_is_active,
)
from services.purchase_payments import SETTLEMENT_PAID, is_settlement_paid

SOURCE_TYPE = "purchase_payment"
SOURCE_REVERSAL = "purchase_payment_reversal"
PERM_POST = "purchase.payment.accounting.post"
PERM_REVERSE = "purchase.payment.accounting.reverse"
CLAIM_FIELD = "accounting_posting_claim"
CLAIM_AT_FIELD = "accounting_posting_claim_at"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"
REVERSE_CLAIM_AT_FIELD = "accounting_reverse_claim_at"
ACCOUNTING_POST_CLAIM_TTL_SECONDS = 600
ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS = 600
REVERSAL_REASON_MIN_LEN = 3

INVOICE_NOT_POSTED_MESSAGE = "Faktur harus diposting ke accounting terlebih dahulu."
INVOICE_REVERSE_IN_PROGRESS_MESSAGE = (
    "Faktur sedang di-reverse dari accounting dan tidak dapat diposting pembayarannya."
)
INVOICE_NOT_ACTIVE_MESSAGE = "Faktur terkait tidak aktif; pembayaran tidak dapat diposting."
CANCELLED_POST_MESSAGE = "Pembayaran yang dibatalkan tidak dapat diposting ke accounting."
NOT_PAID_POST_MESSAGE = "Hanya pembayaran yang sudah DIBAYAR yang dapat diposting ke accounting."
INACTIVE_CASH_BANK_MESSAGE = "Kas/Bank nonaktif tidak dapat digunakan untuk posting."
MISSING_CASH_BANK_MESSAGE = "Kas/Bank wajib dipilih untuk posting accounting."
INVOICE_AP_MISSING_MESSAGE = (
    "Jurnal faktur tidak memiliki akun hutang yang dapat digunakan untuk pembayaran."
)
INVOICE_AP_AMBIGUOUS_MESSAGE = (
    "Jurnal Faktur Pembelian memiliki lebih dari satu akun Hutang Usaha. "
    "Posting pembayaran harus diperiksa."
)
POSTED_CANCEL_MESSAGE = (
    "Pembayaran sudah diposting ke accounting dan tidak dapat dihapus. "
    "Gunakan Reverse Accounting."
)
REVERSED_CANCEL_MESSAGE = (
    "Pembayaran yang sudah di-reverse tidak dapat dihapus. "
    "Dokumen dipertahankan untuk jejak audit."
)
REASON_REQUIRED_MESSAGE = "Alasan reversal wajib diisi minimal 3 karakter."


def _money(raw: Any, field: str) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(400, f"{field} tidak valid.") from exc


def _actor_public(user: Optional[dict]) -> Dict[str, str]:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def journal_date_from_payment(payment: dict) -> str:
    raw = str((payment or {}).get("payment_date") or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal pembayaran tidak valid.")
    return raw[:10]


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


def posting_claim_is_active(payment: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        payment, CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_is_active(payment: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        payment, REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def posting_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def normalize_reversal_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < REVERSAL_REASON_MIN_LEN:
        raise HTTPException(400, REASON_REQUIRED_MESSAGE)
    return reason


async def find_purchase_payment_source_journal(db, payment_id: str) -> Optional[dict]:
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(payment_id))


async def load_source_journals_by_payment_ids(db, payment_ids: List[str]) -> Dict[str, dict]:
    ids = [str(i) for i in payment_ids if i]
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


def cash_bank_snapshot(master: dict, coa: Optional[dict] = None) -> Dict[str, Any]:
    account = coa or {}
    return {
        "cash_bank_id": master.get("id"),
        "cash_bank_name": master.get("name"),
        "cash_bank_account_id": master.get("account_id") or account.get("id"),
        "cash_bank_account_code": account.get("code") or master.get("account_code"),
        "cash_bank_account_name": account.get("name") or master.get("account_name"),
    }


async def resolve_cash_bank_for_posting(
    db, payment: dict, payload_cash_bank_id: Optional[str] = None
) -> Dict[str, Any]:
    stored = str((payment or {}).get("cash_bank_id") or "").strip()
    incoming = str(payload_cash_bank_id or "").strip()
    cash_bank_id = stored or incoming
    if not cash_bank_id:
        raise HTTPException(400, MISSING_CASH_BANK_MESSAGE)
    if stored and incoming and incoming != stored:
        raise HTTPException(400, "Kas/Bank pembayaran tidak dapat diganti setelah dipilih.")
    raw = await get_cash_bank_raw(db, cash_bank_id)
    if not raw:
        raise HTTPException(400, "Kas/Bank tidak ditemukan.")
    if not raw.get("active", True):
        raise HTTPException(400, INACTIVE_CASH_BANK_MESSAGE)
    try:
        coa = require_bank_coa(await get_account(db, raw.get("account_id") or ""))
    except CashBankValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    snap = cash_bank_snapshot(raw, coa)
    if not snap.get("cash_bank_account_id"):
        raise HTTPException(400, "Akun perkiraan Kas/Bank tidak valid.")
    return snap


def _line_amount(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


async def _journal_lines_for(db, journal: Optional[dict]) -> List[dict]:
    if not journal:
        return []
    lines = journal.get("lines")
    if isinstance(lines, list) and lines:
        return lines
    jid = str(journal.get("id") or "").strip()
    if not jid:
        return []
    return await db.journal_lines.find({"journal_id": jid}, {"_id": 0}).to_list(50)


async def resolve_invoice_payable_account(db, invoice_id: str) -> dict:
    """AP account is the APAY credit on the posted invoice journal, not current settings.

    Same APAY account split across multiple credit lines: sum and resolve that account.
    More than one distinct APAY account: block. Never pick a first-account fallback.
    """
    journal = await find_purchase_invoice_source_journal(db, invoice_id)
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, INVOICE_NOT_POSTED_MESSAGE)
    lines = await _journal_lines_for(db, journal)
    totals: Dict[str, Decimal] = {}
    accounts: Dict[str, dict] = {}
    for ln in lines:
        debit = _line_amount(ln.get("debit"))
        credit = _line_amount(ln.get("credit"))
        if credit <= 0 or debit > 0:
            continue
        account = await get_account(db, ln.get("account_id") or "")
        if not account:
            continue
        try:
            valid = _validate_payable(account)
        except HTTPException:
            continue
        aid = str(valid.get("id") or "")
        if not aid:
            continue
        totals[aid] = totals.get(aid, Decimal("0.00")) + credit
        accounts[aid] = valid
    if not totals:
        raise HTTPException(400, INVOICE_AP_MISSING_MESSAGE)
    if len(totals) > 1:
        raise HTTPException(400, INVOICE_AP_AMBIGUOUS_MESSAGE)
    only_id = next(iter(totals))
    return accounts[only_id]


async def snapshot_cash_bank_from_journal(db, journal: dict) -> Dict[str, Any]:
    lines = await _journal_lines_for(db, journal)
    credit_lines = [ln for ln in lines if _line_amount(ln.get("credit")) > 0]
    if not credit_lines:
        raise HTTPException(400, "Jurnal pembayaran tidak memiliki baris Kas/Bank.")
    credit_lines.sort(key=lambda ln: _line_amount(ln.get("credit")), reverse=True)
    account_id = str(credit_lines[0].get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(400, "Akun perkiraan Kas/Bank pada jurnal tidak valid.")
    raw = await db[CASH_BANK_COLLECTION].find_one({"account_id": account_id}, {"_id": 0})
    coa = await get_account(db, account_id)
    if raw:
        return cash_bank_snapshot(raw, coa)
    return {
        "cash_bank_id": None,
        "cash_bank_name": (coa or {}).get("name"),
        "cash_bank_account_id": account_id,
        "cash_bank_account_code": (coa or {}).get("code"),
        "cash_bank_account_name": (coa or {}).get("name"),
    }


async def _heal_posted_payment_from_journal(db, payment: dict, journal: dict, user: dict) -> None:
    snap = await snapshot_cash_bank_from_journal(db, journal)
    now = now_iso()
    stored = await db.purchase_payments.find_one({"id": payment["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {**snap, "updated_at": now}
    if not stored.get("accounting_posted_at"):
        patch["accounting_posted_at"] = journal.get("posted_at") or now
        patch["accounting_posted_by"] = stored.get("accounting_posted_by") or _actor_public(user)
    await db.purchase_payments.update_one(
        {"id": payment["id"]},
        {
            "$set": patch,
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
        },
    )


async def _finalize_reversal_operational_state(db, payment: dict, user: dict, reason: str, reversal_journal: Optional[dict] = None) -> None:
    now = now_iso()
    stored = await db.purchase_payments.find_one({"id": payment["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {"updated_at": now}
    if not is_cancelled(stored):
        patch.update(cancellation_fields(user, reason))
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
    await db.purchase_payments.update_one(
        {"id": payment["id"]},
        {
            "$set": patch,
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
        },
    )


def _require_payment_amount(payment: dict) -> Decimal:
    amount = _money(payment.get("amount"), "amount")
    if amount <= 0:
        raise HTTPException(400, "Jumlah pembayaran harus lebih besar dari 0 untuk posting accounting.")
    return amount


def _journal_description(payment: dict) -> str:
    payment_number = str(payment.get("payment_number") or payment.get("id") or "PAY")
    invoice_number = str(payment.get("invoice_number") or payment.get("purchase_invoice_id") or "INV")
    supplier = str(payment.get("supplier") or "").strip()
    if supplier:
        return f"Pembayaran {payment_number} untuk {invoice_number} — {supplier}"
    return f"Pembayaran {payment_number} untuk {invoice_number}"


def _build_journal_lines(
    *,
    payable_account_id: str,
    bank_account_id: str,
    amount: Decimal,
    payment_number: str,
) -> List[dict]:
    ref = payment_number or "pembayaran"
    return [
        {
            "account_id": payable_account_id,
            "debit": amount,
            "credit": Decimal("0.00"),
            "description": f"Hutang Usaha {ref}",
        },
        {
            "account_id": bank_account_id,
            "debit": Decimal("0.00"),
            "credit": amount,
            "description": f"Kas/Bank {ref}",
        },
    ]


async def list_cash_bank_options(db) -> List[dict]:
    from services.cash_bank_master import list_cash_banks

    rows = await list_cash_banks(db, active_only=True)
    return [{"id": row.get("id"), "name": row.get("name")} for row in rows if row.get("id")]


async def attach_payment_accounting_fields(db, payments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derived journal status + posting readiness. Never writes payments or journals."""
    if not payments:
        return payments

    journals = await load_source_journals_by_payment_ids(
        db, [rec.get("id") for rec in payments if rec.get("id")]
    )
    invoice_ids = list({str(rec.get("purchase_invoice_id")) for rec in payments if rec.get("purchase_invoice_id")})
    invoice_journals = {}
    if invoice_ids:
        from services.purchase_invoice_posting import load_source_journals_by_invoice_ids

        invoice_journals = await load_source_journals_by_invoice_ids(db, invoice_ids)

    for rec in payments:
        journal = journals.get(str(rec.get("id")))
        accounting_status = accounting_status_from_journal(journal)
        invoice_journal = invoice_journals.get(str(rec.get("purchase_invoice_id") or ""))
        invoice_posted = accounting_status_from_journal(invoice_journal) == ACCOUNTING_POSTED
        cancelled = is_cancelled(rec)
        paid = is_settlement_paid(rec)
        amount_ok = False
        try:
            amount_ok = _money(rec.get("amount"), "amount") > 0
        except HTTPException:
            amount_ok = False
        rec["accounting_status"] = accounting_status
        rec["accounting_posted"] = accounting_status == ACCOUNTING_POSTED
        rec["accounting_journal_id"] = journal.get("id") if journal else None
        rec["can_post_accounting"] = (
            not cancelled
            and paid
            and accounting_status == ACCOUNTING_NOT_POSTED
            and invoice_posted
            and amount_ok
            and bool(rec.get("purchase_invoice_id"))
            and not posting_claim_is_active(rec)
            and not reverse_claim_is_active(rec)
        )
        rec["can_reverse_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_POSTED
            and not reverse_claim_is_active(rec)
        )
        if journal:
            rec["can_cancel"] = False
            rec["cancel_block_reason"] = (
                REVERSED_CANCEL_MESSAGE if accounting_status == ACCOUNTING_REVERSED else POSTED_CANCEL_MESSAGE
            )
        rec.pop(CLAIM_FIELD, None)
        rec.pop(CLAIM_AT_FIELD, None)
        rec.pop(REVERSE_CLAIM_FIELD, None)
        rec.pop(REVERSE_CLAIM_AT_FIELD, None)
    return payments


async def clear_posting_claim(db, payment_id: str) -> None:
    await db.purchase_payments.update_one(
        {"id": str(payment_id)},
        {
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def clear_reverse_claim(db, payment_id: str) -> None:
    await db.purchase_payments.update_one(
        {"id": str(payment_id)},
        {
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_posting_claim(db, payment_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.purchase_payments.find_one_and_update(
        {
            "id": str(payment_id),
            "cancelled": {"$ne": True},
            "settlement_status": SETTLEMENT_PAID,
            **reverse_claim_absent_or_stale_clause(),
        },
        {
            "$set": {CLAIM_FIELD: claim_token, CLAIM_AT_FIELD: now, "updated_at": now},
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def acquire_reverse_claim(db, payment_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db.purchase_payments.find_one_and_update(
        {
            "id": str(payment_id),
            "cancelled": {"$ne": True},
            **posting_claim_absent_or_stale_clause(),
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


@asynccontextmanager
async def _optional_payment_lock(enabled: bool):
    from services.purchase_common import get_named_lock

    if not enabled:
        yield
        return
    async with get_named_lock("purchase_payment_accounting"):
        yield


async def _load_payment(db, payment_id: str) -> dict:
    rec = await db.purchase_payments.find_one({"id": str(payment_id)}, {"_id": 0})
    if not rec:
        rec = await db.purchase_payments.find_one({"payment_number": str(payment_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, f"Pembayaran Pembelian '{payment_id}' tidak ditemukan.")
    return rec


async def assert_invoice_allows_payment_journal(db, invoice_id: str) -> dict:
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Pembelian (purchase_invoice_id) wajib diisi.")
    invoice = await db.purchase_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        invoice = await db.purchase_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")
    if is_cancelled(invoice):
        raise HTTPException(409, INVOICE_NOT_ACTIVE_MESSAGE)
    if invoice_reverse_claim_is_active(invoice):
        raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_MESSAGE)
    journal = await find_purchase_invoice_source_journal(db, invoice["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, INVOICE_NOT_POSTED_MESSAGE)
    return invoice


async def _write_source_journal(db, payment: dict, user: dict, cash_snap: dict) -> dict:
    amount = _require_payment_amount(payment)
    payable = await resolve_invoice_payable_account(db, payment.get("purchase_invoice_id"))
    lines = _build_journal_lines(
        payable_account_id=str(payable["id"]),
        bank_account_id=str(cash_snap["cash_bank_account_id"]),
        amount=amount,
        payment_number=str(payment.get("payment_number") or ""),
    )
    try:
        journal = await post_balanced_journal(
            db,
            date=journal_date_from_payment(payment),
            source_type=SOURCE_TYPE,
            source_id=str(payment["id"]),
            description=_journal_description(payment),
            lines=lines,
            posted_by=user,
        )
        return journal
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_purchase_payment_source_journal(db, payment["id"])
        if existing:
            return existing
        raise HTTPException(409, "Jurnal untuk pembayaran ini sudah ada.")


async def _public_payment_detail(db, payment_id: str) -> Dict[str, Any]:
    from services.purchase_payments import get_purchase_payment

    return await get_purchase_payment(db, payment_id)


async def find_purchase_payment_reversal_journal(db, original_journal_id: str) -> Optional[dict]:
    oid = str(original_journal_id or "").strip()
    if not oid:
        return None
    return await db.journal_entries.find_one({"reversal_of": oid}, {"_id": 0})


async def post_purchase_payment_accounting(
    db,
    payment_id: str,
    user: dict,
    *,
    cash_bank_id: Optional[str] = None,
    serialize_in_process: bool = True,
) -> Dict[str, Any]:
    """Post one PAID payment. Idempotent. Finalize metadata from the actual journal."""
    async with _optional_payment_lock(serialize_in_process):
        rec = await _load_payment(db, payment_id)
        if is_cancelled(rec):
            raise HTTPException(409, CANCELLED_POST_MESSAGE)
        if not is_settlement_paid(rec):
            raise HTTPException(409, NOT_PAID_POST_MESSAGE)

        existing = await find_purchase_payment_source_journal(db, rec["id"])
        if existing:
            await _heal_posted_payment_from_journal(db, rec, existing, user)
            return await _public_payment_detail(db, rec["id"])

        _require_payment_amount(rec)
        await assert_invoice_allows_payment_journal(db, rec.get("purchase_invoice_id"))
        await resolve_invoice_payable_account(db, rec.get("purchase_invoice_id"))
        cash_snap = await resolve_cash_bank_for_posting(db, rec, cash_bank_id)

        claimed = await acquire_posting_claim(db, rec["id"])
        if claimed is None:
            current = await db.purchase_payments.find_one({"id": rec["id"]}, {"_id": 0})
            if current and is_cancelled(current):
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            existing = await find_purchase_payment_source_journal(db, rec["id"])
            if existing:
                await _heal_posted_payment_from_journal(db, rec, existing, user)
                return await _public_payment_detail(db, rec["id"])
            if current and reverse_claim_is_active(current):
                raise HTTPException(409, "Pembayaran sedang di-reverse dari accounting.")
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_purchase_payment_source_journal(db, rec["id"])
        if existing:
            await _heal_posted_payment_from_journal(db, rec, existing, user)
            return await _public_payment_detail(db, rec["id"])

        try:
            await assert_invoice_allows_payment_journal(db, claimed.get("purchase_invoice_id") or rec.get("purchase_invoice_id"))
            journal = await _write_source_journal(db, claimed, user, cash_snap)
        except Exception:
            still = await find_purchase_payment_source_journal(db, rec["id"])
            if still:
                await _heal_posted_payment_from_journal(db, rec, still, user)
                return await _public_payment_detail(db, rec["id"])
            await clear_posting_claim(db, rec["id"])
            raise

        await _heal_posted_payment_from_journal(db, rec, journal, user)
        return await _public_payment_detail(db, rec["id"])


async def reverse_purchase_payment_accounting(
    db, payment_id: str, user: dict, reason: Any, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse a posted payment journal and exclude it from operational paid_total."""
    reason = normalize_reversal_reason(reason)

    async with _optional_payment_lock(serialize_in_process):
        rec = await _load_payment(db, payment_id)
        existing = await find_purchase_payment_source_journal(db, rec["id"])
        status = accounting_status_from_journal(existing)

        if status == ACCOUNTING_REVERSED:
            rev = await find_purchase_payment_reversal_journal(db, existing.get("id") if existing else "")
            await _finalize_reversal_operational_state(db, rec, user, reason, rev)
            return await _public_payment_detail(db, rec["id"])

        if is_cancelled(rec):
            if reverse_claim_is_active(rec):
                await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, "Hanya pembayaran yang sudah diposting yang dapat di-reverse.")

        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, "Hanya pembayaran yang sudah diposting yang dapat di-reverse.")

        claimed = await acquire_reverse_claim(db, rec["id"])
        if claimed is None:
            current = await db.purchase_payments.find_one({"id": rec["id"]}, {"_id": 0})
            still = await find_purchase_payment_source_journal(db, rec["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_purchase_payment_reversal_journal(db, still.get("id") if still else "")
                await _finalize_reversal_operational_state(db, rec, user, reason, rev)
                return await _public_payment_detail(db, rec["id"])
            if current and posting_claim_is_active(current):
                raise HTTPException(409, "Pembayaran sedang diposting ke accounting.")
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        existing = await find_purchase_payment_source_journal(db, rec["id"])
        if accounting_status_from_journal(existing) == ACCOUNTING_REVERSED:
            rev = await find_purchase_payment_reversal_journal(db, existing.get("id") if existing else "")
            await _finalize_reversal_operational_state(db, rec, user, reason, rev)
            return await _public_payment_detail(db, rec["id"])
        if accounting_status_from_journal(existing) != ACCOUNTING_POSTED or not existing:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, "Hanya pembayaran yang sudah diposting yang dapat di-reverse.")

        try:
            rev_doc = await reverse_journal(
                db,
                existing["id"],
                source_type=SOURCE_REVERSAL,
                source_id=str(rec["id"]),
                posted_by=user,
                description=f"Reversal {_journal_description(rec)} — {reason}",
            )
        except KeyError as exc:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(404, "Jurnal pembayaran tidak ditemukan.") from exc
        except PeriodLockedError as exc:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            still = await find_purchase_payment_source_journal(db, rec["id"])
            if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                await clear_reverse_claim(db, rec["id"])
                raise HTTPException(409, "Reversal jurnal untuk pembayaran ini sudah ada.")
            rev_doc = await find_purchase_payment_reversal_journal(db, still.get("id") if still else "")

        await _finalize_reversal_operational_state(db, rec, user, reason, rev_doc)
        return await _public_payment_detail(db, rec["id"])
