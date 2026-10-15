"""SERVICE Add On process qty must not inflate packing / loading / SJ physical qty.

OWNER: Barang A 1000 + two SERVICE Add On 1000 → physical targets stay 1000.
Billing/invoice still uses ordered line qty × unit_price (including SERVICE).
"""
from __future__ import annotations

from quantity_rules import (
    available_for_delivery_note_line,
    available_for_packing_line,
    is_service_process_line,
    packing_line_breakdown,
    packing_required_total,
    packing_status_semantic,
    physical_print_qty_total,
    physical_qty_for_loading_line,
    print_qty_total,
    remaining_delivery_note_updates,
    shipment_line_breakdown,
    validate_packing_lines,
)
from services.order_line_addon import validate_parent_relation
from services.physical_stock import aggregate_operational_orders


def _addon_po(*, packed_main: int = 0):
    lines = [
        {
            "line_id": "ln-main",
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
            "parent_line_id": "ln-main",
            "unit_price": 500,
        },
        {
            "line_id": "ln-w3",
            "name": "Add On Warna 3",
            "quantity": 1000,
            "production_process": "print",
            "master_item_id": "svc-w3",
            "item_type": "SERVICE",
            "parent_line_id": "ln-main",
            "unit_price": 700,
        },
    ]
    order = {
        "id": "po-addon-phys",
        "cancelled": False,
        "line_items": lines,
        "total_quantity": 3000,  # legacy sum — must NOT drive physical targets
        "operator_splits": [
            {"line_idx": 0, "quantity": 1000, "qty_completed": 1000},
            {"line_idx": 1, "quantity": 1000, "qty_completed": 1000},
            {"line_idx": 2, "quantity": 1000, "qty_completed": 400},
        ],
        "helper_prep": {"qty_prepared": 1000, "items": [{"line_idx": 0, "qty_prepared": 1000}]},
        "packing_info": {},
        "current_stage": 11,
    }
    if packed_main:
        order["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": packed_main}]}
    return order


def test_service_lines_detected_without_name_inference():
    o = _addon_po()
    assert is_service_process_line(o["line_items"][1]) is True
    assert is_service_process_line(o["line_items"][0]) is False
    # Name alone never classifies SERVICE
    assert is_service_process_line({"name": "Add On Warna Sablon", "quantity": 1}) is False


def test_physical_packing_loading_sj_stay_1000():
    o = _addon_po(packed_main=1000)
    assert packing_required_total(o) == 1000
    assert physical_print_qty_total(o) == 1000
    # Operator process work still includes SERVICE print qty
    assert print_qty_total(o) == 3000

    assert available_for_packing_line(o, 0) == 1000
    assert available_for_packing_line(o, 1) == 0
    assert available_for_packing_line(o, 2) == 0
    assert validate_packing_lines([{"line_idx": 1, "packed_qty": 100}], o) is not None
    assert packing_status_semantic(o) == "Sudah"

    rows = packing_line_breakdown(o)
    assert len(rows) == 3  # SERVICE remains visible as process info
    assert rows[0]["available"] == 1000 and rows[0].get("is_service") is not True
    assert rows[1]["is_service"] is True and rows[1]["available"] == 0
    assert rows[2]["is_service"] is True and rows[2]["available"] == 0

    assert physical_qty_for_loading_line(o, 0) == 1000
    assert physical_qty_for_loading_line(o, 1) == 0
    assert physical_qty_for_loading_line(o, 2) == 0

    assert available_for_delivery_note_line(o, 0) == 1000
    assert available_for_delivery_note_line(o, 1) == 0
    assert available_for_delivery_note_line(o, 2) == 0
    updates = remaining_delivery_note_updates(o)
    assert updates == [{"line_idx": 0, "qty": 1000}]

    ship_rows = shipment_line_breakdown(o)
    assert ship_rows[0]["available_sj"] == 1000
    assert ship_rows[1]["is_service"] is True and ship_rows[1]["available_sj"] == 0
    assert ship_rows[2]["is_service"] is True and ship_rows[2]["available_sj"] == 0


def test_partial_ship_loading_service_has_zero_physical():
    from quantity_rules import (
        available_for_loading_confirm_line,
        qty_shipped,
    )

    o = _addon_po(packed_main=1000)
    o["shipments"] = [{
        "id": "sj-1",
        "lines": [{"line_idx": 0, "qty": 400}],
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "2026-01-01T00:00:00+00:00",
        "delivery_note_created_by": "t",
    }]
    assert qty_shipped(o) == 400
    # Remaining SJ after partial: only physical main line
    assert available_for_delivery_note_line(o, 0) == 600
    assert available_for_delivery_note_line(o, 1) == 0
    assert available_for_delivery_note_line(o, 2) == 0
    assert physical_qty_for_loading_line(o, 0) == 1000
    assert physical_qty_for_loading_line(o, 1) == 0
    assert physical_qty_for_loading_line(o, 2) == 0
    assert available_for_loading_confirm_line(o, 1) == 0
    assert available_for_loading_confirm_line(o, 2) == 0


def test_parent_rejects_service_induk():
    lines = [
        {"line_id": "svc-a", "item_type": "SERVICE", "quantity": 1},
        {"line_id": "svc-b", "item_type": "SERVICE", "parent_line_id": "svc-a", "quantity": 1},
    ]
    err = validate_parent_relation(lines[1], lines, idx=1)
    assert err and "SERVICE" in err


def test_invoice_line_math_keeps_service_prices():
    """Invoice uses ordered qty × unit_price — SERVICE Add On still billed."""
    o = _addon_po()
    billed = 0
    for li in o["line_items"]:
        billed += int(li["quantity"]) * int(li["unit_price"])
    # 1000*5000 + 1000*500 + 1000*700
    assert billed == 5_000_000 + 500_000 + 700_000
    # Physical packing target remains 1000 despite billing 3000 process+goods
    assert packing_required_total(o) == 1000


def test_stock_aggregate_physical_demand_1000_unverified_siap():
    orders = [{
        "id": "so-1",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": "inv-a",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "INVENTORY",
            },
            {
                "line_id": "ln-w2",
                "master_item_id": "svc-w2",
                "parent_line_id": "ln-main",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
            {
                "line_id": "ln-w3",
                "master_item_id": "svc-w3",
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
        orders, exclude_master_ids={"svc-w2", "svc-w3"},
    )
    assert "svc-w2" not in progress and "svc-w3" not in progress
    slot = progress["inv-a"]
    assert slot["ordered_qty"] == 1000
    assert slot["completion_unproven"] is True
