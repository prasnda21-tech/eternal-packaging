"""Neraca Saldo — read model from journal_entries + journal_lines.

Phase 1: zero-write. Per-COA opening / period / ending are derived ONLY from
journal_lines of legitimate journal_entries (posted / reversed). Draft vouchers
never create journals. Original + reversal journals both remain so nets stay
correct.

Search narrows returned rows only. Global totals stay full-period for all COA.
Presentation sides use actual signed journal balance, not account_type.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from services.financial_statement_common import (
    load_statement_accounts,
    money,
    money_float,
    parse_financial_date_range,
    sum_journal_lines_by_account,
)

PERM_VIEW = "trial_balance.view"


class TrialBalanceValidationError(ValueError):
    pass


def _sides(signed: Decimal) -> Tuple[Decimal, Decimal]:
    signed = money(signed)
    if signed > 0:
        return signed, Decimal("0.00")
    if signed < 0:
        return Decimal("0.00"), money(-signed)
    return Decimal("0.00"), Decimal("0.00")


def _row_matches_search(row: dict, needle: str) -> bool:
    blob = " ".join([
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        str(row.get("account_type") or ""),
    ]).lower()
    return needle in blob


async def get_trial_balance(
    db,
    *,
    start_date: str,
    end_date: str,
    q: Optional[str] = None,
) -> dict:
    """Build Neraca Saldo for all COA over [start_date, end_date]. GET-safe."""
    start, end = parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Rentang tanggal wajib diisi",
        start_invalid_message="Tanggal mulai tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal mulai tidak boleh setelah tanggal akhir",
        error_cls=TrialBalanceValidationError,
    )

    accounts = await load_statement_accounts(db)
    opening_map = await sum_journal_lines_by_account(db, {"date": {"$lt": start}})
    period_map = await sum_journal_lines_by_account(
        db, {"date": {"$gte": start, "$lte": end}}
    )

    items: List[dict] = []
    tot_open_dr = Decimal("0.00")
    tot_open_cr = Decimal("0.00")
    tot_period_dr = Decimal("0.00")
    tot_period_cr = Decimal("0.00")
    tot_end_dr = Decimal("0.00")
    tot_end_cr = Decimal("0.00")

    for acc in accounts:
        aid = str(acc.get("id") or "").strip()
        if not aid:
            continue
        open_dr_raw, open_cr_raw = opening_map.get(aid, (Decimal("0.00"), Decimal("0.00")))
        opening = money(open_dr_raw - open_cr_raw)
        period_debit, period_credit = period_map.get(aid, (Decimal("0.00"), Decimal("0.00")))
        period_debit = money(period_debit)
        period_credit = money(period_credit)
        ending = money(opening + period_debit - period_credit)

        opening_debit, opening_credit = _sides(opening)
        ending_debit, ending_credit = _sides(ending)

        tot_open_dr += opening_debit
        tot_open_cr += opening_credit
        tot_period_dr += period_debit
        tot_period_cr += period_credit
        tot_end_dr += ending_debit
        tot_end_cr += ending_credit

        items.append({
            "account_id": aid,
            "code": acc.get("code"),
            "name": acc.get("name"),
            "account_type": acc.get("account_type"),
            "opening_debit": money_float(opening_debit),
            "opening_credit": money_float(opening_credit),
            "period_debit": money_float(period_debit),
            "period_credit": money_float(period_credit),
            "ending_debit": money_float(ending_debit),
            "ending_credit": money_float(ending_credit),
        })

    items.sort(key=lambda row: (str(row.get("code") or ""), str(row.get("account_id") or "")))

    totals = {
        "opening_debit": money_float(money(tot_open_dr)),
        "opening_credit": money_float(money(tot_open_cr)),
        "opening_diff": money_float(money(tot_open_dr - tot_open_cr)),
        "period_debit": money_float(money(tot_period_dr)),
        "period_credit": money_float(money(tot_period_cr)),
        "period_diff": money_float(money(tot_period_dr - tot_period_cr)),
        "ending_debit": money_float(money(tot_end_dr)),
        "ending_credit": money_float(money(tot_end_cr)),
        "ending_diff": money_float(money(tot_end_dr - tot_end_cr)),
    }

    visible = items
    needle = str(q or "").strip().lower()
    if needle:
        visible = [row for row in visible if _row_matches_search(row, needle)]

    return {
        "start_date": start,
        "end_date": end,
        "items": visible,
        "total": len(visible),
        "account_count": len(items),
        "totals": totals,
    }
