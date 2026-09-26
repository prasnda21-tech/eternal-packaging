"""Sales receipt → journal_core posting (Phase 1A).

Explicit POST only. Does not auto-post on create, write invoice journals,
or mutate production workflow. Reuses journal_core.post_balanced_journal
and journal_core.reverse_journal.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.cash_bank_master import (
    COLLECTION as CASH_BANK_COLLECTION,
    CashBankValidationError,
    get_cash_bank_raw,
    require_bank_coa,
)
from services.chart_of_accounts import get_account
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
from services.sales_accounting_settings import _validate_receivable
from services.sales_invoice_posting import (
    ACCOUNTING_NOT_POSTED,
    ACCOUNTING_POSTED,
    ACCOUNTING_REVERSED,
    accounting_status_from_journal,
    find_sales_invoice_source_journal,
    reverse_claim_is_active,
)

SOURCE_TYPE = "sales_receipt"
SOURCE_REVERSAL = "sales_receipt_reversal"
PERM_POST = "sales.receipt.accounting.post"
PERM_REVERSE = "sales.receipt.accounting.reverse"
CLAIM_FIELD = "accounting_posting_claim"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"
DELETE_CLAIM_FIELD = "accounting_delete_claim"
DELETE_CLAIM_AT_FIELD = "accounting_delete_claim_at"
DELETE_CLAIM_TTL_SECONDS = 600

INVOICE_NOT_POSTED_MESSAGE = "Faktur harus diposting ke accounting terlebih dahulu."
INVOICE_REVERSE_IN_PROGRESS_RECEIPT_MESSAGE = (
    "Faktur sedang di-reverse dari accounting dan tidak dapat diposting penerimaan."
)
INVOICE_NOT_FINALIZED_RECEIPT_POST_MESSAGE = (
    "Hanya faktur finalized yang dapat diposting penerimaannya."
)
POSTED_DELETE_MESSAGE = (
    "Penerimaan sudah diposting ke accounting dan tidak dapat dihapus. "
    "Gunakan Reverse Accounting."
)
REVERSED_DELETE_MESSAGE = (
    "Penerimaan yang sudah di-reverse tidak dapat dihapus. "
    "Dokumen dipertahankan untuk jejak audit."
)
CANCELLED_POST_MESSAGE = "Penerimaan yang dibatalkan tidak dapat diposting ke accounting."
INACTIVE_CASH_BANK_MESSAGE = "Kas/Bank nonaktif tidak dapat digunakan untuk posting."
MISSING_CASH_BANK_MESSAGE = "Kas/Bank wajib dipilih untuk posting accounting."
POSTING_IN_PROGRESS_DELETE_MESSAGE = (
    "Penerimaan sedang diposting ke accounting dan tidak dapat dihapus."
)
DELETE_IN_PROGRESS_POST_MESSAGE = (
    "Penerimaan sedang dihapus dan tidak dapat diposting ke accounting."
)
DELETE_IN_PROGRESS_DELETE_MESSAGE = (
    "Penerimaan sedang dihapus dan tidak dapat dihapus secara satuan."
)
INVOICE_AR_MISSING_MESSAGE = (
    "Jurnal faktur tidak memiliki akun piutang yang dapat digunakan untuk penerimaan."
)


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


def journal_date_from_receipt(receipt: dict) -> str:
    raw = str((receipt or {}).get("receipt_date") or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal penerimaan tidak valid.")
    return raw[:10]


def receipt_is_cancelled(receipt: Optional[dict]) -> bool:
    if not receipt:
        return False
    status = str(receipt.get("financial_status") or "").strip().lower()
    if status in ("cancelled", "reversed"):
        return True
    return receipt.get("cancelled") is True


def receipt_counts_toward_outstanding(receipt: Optional[dict]) -> bool:
    return not receipt_is_cancelled(receipt)


def posting_claim_is_active(receipt: Optional[dict]) -> bool:
    raw = (receipt or {}).get(CLAIM_FIELD)
    return bool(str(raw or "").strip())


def reverse_claim_is_active(receipt: Optional[dict]) -> bool:
    raw = (receipt or {}).get(REVERSE_CLAIM_FIELD)
    return bool(str(raw or "").strip())


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


def delete_claim_cutoff_iso(*, now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(seconds=DELETE_CLAIM_TTL_SECONDS)).isoformat()


def delete_claim_is_active(receipt: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """Fresh lease only. Token without timestamp is stale/recoverable."""
    token = str((receipt or {}).get(DELETE_CLAIM_FIELD) or "").strip()
    if not token:
        return False
    claimed_at = _parse_claim_at((receipt or {}).get(DELETE_CLAIM_AT_FIELD))
    if claimed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < DELETE_CLAIM_TTL_SECONDS


def _claim_absent_clause(field: str) -> Dict[str, Any]:
    return {"$or": [{field: {"$exists": False}}, {field: None}, {field: ""}]}


def delete_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    cutoff = delete_claim_cutoff_iso(now=now)
    return {
        "$or": [
            {DELETE_CLAIM_FIELD: {"$exists": False}},
            {DELETE_CLAIM_FIELD: None},
            {DELETE_CLAIM_FIELD: ""},
            {DELETE_CLAIM_AT_FIELD: {"$exists": False}},
            {DELETE_CLAIM_AT_FIELD: None},
            {DELETE_CLAIM_AT_FIELD: ""},
            {DELETE_CLAIM_AT_FIELD: {"$lte": cutoff}},
        ]
    }


async def find_sales_receipt_source_journal(db, receipt_id: str) -> Optional[dict]:
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(receipt_id))


async def load_source_journals_by_receipt_ids(db, receipt_ids: List[str]) -> Dict[str, dict]:
    ids = [str(i) for i in receipt_ids if i]
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


def cash_bank_snapshot(master: dict, coa: Optional[dict] = None) -> Dict[str, Any]:
    account = coa or {}
    return {
        "cash_bank_id": master.get("id"),
        "cash_bank_name": master.get("name"),
        "cash_bank_account_id": master.get("account_id") or account.get("id"),
        "cash_bank_account_code": account.get("code") or master.get("account_code"),
        "cash_bank_account_name": account.get("name") or master.get("account_name"),
    }


async def resolve_cash_bank_for_create(db, cash_bank_id: Any) -> Dict[str, Any]:
    cid = str(cash_bank_id or "").strip()
    if not cid:
        raise HTTPException(400, "Kas/Bank wajib dipilih.")
    raw = await get_cash_bank_raw(db, cid)
    if not raw:
        raise HTTPException(400, "Kas/Bank tidak ditemukan.")
    if not raw.get("active", True):
        raise HTTPException(400, INACTIVE_CASH_BANK_MESSAGE)
    try:
        coa = require_bank_coa(await get_account(db, raw.get("account_id") or ""))
    except CashBankValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return cash_bank_snapshot(raw, coa)


async def resolve_cash_bank_for_posting(
    db, receipt: dict, payload_cash_bank_id: Optional[str] = None
) -> Dict[str, Any]:
    stored = str((receipt or {}).get("cash_bank_id") or "").strip()
    incoming = str(payload_cash_bank_id or "").strip()
    cash_bank_id = stored or incoming
    if not cash_bank_id:
        raise HTTPException(400, MISSING_CASH_BANK_MESSAGE)
    if stored and incoming and incoming != stored:
        raise HTTPException(400, "Kas/Bank penerimaan tidak dapat diganti setelah dipilih.")
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


async def resolve_invoice_receivable_account(db, invoice_id: str) -> dict:
    """AR account is the AREC debit on the posted invoice journal, not current settings."""
    journal = await find_sales_invoice_source_journal(db, invoice_id)
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, INVOICE_NOT_POSTED_MESSAGE)
    lines = await _journal_lines_for(db, journal)
    candidates: List[dict] = []
    for ln in lines:
        debit = _line_amount(ln.get("debit"))
        credit = _line_amount(ln.get("credit"))
        if debit <= 0 or credit > 0:
            continue
        account = await get_account(db, ln.get("account_id") or "")
        if not account:
            continue
        try:
            candidates.append(_validate_receivable(account))
        except HTTPException:
            continue
    if not candidates:
        raise HTTPException(400, INVOICE_AR_MISSING_MESSAGE)
    return candidates[0]


async def snapshot_cash_bank_from_journal(db, journal: dict) -> Dict[str, Any]:
    """Cash/Bank snapshot from the actual receipt journal debit line."""
    lines = await _journal_lines_for(db, journal)
    debit_lines = [ln for ln in lines if _line_amount(ln.get("debit")) > 0]
    if not debit_lines:
        raise HTTPException(400, "Jurnal penerimaan tidak memiliki baris Kas/Bank.")
    debit_lines.sort(key=lambda ln: _line_amount(ln.get("debit")), reverse=True)
    account_id = str(debit_lines[0].get("account_id") or "").strip()
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


async def _heal_posted_receipt_from_journal(db, receipt: dict, journal: dict, user: dict) -> None:
    from services.sales_receipts import now_iso

    snap = await snapshot_cash_bank_from_journal(db, journal)
    now = now_iso()
    stored = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {**snap, "updated_at": now}
    if not stored.get("accounting_posted_at"):
        patch["accounting_posted_at"] = journal.get("posted_at") or now
        patch["accounting_posted_by"] = stored.get("accounting_posted_by") or _actor_public(user)
    await db.sales_receipts.update_one(
        {"id": receipt["id"]},
        {"$set": patch, "$unset": {CLAIM_FIELD: ""}},
    )


async def _finalize_reversal_operational_state(db, receipt: dict, user: dict) -> None:
    from services.sales_receipts import _recalc_invoice_payment_from_receipts, now_iso

    now = now_iso()
    stored = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {
        "financial_status": "cancelled",
        "cancelled": True,
        "updated_at": now,
    }
    if not stored.get("cancelled_at"):
        patch["cancelled_at"] = now
        patch["cancelled_by"] = _actor_public(user)
    if not stored.get("accounting_reversed_at"):
        patch["accounting_reversed_at"] = now
        patch["accounting_reversed_by"] = _actor_public(user)
    await db.sales_receipts.update_one(
        {"id": receipt["id"]},
        {"$set": patch, "$unset": {REVERSE_CLAIM_FIELD: ""}},
    )
    await _recalc_invoice_payment_from_receipts(db, stored.get("invoice_id") or receipt.get("invoice_id"))


def _require_receipt_amount(receipt: dict) -> Decimal:
    amount = _money(receipt.get("amount"), "amount")
    if amount <= 0:
        raise HTTPException(400, "Jumlah penerimaan harus lebih besar dari 0 untuk posting accounting.")
    return amount


def receipt_allocated_amount(receipt: Optional[dict]) -> Decimal:
    """Cash applied to the receipt invoice. Legacy rows without the field use amount."""
    if not receipt:
        return Decimal("0.00")
    if "allocated_amount" in receipt and receipt.get("allocated_amount") is not None:
        return _money(receipt.get("allocated_amount"), "allocated_amount")
    return _money(receipt.get("amount"), "amount")


def receipt_customer_credit_amount(receipt: Optional[dict]) -> Decimal:
    """Excess cash held as customer credit. Legacy rows are zero, not a guessed balance."""
    if not receipt or "customer_credit_amount" not in receipt or receipt.get("customer_credit_amount") is None:
        return Decimal("0.00")
    return _money(receipt.get("customer_credit_amount"), "customer_credit_amount")


def _journal_description(receipt: dict) -> str:
    receipt_number = str(receipt.get("receipt_number") or receipt.get("id") or "PAY")
    invoice_number = str(receipt.get("invoice_number") or receipt.get("invoice_id") or "INV")
    customer = str(receipt.get("customer") or "").strip()
    if customer:
        return f"Penerimaan {receipt_number} untuk {invoice_number} — {customer}"
    return f"Penerimaan {receipt_number} untuk {invoice_number}"


def _build_journal_lines(
    *,
    cash_ledger_account_id: str,
    receivable_account_id: str,
    amount: Decimal,
    receipt_number: str,
    allocated_amount: Optional[Decimal] = None,
    customer_credit_amount: Optional[Decimal] = None,
    deposit_account_id: Optional[str] = None,
) -> List[dict]:
    ref = receipt_number or "penerimaan"
    allocated = amount if allocated_amount is None else allocated_amount
    credit = customer_credit_amount or Decimal("0.00")
    if allocated < 0 or credit < 0 or (allocated + credit) != amount:
        raise HTTPException(400, "Alokasi penerimaan dan saldo pelanggan tidak seimbang dengan jumlah diterima.")
    lines = [
        {
            "account_id": cash_ledger_account_id,
            "debit": amount,
            "credit": Decimal("0.00"),
            "description": f"Kas/Bank {ref}",
        },
    ]
    if allocated > 0:
        lines.append({
            "account_id": receivable_account_id,
            "debit": Decimal("0.00"),
            "credit": allocated,
            "description": f"Piutang {ref}",
        })
    if credit > 0:
        if not deposit_account_id:
            raise HTTPException(409, "Akun Titipan/Uang Muka Pelanggan belum dipetakan pada Pengaturan Akuntansi Penjualan.")
        lines.append({
            "account_id": str(deposit_account_id),
            "debit": Decimal("0.00"),
            "credit": credit,
            "description": f"Titipan pelanggan {ref}",
        })
    return lines


async def list_cash_bank_options(db) -> List[dict]:
    """Active Kas/Bank options for receipt create/post. id + name only."""
    from services.cash_bank_master import list_cash_banks

    rows = await list_cash_banks(db, active_only=True)
    return [{"id": row.get("id"), "name": row.get("name")} for row in rows if row.get("id")]


async def attach_receipt_accounting_fields(db, receipts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derived journal status + posting readiness. Never writes receipts or journals."""
    if not receipts:
        return receipts

    journals = await load_source_journals_by_receipt_ids(
        db, [rec.get("id") for rec in receipts if rec.get("id")]
    )
    invoice_ids = list({str(rec.get("invoice_id")) for rec in receipts if rec.get("invoice_id")})
    invoice_journals = {}
    if invoice_ids:
        from services.sales_invoice_posting import load_source_journals_by_invoice_ids

        invoice_journals = await load_source_journals_by_invoice_ids(db, invoice_ids)

    for rec in receipts:
        journal = journals.get(str(rec.get("id")))
        accounting_status = accounting_status_from_journal(journal)
        invoice_journal = invoice_journals.get(str(rec.get("invoice_id") or ""))
        invoice_posted = accounting_status_from_journal(invoice_journal) == ACCOUNTING_POSTED
        cancelled = receipt_is_cancelled(rec)
        amount_ok = False
        try:
            amount_ok = _money(rec.get("amount"), "amount") > 0
        except HTTPException:
            amount_ok = False
        rec["financial_status"] = "cancelled" if cancelled else "active"
        rec["accounting_status"] = accounting_status
        rec["accounting_posted"] = accounting_status == ACCOUNTING_POSTED
        rec["accounting_journal_id"] = journal.get("id") if journal else None
        rec["can_post_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_NOT_POSTED
            and invoice_posted
            and amount_ok
            and bool(rec.get("invoice_id"))
            and not posting_claim_is_active(rec)
            and not delete_claim_is_active(rec)
        )
        rec["can_reverse_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_POSTED
            and not reverse_claim_is_active(rec)
        )
        rec["can_delete"] = (
            accounting_status == ACCOUNTING_NOT_POSTED
            and not cancelled
            and not journal
            and not posting_claim_is_active(rec)
            and not delete_claim_is_active(rec)
        )
        from services.sales_customer_credit import CLAIM_AT_FIELD as CREDIT_CLAIM_AT
        from services.sales_customer_credit import CLAIM_FIELD as CREDIT_CLAIM
        from services.sales_customer_credit import credit_summary

        rec["customer_credit"] = credit_summary(
            rec, accounting_posted=accounting_status == ACCOUNTING_POSTED
        )
        rec["allocated_amount"] = float(receipt_allocated_amount(rec))
        rec["customer_credit_amount"] = float(receipt_customer_credit_amount(rec))
        rec.pop(CLAIM_FIELD, None)
        rec.pop(REVERSE_CLAIM_FIELD, None)
        rec.pop(DELETE_CLAIM_FIELD, None)
        rec.pop(DELETE_CLAIM_AT_FIELD, None)
        rec.pop(CREDIT_CLAIM, None)
        rec.pop(CREDIT_CLAIM_AT, None)
    return receipts


async def clear_posting_claim(db, receipt_id: str) -> None:
    from services.sales_receipts import now_iso

    await db.sales_receipts.update_one(
        {"id": str(receipt_id)},
        {"$unset": {CLAIM_FIELD: ""}, "$set": {"updated_at": now_iso()}},
    )


async def clear_reverse_claim(db, receipt_id: str) -> None:
    from services.sales_receipts import now_iso

    await db.sales_receipts.update_one(
        {"id": str(receipt_id)},
        {"$unset": {REVERSE_CLAIM_FIELD: ""}, "$set": {"updated_at": now_iso()}},
    )


async def acquire_posting_claim(db, receipt_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    from services.sales_receipts import now_iso

    claim_token = str(token or uuid.uuid4())
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "$nor": [
                {"financial_status": {"$in": ["cancelled", "reversed"]}},
                {"cancelled": True},
            ],
            **delete_claim_absent_or_stale_clause(),
        },
        {
            "$set": {CLAIM_FIELD: claim_token, "updated_at": now_iso()},
            "$unset": {DELETE_CLAIM_FIELD: "", DELETE_CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def acquire_delete_claim(db, receipt_id: str, token: str) -> Optional[dict]:
    from services.sales_receipts import now_iso

    now = now_iso()
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "$nor": [
                {"financial_status": {"$in": ["cancelled", "reversed"]}},
                {"cancelled": True},
            ],
            "$and": [
                _claim_absent_clause(CLAIM_FIELD),
                delete_claim_absent_or_stale_clause(),
            ],
        },
        {
            "$set": {
                DELETE_CLAIM_FIELD: str(token),
                DELETE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def release_delete_claims(db, token: str) -> None:
    from services.sales_receipts import now_iso

    await db.sales_receipts.update_many(
        {DELETE_CLAIM_FIELD: str(token)},
        {
            "$unset": {DELETE_CLAIM_FIELD: "", DELETE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_reverse_claim(db, receipt_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    from services.sales_receipts import now_iso

    claim_token = str(token or uuid.uuid4())
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "$nor": [
                {"financial_status": {"$in": ["cancelled", "reversed"]}},
                {"cancelled": True},
            ],
        },
        {"$set": {REVERSE_CLAIM_FIELD: claim_token, "updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER,
    )


@asynccontextmanager
async def _optional_receipt_lock(enabled: bool):
    from services.sales_receipts import _get_receipt_lock

    if not enabled:
        yield
        return
    async with _get_receipt_lock():
        yield


async def _load_receipt(db, receipt_id: str) -> dict:
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")
    return rec


async def assert_invoice_allows_receipt_journal(db, invoice_id: str) -> dict:
    """Recheck invoice immediately before writing a receipt journal."""
    from services.sales_invoices import FINANCIAL_FINALIZED, effective_financial_status

    if not invoice_id:
        raise HTTPException(400, "ID Faktur Penjualan (invoice_id) wajib diisi.")
    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        invoice = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")
    if effective_financial_status(invoice) != FINANCIAL_FINALIZED:
        raise HTTPException(409, INVOICE_NOT_FINALIZED_RECEIPT_POST_MESSAGE)
    if reverse_claim_is_active(invoice):
        raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_RECEIPT_MESSAGE)
    journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, INVOICE_NOT_POSTED_MESSAGE)
    return invoice


async def _require_invoice_posted(db, invoice_id: str) -> dict:
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Penjualan (invoice_id) wajib diisi.")
    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")
    journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, INVOICE_NOT_POSTED_MESSAGE)
    return invoice


async def _write_source_journal(db, receipt: dict, user: dict, cash_snap: dict) -> dict:
    amount = _require_receipt_amount(receipt)
    allocated = receipt_allocated_amount(receipt)
    credit = receipt_customer_credit_amount(receipt)
    if allocated + credit != amount:
        raise HTTPException(400, "Alokasi penerimaan dan saldo pelanggan tidak seimbang dengan jumlah diterima.")
    receivable = await resolve_invoice_receivable_account(db, receipt.get("invoice_id"))
    deposit_id = None
    if credit > 0:
        from services.sales_accounting_settings import require_customer_deposit_account

        deposit = await require_customer_deposit_account(db)
        deposit_id = str(deposit["id"])
    lines = _build_journal_lines(
        cash_ledger_account_id=str(cash_snap["cash_bank_account_id"]),
        receivable_account_id=str(receivable["id"]),
        amount=amount,
        receipt_number=str(receipt.get("receipt_number") or ""),
        allocated_amount=allocated,
        customer_credit_amount=credit,
        deposit_account_id=deposit_id,
    )
    try:
        journal = await post_balanced_journal(
            db,
            date=journal_date_from_receipt(receipt),
            source_type=SOURCE_TYPE,
            source_id=str(receipt["id"]),
            description=_journal_description(receipt),
            lines=lines,
            posted_by=user,
        )
        return journal
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_sales_receipt_source_journal(db, receipt["id"])
        if existing:
            return existing
        raise HTTPException(409, "Jurnal untuk penerimaan ini sudah ada.")


async def _public_receipt_detail(db, receipt_id: str) -> Dict[str, Any]:
    from services.sales_receipts import get_sales_receipt_detail

    return await get_sales_receipt_detail(db, receipt_id)


async def post_sales_receipt_accounting(
    db,
    receipt_id: str,
    user: dict,
    *,
    cash_bank_id: Optional[str] = None,
    serialize_in_process: bool = True,
) -> Dict[str, Any]:
    """Post one active receipt. Idempotent. Finalize metadata from the actual journal."""
    async with _optional_receipt_lock(serialize_in_process):
        rec = await _load_receipt(db, receipt_id)
        if receipt_is_cancelled(rec):
            raise HTTPException(409, CANCELLED_POST_MESSAGE)

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if existing:
            await _heal_posted_receipt_from_journal(db, rec, existing, user)
            return await _public_receipt_detail(db, rec["id"])

        _require_receipt_amount(rec)
        await _require_invoice_posted(db, rec.get("invoice_id"))
        await resolve_invoice_receivable_account(db, rec.get("invoice_id"))
        cash_snap = await resolve_cash_bank_for_posting(db, rec, cash_bank_id)

        claimed = await acquire_posting_claim(db, rec["id"])
        if claimed is None:
            current = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
            if current and receipt_is_cancelled(current):
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            existing = await find_sales_receipt_source_journal(db, rec["id"])
            if existing:
                await _heal_posted_receipt_from_journal(db, rec, existing, user)
                return await _public_receipt_detail(db, rec["id"])
            if current and delete_claim_is_active(current):
                raise HTTPException(409, DELETE_IN_PROGRESS_POST_MESSAGE)
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if existing:
            await _heal_posted_receipt_from_journal(db, rec, existing, user)
            return await _public_receipt_detail(db, rec["id"])

        try:
            await assert_invoice_allows_receipt_journal(db, claimed.get("invoice_id") or rec.get("invoice_id"))
            journal = await _write_source_journal(db, claimed, user, cash_snap)
        except Exception:
            still = await find_sales_receipt_source_journal(db, rec["id"])
            if still:
                await _heal_posted_receipt_from_journal(db, rec, still, user)
                return await _public_receipt_detail(db, rec["id"])
            await clear_posting_claim(db, rec["id"])
            raise

        await _heal_posted_receipt_from_journal(db, rec, journal, user)
        return await _public_receipt_detail(db, rec["id"])


async def reverse_sales_receipt_accounting(
    db, receipt_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse a posted receipt journal and exclude it from operational outstanding."""
    async with _optional_receipt_lock(serialize_in_process):
        rec = await _load_receipt(db, receipt_id)
        existing = await find_sales_receipt_source_journal(db, rec["id"])
        status = accounting_status_from_journal(existing)

        if status == ACCOUNTING_REVERSED:
            await _finalize_reversal_operational_state(db, rec, user)
            return await _public_receipt_detail(db, rec["id"])

        if receipt_is_cancelled(rec):
            if reverse_claim_is_active(rec):
                await clear_reverse_claim(db, rec["id"])
            return await _public_receipt_detail(db, rec["id"])

        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, "Hanya penerimaan yang sudah diposting yang dapat di-reverse.")

        from services.sales_customer_credit import (
            assert_source_receipt_reversible,
            hold_source_credit_claim,
            release_source_credit_claim,
            release_unfenced_credit_reservations,
        )

        await assert_source_receipt_reversible(db, rec["id"])

        claimed = await acquire_reverse_claim(db, rec["id"])
        if claimed is None:
            current = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
            still = await find_sales_receipt_source_journal(db, rec["id"])
            if current and (
                receipt_is_cancelled(current)
                or accounting_status_from_journal(still) == ACCOUNTING_REVERSED
            ):
                if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                    await _finalize_reversal_operational_state(db, rec, user)
                return await _public_receipt_detail(db, rec["id"])
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if accounting_status_from_journal(existing) == ACCOUNTING_REVERSED:
            await _finalize_reversal_operational_state(db, rec, user)
            return await _public_receipt_detail(db, rec["id"])
        if accounting_status_from_journal(existing) != ACCOUNTING_POSTED or not existing:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, "Hanya penerimaan yang sudah diposting yang dapat di-reverse.")

        credit_token = str(uuid.uuid4())
        credit_held = await hold_source_credit_claim(db, rec["id"], credit_token)
        if not credit_held:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, "Saldo pelanggan sedang dipakai. Ulangi permintaan.")
        try:
            try:
                await release_unfenced_credit_reservations(db, rec["id"], credit_token)
                await assert_source_receipt_reversible(db, rec["id"])
                await reverse_journal(
                    db,
                    existing["id"],
                    source_type=SOURCE_REVERSAL,
                    source_id=str(rec["id"]),
                    posted_by=user,
                    description=f"Reversal {_journal_description(rec)}",
                )
            except HTTPException:
                await clear_reverse_claim(db, rec["id"])
                raise
            except KeyError as exc:
                await clear_reverse_claim(db, rec["id"])
                raise HTTPException(404, "Jurnal penerimaan tidak ditemukan.") from exc
            except PeriodLockedError as exc:
                await clear_reverse_claim(db, rec["id"])
                raise HTTPException(409, str(exc)) from exc
            except JournalValidationError as exc:
                await clear_reverse_claim(db, rec["id"])
                raise HTTPException(400, str(exc)) from exc
            except JournalConflictError:
                still = await find_sales_receipt_source_journal(db, rec["id"])
                if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                    await clear_reverse_claim(db, rec["id"])
                    raise HTTPException(409, "Reversal jurnal untuk penerimaan ini sudah ada.")

            await _finalize_reversal_operational_state(db, rec, user)
            return await _public_receipt_detail(db, rec["id"])
        finally:
            await release_source_credit_claim(db, rec["id"], credit_token)
