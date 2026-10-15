"""Per-PO + per-parent Add On process group completion (OWNER final rules).

A) PO-001 Barang A 1000 + two SERVICE 1000 — packing only when FULLY_COMPLETED.
B) Same PO Barang A done, Barang B unfinished — A packs, B blocked.
C) PO-002 same master as A — progress isolated.
D) Two SERVICE parents in one PO — each gates own induk.
E) Cancelled addon / ambiguous parent / partial — fail-closed.
F) SERVICE prices still billable (qty × unit_price).
"""
from __future__ import annotations

from quantity_rules import (
    available_for_delivery_note_line,
    available_for_packing_line,
    packing_required_total,
    remaining_delivery_note_updates,
    validate_packing_lines,
)
from services.order_line_addon import (
    STATUS_FULLY_COMPLETED,
    STATUS_INCOMPLETE,
    evaluate_process_group,
)
from services.physical_stock import aggregate_operational_orders


def _po001_a_with_two_addons(*, w1=0, w2=0, w3=0, helper=True):
    order = {
        "id": "PO-001",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-a",
                "name": "Barang A",
                "quantity": 1000,
                "production_process": "print",
                "master_item_id": "inv-a",
                "item_type": "INVENTORY",
                "unit_price": 5000,
            },
            {
                "line_id": "ln-w2",
                "name": "Add On Warna 2",
                "quantity": 1000,
                "production_process": "print",
                "master_item_id": "svc-w2",
                "item_type": "SERVICE",
                "parent_line_id": "ln-a",
                "unit_price": 500,
            },
            {
                "line_id": "ln-w3",
                "name": "Add On Warna 3",
                "quantity": 1000,
                "production_process": "print",
                "master_item_id": "svc-w3",
                "item_type": "SERVICE",
                "parent_line_id": "ln-a",
                "unit_price": 700,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "quantity": 1000, "qty_completed": w1},
            {"line_idx": 1, "quantity": 1000, "qty_completed": w2},
            {"line_idx": 2, "quantity": 1000, "qty_completed": w3},
        ],
        "packing_info": {},
        "current_stage": 11,
        "total_quantity": 3000,
    }
    if helper:
        order["helper_prep"] = {
            "qty_prepared": 1000,
            "items": [{"line_idx": 0, "qty_prepared": 1000}],
        }
    return order


def test_a_partial_colors_block_packing_full_allows_1000():
    # Warna 1 only
    o = _po001_a_with_two_addons(w1=1000, w2=0, w3=0)
    g = evaluate_process_group(o, 0)
    assert g.status == STATUS_INCOMPLETE
    assert g.physical_ready_qty == 0
    assert available_for_packing_line(o, 0) == 0
    assert packing_required_total(o) == 1000

    # Warna 1+2; warna 3 unfinished
    o = _po001_a_with_two_addons(w1=1000, w2=1000, w3=0)
    assert evaluate_process_group(o, 0).status == STATUS_INCOMPLETE
    assert available_for_packing_line(o, 0) == 0

    # All complete → siap Packing 1000
    o = _po001_a_with_two_addons(w1=1000, w2=1000, w3=1000)
    g = evaluate_process_group(o, 0)
    assert g.status == STATUS_FULLY_COMPLETED
    assert g.physical_ready_qty == 1000
    assert available_for_packing_line(o, 0) == 1000
    assert packing_required_total(o) == 1000
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 1000}], o) is None
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 1000}]}
    assert remaining_delivery_note_updates(o) == [{"line_idx": 0, "qty": 1000}]
    assert available_for_delivery_note_line(o, 1) == 0


def test_b_sibling_b_does_not_block_completed_a():
    o = _po001_a_with_two_addons(w1=1000, w2=1000, w3=1000)
    o["line_items"].append({
        "line_id": "ln-b",
        "name": "Barang B",
        "quantity": 500,
        "production_process": "print",
        "master_item_id": "inv-b",
        "item_type": "INVENTORY",
    })
    o["operator_splits"].append({"line_idx": 3, "quantity": 500, "qty_completed": 0})
    o["helper_prep"]["items"].append({"line_idx": 3, "qty_prepared": 0})
    assert evaluate_process_group(o, 0).fully_completed is True
    assert available_for_packing_line(o, 0) == 1000
    assert evaluate_process_group(o, 3).status == STATUS_INCOMPLETE
    assert available_for_packing_line(o, 3) == 0
    assert packing_required_total(o) == 1500  # A + B physical only


def test_c_po002_same_master_isolated():
    po1 = _po001_a_with_two_addons(w1=1000, w2=1000, w3=1000)
    po2 = {
        "id": "PO-002",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-a2",
            "master_item_id": "inv-a",
            "quantity": 200,
            "production_process": "print",
            "item_type": "INVENTORY",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
    }
    progress = aggregate_operational_orders(
        [po1, po2], exclude_master_ids={"svc-w2", "svc-w3"},
    )
    # PO-001 contributes 1000 siap; PO-002 still 200 in pesanan (0 completed)
    assert progress["inv-a"]["ordered_qty"] == 1200
    assert progress["inv-a"]["completed_qty"] == 1000
    assert progress["inv-a"].get("completion_unproven") is False


def test_d_two_parents_each_own_addons():
    o = {
        "id": "PO-multi",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-a", "master_item_id": "inv-a", "quantity": 1000, "production_process": "print", "item_type": "INVENTORY"},
            {"line_id": "ln-aw", "master_item_id": "svc-a", "parent_line_id": "ln-a", "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
            {"line_id": "ln-b", "master_item_id": "inv-b", "quantity": 500, "production_process": "print", "item_type": "INVENTORY"},
            {"line_id": "ln-bw", "master_item_id": "svc-b", "parent_line_id": "ln-b", "quantity": 500, "production_process": "print", "item_type": "SERVICE"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 500},
            {"line_idx": 3, "qty_completed": 0},
        ],
        "helper_prep": {
            "items": [
                {"line_idx": 0, "qty_prepared": 1000},
                {"line_idx": 2, "qty_prepared": 500},
            ]
        },
    }
    assert evaluate_process_group(o, 0).fully_completed is True
    assert available_for_packing_line(o, 0) == 1000
    assert evaluate_process_group(o, 2).status == STATUS_INCOMPLETE
    assert available_for_packing_line(o, 2) == 0


def test_e_cancelled_addon_and_ambiguous_parent():
    o = _po001_a_with_two_addons(w1=1000, w2=1000, w3=0)
    o["line_items"][2]["cancelled"] = True
    # Cancelled warna 3 ignored → group fully complete with w1+w2
    g = evaluate_process_group(o, 0)
    assert g.fully_completed is True
    assert available_for_packing_line(o, 0) == 1000

    # Ambiguous: SERVICE without parent on same PO with inventory parent
    bad = _po001_a_with_two_addons(w1=1000, w2=1000, w3=1000)
    bad["line_items"][1].pop("parent_line_id")
    progress = aggregate_operational_orders(
        [bad], exclude_master_ids={"svc-w2", "svc-w3"},
    )
    assert progress["inv-a"].get("completion_unproven") is True


def test_f_service_prices_still_billed():
    o = _po001_a_with_two_addons()
    billed = sum(int(li["quantity"]) * int(li["unit_price"]) for li in o["line_items"])
    assert billed == 5_000_000 + 500_000 + 700_000
    assert packing_required_total(o) == 1000
