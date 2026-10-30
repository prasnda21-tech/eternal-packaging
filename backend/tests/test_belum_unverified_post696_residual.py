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


def test_po_20260915_000003_around_shipment_ui_20000_not_physical_double_count():
    """Regression: PO/2026/09/15/000003 (AROUND) — Owner live evidence.

    Cup print + 2× Warna Sablon SERVICE + LID. Shipment UI total 20.000 =
    Σ all line qtys (5k×4). That is a display/header aggregate, not physical
    cup demand. Post-#696 delivered credit must count cup once + lid once;
    unlinked SERVICE skipped (no invent parent). Parent links not assumed
    persisted — completion_unproven stays True until B31 PATCH.
    """
    from quantity_rules import packing_required_total, shipment_qty_total

    cup = "511e1d5d-90b1-4af8-b482-5b90abaada76"
    lid = "9d10b833-82b4-4352-9bd7-a74aeae20e4d"
    svc = "9bfd5938-6868-4d1b-bf9b-6212225134a2"
    q = 5000.0
    # Intentionally unlinked — do not invent parent_line_id
    order = {
        "id": "e2978ceb-1934-4c7b-8800-9b2c39482e37",
        "po_number": "PO/2026/09/15/000003",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "L0",
                "master_item_id": cup,
                "quantity": q,
                "production_process": "print",
                "name": "12 oz PET Datar 11 gr SJP",
            },
            {
                "line_id": "L1",
                "master_item_id": svc,
                "quantity": q,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
                "name": "Warna Sablon 1",
            },
            {
                "line_id": "L2",
                "master_item_id": svc,
                "quantity": q,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
                "name": "Warna Sablon 2",
            },
            {
                "line_id": "L3",
                "master_item_id": lid,
                "quantity": q,
                "name": "LID",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": q},
            {"line_idx": 1, "qty_completed": q},
            {"line_idx": 2, "qty_completed": q},
        ],
        "shipments": [
            {
                "id": "bb1cbba3-d58b-40fb-9488-5a2c5ce114bc",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-09-20T00:00:00+00:00",
                "total_qty": 20000,  # UI aggregate cup+2svc+lid
                "lines": [
                    {"line_idx": 0, "qty": q},
                    {"line_idx": 1, "qty": q},
                    {"line_idx": 2, "qty": q},
                    {"line_idx": 3, "qty": q},
                ],
            },
        ],
    }

    # UI / shipment header: sum of every line including SERVICE
    assert shipment_qty_total(order["shipments"][0]) == 20000
    assert sum(float(li["quantity"]) for li in order["line_items"]) == 20000.0

    # Physical packing target excludes process-only SERVICE (cup + lid only)
    assert packing_required_total(order) == 10000

    delivered, prov = aggregate_legacy_delivered_qty(
        [order], [], process_master_ids={svc},
    )
    # Cup credited once (native physical), not 5k+5k+5k from SERVICE blast
    assert delivered.get(cup) == 5000.0
    assert delivered.get(lid) == 5000.0
    assert svc not in delivered
    assert prov.get(cup) == "confirmed_shipment"
    assert prov.get(cup) != "unproven"
    assert prov.get(lid) == "confirmed_shipment"

    progress = aggregate_operational_orders(
        [order], {}, exclude_master_ids={svc},
    )
    # Parent links not persisted → Belum UNVERIFIED residual (B31 target)
    assert progress[cup]["completion_unproven"] is True
    assert progress[cup]["completed_qty"] == 5000.0
    assert _belum_status(
        progress[cup]["completed_qty"],
        delivered[cup],
        prov.get(cup),
        progress[cup]["completion_unproven"],
    ) == BELUM_STATUS_UNVERIFIED

    # After explicit B31-style links (idx 1,2 → parent 0), Belum READY numeric
    linked = {
        **order,
        "line_items": [
            order["line_items"][0],
            {**order["line_items"][1], "parent_line_id": "L0"},
            {**order["line_items"][2], "parent_line_id": "L0"},
            order["line_items"][3],
        ],
    }
    delivered2, prov2 = aggregate_legacy_delivered_qty(
        [linked], [], process_master_ids={svc},
    )
    progress2 = aggregate_operational_orders(
        [linked], {}, exclude_master_ids={svc},
    )
    assert delivered2.get(cup) == 5000.0  # still not triple-counted
    assert progress2[cup]["completion_unproven"] is False
    assert _belum_status(
        progress2[cup]["completed_qty"],
        delivered2[cup],
        prov2.get(cup),
        progress2[cup]["completion_unproven"],
    ) == BELUM_STATUS_READY
    buckets = operational_availability(
        physical_qty=50000,
        ordered_qty=progress2[cup]["ordered_qty"],
        completed_qty=progress2[cup]["completed_qty"],
        r2_delivered_qty=delivered2[cup],
    )
    assert buckets["qty_belum_terkirim"] == 0.0  # siap 5k − delivered 5k
