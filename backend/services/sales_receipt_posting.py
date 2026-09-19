"""Sales receipt → journal_core posting (Phase 1A).

Explicit POST only. Does not auto-post on create, write invoice journals,
or mutate production workflow. Reuses journal_core.post_balanced_journal
and journal_core.reverse_journal.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.cash_bank_master import (
    CashBankValidationError,
    get_cash_bank_raw,
    require_bank_coa,
)
from services.chart_of_accounts import get_account
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
    _validate_receivable,
    get_sales_accounting_settings,
)
from services.sales_invoice_posting import (
    ACCOUNTING_NOT_POSTED,
    ACCOUNTING_POSTED,
    ACCOUNTING_REVERSED,
    accounting_status_from_journal,
    find_sales_invoice_source_journal,
)

SOURCE_TYPE = "sales_receipt"
SOURCE_REVERSAL = "sales_receipt_reversal"
PERM_POST = "sales.receipt.accounting.post"
PERM_REVERSE = "sales.receipt.accounting.reverse"
CLAIM_FIELD = "accounting_posting_claim"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"

INVOICE_NOT_POSTED_MESSAGE = "Faktur harus diposting ke accounting terlebih dahulu."
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


async def _require_receivable_account(db) -> dict:
    settings = await get_sales_accounting_settings(db)
    receivable_id = str(settings.get("receivable_account_id") or "").strip()
    if not receivable_id:
        raise HTTPException(400, "Pengaturan Akuntansi Penjualan belum dikonfigurasi.")
    return _validate_receivable(await _load_account(db, receivable_id))


def _require_receipt_amount(receipt: dict) -> Decimal:
    amount = _money(receipt.get("amount"), "amount")
    if amount <= 0:
        raise HTTPException(400, "Jumlah penerimaan harus lebih besar dari 0 untuk posting accounting.")
    return amount


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
) -> List[dict]:
    ref = receipt_number or "penerimaan"
    return [
        {
            "account_id": cash_ledger_account_id,
            "debit": amount,
            "credit": Decimal("0.00"),
            "description": f"Kas/Bank {ref}",
        },
        {
            "account_id": receivable_account_id,
            "debit": Decimal("0.00"),
            "credit": amount,
            "description": f"Piutang {ref}",
        },
    ]


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

    settings = await get_sales_accounting_settings(db)
    receivable_ready = bool(settings.get("configured") and settings.get("receivable_account_id"))

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
            and receivable_ready
            and amount_ok
            and bool(rec.get("invoice_id"))
            and not posting_claim_is_active(rec)
        )
        rec["can_reverse_accounting"] = (
            not cancelled
            and accounting_status == ACCOUNTING_POSTED
            and not reverse_claim_is_active(rec)
        )
        rec["can_delete"] = accounting_status == ACCOUNTING_NOT_POSTED and not cancelled and not journal
        rec.pop(CLAIM_FIELD, None)
        rec.pop(REVERSE_CLAIM_FIELD, None)
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
        },
        {"$set": {CLAIM_FIELD: claim_token, "updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER,
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


async def _write_source_journal(db, receipt: dict, user: dict, cash_snap: dict) -> None:
    amount = _require_receipt_amount(receipt)
    receivable = await _require_receivable_account(db)
    lines = _build_journal_lines(
        cash_ledger_account_id=str(cash_snap["cash_bank_account_id"]),
        receivable_account_id=str(receivable["id"]),
        amount=amount,
        receipt_number=str(receipt.get("receipt_number") or ""),
    )
    try:
        await post_balanced_journal(
            db,
            date=journal_date_from_receipt(receipt),
            source_type=SOURCE_TYPE,
            source_id=str(receipt["id"]),
            description=_journal_description(receipt),
            lines=lines,
            posted_by=user,
        )
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_sales_receipt_source_journal(db, receipt["id"])
        if existing:
            return
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
    """Post one active receipt. Idempotent. Safety is the Mongo claim, not the lock."""
    from services.sales_receipts import now_iso

    async with _optional_receipt_lock(serialize_in_process):
        rec = await _load_receipt(db, receipt_id)
        if receipt_is_cancelled(rec):
            raise HTTPException(409, CANCELLED_POST_MESSAGE)

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if existing:
            if posting_claim_is_active(rec):
                await clear_posting_claim(db, rec["id"])
            return await _public_receipt_detail(db, rec["id"])

        _require_receipt_amount(rec)
        await _require_invoice_posted(db, rec.get("invoice_id"))
        await _require_receivable_account(db)
        cash_snap = await resolve_cash_bank_for_posting(db, rec, cash_bank_id)

        claimed = await acquire_posting_claim(db, rec["id"])
        if claimed is None:
            current = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
            if current and receipt_is_cancelled(current):
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            existing = await find_sales_receipt_source_journal(db, rec["id"])
            if existing:
                return await _public_receipt_detail(db, rec["id"])
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if existing:
            await clear_posting_claim(db, rec["id"])
            return await _public_receipt_detail(db, rec["id"])

        try:
            await _write_source_journal(db, claimed, user, cash_snap)
        except Exception:
            still = await find_sales_receipt_source_journal(db, rec["id"])
            if not still:
                await clear_posting_claim(db, rec["id"])
            raise

        now = now_iso()
        await db.sales_receipts.update_one(
            {"id": rec["id"]},
            {
                "$set": {
                    **cash_snap,
                    "accounting_posted_at": now,
                    "accounting_posted_by": _actor_public(user),
                    "updated_at": now,
                },
                "$unset": {CLAIM_FIELD: ""},
            },
        )
        return await _public_receipt_detail(db, rec["id"])


async def reverse_sales_receipt_accounting(
    db, receipt_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse a posted receipt journal and exclude it from operational outstanding."""
    from services.sales_receipts import _recalc_invoice_payment_from_receipts, now_iso

    async with _optional_receipt_lock(serialize_in_process):
        rec = await _load_receipt(db, receipt_id)
        existing = await find_sales_receipt_source_journal(db, rec["id"])
        status = accounting_status_from_journal(existing)

        if status == ACCOUNTING_REVERSED or receipt_is_cancelled(rec):
            if receipt_is_cancelled(rec) or status == ACCOUNTING_REVERSED:
                if reverse_claim_is_active(rec):
                    await clear_reverse_claim(db, rec["id"])
                return await _public_receipt_detail(db, rec["id"])

        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, "Hanya penerimaan yang sudah diposting yang dapat di-reverse.")

        claimed = await acquire_reverse_claim(db, rec["id"])
        if claimed is None:
            current = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
            if current and (
                receipt_is_cancelled(current)
                or accounting_status_from_journal(
                    await find_sales_receipt_source_journal(db, rec["id"])
                )
                == ACCOUNTING_REVERSED
            ):
                return await _public_receipt_detail(db, rec["id"])
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_receipt_source_journal(db, rec["id"])
        if accounting_status_from_journal(existing) == ACCOUNTING_REVERSED:
            await clear_reverse_claim(db, rec["id"])
            return await _public_receipt_detail(db, rec["id"])
        if accounting_status_from_journal(existing) != ACCOUNTING_POSTED or not existing:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(409, "Hanya penerimaan yang sudah diposting yang dapat di-reverse.")

        try:
            await reverse_journal(
                db,
                existing["id"],
                source_type=SOURCE_REVERSAL,
                source_id=str(rec["id"]),
                posted_by=user,
                description=f"Reversal {_journal_description(rec)}",
            )
        except KeyError as exc:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(404, "Jurnal penerimaan tidak ditemukan.") from exc
        except JournalValidationError as exc:
            await clear_reverse_claim(db, rec["id"])
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            still = await find_sales_receipt_source_journal(db, rec["id"])
            if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                await clear_reverse_claim(db, rec["id"])
                raise HTTPException(409, "Reversal jurnal untuk penerimaan ini sudah ada.")

        now = now_iso()
        await db.sales_receipts.update_one(
            {"id": rec["id"]},
            {
                "$set": {
                    "financial_status": "cancelled",
                    "cancelled": True,
                    "cancelled_at": now,
                    "cancelled_by": _actor_public(user),
                    "accounting_reversed_at": now,
                    "accounting_reversed_by": _actor_public(user),
                    "updated_at": now,
                },
                "$unset": {REVERSE_CLAIM_FIELD: ""},
            },
        )
        await _recalc_invoice_payment_from_receipts(db, rec.get("invoice_id"))
        return await _public_receipt_detail(db, rec["id"])
