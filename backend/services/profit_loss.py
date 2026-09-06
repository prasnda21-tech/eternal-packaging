"""Laba/Rugi — GET-only read model from journal_entries + journal_lines.

Phase 1: zero-write. Period movement only (no opening-balance carry-forward).
Uses the same posted/reversed inclusion as Buku Besar / Neraca Saldo.
Classification comes only from the static account_type reporting map.
normal_balance is presentation sign convention only; debit/credit stay raw.
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

PERM_VIEW = "profit_loss.view"

LEGITIMATE_STATUSES = frozenset({STATUS_POSTED, STATUS_REVERSED})

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


def _presentation_amount(debit: Decimal, credit: Decimal, normal_balance: str) -> Decimal:
    if str(normal_balance or "").strip() == "credit":
        return _money(credit - debit)
    return _money(debit - credit)


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


async def get_profit_loss(
    db,
    *,
    start_date: str,
    end_date: str,
    q: Optional[str] = None,
) -> dict:
    """Build Laba/Rugi for income-statement COA over [start_date, end_date]. GET-safe."""
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if not start or not end:
        raise ProfitLossValidationError("Rentang tanggal wajib diisi")
    if start > end:
        raise ProfitLossValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    accounts = await list_accounts(db)

    period_journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$gte": start, "$lte": end}},
        {"_id": 0, "id": 1, "status": 1},
    ).to_list(100000)
    period_ids = [j["id"] for j in period_journals if j.get("id") and _journal_is_legitimate(j)]
    period_map = await _sum_by_account(db, period_ids)

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
        period_debit = _money(period_debit)
        period_credit = _money(period_credit)
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
            "debit": _money_float(period_debit),
            "credit": _money_float(period_credit),
            "amount": _money_float(amount),
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

    revenue_total = _money(group_totals["revenue"])
    cogs_total = _money(group_totals["cogs"])
    expense_total = _money(group_totals["expense"])
    other_income_total = _money(group_totals["other_income"])
    other_expense_total = _money(group_totals["other_expense"])
    gross_profit = _money(revenue_total - cogs_total)
    operating_profit = _money(gross_profit - expense_total)
    net_profit = _money(operating_profit + other_income_total - other_expense_total)

    totals = {
        "revenue_total": _money_float(revenue_total),
        "cogs_total": _money_float(cogs_total),
        "gross_profit": _money_float(gross_profit),
        "expense_total": _money_float(expense_total),
        "operating_profit": _money_float(operating_profit),
        "other_income_total": _money_float(other_income_total),
        "other_expense_total": _money_float(other_expense_total),
        "net_profit": _money_float(net_profit),
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
