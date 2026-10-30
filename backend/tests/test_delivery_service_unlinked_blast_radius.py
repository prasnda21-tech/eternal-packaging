"""Stock Saat Ini SERVICE attribution — narrow unlinked-SERVICE skip.

Unlinked SERVICE on a confirmed shipment must not invent a parent, must not
earn physical delivered credit, and must not blast-invalidate sibling physical
masters that are independently attributed on the same shipment.

qty_delivered fallback is blocked when any SERVICE/process line is on the PO
so header totals that mix SERVICE volume cannot inflate physical credit.

Scope: aggregate_legacy_delivered_qty (Stock Saat Ini / Compatibility catalog).
Does not change proven_delivered_qty_by_order_line shipping semantics.
Does not invent Rule A Belum formula (Belum remains siap − delivered).
"""
from __future__ import annotations

from services.inventory_accounting import ITEM_TYPE_SERVICE
from services.physical_stock import (
    aggregate_operational_orders,
    operational_availability,
)
from services.stock_compatibility_read import (
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
    aggregate_legacy_delivered_qty,
    proven_delivered_qty_by_order_line,
)


def _terkirim_ship(ship_id, lines, *, total_qty=None, cancelled=False):
    qty = total_qty if total_qty is not None else sum(
        float(ln.get("qty") or 0) for ln in lines
    )
    doc = {
        "id": ship_id,
        "delivery_status": "Terkirim",
        "delivered_at": "2026-10-20T00:00:00+00:00",
        "total_qty": qty,
        "lines": lines,
    }
    if cancelled:
        doc["cancelled"] = True
        doc["is_cancelled"] = True
    return doc


def _belum_status(completed, delivered, prov, completion_unproven):
    if (completion_unproven and completed > 0) or (completed > 0 and prov == "unproven"):
        return BELUM_STATUS_UNVERIFIED
    return BELUM_STATUS_READY


def test_mixed_physical_plus_unlinked_service_credits_physical_only():
    orders = [{
        "id": "po-mixed",
        "cancelled": False,
        "line_items": [
            {
                "master_item_id": "phys-A",
                "quantity": 1000,
                "production_process": "print",
                "line_id": "L0",
            },
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
        ],
        "shipments": [
            _terkirim_ship("ship-1", [
                {"line_idx": 0, "qty": 400},
                {"line_idx": 1, "qty": 100},
            ], total_qty=500),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 400.0
    assert prov.get("phys-A") == "confirmed_shipment"
    assert "svc-W" not in delivered
    # Delivery side no longer blast-unproven; completion_unproven may still
    # hold Belum UNVERIFIED until parent link exists (data repair).
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-W"},
    )
    assert progress["phys-A"]["completion_unproven"] is True
    assert prov.get("phys-A") != "unproven"
    buckets = operational_availability(
        physical_qty=5000,
        ordered_qty=progress["phys-A"]["ordered_qty"],
        completed_qty=progress["phys-A"]["completed_qty"],
        r2_delivered_qty=delivered["phys-A"],
    )
    assert buckets["qty_belum_terkirim"] == 600.0


def test_multi_physical_unlinked_service_does_not_blast_sibling():
    """phys-A credited; phys-B not on shipment stays clear (not unproven blast)."""
    orders = [{
        "id": "po-multi",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 500, "production_process": "print"},
            {"master_item_id": "phys-B", "quantity": 500, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 500,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-3", [
                {"line_idx": 0, "qty": 50},
                {"line_idx": 2, "qty": 50},
            ], total_qty=100),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 50.0
    assert prov.get("phys-A") == "confirmed_shipment"
    assert prov.get("phys-B") != "unproven"
    assert delivered.get("phys-B", 0) == 0


def test_service_only_shipment_no_credit_no_sibling_unproven():
    orders = [{
        "id": "po-svc-only",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 1000, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-svc", [{"line_idx": 1, "qty": 40}], total_qty=40),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A", 0) == 0
    assert "svc-W" not in delivered
    assert prov.get("phys-A") != "unproven"


def test_physical_only_shipment_on_po_with_service_line_confirmed():
    orders = [{
        "id": "po-ok",
        "cancelled": False,
        "line_items": [
            {
                "master_item_id": "phys-A",
                "quantity": 1000,
                "production_process": "print",
                "line_id": "L0",
            },
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-2", [{"line_idx": 0, "qty": 400}], total_qty=400),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert prov.get("phys-A") == "confirmed_shipment"
    assert delivered.get("phys-A") == 400.0


def test_qty_delivered_fallback_blocked_when_service_line_present():
    """Header qty_delivered must not credit physical when SERVICE is on the PO."""
    orders = [{
        "id": "po-mould-mix",
        "cancelled": False,
        "qty_delivered": 100905,
        "line_items": [
            {
                "master_item_id": "mould-100192",
                "quantity": 5,
                "production_process": "print",
            },
            {
                "master_item_id": "svc-W",
                "quantity": 100900,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("mould-100192", 0) == 0
    assert prov.get("mould-100192") != "order_qty_delivered"


def test_qty_delivered_fallback_still_works_physical_only_po():
    orders = [{
        "id": "po-phys-only",
        "cancelled": False,
        "qty_delivered": 30,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 100, "production_process": "print"},
        ],
        "shipments": [],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 30.0
    assert prov.get("phys-A") == "order_qty_delivered"


def test_100051_style_over_delivery_from_mixed_header_blocked():
    """Mixed SERVICE+INVENTORY: credit native physical only; no header inflate."""
    orders = [{
        "id": "po-100051",
        "cancelled": False,
        "qty_delivered": 10201,
        "line_items": [
            {
                "master_item_id": "phys-051",
                "quantity": 9701,
                "production_process": "print",
            },
            {
                "master_item_id": "svc-W",
                "quantity": 500,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-051", [
                {"line_idx": 0, "qty": 9701},
                {"line_idx": 1, "qty": 500},
            ], total_qty=10201),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-051") == 9701.0
    assert prov.get("phys-051") == "confirmed_shipment"


def test_partial_delivery_sums_across_identities_with_unlinked_service():
    orders = [{
        "id": "po-partial",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 1000, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-p1", [
                {"line_idx": 0, "qty": 400},
                {"line_idx": 1, "qty": 400},
            ], total_qty=800),
            _terkirim_ship("ship-p2", [
                {"line_idx": 0, "qty": 300},
                {"line_idx": 1, "qty": 300},
            ], total_qty=600),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 700.0
    assert prov.get("phys-A") == "confirmed_shipment"


def test_duplicate_shipment_identity_not_double_counted_with_service():
    ship = _terkirim_ship("ship-dup", [
        {"line_idx": 0, "qty": 1000},
        {"line_idx": 1, "qty": 1000},
    ], total_qty=2000)
    orders = [{
        "id": "po-dup",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 1000, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [ship, dict(ship)],
    }]
    delivered, _ = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 1000.0


def test_cancelled_shipment_ignored_with_unlinked_service():
    orders = [{
        "id": "po-cancel",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 1000, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-dead", [
                {"line_idx": 0, "qty": 500},
                {"line_idx": 1, "qty": 500},
            ], total_qty=1000, cancelled=True),
            _terkirim_ship("ship-live", [
                {"line_idx": 0, "qty": 200},
                {"line_idx": 1, "qty": 200},
            ], total_qty=400),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 200.0
    assert prov.get("phys-A") == "confirmed_shipment"


def test_linked_service_still_remaps_to_parent():
    orders = [{
        "id": "po-linked",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "L0",
                "master_item_id": "phys-A",
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "L1",
                "master_item_id": "svc-W",
                "parent_line_id": "L0",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
        ],
        "shipments": [
            _terkirim_ship("ship-linked", [{"line_idx": 1, "qty": 40}], total_qty=40),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-W"},
    )
    assert delivered.get("phys-A") == 40.0
    assert prov.get("phys-A") == "confirmed_shipment"
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-W"},
    )
    # Linked + FULLY_COMPLETED → siap proven; Belum READY = 1000 - 40
    assert progress["phys-A"]["completion_unproven"] is False
    assert progress["phys-A"]["completed_qty"] == 1000.0
    status = _belum_status(
        progress["phys-A"]["completed_qty"],
        delivered["phys-A"],
        prov["phys-A"],
        progress["phys-A"]["completion_unproven"],
    )
    assert status == BELUM_STATUS_READY
    buckets = operational_availability(
        physical_qty=5000,
        ordered_qty=1000,
        completed_qty=1000,
        r2_delivered_qty=40,
    )
    assert buckets["qty_belum_terkirim"] == 960.0


def test_missing_line_idx_still_fail_closed_multi_line():
    """Genuine ambiguity (bad line_idx) must still mark physical unproven."""
    orders = [{
        "id": "po-bad-idx",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 100, "production_process": "print"},
            {"master_item_id": "phys-B", "quantity": 100, "production_process": "print"},
        ],
        "shipments": [{
            "id": "ship-bad",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 50,
            "lines": [{"qty": 50}],  # missing line_idx
        }],
    }]
    _delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids=set(),
    )
    assert prov.get("phys-A") == "unproven"
    assert prov.get("phys-B") == "unproven"


def test_multiwarna_does_not_multiply_physical_when_linked():
    """1 parent + 2 warna SERVICE linked → physical credit = parent qty only."""
    orders = [{
        "id": "po-warna",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "L0",
                "master_item_id": "phys-A",
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "L1",
                "master_item_id": "svc-w1",
                "parent_line_id": "L0",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
            {
                "line_id": "L2",
                "master_item_id": "svc-w2",
                "parent_line_id": "L0",
                "quantity": 700,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 700},
        ],
        "shipments": [
            _terkirim_ship("ship-w", [
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 700},
            ], total_qty=1700),
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={"svc-w1", "svc-w2"},
    )
    # SERVICE-only SJ path: max(linked process qtys) = 1000, not 1700
    assert delivered.get("phys-A") == 1000.0
    assert prov.get("phys-A") == "confirmed_shipment"
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-w1", "svc-w2"},
    )
    assert progress["phys-A"]["completed_qty"] == 1000.0
    assert progress["phys-A"]["completion_unproven"] is False


def test_shipping_ready_qty_helper_unchanged_by_catalog_narrow_skip():
    """proven_delivered_qty_by_order_line is shared shipping semantics — untouched."""
    order = {
        "id": "po-ship-sem",
        "cancelled": False,
        "line_items": [
            {"master_item_id": "phys-A", "quantity": 1000, "production_process": "print"},
            {
                "master_item_id": "svc-W",
                "quantity": 1000,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "shipments": [
            _terkirim_ship("ship-sem", [
                {"line_idx": 0, "qty": 400},
                {"line_idx": 1, "qty": 100},
            ], total_qty=500),
        ],
    }
    by_line, unproven = proven_delivered_qty_by_order_line(order)
    assert by_line.get(0) == 400.0
    assert by_line.get(1) == 100.0
    assert unproven is False
