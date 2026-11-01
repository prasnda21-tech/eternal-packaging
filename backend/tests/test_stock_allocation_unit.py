"""Focused unit tests for warehouse allocation recording (warnings only)."""
from services.stock_allocation import (
    attach_allocations_to_lines,
    normalize_allocations_for_line,
    realization_mismatch_warning,
    summarize_order_allocation_warnings,
    WARNING_EMPTY,
    WARNING_OVER,
    WARNING_UNDER,
)


ALLOWED = {
    "w1": {"id": "w1", "code": "WH1", "name": "Gudang 1", "branch_id": "b1"},
    "w2": {"id": "w2", "code": "WH2", "name": "Gudang 2", "branch_id": "b1"},
}


def test_split_one_item_two_warehouses():
    result = normalize_allocations_for_line(
        [{"warehouse_id": "w1", "qty": 4}, {"warehouse_id": "w2", "qty": 6}],
        line_id="ln1",
        ordered_qty=10,
        unit="PCS",
        branch_id="b1",
        allowed_warehouses=ALLOWED,
        master_item_id="m1",
        is_inventory=True,
    )
    assert result["status"] == "complete"
    assert result["allocated_total"] == 10
    assert len(result["stock_allocations"]) == 2
    assert result["complete"] is True
    assert result["warnings"] == []


def test_lines_do_not_mix_masters_or_units():
    lines = [
        {"line_id": "a", "master_item_id": "m1", "quantity": 5, "unit": "PCS", "item_type": "INVENTORY"},
        {"line_id": "b", "master_item_id": "m2", "quantity": 3, "unit": "KG", "item_type": "INVENTORY"},
    ]
    raw = {
        "a": [{"warehouse_id": "w1", "qty": 5}],
        "b": [{"warehouse_id": "w2", "qty": 3}],
    }
    out, warnings, complete = attach_allocations_to_lines(
        lines,
        branch_id="b1",
        allowed_warehouses=ALLOWED,
        raw_by_line_id=raw,
    )
    assert complete is True
    assert out[0]["stock_allocations"][0]["warehouse_id"] == "w1"
    assert out[0]["stock_allocations"][0]["master_item_id"] == "m1"
    assert out[1]["stock_allocations"][0]["warehouse_id"] == "w2"
    assert out[1]["stock_allocations"][0]["master_item_id"] == "m2"
    assert warnings == []


def test_empty_under_over_are_warnings_not_errors():
    empty = normalize_allocations_for_line(
        [],
        line_id="ln1",
        ordered_qty=10,
        allowed_warehouses=ALLOWED,
        master_item_id="m1",
        is_inventory=True,
    )
    assert empty["status"] == "empty"
    assert any(w["message"] == WARNING_EMPTY for w in empty["warnings"])

    under = normalize_allocations_for_line(
        [{"warehouse_id": "w1", "qty": 3}],
        line_id="ln1",
        ordered_qty=10,
        allowed_warehouses=ALLOWED,
        master_item_id="m1",
        is_inventory=True,
    )
    assert under["status"] == "under"
    assert any(w["message"] == WARNING_UNDER for w in under["warnings"])

    over = normalize_allocations_for_line(
        [{"warehouse_id": "w1", "qty": 12}],
        line_id="ln1",
        ordered_qty=10,
        allowed_warehouses=ALLOWED,
        master_item_id="m1",
        is_inventory=True,
    )
    assert over["status"] == "over"
    assert any(w["message"] == WARNING_OVER for w in over["warnings"])


def test_service_line_skips_allocation():
    result = normalize_allocations_for_line(
        [{"warehouse_id": "w1", "qty": 1}],
        line_id="svc",
        ordered_qty=1,
        allowed_warehouses=ALLOWED,
        is_inventory=False,
    )
    assert result["stock_allocations"] == []
    assert result["status"] == "empty"


def test_bad_qty_and_fake_warehouse_stripped():
    result = normalize_allocations_for_line(
        [
            {"warehouse_id": "w1", "qty": "NaN"},
            {"warehouse_id": "fake", "qty": 5},
            {"warehouse_id": "w2", "qty": 2},
        ],
        line_id="ln1",
        ordered_qty=2,
        allowed_warehouses=ALLOWED,
        master_item_id="m1",
        is_inventory=True,
    )
    assert len(result["stock_allocations"]) == 1
    assert result["stock_allocations"][0]["warehouse_id"] == "w2"
    assert result["complete"] is True


def test_realization_mismatch_is_warning_only():
    warn = realization_mismatch_warning(
        line_id="ln1",
        planned_warehouse_id="w1",
        actual_warehouse_id="w2",
        planned_remaining=5,
        shipped_qty=7,
    )
    assert warn is not None
    assert warn["code"] == "REALIZATION_MISMATCH"
    assert realization_mismatch_warning(
        line_id="ln1",
        planned_warehouse_id="w1",
        actual_warehouse_id="w1",
        planned_remaining=5,
        shipped_qty=3,
    ) is None


def test_summarize_marks_incomplete():
    lines = [
        {
            "line_id": "a",
            "master_item_id": "m1",
            "quantity": 10,
            "item_type": "INVENTORY",
            "stock_allocations": [{"warehouse_id": "w1", "qty": 4}],
            "stock_allocation_status": "under",
        }
    ]
    summary = summarize_order_allocation_warnings(lines)
    assert summary["allocation_incomplete"] is True
    assert summary["incomplete_line_count"] == 1
