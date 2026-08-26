"""Partial cancel / reassign Operator. Helper prepared stays independent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operator_schedule import apply_operator_assignments, cancel_split_remaining, pending_splits_for_user, split_remaining
from quantity_rules import (
    helper_over_prepared_qty,
    helper_target_for_line,
    helper_target_qty,
    validate_print_completed_total,
)


def _order(helper=10000, splits=None):
    return {
        "id": "po-1",
        "line_items": [{"name": "Cup", "quantity": 10000, "production_process": "print", "accurate_item_id": "104"}],
        "helper_prep": {"qty_prepared": helper, "qty_required": 10000},
        "operator_splits": splits or [],
        "timeline": [],
        "current_stage": 8,
    }


def test_partial_cancel_keeps_completed_and_releases_remaining():
    o = _order(splits=[{
        "id": "a", "operator_email": "a@x.com", "operator_name": "A",
        "quantity": 5000, "qty_completed": 3000, "line_idx": 0, "progress_entries": [],
    }])
    helper_before = o["helper_prep"]
    found = cancel_split_remaining(o, "a", 2000, {"email": "sched@x.com", "name": "Sched"}, "2026-08-26T10:00:00+07:00")
    assert found["qty_completed"] == 3000
    assert found["quantity"] == 3000
    assert found["qty_cancelled"] == 2000
    assert split_remaining(found) == 0
    assert o["helper_prep"] == helper_before
    assert found["cancel_events"][0]["assignment_id"] == "a"
    assert found["cancel_events"][0]["cancel_qty"] == 2000


def test_cannot_cancel_completed_qty():
    o = _order(splits=[{
        "id": "a", "operator_email": "a@x.com", "operator_name": "A",
        "quantity": 5000, "qty_completed": 3000, "line_idx": 0,
    }])
    try:
        cancel_split_remaining(o, "a", 3000, {"email": "s"}, "t")
        assert False, "should reject"
    except ValueError as e:
        assert "melebihi sisa" in str(e)


def test_reassign_remaining_to_b_no_double_count():
    o = _order(splits=[{
        "id": "a", "operator_email": "a@x.com", "operator_name": "A",
        "quantity": 5000, "qty_completed": 3000, "line_idx": 0, "progress_entries": [{"qty": 3000}],
    }])
    helper_before = dict(o["helper_prep"])
    cancel_split_remaining(o, "a", 2000, {"email": "s"}, "t")
    new_splits, _notes = apply_operator_assignments(
        o,
        [
            {"id": "a", "operator_email": "a@x.com", "operator_name": "A", "quantity": 3000, "line_idx": 0},
            {"operator_email": "b@x.com", "operator_name": "B", "quantity": 2000, "line_idx": 0},
        ],
        {"email": "s"},
        "t2",
        new_id_fn=lambda: "b",
    )
    by = {s["operator_email"]: s for s in new_splits}
    assert by["a@x.com"]["qty_completed"] == 3000
    assert by["a@x.com"]["quantity"] == 3000
    assert by["b@x.com"]["quantity"] == 2000
    assert by["b@x.com"]["qty_completed"] == 0
    assert o["helper_prep"]["qty_prepared"] == 10000
    assert o["helper_prep"] == helper_before
    assert sum(s["quantity"] for s in new_splits) == 5000


def test_tugas_saya_only_own_assignment():
    o = {
        "operator_splits": [
            {"id": "a", "operator_email": "a@x.com", "quantity": 0, "qty_completed": 0},
            {"id": "b", "operator_email": "b@x.com", "quantity": 5000, "qty_completed": 0},
        ]
    }
    assert pending_splits_for_user(o, "a@x.com") == []
    mine = pending_splits_for_user(o, "b@x.com")
    assert len(mine) == 1
    assert mine[0]["id"] == "b"


def test_schedule_does_not_mutate_helper_on_assign():
    o = _order(helper=10000, splits=[])
    n = {"i": 0}
    def nid():
        n["i"] += 1
        return f"n{n['i']}"
    apply_operator_assignments(
        o,
        [
            {"operator_email": "a@x.com", "operator_name": "A", "quantity": 5000, "line_idx": 0},
            {"operator_email": "b@x.com", "operator_name": "B", "quantity": 3000, "line_idx": 0},
        ],
        {"email": "s"},
        "t",
        new_id_fn=nid,
    )
    assert o["helper_prep"]["qty_prepared"] == 10000


def test_case_a_schedule_full_when_helper_low():
    o = _order(helper=2000, splits=[])
    helper_before = dict(o["helper_prep"])
    n = {"i": 0}
    def nid():
        n["i"] += 1
        return f"n{n['i']}"
    apply_operator_assignments(
        o,
        [
            {"operator_email": "a@x.com", "operator_name": "A", "quantity": 6000, "line_idx": 0},
            {"operator_email": "b@x.com", "operator_name": "B", "quantity": 4000, "line_idx": 0},
        ],
        {"email": "s"},
        "t",
        new_id_fn=nid,
    )
    assert sum(s["quantity"] for s in o["operator_splits"]) == 10000
    assert helper_target_qty(o) == 10000
    assert o["helper_prep"]["qty_prepared"] == 2000
    assert o["helper_prep"] == helper_before
    assert max(0, helper_target_qty(o) - 2000) == 8000


def test_case_b_helper_actual_from_entries_not_schedule():
    o = _order(helper=5000, splits=[
        {"id": "a", "operator_email": "a@x.com", "quantity": 6000, "qty_completed": 0, "line_idx": 0},
        {"id": "b", "operator_email": "b@x.com", "quantity": 4000, "qty_completed": 0, "line_idx": 0},
    ])
    o["helper_prep"]["entries"] = [{"qty": 3000, "line_idx": 0}, {"qty": 2000, "line_idx": 0}]
    o["helper_prep"]["qty_prepared"] = 5000
    assert helper_target_qty(o) == 10000
    assert o["helper_prep"]["qty_prepared"] == 5000
    assert max(0, helper_target_qty(o) - 5000) == 5000


def test_case_c_schedule_edit_updates_target_keeps_actual():
    o = _order(helper=5000, splits=[
        {"id": "a", "operator_email": "a@x.com", "operator_name": "A", "quantity": 6000, "qty_completed": 0, "line_idx": 0},
        {"id": "b", "operator_email": "b@x.com", "operator_name": "B", "quantity": 4000, "qty_completed": 0, "line_idx": 0},
    ])
    helper_before = dict(o["helper_prep"])
    apply_operator_assignments(
        o,
        [
            {"id": "a", "operator_email": "a@x.com", "operator_name": "A", "quantity": 3000, "line_idx": 0},
            {"id": "b", "operator_email": "b@x.com", "operator_name": "B", "quantity": 5000, "line_idx": 0},
        ],
        {"email": "s"},
        "t",
        new_id_fn=lambda: "x",
    )
    assert helper_target_qty(o) == 8000
    assert o["helper_prep"]["qty_prepared"] == 5000
    assert o["helper_prep"] == helper_before
    assert max(0, 8000 - 5000) == 3000


def test_case_d_over_prepared_not_clamped():
    o = _order(helper=8000, splits=[
        {"id": "a", "operator_email": "a@x.com", "operator_name": "A", "quantity": 10000, "qty_completed": 0, "line_idx": 0},
    ])
    helper_before = dict(o["helper_prep"])
    apply_operator_assignments(
        o,
        [{"id": "a", "operator_email": "a@x.com", "operator_name": "A", "quantity": 6000, "line_idx": 0}],
        {"email": "s"},
        "t",
        new_id_fn=lambda: "x",
    )
    assert helper_target_qty(o) == 6000
    assert o["helper_prep"]["qty_prepared"] == 8000
    assert o["helper_prep"] == helper_before
    assert helper_over_prepared_qty(o) == 2000
    assert helper_target_for_line(o, 0) == 6000


def test_case_e_operator_execution_still_capped_by_helper():
    o = _order(helper=5000, splits=[
        {"id": "a", "operator_email": "a@x.com", "quantity": 10000, "qty_completed": 0, "line_idx": 0},
    ])
    assert validate_print_completed_total(5000, o) is None
    assert validate_print_completed_total(7000, o) is not None


def test_tugas_operator_a_does_not_see_b():
    o = {
        "operator_splits": [
            {"id": "a", "operator_email": "a@x.com", "quantity": 0, "qty_completed": 0},
            {"id": "b", "operator_email": "b@x.com", "quantity": 5000, "qty_completed": 0},
        ]
    }
    assert pending_splits_for_user(o, "a@x.com") == []
    assert pending_splits_for_user(o, "b@x.com")[0]["id"] == "b"
