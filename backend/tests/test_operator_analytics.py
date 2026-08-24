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


def _order(po_id, customer="Cust A", item="Karton A", splits=None, timeline=None, total_quantity=99999,
           deadline=None, sample_files=None, sample_approval_history=None, sample_approved=False):
    return {
        "id": po_id,
        "po_number": po_id,
        "customer": customer,
        "total_quantity": total_quantity,
        "deadline": deadline,
        "line_items": [{"name": item, "item_code": "ITM", "quantity": total_quantity}],
        "operator_splits": splits or [],
        "timeline": timeline or [],
        "sample_files": sample_files or [],
        "sample_approval_history": sample_approval_history or [],
        "sample_approved": sample_approved,
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


# ---------- Batch 2: remaining / workload ----------

def test_batch2_a_remaining_5000_3500():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-REM", splits=[_split(op, name, 5000, 3500, day=10, split_id="r1")], timeline=[
            _qty_note(name, 3500, 5000, 10, op),
        ]),
    ]
    row = _run(orders)["operators"][0]
    assert row["assigned"] == 5000
    assert row["completed"] == 3500
    assert row["remaining"] == 1500
    assert row["active_jobs"] == 1
    assert row["completed_jobs"] == 0


def test_batch2_b_completed_remaining_zero():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-DONE", splits=[_split(op, name, 5000, 5000, day=10, split_id="d1")], timeline=[
            _qty_note(name, 5000, 5000, 10, op),
        ]),
    ]
    row = _run(orders)["operators"][0]
    assert row["remaining"] == 0
    assert row["completed_jobs"] == 1
    assert row["active_jobs"] == 0


def test_batch2_c_invalid_completed_over_assigned_remaining_not_negative():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    assert remaining_qty(1000, 1200) == 0
    orders = [
        _order("PO-OVER", splits=[_split(op, name, 1000, 1200, day=10, split_id="x1")], timeline=[
            _qty_note(name, 1200, 1000, 10, op),
        ]),
    ]
    row = _run(orders)["operators"][0]
    assert row["remaining"] == 0
    assert row["remaining"] >= 0
    assert row["completed_jobs"] == 1


def test_batch2_multi_operator_remaining_no_double_count():
    a, b, c = "a@x.com", "b@x.com", "c@x.com"
    orders = [
        _order(
            "PO-3000",
            total_quantity=3000,
            splits=[
                _split(a, "A", 1000, 700, day=5, split_id="sa"),
                _split(b, "B", 1000, 1000, day=5, split_id="sb"),
                _split(c, "C", 1000, 500, day=5, split_id="sc"),
            ],
            timeline=[
                _qty_note("A", 700, 1000, 5, a),
                _qty_note("B", 1000, 1000, 5, b),
                _qty_note("C", 500, 1000, 5, c),
            ],
        ),
    ]
    by = {r["email"]: r for r in _run(orders)["operators"]}
    assert by[a]["remaining"] == 300
    assert by[b]["remaining"] == 0
    assert by[c]["remaining"] == 500
    assert by[a]["active_jobs"] == 1
    assert by[b]["completed_jobs"] == 1
    assert by[c]["active_jobs"] == 1
    assert _run(orders)["kpis"]["total_completed"] == 2200


def test_batch2_progress_updates_are_one_assignment_job():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-PROG", splits=[_split(op, name, 3000, 2500, day=10)], timeline=[
            _qty_note(name, 1000, 3000, 8, op),
            _qty_note(name, 2000, 3000, 9, op),
            _qty_note(name, 2500, 3000, 10, op),
        ]),
    ]
    row = _run(orders)["operators"][0]
    assert row["active_jobs"] + row["completed_jobs"] == 1
    assert row["job_count"] == 1


# ---------- Batch 2: on-time ----------

def test_batch2_d_on_time():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-OT",
            deadline="2026-08-10",
            splits=[_split(op, name, 100, 100, day=9, split_id="ot")],
            timeline=[_qty_note(name, 100, 100, 9, op)],
        ),
    ]
    row = _run(orders, now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc))["operators"][0]
    assert row["on_time"] == 1
    assert row["late"] == 0
    assert row["on_time_pct"] == 100.0


def test_batch2_e_late():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-LATE",
            deadline="2026-08-10",
            splits=[_split(op, name, 100, 100, day=12, split_id="lt")],
            timeline=[_qty_note(name, 100, 100, 12, op)],
        ),
    ]
    row = _run(orders, now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc))["operators"][0]
    assert row["late"] == 1
    assert row["on_time"] == 0
    assert row["on_time_pct"] == 0.0


def test_batch2_f_overdue_active():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-OV",
            deadline="2026-08-10",
            splits=[_split(op, name, 100, 40, day=5, split_id="ov")],
            timeline=[_qty_note(name, 40, 100, 5, op)],
        ),
    ]
    row = _run(orders, now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc))["operators"][0]
    assert row["overdue_active"] == 1
    assert row["completed_jobs"] == 0
    assert row["on_time_pct"] is None


def test_batch2_g_no_deadline_excluded_from_ontime_pct():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-ND",
            deadline=None,
            splits=[_split(op, name, 100, 100, day=9, split_id="nd")],
            timeline=[_qty_note(name, 100, 100, 9, op)],
        ),
        _order(
            "PO-HAS",
            deadline="2026-08-10",
            splits=[_split(op, name, 50, 50, day=9, split_id="has")],
            timeline=[_qty_note(name, 50, 50, 9, op)],
        ),
    ]
    row = _run(orders, now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc))["operators"][0]
    assert row["completed_jobs"] == 2
    assert row["on_time_basis"] == 1
    assert row["on_time"] == 1
    assert row["on_time_pct"] == 100.0


def test_batch2_ontime_pct_na_when_no_deadline_jobs():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order(
            "PO-NONE",
            deadline="",
            splits=[_split(op, name, 100, 100, day=9)],
            timeline=[_qty_note(name, 100, 100, 9, op)],
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["on_time_pct"] is None
    assert math.isfinite(row["achievement"])


def test_batch2_multi_operator_ontime_attributed_per_assignment():
    a, b = "a@x.com", "b@x.com"
    orders = [
        _order(
            "PO-SHARE",
            deadline="2026-08-10",
            splits=[
                _split(a, "A", 1000, 1000, day=9, split_id="a1"),
                _split(b, "B", 1000, 1000, day=12, split_id="b1"),
            ],
            timeline=[
                _qty_note("A", 1000, 1000, 9, a),
                _qty_note("B", 1000, 1000, 12, b),
            ],
        ),
    ]
    by = {r["email"]: r for r in _run(orders)["operators"]}
    assert by[a]["on_time"] == 1
    assert by[b]["late"] == 1
    assert by[a]["on_time_pct"] == 100.0
    assert by[b]["on_time_pct"] == 0.0


# ---------- Batch 2: speed ----------

def test_batch2_speed_uses_active_days_not_invented_hours():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-SPD", splits=[_split(op, name, 4900, 4900, day=10)], timeline=[
            _qty_note(name, 2450, 4900, 4, op),
            _qty_note(name, 4900, 4900, 5, op),
        ]),
    ]
    row = _run(orders)["operators"][0]
    assert row["avg_daily_qty"] == 2450.0
    assert row["pcs_per_hour"] is None
    assert row["avg_completion_days"] is not None
    assert row["avg_completion_days"] >= 0


def test_batch2_completion_duration_na_without_timestamps():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    split = {
        "id": "no-ts",
        "operator_email": op,
        "operator_name": name,
        "quantity": 100,
        "qty_completed": 100,
        "work_date": "2026-08-05",
        # no updated_at, no qty notes → start from work_date, no end timestamp
    }
    orders = [_order("PO-NOTS", splits=[split], timeline=[])]
    out = _run(orders)
    # work_date in period includes the split; without end ts duration is N/A
    if out["operators"]:
        assert out["operators"][0]["pcs_per_hour"] is None


# ---------- Batch 2: sample ----------

def _sample_file(email, day, role="operator", name="Op"):
    return {
        "uploaded_by": email,
        "uploaded_by_role": role,
        "uploaded_by_name": name,
        "uploaded_at": _at(day),
    }


def _hist(action, day):
    return {"action": action, "at": _at(day)}


def test_batch2_h_sample_approved():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-SA",
            splits=[_split(op, "A", 100, 100, day=5)],
            timeline=[_qty_note("A", 100, 100, 5, op)],
            sample_files=[_sample_file(op, 4)],
            sample_approval_history=[_hist("approve", 5)],
            sample_approved=True,
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["sample_jobs"] == 1
    assert row["sample_submissions"] == 1
    assert row["samples_approved"] == 1
    assert row["samples_rejected"] == 0
    assert row["samples_pending"] == 0
    assert row["first_pass_rate"] == 100.0


def test_batch2_i_sample_rejected():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-SR",
            splits=[_split(op, "A", 100, 0, day=5)],
            timeline=[],
            sample_files=[_sample_file(op, 4)],
            sample_approval_history=[_hist("reject", 5)],
            sample_approved=False,
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["samples_rejected"] == 1
    assert row["samples_approved"] == 0
    assert row["sample_jobs"] == 1


def test_batch2_j_sample_pending():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-SP",
            splits=[_split(op, "A", 100, 0, day=5)],
            timeline=[],
            sample_files=[_sample_file(op, 4)],
            sample_approval_history=[],
            sample_approved=False,
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["samples_pending"] == 1
    assert row["first_pass_rate"] is None


def test_batch2_k_sample_resubmission_one_job_not_first_pass():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-RS",
            splits=[_split(op, "A", 100, 100, day=12)],
            timeline=[_qty_note("A", 100, 100, 12, op)],
            sample_files=[_sample_file(op, 4), _sample_file(op, 8)],
            sample_approval_history=[
                _hist("reject", 5),
                _hist("reset-on-new-upload", 8),
                _hist("approve", 9),
            ],
            sample_approved=True,
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["sample_jobs"] == 1
    assert row["sample_submissions"] == 2
    assert row["samples_approved"] == 1
    assert row["first_pass_approved"] == 0
    assert row["first_pass_decided"] == 1
    assert row["first_pass_rate"] == 0.0
    assert row["job_count"] == 1


def test_batch2_sample_not_attributed_to_designer():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-DES",
            splits=[_split(op, "A", 100, 0, day=5)],
            sample_files=[_sample_file("desainer@x.com", 4, role="designer", name="Desainer")],
            sample_approval_history=[_hist("approve", 5)],
            sample_approved=True,
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["sample_jobs"] == 0
    assert row["samples_approved"] == 0


def test_batch2_filters_apply_to_workload_and_sample():
    a = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-A", customer="Alpha", item="Karton",
            splits=[_split(a, "A", 100, 50, day=5)],
            timeline=[_qty_note("A", 50, 100, 5, a)],
            sample_files=[_sample_file(a, 5)],
            sample_approved=False,
        ),
        _order(
            "PO-B", customer="Beta", item="Paperbag",
            splits=[_split(a, "A", 200, 200, day=6)],
            timeline=[_qty_note("A", 200, 200, 6, a)],
            sample_files=[_sample_file(a, 6)],
            sample_approval_history=[_hist("approve", 6)],
            sample_approved=True,
        ),
    ]
    only_alpha = _run(orders, customer="Alpha")["operators"][0]
    assert only_alpha["assigned"] == 100
    assert only_alpha["sample_jobs"] == 1
    assert only_alpha["samples_pending"] == 1
    only_bag = _run(orders, item="Paperbag")["operators"][0]
    assert only_bag["completed"] == 200
    assert only_bag["samples_approved"] == 1
    assert only_bag["sample_jobs"] == 1

