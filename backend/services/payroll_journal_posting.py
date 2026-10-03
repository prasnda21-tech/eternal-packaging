"""Payroll accrual journal posting.

APPROVAL → FINAL posts one accrual journal from the caller in hr_payroll.
Manual POST remains for a legacy FINAL period that has no journal.
Does not create payroll payments, cash-bank vouchers, or reversals.

One primary journal per payroll period:
  source_type = payroll_period
  source_id   = hr_payroll_periods.id
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.accounting_periods import (
    PeriodLockedError,
    PeriodValidationError,
    STATUS_CLOSED,
    get_period_raw,
    period_key_from_date,
)
from services.hr_payroll import (
    PERIODS,
    STATUS_APPROVAL,
    STATUS_FINAL,
    WORKFLOW_REOPEN,
    get_period,
    money_float,
    to_money,
)
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    JournalConflictError,
    JournalValidationError,
    ensure_indexes as ensure_journal_indexes,
    find_primary_journal_for_source,
    get_journal,
    post_balanced_journal,
)
from services.payroll_accounting_readiness import (
    REASON_DIFFERENCE,
    REASON_INVALID,
    REASON_NOT_FINAL,
    REASON_PAYABLE_MISSING,
    REASON_UNMAPPED,
    evaluate_payroll_accounting_readiness,
    explain_readiness_block,
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
PAYABLE_LINE_CODE = "PAYROLL_PAYABLE"
PAYABLE_MARKER = "Utang Gaji"
REOPEN_BUSY_MESSAGE = "Periode payroll sedang dibuka ulang."
LOCKED_PERIOD_MESSAGE = "Periode akuntansi {key} sudah ditutup."

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


def _reject_if_not_postable(period: dict, readiness: dict, *, for_finalization: bool = False) -> None:
    status = str(period.get("status") or "")
    if for_finalization:
        if status != STATUS_APPROVAL:
            raise HTTPException(409, "Finalisasi akrual hanya berlaku saat periode berstatus APPROVAL.")
    elif status != STATUS_FINAL:
        raise HTTPException(409, NOT_FINAL_MESSAGE)
    reasons = list(readiness.get("reasons") or [])
    if not readiness.get("accounting_ready"):
        if for_finalization:
            raise HTTPException(409, explain_readiness_block(readiness))
        if REASON_NOT_FINAL in reasons or status != STATUS_FINAL:
            raise HTTPException(409, NOT_FINAL_MESSAGE)
        if REASON_DIFFERENCE in reasons:
            raise HTTPException(409, UNBALANCED_MESSAGE)
        if any(reason in MAPPING_REASONS for reason in reasons):
            raise HTTPException(409, MAPPING_MESSAGE)
        raise HTTPException(409, NOT_READY_MESSAGE)
    _journal_date(period)


def payable_snapshot_from_planned(planned: List[dict]) -> Optional[dict]:
    """Identity of the Utang Gaji line. Does not sum other credits on the same account."""
    matches = []
    for row in planned or []:
        if not isinstance(row, dict):
            continue
        keys = list(row.get("source_keys") or [])
        if row.get("code") == PAYABLE_LINE_CODE or "payroll_payable" in keys:
            matches.append(row)
    if len(matches) != 1:
        return None
    row = matches[0]
    account_id = str(row.get("account_id") or "").strip()
    amount = to_money(row.get("credit"))
    if not account_id or amount <= 0:
        return None
    return {
        "account_id": account_id,
        "amount": money_float(amount),
        "line_code": PAYABLE_LINE_CODE,
    }


def _snapshot_ok(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    return bool(str(raw.get("account_id") or "").strip()) and to_money(raw.get("amount")) > 0


async def _assert_accounting_period_open(db, end_date: str) -> None:
    """Read-only. A missing accounting period row is open. Does not take a writer claim."""
    try:
        key = period_key_from_date(str(end_date or "").strip()[:10])
    except PeriodValidationError as exc:
        raise HTTPException(400, DATE_MESSAGE) from exc
    row = await get_period_raw(db, key)
    if row and str(row.get("status") or "") == STATUS_CLOSED:
        raise HTTPException(409, LOCKED_PERIOD_MESSAGE.format(key=key))


async def _store_payable_snapshot_if_absent(db, journal_id: str, planned: List[dict]) -> None:
    snap = payable_snapshot_from_planned(planned)
    if not snap or not journal_id:
        return
    await db[JOURNAL_ENTRIES].update_one(
        {"id": str(journal_id), "payroll_payable": {"$exists": False}},
        {"$set": {"payroll_payable": snap}},
    )


async def fill_missing_payable_snapshot(db, period_id: str) -> None:
    """Recovery only while automatic finalization is still APPROVAL.

    Copies the journal's own unique Utang Gaji line. A FINAL or historical
    journal is left unchanged. Manual posting and payment do not call this.
    """
    from services.hr_payroll import PERIODS, STATUS_APPROVAL
    period = await db[PERIODS].find_one({"id": str(period_id)}, {"_id": 0, "status": 1})
    if not period or str(period.get("status") or "") != STATUS_APPROVAL:
        return
    doc = await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": SOURCE_TYPE,
            "source_id": str(period_id),
            "reversal_of": None,
            "status": STATUS_POSTED,
        },
        {"_id": 0, "id": 1, "payroll_payable": 1},
    )
    if not doc or _snapshot_ok(doc.get("payroll_payable")):
        return
    lines = await db[JOURNAL_LINES].find({"journal_id": doc["id"]}, {"_id": 0}).to_list(100)
    matches = []
    for line in lines:
        credit = to_money(line.get("credit"))
        debit = to_money(line.get("debit"))
        description = str(line.get("description") or "")
        account_id = str(line.get("account_id") or "").strip()
        if credit > 0 and debit == 0 and account_id and description.endswith(PAYABLE_MARKER):
            matches.append((account_id, credit))
    if len(matches) != 1:
        return
    account_id, amount = matches[0]
    await db[JOURNAL_ENTRIES].update_one(
        {"id": doc["id"], "payroll_payable": {"$exists": False}},
        {"$set": {"payroll_payable": {
            "account_id": account_id,
            "amount": money_float(amount),
            "line_code": PAYABLE_LINE_CODE,
        }}},
    )


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
        "planned_lines": readiness.get("planned_lines") or [],
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


def _keep_existing(field: str, incoming: Any) -> Dict[str, Any]:
    """Aggregation expression: write incoming only while the stored field is blank."""
    return {"$cond": [
        {"$in": [{"$ifNull": [f"${field}", None]}, [None, ""]]},
        incoming,
        f"${field}",
    ]}


async def _stamp_period_metadata(db, period_id: str, journal: dict, user: dict) -> None:
    """Fill blank journal pointers. A value already stored is not replaced."""
    journal_id = str(journal.get("id") or "")
    if not journal_id:
        return
    if await db[PERIODS].find_one({"id": str(period_id)}, {"_id": 1}) is None:
        return
    await db[PERIODS].update_one(
        {"id": str(period_id)},
        [{"$set": {
            "accounting_journal_id": _keep_existing("accounting_journal_id", journal_id),
            "accounting_posted_at": _keep_existing("accounting_posted_at", journal.get("posted_at") or now_iso()),
            "accounting_posted_by": _keep_existing("accounting_posted_by", actor_snapshot(user)),
        }}],
    )


async def read_period_journal_reference(db, period_id: str) -> Dict[str, Any]:
    """Read the posted journal number from accounting_journal_id. Does not post."""
    period = await get_period(db, period_id)
    journal_id = str(period.get("accounting_journal_id") or "").strip()
    if not journal_id:
        return {"journal_id": None, "journal_number": None}
    journal = await get_journal(db, journal_id, with_lines=False)
    same_source = (
        journal
        and str(journal.get("source_type") or "") == SOURCE_TYPE
        and str(journal.get("source_id") or "") == str(period["id"])
    )
    if not same_source:
        return {"journal_id": journal_id, "journal_number": None}
    return {"journal_id": str(journal.get("id") or journal_id), "journal_number": journal.get("number")}


async def _insert_accrual(db, source_id: str, preview: dict, user: dict) -> tuple:
    before = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
    if before and before.get("status") == STATUS_POSTED:
        return before, False
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
            return existing, False
        raise HTTPException(409, ALREADY_MESSAGE)
    if str(journal.get("source_type") or "") != SOURCE_TYPE or str(journal.get("source_id") or "") != source_id:
        raise HTTPException(409, "Jurnal payroll tidak terikat ke periode yang diminta.")
    created = not (before and before.get("status") == STATUS_POSTED)
    if before and str(before.get("id") or "") == str(journal.get("id") or ""):
        created = False
    return journal, created


async def _finalization_still_owns(db, period_id: str, workflow_token: str) -> bool:
    if not workflow_token:
        return True
    from services.hr_payroll import STATUS_APPROVAL, _touch_workflow_claim
    return await _touch_workflow_claim(db, period_id, workflow_token, STATUS_APPROVAL)


async def post_payroll_journal(
    db,
    period_id: str,
    user: dict,
    *,
    for_finalization: bool = False,
    workflow_token: str = "",
) -> Dict[str, Any]:
    """Post one accrual journal. Idempotent: a second call returns the same journal.

    for_finalization is only for the APPROVAL finalization caller, which already
    holds the finalize claim. A stolen token returns the existing journal without
    writing. Manual posting still requires FINAL and does not rewrite
    payroll_payable on a journal that already exists.
    """
    await ensure_journal_indexes(db)
    period = await get_period(db, period_id)
    source_id = str(period["id"])
    existing = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
    if existing and existing.get("status") == STATUS_POSTED:
        if for_finalization and workflow_token and not await _finalization_still_owns(db, source_id, workflow_token):
            return _public_result(existing, already=True)
        await _stamp_period_metadata(db, source_id, existing, user)
        if for_finalization:
            await fill_missing_payable_snapshot(db, source_id)
        return _public_result(existing, already=True)

    if str(period.get("workflow_claim") or "") and str(period.get("workflow_op") or "") == WORKFLOW_REOPEN:
        raise HTTPException(409, REOPEN_BUSY_MESSAGE)

    if for_finalization:
        if workflow_token and not await _finalization_still_owns(db, source_id, workflow_token):
            raise HTTPException(409, "Finalisasi payroll belum selesai.")
        readiness = await evaluate_payroll_accounting_readiness(db, source_id, for_finalization=True)
        _reject_if_not_postable(period, readiness, for_finalization=True)
        lines = lines_from_planned(readiness.get("planned_lines") or [], period)
        assert_balanced_journal_lines(lines)
        preview = {
            "lines": lines,
            "planned_lines": readiness.get("planned_lines") or [],
            "description": _description(period),
            "date": _journal_date(period),
        }
        await _assert_accounting_period_open(db, period.get("end_date"))
        journal, created = await _insert_accrual(db, source_id, preview, user)
        await _stamp_period_metadata(db, source_id, journal, user)
        if created:
            await _store_payable_snapshot_if_absent(db, str(journal.get("id") or ""), preview["planned_lines"])
        else:
            await fill_missing_payable_snapshot(db, source_id)
        return _public_result(journal, already=not created)

    preview = await preview_payroll_journal(db, source_id)
    from services.hr_payroll import WORKFLOW_POST, claim_workflow, release_workflow_claim
    claimed = await claim_workflow(db, source_id, WORKFLOW_POST, STATUS_FINAL)
    token = str(claimed.get("workflow_claim") or "")
    try:
        await _assert_accounting_period_open(db, period.get("end_date"))
        journal, created = await _insert_accrual(db, source_id, preview, user)
    except HTTPException:
        await release_workflow_claim(db, source_id, token)
        raise
    await _stamp_period_metadata(db, source_id, journal, user)
    if created:
        await _store_payable_snapshot_if_absent(db, str(journal.get("id") or ""), preview.get("planned_lines") or [])
    await release_workflow_claim(db, source_id, token)
    return _public_result(journal, already=not created)
