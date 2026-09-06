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
from typing import Any, Dict, List, Optional, Tuple

from services.chart_of_accounts import list_accounts
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED,
)

PERM_VIEW = "trial_balance.view"

LEGITIMATE_STATUSES = frozenset({STATUS_POSTED, STATUS_REVERSED})


class TrialBalanceValidationError(ValueError):
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


def _sides(signed: Decimal) -> Tuple[Decimal, Decimal]:
    signed = _money(signed)
    if signed > 0:
        return signed, Decimal("0.00")
    if signed < 0:
        return Decimal("0.00"), _money(-signed)
    return Decimal("0.00"), Decimal("0.00")


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
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if not start or not end:
        raise TrialBalanceValidationError("Rentang tanggal wajib diisi")
    if start > end:
        raise TrialBalanceValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    accounts = await list_accounts(db)

    opening_journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$lt": start}},
        {"_id": 0, "id": 1, "status": 1},
    ).to_list(100000)
    opening_ids = [j["id"] for j in opening_journals if j.get("id") and _journal_is_legitimate(j)]
    opening_map = await _sum_by_account(db, opening_ids)

    period_journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$gte": start, "$lte": end}},
        {"_id": 0, "id": 1, "status": 1},
    ).to_list(100000)
    period_ids = [j["id"] for j in period_journals if j.get("id") and _journal_is_legitimate(j)]
    period_map = await _sum_by_account(db, period_ids)

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
        opening = _money(open_dr_raw - open_cr_raw)
        period_debit, period_credit = period_map.get(aid, (Decimal("0.00"), Decimal("0.00")))
        period_debit = _money(period_debit)
        period_credit = _money(period_credit)
        ending = _money(opening + period_debit - period_credit)

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
            "opening_debit": _money_float(opening_debit),
            "opening_credit": _money_float(opening_credit),
            "period_debit": _money_float(period_debit),
            "period_credit": _money_float(period_credit),
            "ending_debit": _money_float(ending_debit),
            "ending_credit": _money_float(ending_credit),
        })

    items.sort(key=lambda row: (str(row.get("code") or ""), str(row.get("account_id") or "")))

    totals = {
        "opening_debit": _money_float(_money(tot_open_dr)),
        "opening_credit": _money_float(_money(tot_open_cr)),
        "opening_diff": _money_float(_money(tot_open_dr - tot_open_cr)),
        "period_debit": _money_float(_money(tot_period_dr)),
        "period_credit": _money_float(_money(tot_period_cr)),
        "period_diff": _money_float(_money(tot_period_dr - tot_period_cr)),
        "ending_debit": _money_float(_money(tot_end_dr)),
        "ending_credit": _money_float(_money(tot_end_cr)),
        "ending_diff": _money_float(_money(tot_end_dr - tot_end_cr)),
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
