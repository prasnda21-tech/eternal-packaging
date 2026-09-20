"""Neraca — GET-only read model from journal_entries + journal_lines.

Phase 1: zero-write. Cumulative as-of date using the same posted/reversed
inclusion as Buku Besar / Neraca Saldo. Classification comes only from the
static account_type reporting map. No fiscal close is persisted; current
earnings are derived from income-statement accounts.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from services.account_type_reporting import reporting_metadata
from services.financial_statement_common import (
    load_statement_accounts,
    money,
    money_float,
    parse_financial_date,
    sum_journal_lines_by_account,
)

PERM_VIEW = "balance_sheet.view"

BALANCE_SHEET = "BALANCE_SHEET"
INCOME_STATEMENT = "INCOME_STATEMENT"

BS_GROUPS: Tuple[str, ...] = ("asset", "liability", "equity")
PL_GROUPS: Tuple[str, ...] = (
    "revenue",
    "cogs",
    "expense",
    "other_income",
    "other_expense",
)

GROUP_KEYS = {
    "asset": "assets",
    "liability": "liabilities",
    "equity": "equity",
}

GROUP_LABELS = {
    "asset": "ASET",
    "liability": "KEWAJIBAN",
    "equity": "EKUITAS",
}

CURRENT_EARNINGS_LABEL = "Laba/Rugi Berjalan"


class BalanceSheetValidationError(ValueError):
    pass


def _bs_amount(debit: Decimal, credit: Decimal, report_group: str) -> Decimal:
    if report_group == "asset":
        return money(debit - credit)
    if report_group in ("liability", "equity"):
        return money(credit - debit)
    return Decimal("0.00")


def _pl_amount(debit: Decimal, credit: Decimal, report_group: str) -> Decimal:
    if report_group in ("revenue", "other_income"):
        return money(credit - debit)
    if report_group in ("cogs", "expense", "other_expense"):
        return money(debit - credit)
    return Decimal("0.00")


def _row_matches_search(row: dict, needle: str) -> bool:
    blob = " ".join([
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        str(row.get("account_type") or ""),
        str(row.get("account_type_label") or ""),
    ]).lower()
    return needle in blob


async def get_balance_sheet(
    db,
    *,
    as_of_date: str,
    q: Optional[str] = None,
) -> dict:
    """Build Neraca as of as_of_date. GET-safe. Never persists totals."""
    as_of = parse_financial_date(
        as_of_date,
        empty_message="Tanggal neraca wajib diisi",
        invalid_message="Tanggal neraca tidak valid",
        error_cls=BalanceSheetValidationError,
    )

    accounts = await load_statement_accounts(db)
    by_account = await sum_journal_lines_by_account(db, {"date": {"$lte": as_of}})

    bs_rows: List[dict] = []
    group_totals = {key: Decimal("0.00") for key in BS_GROUPS}
    pl_totals = {key: Decimal("0.00") for key in PL_GROUPS}

    for acc in accounts:
        aid = str(acc.get("id") or "").strip()
        if not aid:
            continue
        meta = reporting_metadata(acc.get("account_type"))
        if not meta:
            continue
        statement = meta.get("statement")
        report_group = meta["report_group"]
        debit, credit = by_account.get(aid, (Decimal("0.00"), Decimal("0.00")))
        debit = money(debit)
        credit = money(credit)

        if statement == INCOME_STATEMENT and report_group in pl_totals:
            pl_totals[report_group] += _pl_amount(debit, credit, report_group)
            continue
        if statement != BALANCE_SHEET or report_group not in group_totals:
            continue

        amount = _bs_amount(debit, credit, report_group)
        group_totals[report_group] += amount
        bs_rows.append({
            "account_id": aid,
            "code": acc.get("code"),
            "name": acc.get("name"),
            "account_type": meta["account_type"],
            "account_type_label": meta["account_type_label"],
            "report_group": report_group,
            "normal_balance": meta["normal_balance"],
            "debit": money_float(debit),
            "credit": money_float(credit),
            "amount": money_float(amount),
            "drilldown": {
                "account_id": aid,
                "end_date": as_of,
            },
        })

    bs_rows.sort(key=lambda row: (
        BS_GROUPS.index(row["report_group"]) if row.get("report_group") in BS_GROUPS else 99,
        str(row.get("code") or ""),
        str(row.get("account_id") or ""),
    ))

    current_earnings = money(
        pl_totals["revenue"]
        - pl_totals["cogs"]
        - pl_totals["expense"]
        + pl_totals["other_income"]
        - pl_totals["other_expense"]
    )
    total_assets = money(group_totals["asset"])
    total_liabilities = money(group_totals["liability"])
    equity_accounts_total = money(group_totals["equity"])
    total_equity = money(equity_accounts_total + current_earnings)
    total_liabilities_and_equity = money(total_liabilities + total_equity)
    balance_difference = money(total_assets - total_liabilities_and_equity)
    is_balanced = balance_difference == Decimal("0.00")

    visible = bs_rows
    needle = str(q or "").strip().lower()
    if needle:
        visible = [row for row in visible if _row_matches_search(row, needle)]

    groups = {}
    for key in BS_GROUPS:
        groups[GROUP_KEYS[key]] = {
            "report_group": key,
            "label": GROUP_LABELS[key],
            "accounts": [row for row in visible if row["report_group"] == key],
            "total": money_float(money(group_totals[key])),
        }

    summary = {
        "total_assets": money_float(total_assets),
        "total_liabilities": money_float(total_liabilities),
        "equity_accounts_total": money_float(equity_accounts_total),
        "current_earnings": money_float(current_earnings),
        "total_equity": money_float(total_equity),
        "total_liabilities_and_equity": money_float(total_liabilities_and_equity),
        "balance_difference": money_float(balance_difference),
        "is_balanced": is_balanced,
    }

    return {
        "as_of_date": as_of,
        "groups": groups,
        "current_earnings": {
            "label": CURRENT_EARNINGS_LABEL,
            "amount": money_float(current_earnings),
            "derived": True,
        },
        "summary": summary,
        "account_count": len(bs_rows),
        "total": len(visible),
    }
