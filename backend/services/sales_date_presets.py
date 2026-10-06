"""Shared WIB date-only presets for sales list header filters.

Shipping presets: Pesanan Tanggal Kirim + SJ delivery_date.
Invoice presets: Faktur invoice_date (no overdue/unset).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

WIB = timezone(timedelta(hours=7))

SHIPPING_DATE_PRESETS = (
    "all",
    "today",
    "tomorrow",
    "next_3",
    "next_7",
    "this_week",
    "next_week",
    "overdue",
    "unset",
)

INVOICE_DATE_PRESETS = (
    "all",
    "today",
    "this_week",
    "this_month",
    "last_month",
)


def wib_today(now: Optional[datetime] = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(WIB).date()


def _parse_day(raw: Any) -> Optional[date]:
    text = str(raw or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def shipping_date_preset_bounds(
    preset: Optional[str],
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    key = str(preset or "all").strip().lower() or "all"
    day = today or wib_today()
    if key == "all":
        return {"kind": "all"}
    if key == "unset":
        return {"kind": "unset"}
    if key == "overdue":
        return {"kind": "overdue", "before": day.isoformat()}
    if key == "today":
        return {"kind": "range", "from": day.isoformat(), "to": day.isoformat()}
    if key == "tomorrow":
        nxt = day + timedelta(days=1)
        return {"kind": "range", "from": nxt.isoformat(), "to": nxt.isoformat()}
    if key == "next_3":
        return {
            "kind": "range",
            "from": day.isoformat(),
            "to": (day + timedelta(days=2)).isoformat(),
        }
    if key == "next_7":
        return {
            "kind": "range",
            "from": day.isoformat(),
            "to": (day + timedelta(days=6)).isoformat(),
        }
    if key == "this_week":
        start, end = _week_bounds(day)
        return {"kind": "range", "from": start.isoformat(), "to": end.isoformat()}
    if key == "next_week":
        start, end = _week_bounds(day + timedelta(days=7))
        return {"kind": "range", "from": start.isoformat(), "to": end.isoformat()}
    return {"kind": "all"}


def match_shipping_date(
    date_key: Optional[str],
    preset: Optional[str],
    *,
    today: Optional[date] = None,
    is_active_order: Optional[bool] = None,
) -> bool:
    """Return True when date_key matches the shipping preset.

    For overdue on Pesanan: pass is_active_order=True only for non-cancelled,
    non-completed rows. When is_active_order is False, overdue never matches.
    When is_active_order is None (SJ), overdue matches any date < today.
    """
    bounds = shipping_date_preset_bounds(preset, today=today)
    kind = bounds["kind"]
    if kind == "all":
        return True
    day = _parse_day(date_key)
    if kind == "unset":
        return day is None
    if kind == "overdue":
        if is_active_order is False:
            return False
        if day is None:
            return False
        before = _parse_day(bounds["before"])
        return before is not None and day < before
    if kind == "range":
        if day is None:
            return False
        start = _parse_day(bounds["from"])
        end = _parse_day(bounds["to"])
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True
    return True


def invoice_date_preset_range(
    preset: Optional[str],
    *,
    today: Optional[date] = None,
) -> Dict[str, str]:
    key = str(preset or "all").strip().lower() or "all"
    day = today or wib_today()
    if key == "all":
        return {"date_from": "", "date_to": ""}
    if key == "today":
        iso = day.isoformat()
        return {"date_from": iso, "date_to": iso}
    if key == "this_week":
        start, end = _week_bounds(day)
        return {"date_from": start.isoformat(), "date_to": end.isoformat()}
    if key == "this_month":
        start = day.replace(day=1)
        if day.month == 12:
            end = day.replace(year=day.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = day.replace(month=day.month + 1, day=1) - timedelta(days=1)
        return {"date_from": start.isoformat(), "date_to": end.isoformat()}
    if key == "last_month":
        this_month_start = day.replace(day=1)
        end = this_month_start - timedelta(days=1)
        start = end.replace(day=1)
        return {"date_from": start.isoformat(), "date_to": end.isoformat()}
    return {"date_from": "", "date_to": ""}
