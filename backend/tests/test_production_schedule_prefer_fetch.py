"""Preferential Jadwal Produksi read window — month + active before filler."""
from datetime import datetime, timezone, timedelta

from services.production_dashboard_http import (
    production_schedule_month_clause,
    scoped_read_meta,
    wib_month_date_bounds,
)


WIB = timezone(timedelta(hours=7))


def test_wib_month_date_bounds_inclusive():
    now = datetime(2026, 11, 15, 10, 0, tzinfo=WIB)
    start, end = wib_month_date_bounds(now)
    assert start == "2026-11-01"
    assert end == "2026-11-30"


def test_wib_month_bounds_use_jakarta_not_utc_date():
    # 2026-10-31 17:30 UTC = 2026-11-01 00:30 WIB → November
    now = datetime(2026, 10, 31, 17, 30, tzinfo=timezone.utc)
    start, end = wib_month_date_bounds(now)
    assert start == "2026-11-01"
    assert end == "2026-11-30"


def test_month_clause_covers_order_and_split_work_date():
    clause = production_schedule_month_clause("2026-11-01", "2026-11-30")
    assert "work_date" in clause["$or"][0]
    assert "operator_splits.work_date" in clause["$or"][1]


def test_completeness_meta_still_marks_truncated_window():
    meta = scoped_read_meta(1500, 1000, 1000)
    assert meta["completeness"] == "incomplete"
    assert meta["read_limit"] == 1000
    assert meta["matched_count"] == 1500
    assert scoped_read_meta(800, 800, 1000)["completeness"] == "complete"
