"""Read-only laba/rugi projection for the widget dashboard.

Uses get_profit_loss totals and the same reversal blocker the finance
dashboard already applies. Does not post journals or add a year-to-date route.
"""
from __future__ import annotations

from services.financial_dashboard import _legitimate_journals, money, money_float
from services.journal_core import reversal_incomplete_blocker
from services.profit_loss import ProfitLossValidationError, get_profit_loss

PERM_VIEW = "profit_loss.view"


async def get_widget_profit_loss(db, *, start_date: str, end_date: str) -> dict:
    statement = await get_profit_loss(db, start_date=start_date, end_date=end_date)
    totals = statement.get("totals") or {}
    start = statement.get("start_date")
    end = statement.get("end_date")
    revenue = money(totals.get("revenue_total"))
    cogs = money(totals.get("cogs_total"))
    gross = money(totals.get("gross_profit"))
    expense = money(totals.get("expense_total"))
    other_income = money(totals.get("other_income_total"))
    other_expense = money(totals.get("other_expense_total"))
    net = money(totals.get("net_profit"))
    blocker = await reversal_incomplete_blocker(db, ({"date": {"$gte": start, "$lte": end}},))
    journals = await _legitimate_journals(db, {"date": {"$gte": start, "$lte": end}})
    if not journals:
        status = "empty"
        amounts = None
    elif revenue == 0 and cogs == 0 and expense == 0 and other_income == 0 and other_expense == 0:
        status = "zero"
        amounts = True
    else:
        status = "complete"
        amounts = True
    if blocker:
        status = "incomplete"
    number = (lambda value: None if amounts is None else money_float(value))
    return {
        "status": status,
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "source": "profit_loss.journal_lines",
        "revenue": number(revenue),
        "cogs": number(cogs),
        "gross_profit": number(gross),
        "operating_expense": number(expense),
        "other_income": number(other_income),
        "other_expense": number(other_expense),
        "net_profit": number(net),
        "blocker_code": blocker,
        "detail": (
            "Ada reversal yang belum selesai pada periode ini. Angka memakai jurnal yang sudah sah dan tidak memasukkan jurnal yang belum lengkap."
            if blocker else None
        ),
    }


__all__ = ["PERM_VIEW", "ProfitLossValidationError", "get_widget_profit_loss"]
