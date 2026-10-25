"""Legacy PO automatic delivery uses official sla_working_days + holidays.

Isolated fixtures only. No database writes. No production data.
"""
from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_plan import aggregate_delivery_plan_qty, plan_row
from services.delivery_schedule import schedule_status_code
from services.production_wave2 import add_working_days, is_working_day
from services.tanggal_pengiriman import (
    automatic_tanggal_pengiriman,
    automatic_working_tanggal_pengiriman,
    delivery_sla_effective,
    resolve_tanggal_pengiriman,
)


def _legacy(**extra):
    row = {
        "id": "po-legacy-wd",
        "po_number": "PO-LEGACY-WD",
        "priority": "normal",
        "cancelled": False,
        "buffer_stock": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-10-23T10:00:00+07:00",  # Friday
        "payment_approved_at": "2026-10-23T11:00:00+07:00",
        "qty_delivered": 0,
        "total_quantity": 100,
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs"}],
        "current_stage": 8,
    }
    row.update(extra)
    return row


def test_friday_plus_3_working_days_skips_sunday():
    order = _legacy()
    assert automatic_tanggal_pengiriman(order, 3) == "2026-10-26"  # calendar diagnostic
    assert automatic_working_tanggal_pengiriman(order, 3, []) == "2026-10-27"
    fact = delivery_sla_effective(order, 3, today=date(2026, 10, 24))
    assert fact["contract"] == "legacy_working_days"
    assert fact["date"] == "2026-10-27"
    assert fact["countable"] is False
    assert fact["source"] == "legacy"
    assert fact["legacy_calendar_date"] == "2026-10-26"
    snap = resolve_tanggal_pengiriman(order, 3)
    assert snap["tanggal_pengiriman"] == "2026-10-27"
    assert snap["source"] == "automatic"
    assert snap["delivery_sla_effective"]["countable"] is False
    pub = snap["delivery_sla_effective"]
    assert pub["estimate_adjusted"] is True
    assert pub["estimate_from"] == "2026-10-26"
    assert pub["estimate_to"] == "2026-10-27"
    assert pub["legacy_calendar_date"] == "2026-10-26"


def test_saturday_plus_1_working_day():
    order = _legacy(
        design_approved_at="2026-10-24T10:00:00+07:00",
        payment_approved_at="2026-10-24T12:00:00+07:00",
    )
    assert date(2026, 10, 24).weekday() == 5  # Saturday
    assert automatic_working_tanggal_pengiriman(order, 1, []) == "2026-10-26"  # skip Sunday
    assert automatic_tanggal_pengiriman(order, 1) == "2026-10-25"  # calendar lands on Sunday


def test_operational_holiday_in_range_is_skipped():
    order = _legacy()
    holidays = ["2026-10-26"]  # Monday holiday
    assert add_working_days(date(2026, 10, 23), 3, holidays).isoformat() == "2026-10-28"
    assert automatic_working_tanggal_pengiriman(order, 3, holidays) == "2026-10-28"
    fact = delivery_sla_effective(order, 3, holidays=holidays)
    assert fact["date"] == "2026-10-28"
    assert fact["contract"] == "legacy_working_days"


def test_sla_settings_1_3_5_change_legacy_read_time_only():
    order = _legacy()
    assert delivery_sla_effective(order, 1)["date"] == "2026-10-24"
    assert delivery_sla_effective(order, 3)["date"] == "2026-10-27"
    assert delivery_sla_effective(order, 5)["date"] == "2026-10-29"
    stamped = _legacy(
        delivery_sla_v2={
            "rule": "working_days_3",
            "working_days": 3,
            "unit": "working_days",
            "automatic_date": "2026-10-27",
            "legacy": False,
        },
    )
    before = copy.deepcopy(stamped["delivery_sla_v2"])
    for days, holiday in ((1, ["2026-10-24"]), (5, ["2026-10-28"])):
        fact = delivery_sla_effective(stamped, days, holidays=holiday)
        assert fact["contract"] == "sla_v2"
        assert fact["date"] == "2026-10-27"
        assert fact["countable"] is True
    assert stamped["delivery_sla_v2"] == before


def test_manual_and_urgent_priority_unchanged():
    manual = _legacy(
        delivery_deadline_manual="2026-11-02T00:00:00+07:00",
        scheduled_delivery_date="2026-11-02",
    )
    m = delivery_sla_effective(manual, 3, today=date(2026, 10, 30))
    assert m["date"] == "2026-11-02"
    assert m["source"] == "manual"
    assert m["countable"] is True
    m_pub = resolve_tanggal_pengiriman(manual, 3)["delivery_sla_effective"]
    assert "estimate_adjusted" not in m_pub
    assert "estimate_from" not in m_pub

    urgent = _legacy(priority="urgent", scheduled_delivery_date="2026-10-20")
    u = delivery_sla_effective(urgent, 3, today=date(2026, 10, 30))
    assert u["contract"] == "urgent"
    assert u["date"] == "2026-10-20"
    assert u["source"] == "jadwal_pengiriman"
    u_pub = resolve_tanggal_pengiriman(urgent, 3)["delivery_sla_effective"]
    assert "estimate_adjusted" not in u_pub


def test_estimate_adjusted_false_when_calendar_equals_working():
    """Wed + 3 WD lands same as calendar (no Sunday in range)."""
    order = _legacy(
        design_approved_at="2026-10-21T10:00:00+07:00",  # Wednesday
        payment_approved_at="2026-10-21T11:00:00+07:00",
    )
    assert automatic_tanggal_pengiriman(order, 3) == "2026-10-24"
    assert automatic_working_tanggal_pengiriman(order, 3, []) == "2026-10-24"
    pub = resolve_tanggal_pengiriman(order, 3)["delivery_sla_effective"]
    assert pub["contract"] == "legacy_working_days"
    assert pub["estimate_adjusted"] is False
    assert "estimate_from" not in pub
    stamped = _legacy(
        delivery_sla_v2={
            "rule": "working_days_3",
            "working_days": 3,
            "unit": "working_days",
            "automatic_date": "2026-10-27",
            "legacy": False,
        },
    )
    v2 = resolve_tanggal_pengiriman(stamped, 3)["delivery_sla_effective"]
    assert v2["contract"] == "sla_v2"
    assert "estimate_adjusted" not in v2


def test_incomplete_cancelled_full_and_partial():
    incomplete = _legacy(payment_approved_at=None, payment_status="Waiting")
    inc = delivery_sla_effective(incomplete, 3)
    assert inc["contract"] == "incomplete"
    assert inc["date"] is None
    assert resolve_tanggal_pengiriman(incomplete, 3)["source"] == "none"

    cancelled = _legacy(cancelled=True)
    assert plan_row(cancelled, lead_time_days=3) is None

    full = _legacy(qty_delivered=100, delivery_status="Terkirim", current_stage=14)
    assert plan_row(full, lead_time_days=3) is None

    partial = _legacy(
        qty_delivered=40,
        delivery_status="Sebagian Terkirim",
        total_quantity=100,
        line_items=[{"name": "Cup", "quantity": 100, "unit": "pcs"}],
    )
    row = plan_row(partial, lead_time_days=3, today="2026-10-24")
    assert row is not None
    assert row["qty_remaining"] == 60
    assert row["delivery_sla_effective"]["contract"] == "legacy_working_days"
    assert row["tanggal_pengiriman"] == "2026-10-27"


def test_legacy_overdue_and_qty_widget_follow_working_date():
    order = _legacy()
    fact = delivery_sla_effective(order, 3, today=date(2026, 10, 28))
    assert fact["date"] == "2026-10-27"
    # countable=false → SLA state stays legacy; schedule/plan still mark terlambat.
    assert fact["state"] == "legacy"
    assert fact["countable"] is False
    assert schedule_status_code(order, today="2026-10-28", lead_time_days=3) == "terlambat"
    row = plan_row(order, lead_time_days=3, today="2026-10-28")
    assert row["delivery_schedule_status"] == "terlambat"

    body = aggregate_delivery_plan_qty(
        [order],
        lead_time_days=3,
        today="2026-10-24",
        days=7,
    )
    pcs = next(item for item in body["series"] if item["unit"] == "PCS")
    assert pcs["overdue_qty"] == 0
    point = next(p for p in pcs["points"] if p["date"] == "2026-10-27")
    assert point["qty"] == 100
    assert point["po_count"] == 1
    # Old calendar Sunday must not receive the qty bucket.
    sunday = next(p for p in pcs["points"] if p["date"] == "2026-10-25")
    assert sunday["qty"] == 0


def test_resolver_is_read_only():
    order = _legacy()
    before = copy.deepcopy(order)
    delivery_sla_effective(order, 3, holidays=["2026-10-26"])
    resolve_tanggal_pengiriman(order, 5, holidays=["2026-10-27"])
    plan_row(order, lead_time_days=3, holidays=["2026-10-26"])
    assert order == before
    assert is_working_day(date(2026, 10, 25), []) is False
    assert is_working_day(date(2026, 10, 24), []) is True
