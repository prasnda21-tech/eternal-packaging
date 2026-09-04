"""Shared date formatting utility for user-visible PDF documents."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


def format_pdf_date(raw_date: Any, fallback: str = "") -> str:
    """Format calendar date to dd/mm/yy for user-visible PDF documents.

    Accepts datetime, date, ISO string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS),
    or returns fallback / original string for empty/invalid historical values.

    Examples:
        2026-09-04 -> 04/09/26
        2026-01-05 -> 05/01/26
        2026-12-31 -> 31/12/26
    """
    if raw_date is None:
        return fallback

    if isinstance(raw_date, datetime):
        return raw_date.strftime("%d/%m/%y")

    if isinstance(raw_date, date):
        return raw_date.strftime("%d/%m/%y")

    s = str(raw_date).strip()
    if not s or s == "-":
        return fallback or s

    # Already dd/mm/yy (e.g. 04/09/26)
    if len(s) == 8 and s[2] == "/" and s[5] == "/" and s[:2].isdigit() and s[3:5].isdigit() and s[6:].isdigit():
        return s

    # Fast path for YYYY-MM-DD prefix (e.g. "2026-09-04" or "2026-09-04T10:00:00Z")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            year = int(s[0:4])
            month = int(s[5:7])
            day = int(s[8:10])
            d = date(year, month, day)
            return d.strftime("%d/%m/%y")
        except (ValueError, TypeError):
            pass

    # Try standard ISO parsing
    try:
        clean_s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_s)
        return dt.strftime("%d/%m/%y")
    except Exception:
        pass

    # Fallback to original string if cannot be parsed (preserve invalid/historical values safely)
    return s
