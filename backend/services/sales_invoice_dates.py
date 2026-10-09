"""Sales Invoice calendar dates.

Identify the source format before parsing. Never swap day and month.
New writes store YYYY-MM-DD. Historical values are not rewritten here.
"""
from __future__ import annotations

import re
from datetime import date

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_TIMESTAMP = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$"
)
_YMD_SLASH = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DMY_DASH = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


class SalesInvoiceDateError(ValueError):
    """Unsupported or impossible Sales Invoice calendar date."""


def _clock_ok(hour: str, minute: str, second: str) -> bool:
    return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59 and 0 <= int(second) <= 59


def parse_sales_invoice_date(raw) -> date:
    """Parse one supported Sales Invoice date. Empty and unknown shapes fail."""
    text = str(raw or "").strip()
    if not text:
        raise SalesInvoiceDateError("Tanggal faktur wajib diisi.")
    if match := _ISO_DATE.match(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _ISO_TIMESTAMP.match(text):
        if not _clock_ok(match.group(4), match.group(5), match.group(6)):
            raise SalesInvoiceDateError("Tanggal faktur tidak valid.")
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _YMD_SLASH.match(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _DMY_SLASH.match(text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _DMY_DASH.match(text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        raise SalesInvoiceDateError("Tanggal faktur tidak valid.")
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SalesInvoiceDateError("Tanggal faktur tidak valid.") from exc


def canonical_sales_invoice_date(raw) -> str:
    return parse_sales_invoice_date(raw).isoformat()


def try_canonical_sales_invoice_date(raw) -> str:
    """Read path. Unparseable historical values stay unmatched; they are not today."""
    try:
        return canonical_sales_invoice_date(raw)
    except SalesInvoiceDateError:
        return ""


def format_sales_invoice_user_date(raw) -> str:
    """DD/MM/YYYY for Sales Invoice display. Empty when the value cannot be identified."""
    try:
        parsed = parse_sales_invoice_date(raw)
    except SalesInvoiceDateError:
        return ""
    return parsed.strftime("%d/%m/%Y")


def persist_sales_invoice_date(existing_raw, incoming_raw) -> tuple[date, str]:
    """Calendar day plus the string to store.

    A new or changed day is stored as YYYY-MM-DD.
    The same calendar day keeps the historical spelling already on the document.
    """
    incoming_text = str(incoming_raw or "").strip()
    existing_text = str(existing_raw or "").strip()
    if incoming_text:
        parsed = parse_sales_invoice_date(incoming_text)
        if existing_text:
            try:
                if parse_sales_invoice_date(existing_text) == parsed:
                    return parsed, existing_text
            except SalesInvoiceDateError:
                pass
        return parsed, parsed.isoformat()
    if not existing_text:
        raise SalesInvoiceDateError("Tanggal faktur wajib diisi.")
    return parse_sales_invoice_date(existing_text), existing_text


def sales_invoice_date_sort_key(raw) -> tuple:
    """Normalized sort key. Unparseable dates sort after valid ones, never as today."""
    canonical = try_canonical_sales_invoice_date(raw)
    if not canonical:
        return (1, "")
    return (0, canonical)
