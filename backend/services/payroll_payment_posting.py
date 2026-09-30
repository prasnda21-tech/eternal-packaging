"""Post a payroll payment to the bank.

Explicit user action after the payment is APPROVED.
Debit Utang Gaji, credit the BANK coa. Does not create a cash-bank voucher.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from services.accounting_periods import PeriodLockedError
from services.hr_payroll import money_float, to_money
from services.journal_core import (
    STATUS_POSTED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
)
from services.payroll_payment import (
    SOURCE_TYPE,
    STATUS_APPROVED,
    STATUS_PAID,
    NOT_APPROVED_MESSAGE,
    get_payment,
    mark_payment_paid,
    resolve_cash_bank,
)
from services.purchase_common import get_named_lock

ALREADY_MESSAGE = "Jurnal pembayaran payroll untuk dokumen ini sudah ada."
POSTED_MESSAGE = "Pembayaran payroll diposting ke accounting."


def _public_result(journal: dict, payment: dict, *, already: bool) -> Dict[str, Any]:
    status = str(journal.get("status") or "")
    return {
        "journal_id": journal.get("id"),
        "journal_number": journal.get("number"),
        "status": "POSTED" if status == STATUS_POSTED else status.upper(),
        "payment_id": payment.get("id"),
        "payment_status": STATUS_PAID,
        "message": ALREADY_MESSAGE if already else POSTED_MESSAGE,
    }


def _payment_lines(payment: dict, bank_coa_id: str) -> list:
    amount = to_money(payment.get("total_amount"))
    number = str(payment.get("payment_number") or payment.get("id") or "")
    payable_id = str(payment.get("payable_account_id") or "").strip()
    if not payable_id or amount <= 0:
        raise HTTPException(409, "Nominal atau akun Utang Gaji pembayaran tidak valid.")
    return [
        {
            "account_id": payable_id,
            "debit": money_float(amount),
            "credit": 0,
            "description": f"Utang Gaji {number}",
        },
        {
            "account_id": bank_coa_id,
            "debit": 0,
            "credit": money_float(amount),
            "description": f"Bank {number}",
        },
    ]


async def post_payroll_payment(db, payment_id: str, user: dict) -> Dict[str, Any]:
    """Post one payment journal. A second call returns the existing journal."""
    async with get_named_lock(f"payroll_payment_post:{payment_id}"):
        payment = await get_payment(db, payment_id)
        source_id = str(payment["id"])
        existing = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
        if existing and existing.get("status") == STATUS_POSTED:
            stamped = await mark_payment_paid(db, payment, existing, user)
            return _public_result(existing, stamped, already=True)

        if payment.get("status") != STATUS_APPROVED:
            raise HTTPException(409, NOT_APPROVED_MESSAGE)

        bank = await resolve_cash_bank(db, payment.get("cash_bank_account_id"))
        lines = _payment_lines(payment, bank["bank_coa_id"])
        try:
            journal = await post_balanced_journal(
                db,
                date=str(payment.get("payment_date") or ""),
                source_type=SOURCE_TYPE,
                source_id=source_id,
                description=f"Pembayaran Payroll {payment.get('payment_number') or source_id}",
                lines=lines,
                posted_by=user,
            )
        except PeriodLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalConflictError:
            existing = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
            if existing and existing.get("status") == STATUS_POSTED:
                stamped = await mark_payment_paid(db, payment, existing, user)
                return _public_result(existing, stamped, already=True)
            raise HTTPException(409, ALREADY_MESSAGE)

        if str(journal.get("source_type") or "") != SOURCE_TYPE or str(journal.get("source_id") or "") != source_id:
            raise HTTPException(409, "Jurnal pembayaran tidak terikat ke dokumen yang diminta.")
        stamped = await mark_payment_paid(db, payment, journal, user)
        return _public_result(journal, stamped, already=False)
