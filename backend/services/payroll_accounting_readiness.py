"""Payroll accounting readiness preview — Phase 0.

ZERO journals. GET is zero-write. Uses frozen hr_payroll_lines for FINAL
periods (final_snapshot does not store line amounts).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.hr_payroll import (
    LINES,
    PERIODS,
    STATUS_FINAL,
    get_period,
    money_float,
    to_money,
)
from services.payroll_accounting_settings import (
    ALL_TREATMENTS,
    DEDUCTION_TREATMENTS,
    EARNING_TREATMENTS,
    SOURCE_ADJUSTMENT,
    TREATMENT_ASSET_CREDIT,
    TREATMENT_EXPENSE_CREDIT,
    TREATMENT_EXPENSE_DEBIT,
    TREATMENT_LIABILITY_CREDIT,
    _account_display,
    resolve_mapping,
    resolve_payable,
    source_key_for_row,
)

REASON_NOT_FINAL = "payroll_not_final"
REASON_SUPERSEDED = "superseded_revision"
REASON_PAYABLE_MISSING = "payable_account_missing"
REASON_UNMAPPED = "source_mapping_missing"
REASON_INVALID = "invalid_mapping"
REASON_UNSUPPORTED_ADJUSTMENT = "unsupported_adjustment"
REASON_UNSUPPORTED_NEGATIVE = "unsupported_negative"
REASON_AMBIGUOUS = "ambiguous_source"
REASON_DIFFERENCE = "debit_credit_difference"

ZERO = Decimal("0.00")


def _row_label(row: dict) -> str:
    return str(row.get("name") or row.get("code") or row.get("source") or "")


def _treatments_for_bucket(bucket: str) -> frozenset:
    return EARNING_TREATMENTS if bucket == "earnings" else DEDUCTION_TREATMENTS


async def latest_revision_for_group(db, group_id: str) -> Optional[dict]:
    if not group_id:
        return None
    return await db[PERIODS].find_one(
        {"group_id": group_id},
        {"_id": 0, "id": 1, "revision": 1, "status": 1},
        sort=[("revision", -1)],
    )


async def evaluate_payroll_accounting_readiness(db, period_id: str) -> Dict[str, Any]:
    period = await get_period(db, period_id)
    latest = await latest_revision_for_group(db, str(period.get("group_id") or ""))
    revision = int(period.get("revision") or 1)
    latest_revision = int((latest or {}).get("revision") or revision)
    is_latest = str((latest or {}).get("id") or period["id"]) == str(period["id"])

    lines = await db[LINES].find({"period_id": period["id"]}, {"_id": 0}).to_list(5000)
    aggregated: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    unmapped: List[dict] = []

    gross = sum((to_money(row.get("gross")) for row in lines), ZERO)
    deductions_total = sum((to_money(row.get("total_deductions")) for row in lines), ZERO)
    net = sum((to_money(row.get("net")) for row in lines), ZERO)

    for line in lines:
        for bucket, rows in (("earnings", line.get("earnings") or []), ("deductions", line.get("deductions") or [])):
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                amount = to_money(item.get("amount"))
                key = source_key_for_row(item)
                source = str(item.get("source") or "")
                if key is None:
                    if amount == ZERO:
                        continue
                    reasons.append(REASON_AMBIGUOUS)
                    unmapped.append({
                        "source_key": None,
                        "label": _row_label(item),
                        "source_kind": source or "unknown",
                        "amount": money_float(amount),
                        "reason": REASON_AMBIGUOUS,
                    })
                    continue
                slot = aggregated.setdefault(key, {
                    "source_key": key,
                    "label": _row_label(item),
                    "source_kind": source,
                    "code": str(item.get("code") or ""),
                    "bucket": bucket,
                    "amount": ZERO,
                    "row": item,
                })
                slot["amount"] += amount
                if not slot.get("label"):
                    slot["label"] = _row_label(item)

    planned: List[dict] = []
    total_debit = ZERO
    total_credit = ZERO

    status = str(period.get("status") or "")
    if status != STATUS_FINAL:
        reasons.append(REASON_NOT_FINAL)
    if not is_latest:
        reasons.append(REASON_SUPERSEDED)

    payable = await resolve_payable(db)
    if not payable or not payable.get("valid"):
        reasons.append(REASON_PAYABLE_MISSING)

    for key, slot in sorted(aggregated.items(), key=lambda kv: kv[0]):
        amount = slot["amount"]
        if amount == ZERO:
            continue
        if amount < ZERO:
            reasons.append(REASON_UNSUPPORTED_NEGATIVE)
            unmapped.append({
                "source_key": key,
                "label": slot["label"],
                "source_kind": slot["source_kind"],
                "amount": money_float(amount),
                "reason": REASON_UNSUPPORTED_NEGATIVE,
            })
            continue

        if slot["source_kind"] == SOURCE_ADJUSTMENT:
            row = slot["row"]
            treatment = str(row.get("accounting_treatment") or "")
            account_id = str(row.get("accounting_account_id") or "").strip()
            if treatment not in ALL_TREATMENTS or not account_id:
                reasons.append(REASON_UNSUPPORTED_ADJUSTMENT)
                unmapped.append({
                    "source_key": key,
                    "label": slot["label"],
                    "source_kind": SOURCE_ADJUSTMENT,
                    "amount": money_float(amount),
                    "reason": REASON_UNSUPPORTED_ADJUSTMENT,
                })
                continue
            from services.payroll_accounting_settings import load_account, validate_treatment_account
            account = await load_account(db, account_id)
            try:
                if treatment not in _treatments_for_bucket(slot["bucket"]):
                    raise HTTPException(400, "treatment mismatch")
                validate_treatment_account(account, treatment, label=key)
            except HTTPException:
                reasons.append(REASON_INVALID)
                unmapped.append({
                    "source_key": key,
                    "label": slot["label"],
                    "source_kind": SOURCE_ADJUSTMENT,
                    "amount": money_float(amount),
                    "reason": REASON_INVALID,
                })
                continue
            debit, credit = _split_amount(treatment, amount)
            total_debit += debit
            total_credit += credit
            planned.append(_planned_line(treatment, account, slot, debit, credit))
            continue

        mapping = await resolve_mapping(db, key)
        if not mapping:
            reasons.append(REASON_UNMAPPED)
            unmapped.append({
                "source_key": key,
                "label": slot["label"],
                "source_kind": slot["source_kind"],
                "amount": money_float(amount),
                "reason": REASON_UNMAPPED,
            })
            continue
        if not mapping.get("valid"):
            reasons.append(REASON_INVALID)
            unmapped.append({
                "source_key": key,
                "label": slot["label"],
                "source_kind": slot["source_kind"],
                "amount": money_float(amount),
                "reason": REASON_INVALID,
            })
            continue
        treatment = mapping["treatment"]
        if treatment not in _treatments_for_bucket(slot["bucket"]):
            reasons.append(REASON_INVALID)
            unmapped.append({
                "source_key": key,
                "label": slot["label"],
                "source_kind": slot["source_kind"],
                "amount": money_float(amount),
                "reason": REASON_INVALID,
            })
            continue
        debit, credit = _split_amount(treatment, amount)
        total_debit += debit
        total_credit += credit
        planned.append(_planned_line(treatment, mapping["account"], slot, debit, credit))

    if net < ZERO:
        reasons.append(REASON_UNSUPPORTED_NEGATIVE)
    elif payable and payable.get("valid") and net > ZERO:
        debit, credit = _split_amount(TREATMENT_LIABILITY_CREDIT, net)
        total_debit += debit
        total_credit += credit
        planned.append({
            "treatment": TREATMENT_LIABILITY_CREDIT,
            "account_id": payable["account_id"],
            "account": _account_display(payable.get("account")),
            "code": "PAYROLL_PAYABLE",
            "name": "Utang Gaji",
            "debit": money_float(debit),
            "credit": money_float(credit),
            "source_keys": ["payroll_payable"],
        })

    difference = total_debit - total_credit
    if difference != ZERO:
        reasons.append(REASON_DIFFERENCE)

    # Unique stable reason list, keep first-seen order.
    seen = set()
    unique_reasons = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique_reasons.append(reason)

    accounting_ready = (
        status == STATUS_FINAL
        and is_latest
        and not unmapped
        and payable is not None
        and bool(payable.get("valid"))
        and difference == ZERO
        and net >= ZERO
        and REASON_UNSUPPORTED_NEGATIVE not in unique_reasons
        and REASON_AMBIGUOUS not in unique_reasons
    )

    return {
        "period_id": period["id"],
        "status": status,
        "revision": revision,
        "is_latest_revision": is_latest,
        "latest_revision": latest_revision,
        "latest_period_id": (latest or {}).get("id") or period["id"],
        "accounting_ready": accounting_ready,
        "reasons": unique_reasons,
        "financial_source": "hr_payroll_lines",
        "final_snapshot_present": bool(period.get("final_snapshot")),
        "payroll_payable_account": _account_display((payable or {}).get("account")) if payable and payable.get("valid") else None,
        "unmapped_sources": unmapped,
        "planned_lines": planned,
        "summary": {
            "gross": money_float(gross),
            "deductions": money_float(deductions_total),
            "net": money_float(net),
            "total_debit": money_float(total_debit),
            "total_credit": money_float(total_credit),
            "difference": money_float(difference),
        },
        "preview_only": True,
        "creates_journal": False,
    }


def _split_amount(treatment: str, amount: Decimal) -> tuple:
    if treatment == TREATMENT_EXPENSE_DEBIT:
        return amount, ZERO
    if treatment in (TREATMENT_EXPENSE_CREDIT, TREATMENT_LIABILITY_CREDIT, TREATMENT_ASSET_CREDIT):
        return ZERO, amount
    return ZERO, ZERO


def _planned_line(treatment: str, account: Optional[dict], slot: dict, debit: Decimal, credit: Decimal) -> dict:
    return {
        "treatment": treatment,
        "account_id": str((account or {}).get("id") or ""),
        "account": _account_display(account),
        "code": slot.get("code") or "",
        "name": slot.get("label") or "",
        "debit": money_float(debit),
        "credit": money_float(credit),
        "source_keys": [slot["source_key"]],
    }
