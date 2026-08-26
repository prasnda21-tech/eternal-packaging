"""Isolated scheduling vs Helper target/actual dependency."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    helper_over_prepared_qty,
    helper_target_qty,
    operator_assignment_cap,
    validate_operator_assignment_total,
    validate_print_completed_total,
)


def _order(helper=2000, splits=None, print_qty=10000):
    return {
        "line_items": [{"name": "Cup", "quantity": print_qty, "production_process": "print"}],
        "helper_prep": {
            "qty_prepared": helper,
            "qty_required": print_qty,
            "entries": [{"qty": helper, "line_idx": 0}],
        },
        "operator_splits": splits or [],
    }


def test_case_a_schedule_full_when_helper_low():
    o = _order(helper=2000)
    helper_before = copy.deepcopy(o["helper_prep"])
    assert operator_assignment_cap(o) == 10000
    assert validate_operator_assignment_total(10000, 10000, 2000) is None
    o["operator_splits"] = [
        {"operator_email": "a@x.com", "quantity": 6000, "line_idx": 0},
        {"operator_email": "b@x.com", "quantity": 4000, "line_idx": 0},
    ]
    assert sum(s["quantity"] for s in o["operator_splits"]) == 10000
    assert helper_target_qty(o) == 10000
    assert o["helper_prep"]["qty_prepared"] == 2000
    assert o["helper_prep"] == helper_before
    assert max(0, helper_target_qty(o) - 2000) == 8000


def test_case_b_target_schedule_actual_entries():
    o = _order(helper=5000, splits=[
        {"quantity": 6000, "line_idx": 0},
        {"quantity": 4000, "line_idx": 0},
    ])
    o["helper_prep"]["entries"] = [{"qty": 3000, "line_idx": 0}, {"qty": 2000, "line_idx": 0}]
    assert helper_target_qty(o) == 10000
    assert o["helper_prep"]["qty_prepared"] == 5000
    assert max(0, helper_target_qty(o) - 5000) == 5000


def test_case_c_schedule_edit_updates_target_keeps_actual():
    o = _order(helper=5000, splits=[
        {"quantity": 6000, "line_idx": 0},
        {"quantity": 4000, "line_idx": 0},
    ])
    helper_before = copy.deepcopy(o["helper_prep"])
    o["operator_splits"] = [
        {"quantity": 3000, "line_idx": 0},
        {"quantity": 5000, "line_idx": 0},
    ]
    assert helper_target_qty(o) == 8000
    assert o["helper_prep"]["qty_prepared"] == 5000
    assert o["helper_prep"] == helper_before
    assert max(0, 8000 - 5000) == 3000


def test_case_d_over_prepared_not_clamped():
    o = _order(helper=8000, splits=[{"quantity": 10000, "line_idx": 0}])
    helper_before = copy.deepcopy(o["helper_prep"])
    entries_before = list(o["helper_prep"]["entries"])
    o["operator_splits"] = [{"quantity": 6000, "line_idx": 0}]
    assert helper_target_qty(o) == 6000
    assert o["helper_prep"]["qty_prepared"] == 8000
    assert o["helper_prep"] == helper_before
    assert o["helper_prep"]["entries"] == entries_before
    assert helper_over_prepared_qty(o) == 2000


def test_case_e_operator_execution_still_capped_by_helper():
    o = _order(helper=5000, splits=[{"quantity": 10000, "line_idx": 0}])
    assert validate_print_completed_total(5000, o) is None
    assert validate_print_completed_total(7000, o) is not None


def test_case_f_schedule_reassign_does_not_mutate_helper():
    o = _order(helper=5000, splits=[
        {"operator_email": "a@x.com", "quantity": 5000, "qty_completed": 3000, "line_idx": 0},
    ])
    helper_before = copy.deepcopy(o["helper_prep"])
    o["operator_splits"] = [
        {"operator_email": "a@x.com", "quantity": 3000, "qty_completed": 3000, "line_idx": 0},
        {"operator_email": "b@x.com", "quantity": 2000, "qty_completed": 0, "line_idx": 0},
    ]
    assert o["helper_prep"] == helper_before
    assert helper_target_qty(o) == 5000
    assert o["helper_prep"]["qty_prepared"] == 5000
