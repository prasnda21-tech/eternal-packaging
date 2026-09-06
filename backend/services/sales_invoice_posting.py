"""Sales invoice → journal_core posting (Phase 1).

Explicit POST only. Does not auto-post on finalize, write receipts/HPP, or
mutate production workflow. Reuses journal_core.post_balanced_journal.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
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
)
from services.sales_accounting_settings import (
    _load_account,
    _validate_output_tax,
    _validate_receivable,
    _validate_revenue,
    get_sales_accounting_settings,
)

SOURCE_TYPE = "sales_invoice"
PERM_POST = "sales_accounting.post"
CLAIM_FIELD = "accounting_posting_claim"

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


def posting_claim_is_active(invoice: Optional[dict]) -> bool:
    raw = (invoice or {}).get(CLAIM_FIELD)
    return bool(str(raw or "").strip())


def cancel_unclaimed_filter(invoice_id: str) -> Dict[str, Any]:
    from services.sales_invoices import FINANCIAL_FINALIZED

    return {
        "id": invoice_id,
        "financial_status": FINANCIAL_FINALIZED,
        "$or": [
            {CLAIM_FIELD: {"$exists": False}},
            {CLAIM_FIELD: None},
            {CLAIM_FIELD: ""},
        ],
    }


async def clear_posting_claim(db, invoice_id: str) -> None:
    from services.sales_invoices import now_iso

    await db.sales_invoices.update_one(
        {"id": str(invoice_id)},
        {"$unset": {CLAIM_FIELD: ""}, "$set": {"updated_at": now_iso()}},
    )


async def acquire_posting_claim(db, invoice_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Atomically claim a finalized invoice. Adopts an existing stale claim."""
    from services.sales_invoices import FINANCIAL_FINALIZED, now_iso

    claim_token = str(token or uuid.uuid4())
    return await db.sales_invoices.find_one_and_update(
        {"id": str(invoice_id), "financial_status": FINANCIAL_FINALIZED},
        {"$set": {CLAIM_FIELD: claim_token, "updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER,
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
    from services.sales_invoices import FINANCIAL_CANCELLED, FINANCIAL_FINALIZED, now_iso

    await db.sales_invoices.update_one(
        {"id": invoice_id, "financial_status": FINANCIAL_CANCELLED},
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
            if posting_claim_is_active(inv):
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
            existing = await find_sales_invoice_source_journal(db, inv["id"])
            if existing:
                return await get_sales_invoice_detail(db, inv["id"])
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        existing = await find_sales_invoice_source_journal(db, inv["id"])
        if existing:
            await clear_posting_claim(db, inv["id"])
            return await get_sales_invoice_detail(db, inv["id"])

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
