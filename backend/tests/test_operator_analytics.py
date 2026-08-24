"""Batch 1 operator analytics — pure unit tests (no Mongo / no live API)."""
import math
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operator_analytics import (
    achievement_pct,
    compute_operator_kpis,
    iso_week_key,
    remaining_qty,
    resolve_period,
    wib_today,
)

PERIOD_FROM = date(2026, 8, 1)
PERIOD_TO = date(2026, 8, 31)


def _at(day, hour=10):
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone(timedelta(hours=7))).isoformat()


def _split(email, name, quantity, qty_completed, day=5, split_id="s1"):
    return {
        "id": split_id,
        "operator_email": email,
        "operator_name": name,
        "quantity": quantity,
        "qty_completed": qty_completed,
        "work_date": f"2026-08-{day:02d}",
        "updated_at": _at(day),
    }


def _qty_note(name, qty, cap, day, email):
    return {
        "at": _at(day),
        "note": f"Qty {name}: {qty}/{cap}",
        "by_email": email,
    }


def _order(po_id, customer="Cust A", item="Karton A", splits=None, timeline=None, total_quantity=99999):
    return {
        "id": po_id,
        "po_number": po_id,
        "customer": customer,
        "total_quantity": total_quantity,
        "line_items": [{"name": item, "item_code": "ITM", "quantity": total_quantity}],
        "operator_splits": splits or [],
        "timeline": timeline or [],
        "cancelled": False,
    }


def _run(orders, **kwargs):
    return compute_operator_kpis(
        orders,
        date_from=kwargs.pop("date_from", PERIOD_FROM),
        date_to=kwargs.pop("date_to", PERIOD_TO),
        **kwargs,
    )


def test_a_assigned_3000_completed_2500_two_jobs():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-A1", splits=[_split(op, name, 2000, 1500, day=4, split_id="a1")], timeline=[
            _qty_note(name, 1500, 2000, 4, op),
        ]),
        _order("PO-A2", splits=[_split(op, name, 1000, 1000, day=6, split_id="a2")], timeline=[
            _qty_note(name, 1000, 1000, 6, op),
        ]),
    ]
    out = _run(orders)
    row = out["operators"][0]
    assert row["assigned"] == 3000
    assert row["completed"] == 2500, "must use qty_completed, not PO qty"
    assert row["remaining"] == 500
    assert row["achievement"] == 83.33
    assert row["job_count"] == 2
    assert out["kpis"]["total_completed"] == 2500
    assert out["kpis"]["total_jobs"] == 2


def test_a_same_po_multiple_progress_is_one_job():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-SAME", splits=[_split(op, name, 3000, 2500, day=10)], timeline=[
            _qty_note(name, 1000, 3000, 8, op),
            _qty_note(name, 2000, 3000, 9, op),
            _qty_note(name, 2500, 3000, 10, op),
        ]),
    ]
    out = _run(orders)
    row = out["operators"][0]
    assert row["completed"] == 2500
    assert row["job_count"] == 1


def test_b_multi_operator_same_po_no_double_count():
    a, b = "op.a@eternalpackaging.com", "op.b@eternalpackaging.com"
    orders = [
        _order(
            "PO-MULTI",
            total_quantity=5000,
            splits=[
                _split(a, "Op A", 2000, 1000, day=5, split_id="sA"),
                _split(b, "Op B", 3000, 1500, day=5, split_id="sB"),
            ],
            timeline=[
                _qty_note("Op A", 1000, 2000, 5, a),
                _qty_note("Op B", 1500, 3000, 5, b),
            ],
        ),
    ]
    out = _run(orders)
    by_email = {r["email"]: r for r in out["operators"]}
    assert by_email[a]["completed"] == 1000
    assert by_email[b]["completed"] == 1500
    assert out["kpis"]["total_completed"] == 2500
    assert out["kpis"]["total_completed"] != 5000
    assert out["kpis"]["total_jobs"] == 1  # unique PO, no double count
    assert by_email[a]["job_count"] == 1
    assert by_email[b]["job_count"] == 1


def test_c_avg_weekly_uses_active_iso_weeks_not_calendar_span():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    # Week 32 (Mon 3 Aug) and week 34 (Mon 17 Aug). Week 33 has no activity.
    orders = [
        _order("PO-W1", splits=[_split(op, name, 600, 600, day=3)], timeline=[
            _qty_note(name, 600, 600, 3, op),
        ]),
        _order("PO-W2", splits=[_split(op, name, 400, 400, day=17)], timeline=[
            _qty_note(name, 400, 400, 17, op),
        ]),
    ]
    out = _run(orders)
    row = out["operators"][0]
    assert row["completed"] == 1000
    assert row["active_weeks"] == 2
    assert row["avg_weekly_qty"] == 500.0
    assert iso_week_key(date(2026, 8, 3)) != iso_week_key(date(2026, 8, 17))


def test_c_period_shorter_than_one_week_is_safe():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-SHORT", splits=[_split(op, name, 900, 900, day=4)], timeline=[
            _qty_note(name, 300, 900, 3, op),
            _qty_note(name, 600, 900, 4, op),
            _qty_note(name, 900, 900, 5, op),
        ]),
    ]
    out = _run(orders, date_from=date(2026, 8, 3), date_to=date(2026, 8, 5))
    row = out["operators"][0]
    assert row["completed"] == 900
    assert row["active_weeks"] == 1
    assert row["avg_weekly_qty"] == 900.0
    assert row["active_days"] == 3
    assert row["avg_daily_qty"] == 300.0


def test_d_avg_daily_uses_active_days_not_30_or_31():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    notes = [_qty_note(name, 1000 * i, 10000, i, op) for i in range(1, 11)]
    orders = [
        _order("PO-DAYS", splits=[_split(op, name, 10000, 10000, day=10)], timeline=notes),
    ]
    out = _run(orders)  # full August = 31 calendar days
    row = out["operators"][0]
    assert row["completed"] == 10000
    assert row["active_days"] == 10
    assert row["avg_daily_qty"] == 1000.0
    assert row["avg_daily_qty"] != round(10000 / 31, 2)


def test_e_assigned_zero_is_safe():
    assert achievement_pct(0, 0) == 0.0
    assert achievement_pct(100, 0) == 0.0
    assert remaining_qty(0, 0) == 0
    assert math.isfinite(achievement_pct(1, 0))

    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-ZERO", splits=[_split(op, name, 0, 0, day=5)], timeline=[]),
    ]
    out = _run(orders)
    assert out["operators"], "assigned-0 split still listed when in period"
    row = out["operators"][0]
    assert row["assigned"] == 0
    assert row["completed"] == 0
    assert row["remaining"] == 0
    assert row["achievement"] == 0.0
    assert math.isfinite(row["achievement"])
    assert math.isfinite(row["avg_weekly_qty"])
    assert math.isfinite(row["avg_daily_qty"])


def test_completed_ignores_po_qty_and_assigned_as_selesai():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-NOT-PO-QTY",
            total_quantity=8000,
            splits=[_split(op, name, 3000, 2500, day=5)],
            timeline=[_qty_note(name, 2500, 3000, 5, op)],
        ),
    ]
    out = _run(orders)
    row = out["operators"][0]
    assert row["completed"] == 2500
    assert row["assigned"] == 3000
    assert row["completed"] != 8000
    assert row["completed"] != 3000


def test_customer_and_item_filters():
    a = "op.a@eternalpackaging.com"
    orders = [
        _order("PO-C1", customer="Alpha", item="Karton", splits=[_split(a, "A", 100, 100, day=5)],
               timeline=[_qty_note("A", 100, 100, 5, a)]),
        _order("PO-C2", customer="Beta", item="Paperbag", splits=[_split(a, "A", 200, 200, day=6)],
               timeline=[_qty_note("A", 200, 200, 6, a)]),
    ]
    only_alpha = _run(orders, customer="Alpha")
    assert only_alpha["operators"][0]["completed"] == 100
    only_bag = _run(orders, item="Paperbag")
    assert only_bag["operators"][0]["completed"] == 200


def test_resolve_period_month_and_custom_swap():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    start, end, preset = resolve_period("month", now=now)
    assert preset == "month"
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 31)
    start, end, preset = resolve_period("week", now=now)
    assert start.weekday() == 0
    assert (end - start).days == 6
    start, end, preset = resolve_period("today", now=now)
    assert start == end == wib_today(now)
    start, end, preset = resolve_period("custom", "2026-08-10", "2026-08-01")
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 10)
    assert preset == "custom"
