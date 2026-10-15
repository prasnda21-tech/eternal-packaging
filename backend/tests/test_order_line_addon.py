"""Unit tests for explicit parent_line_id Add On relations."""
from __future__ import annotations

from quantity_rules import is_print_line
from services.order_line_addon import (
    conservative_full_process_ready,
    process_children_for_parent,
    resolve_parent_idx,
    validate_parent_relation,
)
from services.physical_stock import aggregate_operational_orders


def test_resolve_parent_by_line_id_and_idx():
    lines = [
        {"line_id": "ln-main", "quantity": 1000, "production_process": "print"},
        {
            "line_id": "ln-w2",
            "parent_line_id": "ln-main",
            "quantity": 1000,
            "production_process": "print",
        },
        {"line_id": "ln-w3", "parent_line_idx": 0, "quantity": 1000, "production_process": "print"},
    ]
    assert resolve_parent_idx(lines[1], lines) == 0
    assert resolve_parent_idx(lines[2], lines) == 0
    assert resolve_parent_idx(lines[0], lines) is None
    assert validate_parent_relation(lines[1], lines, idx=1) is None
    assert validate_parent_relation(
        {"parent_line_id": "missing"}, lines, idx=1,
    ) is not None


def test_conservative_min_ready():
    assert conservative_full_process_ready(
        parent_ordered=1000,
        parent_completed=1000,
        child_ordered_completed=[(1000, 1000), (1000, 1000)],
    ) == 1000
    assert conservative_full_process_ready(
        parent_ordered=1000,
        parent_completed=1000,
        child_ordered_completed=[(1000, 0), (1000, 1000)],
    ) == 0
    assert conservative_full_process_ready(
        parent_ordered=1000,
        parent_completed=400,
        child_ordered_completed=[(1000, 700), (1000, 200)],
    ) == 200


def test_aggregate_with_parent_links_fully_completed_ready():
    """All linked processes confirmed → physical siap = parent qty (not 3000)."""
    orders = [{
        "id": "o1",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": "inv",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "INVENTORY",
            },
            {
                "line_id": "ln-w2",
                "master_item_id": "svc2",
                "parent_line_id": "ln-main",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
            {
                "line_id": "ln-w3",
                "master_item_id": "svc3",
                "parent_line_id": "ln-main",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 1000},
        ],
    }]
    progress = aggregate_operational_orders(
        orders, exclude_master_ids={"svc2", "svc3"},
    )
    slot = progress["inv"]
    assert slot["ordered_qty"] == 1000
    assert slot["completed_qty"] == 1000
    assert slot.get("completion_unproven") is False
    assert "svc2" not in progress


def test_aggregate_partial_linked_fail_closed():
    orders = [{
        "id": "o1",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": "inv", "quantity": 1000, "production_process": "print", "item_type": "INVENTORY"},
            {"line_id": "ln-w2", "master_item_id": "svc2", "parent_line_id": "ln-main", "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 400},
        ],
    }]
    progress = aggregate_operational_orders(orders, exclude_master_ids={"svc2"})
    assert progress["inv"]["completed_qty"] == 0
    assert progress["inv"].get("completion_unproven") is False


def test_validate_rejects_cancelled_parent_cycle_and_duplicate_ids():
    from services.order_line_addon import (
        duplicate_line_id_error,
        validate_lines_parent_graph,
        validate_parent_relation,
    )

    cancelled_parent = [
        {"line_id": "ln-main", "cancelled": True},
        {"line_id": "ln-w2", "parent_line_id": "ln-main"},
    ]
    assert validate_parent_relation(cancelled_parent[1], cancelled_parent, idx=1)

    cycle = [
        {"line_id": "a", "parent_line_id": "b"},
        {"line_id": "b", "parent_line_id": "a"},
    ]
    assert validate_parent_relation(cycle[0], cycle, idx=0)

    dup = [
        {"line_id": "same", "name": "X"},
        {"line_id": "same", "name": "Y", "parent_line_id": "same"},
    ]
    assert duplicate_line_id_error(dup)
    assert validate_lines_parent_graph(dup)

    service_parent = [
        {"line_id": "ln-svc", "item_type": "SERVICE"},
        {"line_id": "ln-w2", "item_type": "SERVICE", "parent_line_id": "ln-svc"},
    ]
    assert "SERVICE" in (validate_parent_relation(service_parent[1], service_parent, idx=1) or "")


def test_aggregate_service_without_parent_fail_closed():
    orders = [{
        "id": "o1",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": "inv", "quantity": 1000, "production_process": "print"},
            {"line_id": "ln-w2", "master_item_id": "svc2", "quantity": 1000, "production_process": "print"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
        ],
    }]
    progress = aggregate_operational_orders(orders, exclude_master_ids={"svc2"})
    assert progress["inv"]["completion_unproven"] is True
    assert progress["inv"]["ordered_qty"] == 1000


def test_process_children_detects_unlinked():
    lines = [
        {"line_id": "ln-main", "production_process": "print"},
        {"line_id": "ln-w2", "parent_line_id": "ln-main", "production_process": "print"},
        {"line_id": "ln-orphan", "production_process": "print"},
    ]
    master_ids = ["inv", "svc2", "svc3"]
    linked, unlinked = process_children_for_parent(
        0,
        lines,
        process_master_ids={"svc2", "svc3"},
        master_ids=master_ids,
        is_print_line=is_print_line,
    )
    assert linked == [1]
    assert unlinked is True
