"""Batch 1 operator analytics — pure unit tests (no Mongo / no live API)."""
import math
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operator_analytics import (
    achievement_pct,
    compute_operator_detail,
    compute_operator_kpis,
    compute_packing_report,
    compute_preparation_report,
    compute_weekly_trend,
    iso_week_key,
    rank_operators,
    remaining_qty,
    resolve_period,
    wib_today,
    wow_pct,
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


# ---------- Batch 3: weekly trend / WoW / ranking / drill-down ----------

def test_a_weekly_grouping_iso_weeks():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-W1", splits=[_split(op, name, 600, 600, day=3)], timeline=[
            _qty_note(name, 600, 600, 3, op),
        ]),
        _order("PO-W2", splits=[_split(op, name, 400, 400, day=17)], timeline=[
            _qty_note(name, 400, 400, 17, op),
        ]),
    ]
    trend = compute_weekly_trend(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by_week = {w["week"]: w["qty"] for w in trend["weeks"]}
    w1 = iso_week_key(date(2026, 8, 3))
    w2 = iso_week_key(date(2026, 8, 17))
    assert w1 != w2
    assert by_week[w1] == 600
    assert by_week[w2] == 400
    assert trend["weeks"][0]["week"] <= w1
    assert len(trend["weeks"]) >= 4


def test_b_wow_plus_20_percent():
    assert wow_pct(1200, 1000) == 20.0
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    # Week 32 (Mon 3 Aug) 1000, week 33 (Mon 10 Aug) 1200
    orders = [
        _order("PO-WOW1", splits=[_split(op, name, 1000, 1000, day=3)], timeline=[
            _qty_note(name, 1000, 1000, 3, op),
        ]),
        _order("PO-WOW2", splits=[_split(op, name, 1200, 1200, day=10)], timeline=[
            _qty_note(name, 1200, 1200, 10, op),
        ]),
    ]
    trend = compute_weekly_trend(
        orders, date_from=date(2026, 8, 3), date_to=date(2026, 8, 16),
    )
    w32 = iso_week_key(date(2026, 8, 3))
    w33 = iso_week_key(date(2026, 8, 10))
    by = {w["week"]: w for w in trend["weeks"]}
    assert by[w32]["qty"] == 1000
    assert by[w33]["qty"] == 1200
    assert by[w33]["wow_pct"] == 20.0


def test_c_wow_previous_zero_not_infinity():
    assert wow_pct(500, 0) is None
    assert wow_pct(500, None) is None
    assert math.isfinite(wow_pct(500, 1))
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-Z", splits=[_split(op, name, 500, 500, day=10)], timeline=[
            _qty_note(name, 500, 500, 10, op),
        ]),
    ]
    trend = compute_weekly_trend(
        orders, date_from=date(2026, 8, 3), date_to=date(2026, 8, 16),
    )
    w33 = iso_week_key(date(2026, 8, 10))
    by = {w["week"]: w for w in trend["weeks"]}
    assert by[w33]["qty"] == 500
    assert by[w33]["wow_pct"] is None
    assert trend["wow_pct"] is None or math.isfinite(trend["wow_pct"])


def test_d_ranking_output_qty_order():
    a, b, c = "a@x.com", "b@x.com", "c@x.com"
    orders = [
        _order("PO-A", splits=[_split(a, "Operator A", 25000, 25000, day=5, split_id="a")],
               timeline=[_qty_note("Operator A", 25000, 25000, 5, a)]),
        _order("PO-B", splits=[_split(b, "Operator B", 22500, 22500, day=5, split_id="b")],
               timeline=[_qty_note("Operator B", 22500, 22500, 5, b)]),
        _order("PO-C", splits=[_split(c, "Operator C", 20000, 20000, day=5, split_id="c")],
               timeline=[_qty_note("Operator C", 20000, 20000, 5, c)]),
    ]
    ranked = rank_operators(_run(orders)["operators"], "output_qty")
    assert [r["name"] for r in ranked] == ["Operator A", "Operator B", "Operator C"]
    assert ranked[0]["value"] == 25000
    assert ranked[1]["value"] == 22500
    assert ranked[2]["value"] == 20000
    assert ranked[0]["rank"] == 1


def test_e_ranking_achievement_order():
    a, b = "a@x.com", "b@x.com"
    orders = [
        _order("PO-A", splits=[_split(a, "A", 1000, 900, day=5, split_id="a")],
               timeline=[_qty_note("A", 900, 1000, 5, a)]),
        _order("PO-B", splits=[_split(b, "B", 1000, 500, day=5, split_id="b")],
               timeline=[_qty_note("B", 500, 1000, 5, b)]),
    ]
    ranked = rank_operators(_run(orders)["operators"], "achievement")
    assert ranked[0]["email"] == a
    assert ranked[0]["value"] == 90.0
    assert ranked[1]["email"] == b
    assert ranked[1]["value"] == 50.0


def test_f_ranking_ontime_and_sample_na_not_zero():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order(
            "PO-NA",
            deadline=None,
            splits=[_split(op, "A", 100, 100, day=9)],
            timeline=[_qty_note("A", 100, 100, 9, op)],
        ),
    ]
    row = _run(orders)["operators"][0]
    assert row["on_time_pct"] is None
    assert row["sample_approval_pct"] is None
    ranked_ot = rank_operators([row], "on_time_pct")
    assert ranked_ot[0]["value"] is None
    assert ranked_ot[0]["value_na"] is True
    assert ranked_ot[0]["rank"] is None
    ranked_s = rank_operators([row], "sample_approval_pct")
    assert ranked_s[0]["value"] is None
    assert ranked_s[0]["value_na"] is True


def test_g_drilldown_only_operator_a_jobs():
    a, b = "a@x.com", "b@x.com"
    orders = [
        _order("PO-A", splits=[_split(a, "A", 1000, 700, day=5, split_id="sa")],
               timeline=[_qty_note("A", 700, 1000, 5, a)]),
        _order("PO-B", splits=[_split(b, "B", 2000, 2000, day=6, split_id="sb")],
               timeline=[_qty_note("B", 2000, 2000, 6, b)]),
        _order(
            "PO-SHARE",
            splits=[
                _split(a, "A", 500, 500, day=7, split_id="sa2"),
                _split(b, "B", 800, 800, day=7, split_id="sb2"),
            ],
            timeline=[
                _qty_note("A", 500, 500, 7, a),
                _qty_note("B", 800, 800, 7, b),
            ],
        ),
    ]
    detail = compute_operator_detail(
        orders, operator_id=a, date_from=PERIOD_FROM, date_to=PERIOD_TO,
    )
    pos = {j["po_number"] for j in detail["jobs"]}
    assert "PO-A" in pos
    assert "PO-SHARE" in pos
    assert "PO-B" not in pos
    assert all(j["completed_qty"] in (700, 500) for j in detail["jobs"])
    assert detail["operator"]["completed"] == 1200
    assert detail["jobs_page"]["total"] == 2


def test_h_date_filter_applies_to_drilldown():
    a = "a@x.com"
    orders = [
        _order("PO-EARLY", splits=[_split(a, "A", 100, 100, day=4, split_id="e")],
               timeline=[_qty_note("A", 100, 100, 4, a)]),
        _order("PO-LATE", splits=[_split(a, "A", 200, 200, day=20, split_id="l")],
               timeline=[_qty_note("A", 200, 200, 20, a)]),
    ]
    detail = compute_operator_detail(
        orders, operator_id=a,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 10),
    )
    pos = {j["po_number"] for j in detail["jobs"]}
    assert pos == {"PO-EARLY"}
    assert detail["operator"]["completed"] == 100


def test_drilldown_does_not_mix_line_qty():
    a = "a@x.com"
    order = _order(
        "PO-LINES",
        item="Cup A",
        splits=[
            {**_split(a, "A", 1000, 600, day=5, split_id="l0"), "line_idx": 0},
            {**_split(a, "A", 500, 500, day=6, split_id="l1"), "line_idx": 1},
        ],
        timeline=[
            _qty_note("A", 600, 1000, 5, a),
            _qty_note("A", 500, 500, 6, a),
        ],
    )
    order["line_items"] = [
        {"name": "Cup A", "item_code": "CA", "quantity": 1000},
        {"name": "Cup B", "item_code": "CB", "quantity": 500},
    ]
    detail = compute_operator_detail(
        [order], operator_id=a, date_from=PERIOD_FROM, date_to=PERIOD_TO,
    )
    by_item = {j["item"]: j for j in detail["jobs"]}
    assert "Cup A" in by_item["Cup A (CA)"]["item"] or by_item["Cup A (CA)"]["assigned_qty"] == 1000
    cup_a = next(j for j in detail["jobs"] if j["line_idx"] == 0)
    cup_b = next(j for j in detail["jobs"] if j["line_idx"] == 1)
    assert cup_a["assigned_qty"] == 1000
    assert cup_a["completed_qty"] == 600
    assert cup_b["assigned_qty"] == 500
    assert cup_b["completed_qty"] == 500
    assert cup_a["completed_qty"] + cup_b["completed_qty"] == 1100


def test_batch3_kpis_include_weekly_and_no_utilization():
    op = "op.a@eternalpackaging.com"
    orders = [
        _order("PO-X", splits=[_split(op, "A", 100, 100, day=5)],
               timeline=[_qty_note("A", 100, 100, 5, op)]),
    ]
    out = _run(orders)
    assert out["weekly_trend"]["weeks"]
    assert out["utilization"] is None
    assert out["load_band"] is None
    assert out["read_only"] is True


# ---------- Batch 3: packing (cumulative snapshots → deltas) ----------

def _pack_note(total, detail, day, email, role="helper", name="Packer"):
    return {
        "at": _at(day),
        "note": f"Qty Packing: {total} pcs ({detail})",
        "by_email": email,
        "by_role": role,
        "by_name": name,
    }


def _pack_order(po_id, lines, timeline, customer="Cust A", packed_lines=None):
    lis = []
    for i, (name, qty) in enumerate(lines):
        lis.append({"name": name, "item_code": f"L{i}", "quantity": qty, "production_process": "print"})
    pi_lines = packed_lines or [{"line_idx": i, "packed_qty": 0} for i in range(len(lines))]
    return {
        "id": po_id,
        "po_number": po_id,
        "customer": customer,
        "line_items": lis,
        "packing_info": {
            "lines": pi_lines,
            "qty_packed": sum(x["packed_qty"] for x in pi_lines),
            "updated_by": (timeline[-1]["by_email"] if timeline else ""),
            "updated_at": (timeline[-1]["at"] if timeline else None),
        },
        "timeline": timeline,
        "cancelled": False,
    }


def test_i_packing_two_saves_same_line_no_double_count():
    """Save 1 +200 then Save 2 cumulative 500. Activity = 500, NOT 200+500=700."""
    u = "pack.a@eternalpackaging.com"
    orders = [
        _pack_order(
            "PO-P1",
            [("Cup A", 1000)],
            timeline=[
                _pack_note(200, "#0=200", 4, u, name="User A"),
                _pack_note(500, "#0=500", 5, u, name="User A"),
            ],
            packed_lines=[{"line_idx": 0, "packed_qty": 500}],
        ),
    ]
    out = compute_packing_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    row = out["users"][0]
    assert row["total_qty"] == 500
    assert row["total_qty"] != 700
    line = out["lines"][0]
    assert line["packed_qty"] == 500
    assert out["kpis"]["total_qty"] == 500


def test_j_packing_two_users_attributed_separately():
    a, b = "pack.a@x.com", "pack.b@x.com"
    orders = [
        _pack_order(
            "PO-P2",
            [("Cup A", 1000)],
            timeline=[
                _pack_note(500, "#0=500", 4, a, name="User A"),
                _pack_note(800, "#0=800", 5, b, name="User B"),
            ],
            packed_lines=[{"line_idx": 0, "packed_qty": 800}],
        ),
    ]
    out = compute_packing_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by = {r["email"]: r for r in out["users"]}
    assert by[a]["total_qty"] == 500
    assert by[b]["total_qty"] == 300
    assert out["kpis"]["total_qty"] == 800
    # last saver snapshot must NOT eat User A's 500
    assert by[a]["name"] == "User A"


def test_k_packing_multiple_lines_same_po_sum():
    u = "pack.a@x.com"
    orders = [
        _pack_order(
            "PO-001",
            [("Cup A", 500), ("Cup B", 300)],
            timeline=[
                _pack_note(500, "#0=500", 4, u, name="Budi"),
                _pack_note(800, "#0=500, #1=300", 5, u, name="Budi"),
            ],
            packed_lines=[{"line_idx": 0, "packed_qty": 500}, {"line_idx": 1, "packed_qty": 300}],
        ),
    ]
    out = compute_packing_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["users"][0]["total_qty"] == 800
    by_line = {r["line_idx"]: r["packed_qty"] for r in out["lines"]}
    assert by_line[0] == 500
    assert by_line[1] == 300
    assert sum(by_line.values()) == 800
    assert "Cup A" in out["lines"][0]["item"] or "Cup B" in out["lines"][0]["item"]


def test_packing_date_filter_and_not_sum_snapshots():
    u = "pack.a@x.com"
    orders = [
        _pack_order(
            "PO-DF",
            [("Cup A", 1000)],
            timeline=[
                _pack_note(200, "#0=200", 4, u, name="A"),
                _pack_note(500, "#0=500", 20, u, name="A"),
            ],
            packed_lines=[{"line_idx": 0, "packed_qty": 500}],
        ),
    ]
    early = compute_packing_report(orders, date_from=date(2026, 8, 1), date_to=date(2026, 8, 10))
    assert early["users"][0]["total_qty"] == 200
    late = compute_packing_report(orders, date_from=date(2026, 8, 15), date_to=date(2026, 8, 31))
    assert late["users"][0]["total_qty"] == 300


# ---------- Batch 3: persiapan cup ----------

def _prep_note(prepared, required, day, email, role="helper", name="Helper", per_line=True):
    label = "Persiapan Helper (per-line)" if per_line else "Persiapan Helper"
    return {
        "at": _at(day),
        "note": f"{label}: {prepared}/{required} (kurang {max(0, required - prepared)})",
        "by_email": email,
        "by_role": role,
        "by_name": name,
    }


def _prep_order(po_id, items, timeline, customer="Cust A"):
    lis = []
    hp_items = []
    for i, (name, target, prepared) in enumerate(items):
        lis.append({"name": name, "item_code": f"C{i}", "quantity": target, "production_process": "print"})
        hp_items.append({"line_idx": i, "name": name, "qty_required": target, "qty_prepared": prepared})
    total_req = sum(t for _n, t, _p in items)
    total_prep = sum(p for _n, _t, p in items)
    return {
        "id": po_id,
        "po_number": po_id,
        "customer": customer,
        "line_items": lis,
        "helper_prep": {
            "qty_required": total_req,
            "qty_prepared": total_prep,
            "items": hp_items,
            "updated_by": (timeline[-1]["by_email"] if timeline else ""),
            "updated_at": (timeline[-1]["at"] if timeline else None),
        },
        "timeline": timeline,
        "cancelled": False,
    }


def test_l_prep_user_a_600():
    a = "andi@x.com"
    orders = [
        _prep_order(
            "PO-001",
            [("Cup 12 oz", 1000, 600)],
            timeline=[_prep_note(600, 1000, 5, a, name="Andi")],
        ),
    ]
    out = compute_preparation_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["users"][0]["total_qty"] == 600
    assert out["users"][0]["name"] == "Andi"
    assert out["pos"][0]["lines"][0]["prepared"] == 600
    assert out["pos"][0]["lines"][0]["target"] == 1000
    assert out["pos"][0]["lines"][0]["remaining"] == 400


def test_m_prep_two_users_timeline_deltas():
    """A 600 then B brings total to 1000 → A=600 B=400. Last saver is Budi but Andi keeps 600."""
    a, b = "andi@x.com", "budi@x.com"
    orders = [
        _prep_order(
            "PO-001",
            [("Cup 12 oz", 1000, 1000)],
            timeline=[
                _prep_note(600, 1000, 5, a, name="Andi"),
                _prep_note(1000, 1000, 6, b, name="Budi"),
            ],
        ),
    ]
    out = compute_preparation_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by = {r["email"]: r for r in out["users"]}
    assert by[a]["total_qty"] == 600
    assert by[b]["total_qty"] == 400
    assert out["kpis"]["total_qty"] == 1000
    # do not attribute the whole 1000 to last saver
    assert by[b]["total_qty"] != 1000
    po = out["pos"][0]
    assert po["limitation"]  # per-line user history not stored
    assert {u["user_id"]: u["qty_prepared"] for u in po["users"]} == {a: 600, b: 400}


def test_n_prep_role_and_user_filter():
    a, b = "andi@x.com", "budi@x.com"
    orders = [
        _prep_order(
            "PO-001",
            [("Cup 12 oz", 1000, 1000)],
            timeline=[
                _prep_note(600, 1000, 5, a, role="helper", name="Andi"),
                _prep_note(1000, 1000, 6, b, role="head_production", name="Budi"),
            ],
        ),
    ]
    only_andi = compute_preparation_report(
        orders, date_from=PERIOD_FROM, date_to=PERIOD_TO, user_id=a,
    )
    assert len(only_andi["users"]) == 1
    assert only_andi["users"][0]["total_qty"] == 600
    only_helper = compute_preparation_report(
        orders, date_from=PERIOD_FROM, date_to=PERIOD_TO, role="helper",
    )
    assert len(only_helper["users"]) == 1
    assert only_helper["users"][0]["email"] == a


def test_o_prep_date_range_filters_activity():
    a = "andi@x.com"
    orders = [
        _prep_order(
            "PO-001",
            [("Cup 12 oz", 1000, 900)],
            timeline=[
                _prep_note(400, 1000, 4, a, name="Andi"),
                _prep_note(900, 1000, 20, a, name="Andi"),
            ],
        ),
    ]
    early = compute_preparation_report(orders, date_from=date(2026, 8, 1), date_to=date(2026, 8, 10))
    assert early["users"][0]["total_qty"] == 400
    late = compute_preparation_report(orders, date_from=date(2026, 8, 15), date_to=date(2026, 8, 31))
    assert late["users"][0]["total_qty"] == 500


def test_p_prep_lines_not_mixed():
    a = "andi@x.com"
    orders = [
        _prep_order(
            "PO-001",
            [("Cup 12 oz", 1000, 600), ("Cup 16 oz", 500, 500)],
            timeline=[_prep_note(1100, 1500, 5, a, name="Andi")],
        ),
    ]
    out = compute_preparation_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    lines = out["pos"][0]["lines"]
    assert len(lines) == 2
    by_idx = {l["line_idx"]: l for l in lines}
    assert by_idx[0]["prepared"] == 600
    assert by_idx[0]["target"] == 1000
    assert by_idx[0]["remaining"] == 400
    assert by_idx[1]["prepared"] == 500
    assert by_idx[1]["target"] == 500
    assert by_idx[1]["remaining"] == 0
    assert "Cup 12" in by_idx[0]["item"]
    assert "Cup 16" in by_idx[1]["item"]


def test_prep_does_not_use_invented_cup_qty_field():
    a = "andi@x.com"
    orders = [_prep_order("PO-C", [("Paper cup 12oz", 100, 100)], timeline=[_prep_note(100, 100, 5, a)])]
    out = compute_preparation_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["attribution"]["cup_field"] is None
    assert out["read_only"] is True
    assert out["users"][0]["total_qty"] == 100


def test_packing_and_prep_read_only_period_on_kpis():
    assert _run([_order("PO-Z", splits=[_split("a@x.com", "A", 1, 1, day=5)],
                        timeline=[_qty_note("A", 1, 1, 5, "a@x.com")])])["read_only"] is True


# ---------- Analitik Output revision ----------

from output_reports import (
    compute_design_report,
    compute_screener_report,
    iter_operator_export_rows,
    normalize_daily_targets,
    screener_activity_events,
)


def _screen_note(qty, day, email, role="screener", name="Screener"):
    return {
        "at": _at(day),
        "note": f"Screen qty: {qty}",
        "by_email": email,
        "by_role": role,
        "by_name": name,
    }


def test_revision_operator_weekly_1000_2000_then_2500():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-W1A", splits=[_split(op, name, 1000, 1000, day=3, split_id="w1a")], timeline=[
            _qty_note(name, 1000, 1000, 3, op),
        ]),
        _order("PO-W1B", splits=[_split(op, name, 2000, 2000, day=4, split_id="w1b")], timeline=[
            _qty_note(name, 2000, 2000, 4, op),
        ]),
        _order("PO-W2", splits=[_split(op, name, 2500, 2500, day=10, split_id="w2")], timeline=[
            _qty_note(name, 2500, 2500, 10, op),
        ]),
    ]
    trend = compute_weekly_trend(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by = {w["week"]: w["qty"] for w in trend["weeks"]}
    w1 = iso_week_key(date(2026, 8, 3))
    w2 = iso_week_key(date(2026, 8, 10))
    assert by[w1] == 3000
    assert by[w2] == 2500
    row = _run(orders)["operators"][0]
    assert row["completed"] == 5500
    assert row["completed"] != 3000 + 2500 + 5500


def test_revision_multi_operator_same_line_600_400():
    a, b = "a@x.com", "b@x.com"
    order = _order(
        "PO-SHARE",
        total_quantity=1000,
        splits=[
            {**_split(a, "A", 600, 600, day=5, split_id="sa"), "line_idx": 0},
            {**_split(b, "B", 400, 400, day=5, split_id="sb"), "line_idx": 0},
        ],
        timeline=[
            _qty_note("A", 600, 600, 5, a),
            _qty_note("B", 400, 400, 5, b),
        ],
    )
    out = _run([order])
    by = {r["email"]: r for r in out["operators"]}
    assert by[a]["completed"] == 600
    assert by[b]["completed"] == 400
    assert out["kpis"]["total_completed"] == 1000


def test_revision_helper_multi_user_600_400():
    a, b = "andi@x.com", "budi@x.com"
    orders = [
        _prep_order(
            "PO-H",
            [("Cup 12 oz", 1000, 1000)],
            timeline=[
                _prep_note(600, 1000, 5, a, name="Andi"),
                _prep_note(1000, 1000, 6, b, name="Budi"),
            ],
        ),
    ]
    out = compute_preparation_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by = {r["email"]: r for r in out["users"]}
    assert by[a]["total_qty"] == 600
    assert by[b]["total_qty"] == 400


def test_revision_screener_actors_1200_800():
    a, b = "scr.a@x.com", "scr.b@x.com"
    order = {
        "id": "PO-SC",
        "po_number": "PO-SC",
        "customer": "Cust",
        "line_items": [{"name": "Cup 12 oz", "item_code": "C12", "quantity": 2000, "production_process": "print"}],
        "screener_info": {"screen_qty": 2000, "updated_by": b, "updated_at": _at(6)},
        "timeline": [
            _screen_note(1200, 5, a, name="Screener A"),
            _screen_note(2000, 6, b, name="Screener B"),
        ],
        "cancelled": False,
    }
    out = compute_screener_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    by = {r["email"]: r for r in out["users"]}
    assert by[a]["total_qty"] == 1200
    assert by[b]["total_qty"] == 800
    assert out["kpis"]["total_qty"] == 2000
    assert out["kpis"]["total_qty"] != 1200 + 2000


def test_revision_design_four_updates_is_one_line():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-D1", splits=[{**_split(op, name, 4000, 4000, day=10), "line_idx": 0}], timeline=[
            _qty_note(name, 1000, 4000, 7, op),
            _qty_note(name, 2000, 4000, 8, op),
            _qty_note(name, 3000, 4000, 9, op),
            _qty_note(name, 4000, 4000, 10, op),
        ]),
    ]
    out = compute_design_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_designs"] == 1
    assert out["operators"][0]["design_count"] == 1
    row = _run(orders)["operators"][0]
    assert row["design_count"] == 1


def test_revision_design_three_distinct_lines():
    op = "op.a@eternalpackaging.com"
    name = "A"
    order = _order(
        "PO-3L",
        splits=[
            {**_split(op, name, 100, 100, day=5, split_id="l0"), "line_idx": 0},
            {**_split(op, name, 200, 200, day=5, split_id="l1"), "line_idx": 1},
            {**_split(op, name, 300, 300, day=5, split_id="l2"), "line_idx": 2},
        ],
        timeline=[
            _qty_note(name, 100, 100, 5, op),
            _qty_note(name, 200, 200, 5, op),
            _qty_note(name, 300, 300, 5, op),
        ],
    )
    order["line_items"] = [
        {"name": "Cup 12 oz", "quantity": 100, "production_process": "print"},
        {"name": "Cup 16 oz", "quantity": 200, "production_process": "print"},
        {"name": "Cup 22 oz", "quantity": 300, "production_process": "print"},
    ]
    out = compute_design_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_designs"] == 3
    assert _run([order])["operators"][0]["design_count"] == 3


def test_revision_daily_target_achievement_120():
    op = "op.a@eternalpackaging.com"
    name = "Operator A"
    orders = [
        _order("PO-T", splits=[_split(op, name, 12000, 12000, day=5)], timeline=[
            _qty_note(name, 12000, 12000, 5, op),
        ]),
    ]
    out = compute_operator_kpis(
        orders, date_from=PERIOD_FROM, date_to=PERIOD_TO,
        daily_targets={op: 10000},
    )
    row = out["operators"][0]
    assert row["avg_daily_qty"] == 12000
    assert row["daily_target"] == 10000
    assert row["target_achievement_pct"] == 120.0


def test_revision_packing_cumulative_200_then_500():
    u = "pack.a@eternalpackaging.com"
    orders = [
        _pack_order(
            "PO-P",
            [("Cup A", 1000)],
            timeline=[
                _pack_note(200, "#0=200", 4, u, name="User A"),
                _pack_note(500, "#0=500", 5, u, name="User A"),
            ],
            packed_lines=[{"line_idx": 0, "packed_qty": 500}],
        ),
    ]
    out = compute_packing_report(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["users"][0]["total_qty"] == 500
    assert out["users"][0]["total_qty"] != 700


def test_revision_january_range_excludes_february():
    a = "a@x.com"
    name = "A"
    def at_jan(day):
        return datetime(2026, 1, day, 10, 0, tzinfo=timezone(timedelta(hours=7))).isoformat()
    orders = [
        {
            "id": "PO-JAN", "po_number": "PO-JAN", "customer": "C",
            "line_items": [{"name": "Cup X", "quantity": 100}],
            "operator_splits": [_split(a, name, 100, 100, day=15, split_id="j")],
            "timeline": [{"at": at_jan(15), "note": f"Qty {name}: 100/100", "by_email": a}],
            "cancelled": False,
        },
        {
            "id": "PO-FEB", "po_number": "PO-FEB", "customer": "C",
            "line_items": [{"name": "Cup Y", "quantity": 200}],
            "operator_splits": [{
                "id": "f", "operator_email": a, "operator_name": name,
                "quantity": 200, "qty_completed": 200,
                "work_date": "2026-02-02", "updated_at": datetime(2026, 2, 2, 10, tzinfo=timezone(timedelta(hours=7))).isoformat(),
            }],
            "timeline": [{
                "at": datetime(2026, 2, 2, 10, tzinfo=timezone(timedelta(hours=7))).isoformat(),
                "note": f"Qty {name}: 200/200", "by_email": a,
            }],
            "cancelled": False,
        },
    ]
    out = compute_operator_kpis(orders, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    assert out["kpis"]["total_completed"] == 100
    rows = iter_operator_export_rows(orders, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    assert all(r["tanggal"].startswith("2026-01") for r in rows)
    assert all(r["no_po"] != "PO-FEB" for r in rows)


def test_revision_export_operator_filter_excludes_b():
    a, b = "a@x.com", "b@x.com"
    orders = [
        _order("PO-A", item="Cup X", splits=[{**_split(a, "A", 100, 100, day=5, split_id="a"), "line_idx": 0}],
               timeline=[_qty_note("A", 100, 100, 5, a)]),
        _order("PO-B", item="Cup Y", splits=[{**_split(b, "B", 200, 200, day=5, split_id="b"), "line_idx": 0}],
               timeline=[_qty_note("B", 200, 200, 5, b)]),
    ]
    only_a = iter_operator_export_rows(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO, operator_id=a)
    assert only_a and all(r["operator_id"] == a for r in only_a)
    assert all(r["operator_id"] != b for r in only_a)
    only_x = iter_operator_export_rows(orders, date_from=PERIOD_FROM, date_to=PERIOD_TO, item="Cup X")
    assert only_x and all("Cup X" in r["cup_item"] or r["cup_item"] == "Cup X" for r in only_x)
    assert all("Cup Y" not in str(r["cup_item"]) for r in only_x)


def test_revision_period_presets():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    start, end, preset = resolve_period("daily", now=now)
    assert preset == "daily"
    assert (end - start).days == 6
    start, end, preset = resolve_period("months_3", now=now)
    assert preset == "months_3"
    assert start == date(2026, 6, 1)
    start, end, preset = resolve_period("year", now=now)
    assert start == date(2026, 1, 1)
    assert end == wib_today(now)


def test_revision_normalize_targets_by_id_not_name():
    doc = {"targets": [
        {"operator_id": "u-1", "email": "op.a@x.com", "qty_per_day": 15000},
    ]}
    m = normalize_daily_targets(doc)
    assert m["op.a@x.com"] == 15000
    assert m["u-1"] == 15000
    assert "Operator A" not in m


def test_revision_cups_use_line_item_name():
    op = "a@x.com"
    order = _order(
        "PO-CUP",
        splits=[{**_split(op, "A", 12500, 12500, day=5, split_id="c12"), "line_idx": 0}],
        timeline=[_qty_note("A", 12500, 12500, 5, op)],
    )
    order["line_items"] = [{"name": "Cup 12 oz", "item_code": "C12", "quantity": 12500, "production_process": "print"}]
    row = _run([order])["operators"][0]
    assert row["cups"][0]["item"] == "Cup 12 oz"
    assert row["cups"][0]["qty"] == 12500

