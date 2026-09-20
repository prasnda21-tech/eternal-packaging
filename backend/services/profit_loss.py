"""Laba/Rugi — GET-only read model from journal_entries + journal_lines.

Phase 1: zero-write. Period movement only (no opening-balance carry-forward).
Uses the same posted/reversed inclusion as Buku Besar / Neraca Saldo.
Classification comes only from the static account_type reporting map.
normal_balance is presentation sign convention only; debit/credit stay raw.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from services.account_type_reporting import reporting_metadata
from services.financial_statement_common import (
    load_statement_accounts,
    money,
    money_float,
    parse_financial_date_range,
    sum_journal_lines_by_account,
)

PERM_VIEW = "profit_loss.view"

INCOME_STATEMENT = "INCOME_STATEMENT"

REPORT_GROUPS: Tuple[str, ...] = (
    "revenue",
    "cogs",
    "expense",
    "other_income",
    "other_expense",
)

GROUP_LABELS = {
    "revenue": "PENDAPATAN",
    "cogs": "BEBAN POKOK PENJUALAN",
    "expense": "BEBAN",
    "other_income": "PENDAPATAN LAIN-LAIN",
    "other_expense": "BEBAN LAIN-LAIN",
}


class ProfitLossValidationError(ValueError):
    pass


def _presentation_amount(debit: Decimal, credit: Decimal, normal_balance: str) -> Decimal:
    if str(normal_balance or "").strip() == "credit":
        return money(credit - debit)
    return money(debit - credit)


def _row_matches_search(row: dict, needle: str) -> bool:
    blob = " ".join([
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        str(row.get("account_type") or ""),
        str(row.get("account_type_label") or ""),
    ]).lower()
    return needle in blob


async def get_profit_loss(
    db,
    *,
    start_date: str,
    end_date: str,
    q: Optional[str] = None,
) -> dict:
    """Build Laba/Rugi for income-statement COA over [start_date, end_date]. GET-safe."""
    start, end = parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Rentang tanggal wajib diisi",
        start_invalid_message="Tanggal mulai tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal mulai tidak boleh setelah tanggal akhir",
        error_cls=ProfitLossValidationError,
    )

    accounts = await load_statement_accounts(db)
    period_map = await sum_journal_lines_by_account(
        db, {"date": {"$gte": start, "$lte": end}}
    )

    items: List[dict] = []
    group_totals = {key: Decimal("0.00") for key in REPORT_GROUPS}

    for acc in accounts:
        aid = str(acc.get("id") or "").strip()
        if not aid:
            continue
        meta = reporting_metadata(acc.get("account_type"))
        if not meta or meta.get("statement") != INCOME_STATEMENT:
            continue
        report_group = meta["report_group"]
        if report_group not in group_totals:
            continue

        period_debit, period_credit = period_map.get(aid, (Decimal("0.00"), Decimal("0.00")))
        period_debit = money(period_debit)
        period_credit = money(period_credit)
        amount = _presentation_amount(period_debit, period_credit, meta["normal_balance"])
        group_totals[report_group] += amount

        items.append({
            "account_id": aid,
            "code": acc.get("code"),
            "name": acc.get("name"),
            "account_type": meta["account_type"],
            "account_type_label": meta["account_type_label"],
            "report_group": report_group,
            "normal_balance": meta["normal_balance"],
            "debit": money_float(period_debit),
            "credit": money_float(period_credit),
            "amount": money_float(amount),
            "drilldown": {
                "account_id": aid,
                "start_date": start,
                "end_date": end,
            },
        })

    items.sort(key=lambda row: (
        REPORT_GROUPS.index(row["report_group"]) if row.get("report_group") in REPORT_GROUPS else 99,
        str(row.get("code") or ""),
        str(row.get("account_id") or ""),
    ))

    revenue_total = money(group_totals["revenue"])
    cogs_total = money(group_totals["cogs"])
    expense_total = money(group_totals["expense"])
    other_income_total = money(group_totals["other_income"])
    other_expense_total = money(group_totals["other_expense"])
    gross_profit = money(revenue_total - cogs_total)
    operating_profit = money(gross_profit - expense_total)
    net_profit = money(operating_profit + other_income_total - other_expense_total)

    totals = {
        "revenue_total": money_float(revenue_total),
        "cogs_total": money_float(cogs_total),
        "gross_profit": money_float(gross_profit),
        "expense_total": money_float(expense_total),
        "operating_profit": money_float(operating_profit),
        "other_income_total": money_float(other_income_total),
        "other_expense_total": money_float(other_expense_total),
        "net_profit": money_float(net_profit),
    }

    visible = items
    needle = str(q or "").strip().lower()
    if needle:
        visible = [row for row in visible if _row_matches_search(row, needle)]

    groups = []
    for key in REPORT_GROUPS:
        groups.append({
            "report_group": key,
            "label": GROUP_LABELS[key],
            "items": [row for row in visible if row["report_group"] == key],
            "total": totals[{
                "revenue": "revenue_total",
                "cogs": "cogs_total",
                "expense": "expense_total",
                "other_income": "other_income_total",
                "other_expense": "other_expense_total",
            }[key]],
        })

    return {
        "start_date": start,
        "end_date": end,
        "items": visible,
        "groups": groups,
        "total": len(visible),
        "account_count": len(items),
        "totals": totals,
        "labels": {
            "gross_profit": "LABA KOTOR",
            "operating_profit": "LABA OPERASIONAL",
            "net_profit": "LABA BERSIH",
        },
    }
