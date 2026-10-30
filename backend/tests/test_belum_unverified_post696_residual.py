"""Post-#696 residual: Belum UNVERIFIED from completion_unproven (not delivery blast).

#696 skips unlinked SERVICE on SJ without sibling blast. Belum can still be
null when aggregate_operational_orders sets completion_unproven for unlinked
process Add Ons (fail-closed until explicit parent_line_id).

Mirrors YGY cup+lid POs: one print parent + non-print sibling + SERVICE
without parent. Unique print parent is evidence for Owner repair — never
auto-invented here.
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
)


def _terkirim(ship_id, lines, *, total_qty=None):
    qty = total_qty if total_qty is not None else sum(
        float(ln.get("qty") or 0) for ln in lines
    )
    return {
        "id": ship_id,
        "delivery_status": "Terkirim",
        "delivered_at": "2026-10-20T00:00:00+00:00",
        "total_qty": qty,
        "lines": lines,
    }


def _belum_status(completed, delivered, prov, completion_unproven):
    if (completion_unproven and completed > 0) or (completed > 0 and prov == "unproven"):
        return BELUM_STATUS_UNVERIFIED
    return BELUM_STATUS_READY


def _cup_lid_unlinked_service_order(*, linked: bool):
    """Pattern: print cup + non-print lid + SERVICE (unique print parent = cup)."""
    svc = {
        "line_id": "L-svc",
        "master_item_id": "svc-warna",
        "quantity": 1000,
        "production_process": "print",
        "item_type": ITEM_TYPE_SERVICE,
        "name": "Warna",
    }
    if linked:
        svc["parent_line_id"] = "L-cup"
    return {
        "id": "po-cup-lid-svc",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "L-cup",
                "master_item_id": "100003-like",
                "quantity": 1000,
                "production_process": "print",
                "name": "Cup print",
            },
            {
                "line_id": "L-lid",
                "master_item_id": "100123-like",
                "quantity": 1000,
                # non-print sibling (lid) — not a process parent candidate
                "name": "Lid",
            },
            svc,
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 2, "qty_completed": 1000},
        ],
        "shipments": [
            _terkirim("ship-1", [
                {"line_idx": 0, "qty": 400},
                {"line_idx": 2, "qty": 100},  # unlinked/linked SERVICE on SJ
            ], total_qty=500),
        ],
    }


def test_post696_delivery_credits_cup_but_belum_unverified_until_parent_link():
    """A fixed by #696; B residual keeps Belum UNVERIFIED until data repair."""
    orders = [_cup_lid_unlinked_service_order(linked=False)]
    process_ids = {"svc-warna"}

    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids=process_ids,
    )
    assert delivered.get("100003-like") == 400.0
    assert prov.get("100003-like") == "confirmed_shipment"
    assert prov.get("100003-like") != "unproven"
    assert "svc-warna" not in delivered
    # Non-print lid not on confirmation lines → not blast-unproven
    assert prov.get("100123-like") != "unproven"

    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids=process_ids,
    )
    assert progress["100003-like"]["completed_qty"] == 1000.0
    assert progress["100003-like"]["completion_unproven"] is True

    status = _belum_status(
        progress["100003-like"]["completed_qty"],
        delivered["100003-like"],
        prov.get("100003-like"),
        progress["100003-like"]["completion_unproven"],
    )
    assert status == BELUM_STATUS_UNVERIFIED
    # Null Belum (not fake 0) — Opname/SSI suppress when UNVERIFIED
    buckets = operational_availability(
        physical_qty=5000,
        ordered_qty=progress["100003-like"]["ordered_qty"],
        completed_qty=progress["100003-like"]["completed_qty"],
        r2_delivered_qty=delivered["100003-like"],
    )
    assert buckets["qty_belum_terkirim"] == 600.0  # computed, but UI hides when UNVERIFIED


def test_explicit_parent_link_clears_completion_unproven_belum_ready():
    """Owner-approved parent_line_id → Belum READY numeric (siap − delivered)."""
    orders = [_cup_lid_unlinked_service_order(linked=True)]
    process_ids = {"svc-warna"}

    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids=process_ids,
    )
    # Linked SERVICE remaps; Alt-3 uses native physical sum when present
    assert delivered.get("100003-like") == 400.0
    assert prov.get("100003-like") == "confirmed_shipment"

    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids=process_ids,
    )
    assert progress["100003-like"]["completion_unproven"] is False
    assert progress["100003-like"]["completed_qty"] == 1000.0

    status = _belum_status(
        progress["100003-like"]["completed_qty"],
        delivered["100003-like"],
        prov.get("100003-like"),
        progress["100003-like"]["completion_unproven"],
    )
    assert status == BELUM_STATUS_READY
    buckets = operational_availability(
        physical_qty=5000,
        ordered_qty=progress["100003-like"]["ordered_qty"],
        completed_qty=progress["100003-like"]["completed_qty"],
        r2_delivered_qty=delivered["100003-like"],
    )
    assert buckets["qty_belum_terkirim"] == 600.0


def test_two_print_parents_unlinked_service_stays_ambiguous_class_c():
    """Two print parents + unlinked SERVICE → no invented parent; both unproven."""
    orders = [{
        "id": "po-two-print",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "L-a",
                "master_item_id": "print-a",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "L-b",
                "master_item_id": "print-b",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "L-svc",
                "master_item_id": "svc-warna",
                "quantity": 500,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            {"line_idx": 1, "qty_completed": 500},
            {"line_idx": 2, "qty_completed": 500},
        ],
    }]
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-warna"},
    )
    assert progress["print-a"]["completion_unproven"] is True
    assert progress["print-b"]["completion_unproven"] is True
    assert _belum_status(
        progress["print-a"]["completed_qty"], 0, "", True,
    ) == BELUM_STATUS_UNVERIFIED


def test_r2_style_path_delivery_proven_still_unverified_on_completion_gate():
    """Opname R2 path: delivery_unproven N/A; completion_unproven alone nulls Belum."""
    orders = [_cup_lid_unlinked_service_order(linked=False)]
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-warna"},
    )
    # Simulate R2 ledger credit (provenance never "unproven" on R2 path)
    r2_delivered = 400.0
    completion_unproven = progress["100003-like"]["completion_unproven"]
    completed = progress["100003-like"]["completed_qty"]
    delivery_unproven = False  # R2 authority
    belum_unverified = bool(
        (completion_unproven and completed > 0)
        or (delivery_unproven and completed > 0)
    )
    assert belum_unverified is True
    assert completion_unproven is True
