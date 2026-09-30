"""Payroll accrual journal posting — Phase 1.

Explicit POST only. Finalizing a payroll period does not create a journal.
Does not create payroll payments, cash-bank vouchers, or reversals.

One primary journal per payroll period:
  source_type = payroll_period
  source_id   = hr_payroll_periods.id
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from fastapi import HTTPException

from services.accounting_periods import PeriodLockedError
from services.hr_payroll import PERIODS, STATUS_FINAL, get_period, money_float, to_money
from services.journal_core import (
    STATUS_POSTED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
)
from services.payroll_accounting_readiness import (
    REASON_DIFFERENCE,
    REASON_INVALID,
    REASON_NOT_FINAL,
    REASON_PAYABLE_MISSING,
    REASON_UNMAPPED,
    evaluate_payroll_accounting_readiness,
)
from services.purchase_common import actor_snapshot, now_iso

SOURCE_TYPE = "payroll_period"
PERM_POST = "payroll_accounting.post"

NOT_FINAL_MESSAGE = "Periode payroll harus berstatus FINAL sebelum diposting ke accounting."
NOT_READY_MESSAGE = "Payroll belum siap diposting ke accounting."
MAPPING_MESSAGE = "Mapping COA payroll belum lengkap."
UNBALANCED_MESSAGE = "Jurnal payroll tidak seimbang: debit tidak sama dengan kredit."
ZERO_TOTAL_MESSAGE = "Total jurnal payroll tidak boleh nol."
MIN_LINES_MESSAGE = "Jurnal payroll minimal 2 baris."
DATE_MESSAGE = "Tanggal akhir periode payroll tidak valid."
ALREADY_MESSAGE = "Jurnal payroll untuk periode ini sudah ada."
POSTED_MESSAGE = "Jurnal akrual payroll diposting."

MAPPING_REASONS = frozenset({
    REASON_PAYABLE_MISSING,
    REASON_UNMAPPED,
    REASON_INVALID,
    "unsupported_adjustment",
    "ambiguous_source",
})


def _description(period: dict) -> str:
    label = str(period.get("period_number") or "").strip()
    if not label:
        year = period.get("year")
        month = period.get("month")
        if year and month:
            label = f"{int(month):02d}/{int(year)}"
        else:
            label = str(period.get("id") or "")
    return f"Payroll {label}"


def _journal_date(period: dict) -> str:
    raw = str(period.get("end_date") or "").strip()
    if len(raw) < 10 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:7].isdigit():
        raise HTTPException(400, DATE_MESSAGE)
    return raw[:10]


def lines_from_planned(planned: List[dict], period: dict) -> List[dict]:
    """Turn readiness planned lines into journal lines. Skips zero amounts."""
    header = _description(period)
    lines: List[dict] = []
    for row in planned or []:
        if not isinstance(row, dict):
            continue
        debit = to_money(row.get("debit"))
        credit = to_money(row.get("credit"))
        if debit == 0 and credit == 0:
            continue
        if debit > 0 and credit > 0:
            raise HTTPException(409, UNBALANCED_MESSAGE)
        account_id = str(row.get("account_id") or "").strip()
        if not account_id:
            raise HTTPException(409, MAPPING_MESSAGE)
        name = str(row.get("name") or row.get("code") or "").strip()
        lines.append({
            "account_id": account_id,
            "debit": money_float(debit),
            "credit": money_float(credit),
            "description": f"{header} — {name}" if name else header,
        })
    return lines


def assert_balanced_journal_lines(lines: List[dict]) -> None:
    if not lines or len(lines) < 2:
        raise HTTPException(409, MIN_LINES_MESSAGE if lines else ZERO_TOTAL_MESSAGE)
    total_dr = sum((to_money(row.get("debit")) for row in lines), Decimal("0.00"))
    total_cr = sum((to_money(row.get("credit")) for row in lines), Decimal("0.00"))
    if total_dr != total_cr:
        raise HTTPException(409, UNBALANCED_MESSAGE)
    if total_dr <= 0:
        raise HTTPException(409, ZERO_TOTAL_MESSAGE)


def _reject_if_not_postable(period: dict, readiness: dict) -> None:
    if str(period.get("status") or "") != STATUS_FINAL:
        raise HTTPException(409, NOT_FINAL_MESSAGE)
    reasons = list(readiness.get("reasons") or [])
    if not readiness.get("accounting_ready"):
        if REASON_NOT_FINAL in reasons or str(readiness.get("status") or "") != STATUS_FINAL:
            raise HTTPException(409, NOT_FINAL_MESSAGE)
        if REASON_DIFFERENCE in reasons:
            raise HTTPException(409, UNBALANCED_MESSAGE)
        if any(reason in MAPPING_REASONS for reason in reasons):
            raise HTTPException(409, MAPPING_MESSAGE)
        raise HTTPException(409, NOT_READY_MESSAGE)
    _journal_date(period)


async def preview_payroll_journal(db, period_id: str) -> Dict[str, Any]:
    """Read-only journal preview. Does not insert journals."""
    period = await get_period(db, period_id)
    readiness = await evaluate_payroll_accounting_readiness(db, period_id)
    _reject_if_not_postable(period, readiness)
    lines = lines_from_planned(readiness.get("planned_lines") or [], period)
    assert_balanced_journal_lines(lines)
    return {
        "journal_ready": True,
        "lines": lines,
        "period": period,
        "description": _description(period),
        "date": _journal_date(period),
    }


def _public_result(journal: dict, *, already: bool) -> Dict[str, Any]:
    status = str(journal.get("status") or "")
    return {
        "journal_id": journal.get("id"),
        "journal_number": journal.get("number"),
        "status": "POSTED" if status == STATUS_POSTED else status.upper(),
        "message": ALREADY_MESSAGE if already else POSTED_MESSAGE,
    }


async def _stamp_period_metadata(db, period_id: str, journal: dict, user: dict) -> None:
    """Store journal pointers only. Never changes payroll status."""
    current = await db[PERIODS].find_one(
        {"id": str(period_id)},
        {
            "_id": 0,
            "accounting_journal_id": 1,
            "accounting_posted_at": 1,
            "accounting_posted_by": 1,
        },
    )
    if current is None:
        return
    patch: Dict[str, Any] = {}
    journal_id = str(journal.get("id") or "")
    if journal_id and current.get("accounting_journal_id") != journal_id:
        patch["accounting_journal_id"] = journal_id
    if not current.get("accounting_posted_at"):
        patch["accounting_posted_at"] = journal.get("posted_at") or now_iso()
    if not current.get("accounting_posted_by"):
        patch["accounting_posted_by"] = actor_snapshot(user)
    if not patch:
        return
    await db[PERIODS].update_one({"id": str(period_id)}, {"$set": patch})


async def post_payroll_journal(db, period_id: str, user: dict) -> Dict[str, Any]:
    """Post one accrual journal. Idempotent: a second call returns the same journal."""
    period = await get_period(db, period_id)
    source_id = str(period["id"])
    existing = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
    if existing and existing.get("status") == STATUS_POSTED:
        await _stamp_period_metadata(db, source_id, existing, user)
        return _public_result(existing, already=True)

    preview = await preview_payroll_journal(db, source_id)
    try:
        journal = await post_balanced_journal(
            db,
            date=preview["date"],
            source_type=SOURCE_TYPE,
            source_id=source_id,
            description=preview["description"],
            lines=preview["lines"],
            posted_by=user,
        )
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        message = str(exc)
        if "seimbang" in message.lower():
            raise HTTPException(409, UNBALANCED_MESSAGE) from exc
        if "lebih besar dari 0" in message or "minimal 2" in message.lower():
            raise HTTPException(409, ZERO_TOTAL_MESSAGE) from exc
        raise HTTPException(409, message) from exc
    except JournalConflictError:
        existing = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
        if existing and existing.get("status") == STATUS_POSTED:
            await _stamp_period_metadata(db, source_id, existing, user)
            return _public_result(existing, already=True)
        raise HTTPException(409, ALREADY_MESSAGE)

    if str(journal.get("source_type") or "") != SOURCE_TYPE or str(journal.get("source_id") or "") != source_id:
        raise HTTPException(409, "Jurnal payroll tidak terikat ke periode yang diminta.")
    await _stamp_period_metadata(db, source_id, journal, user)
    already = str(journal.get("id") or "") != "" and existing is not None
    return _public_result(journal, already=already)
