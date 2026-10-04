"""Purchasing due-date + payment target-date display semantics."""
from __future__ import annotations

import pytest

from services.purchase_common import invoice_due_display_status, parse_optional_ymd


def test_parse_optional_ymd_blank_is_none():
    assert parse_optional_ymd(None) is None
    assert parse_optional_ymd("") is None
    assert parse_optional_ymd("  ") is None


def test_parse_optional_ymd_valid():
    assert parse_optional_ymd("2026-10-05") == "2026-10-05"
    assert parse_optional_ymd("2026-10-05T12:00:00+00:00") == "2026-10-05"


def test_parse_optional_ymd_invalid_raises():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        parse_optional_ymd("not-a-date")


@pytest.mark.parametrize(
    "due,pay,today,expected",
    [
        (None, "BELUM DIBAYAR", "2026-09-28", "BELUM ADA JATUH TEMPO"),
        ("", "BELUM DIBAYAR", "2026-09-28", "BELUM ADA JATUH TEMPO"),
        ("2026-10-05", "BELUM DIBAYAR", "2026-09-28", "BELUM JATUH TEMPO"),
        ("2026-09-28", "BELUM DIBAYAR", "2026-09-28", "JATUH TEMPO HARI INI"),
        ("2026-09-01", "BELUM DIBAYAR", "2026-09-28", "TERLAMBAT"),
        ("2026-09-01", "LUNAS", "2026-09-28", "LUNAS"),
        ("2026-09-01", "DP", "2026-09-28", "TERLAMBAT"),
    ],
)
def test_invoice_due_display_status(due, pay, today, expected):
    assert invoice_due_display_status(due_date=due, payment_status=pay, today=today) == expected
