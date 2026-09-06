"""Neraca — GET-only read model from journal_entries + journal_lines.

Phase 1: zero-write. Cumulative as-of date using the same posted/reversed
inclusion as Buku Besar / Neraca Saldo. Classification comes only from the
static account_type reporting map. No fiscal close is persisted; current
earnings are derived from income-statement accounts.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from services.account_type_reporting import reporting_metadata
from services.chart_of_accounts import list_accounts
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED,
)

PERM_VIEW = "balance_sheet.view"

LEGITIMATE_STATUSES = frozenset({STATUS_POSTED, STATUS_REVERSED})

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


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _money_float(d: Decimal) -> float:
    return float(d)


def _journal_is_legitimate(doc: dict) -> bool:
    status = str((doc or {}).get("status") or "").strip()
    if not status:
        return True
    return status in LEGITIMATE_STATUSES


def _bs_amount(debit: Decimal, credit: Decimal, report_group: str) -> Decimal:
    if report_group == "asset":
        return _money(debit - credit)
    if report_group in ("liability", "equity"):
        return _money(credit - debit)
    return Decimal("0.00")


def _pl_amount(debit: Decimal, credit: Decimal, report_group: str) -> Decimal:
    if report_group in ("revenue", "other_income"):
        return _money(credit - debit)
    if report_group in ("cogs", "expense", "other_expense"):
        return _money(debit - credit)
    return Decimal("0.00")


def _row_matches_search(row: dict, needle: str) -> bool:
    blob = " ".join([
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        str(row.get("account_type") or ""),
        str(row.get("account_type_label") or ""),
    ]).lower()
    return needle in blob


async def _sum_by_account(db, journal_ids: List[str]) -> Dict[str, Tuple[Decimal, Decimal]]:
    out: Dict[str, Tuple[Decimal, Decimal]] = {}
    if not journal_ids:
        return out
    lines = await db[JOURNAL_LINES].find(
        {"journal_id": {"$in": journal_ids}},
        {"_id": 0, "account_id": 1, "debit": 1, "credit": 1},
    ).to_list(500000)
    for ln in lines:
        aid = str(ln.get("account_id") or "").strip()
        if not aid:
            continue
        debit, credit = out.get(aid, (Decimal("0.00"), Decimal("0.00")))
        out[aid] = (debit + _money(ln.get("debit")), credit + _money(ln.get("credit")))
    return out


def _validate_as_of_date(raw: str) -> str:
    as_of = str(raw or "").strip()
    if not as_of:
        raise BalanceSheetValidationError("Tanggal neraca wajib diisi")
    parts = as_of.split("-")
    if len(as_of) != 10 or len(parts) != 3:
        raise BalanceSheetValidationError("Tanggal neraca tidak valid")
    year, month, day = parts
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        raise BalanceSheetValidationError("Tanggal neraca tidak valid")
    if not (len(year) == 4 and len(month) == 2 and len(day) == 2):
        raise BalanceSheetValidationError("Tanggal neraca tidak valid")
    yi, mi, di = int(year), int(month), int(day)
    if mi < 1 or mi > 12 or di < 1 or di > 31:
        raise BalanceSheetValidationError("Tanggal neraca tidak valid")
    return as_of


async def get_balance_sheet(
    db,
    *,
    as_of_date: str,
    q: Optional[str] = None,
) -> dict:
    """Build Neraca as of as_of_date. GET-safe. Never persists totals."""
    as_of = _validate_as_of_date(as_of_date)

    accounts = await list_accounts(db)

    journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$lte": as_of}},
        {"_id": 0, "id": 1, "status": 1},
    ).to_list(100000)
    journal_ids = [j["id"] for j in journals if j.get("id") and _journal_is_legitimate(j)]
    by_account = await _sum_by_account(db, journal_ids)

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
        debit = _money(debit)
        credit = _money(credit)

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
            "debit": _money_float(debit),
            "credit": _money_float(credit),
            "amount": _money_float(amount),
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

    current_earnings = _money(
        pl_totals["revenue"]
        - pl_totals["cogs"]
        - pl_totals["expense"]
        + pl_totals["other_income"]
        - pl_totals["other_expense"]
    )
    total_assets = _money(group_totals["asset"])
    total_liabilities = _money(group_totals["liability"])
    equity_accounts_total = _money(group_totals["equity"])
    total_equity = _money(equity_accounts_total + current_earnings)
    total_liabilities_and_equity = _money(total_liabilities + total_equity)
    balance_difference = _money(total_assets - total_liabilities_and_equity)
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
            "total": _money_float(_money(group_totals[key])),
        }

    summary = {
        "total_assets": _money_float(total_assets),
        "total_liabilities": _money_float(total_liabilities),
        "equity_accounts_total": _money_float(equity_accounts_total),
        "current_earnings": _money_float(current_earnings),
        "total_equity": _money_float(total_equity),
        "total_liabilities_and_equity": _money_float(total_liabilities_and_equity),
        "balance_difference": _money_float(balance_difference),
        "is_balanced": is_balanced,
    }

    return {
        "as_of_date": as_of,
        "groups": groups,
        "current_earnings": {
            "label": CURRENT_EARNINGS_LABEL,
            "amount": _money_float(current_earnings),
            "derived": True,
        },
        "summary": summary,
        "account_count": len(bs_rows),
        "total": len(visible),
    }
