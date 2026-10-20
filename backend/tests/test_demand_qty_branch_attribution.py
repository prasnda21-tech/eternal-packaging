"""Focused regression: demand Qty Pesanan / Belum Terkirim vs branch attribution.

Locks the UAT recovery contract:
- Print order with valid branch_id → contributes demand (e.g. qty_pesanan=120).
- Print order without branch → fail-closed exclusion (never invent onto a cabang).
- Partial operator completion moves pesanan → belum.
- Proven SJ Terkirim (partial) reduces belum deterministically.
- Unknown delivery provenance stays BELUM_TERVERIFIKASI (null belum, not fake 0).

Does NOT enable Mode B null-branch auto-assign.
Does NOT write preview/production data.
"""
from __future__ import annotations

from services.physical_stock import (
    _orders_for_branch,
    aggregate_operational_orders,
    operational_availability,
)
from services.stock_compatibility_read import (
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
    aggregate_legacy_delivered_qty,
    compatibility_row,
)

BRANCH_YGY = "uat-branch-ygy"
ITEM = "uat-item-karton"


def _print_order(
    order_id: str,
    *,
    quantity: float = 120,
    completed: float = 0,
    branch_id: str | None = None,
    shipments: list | None = None,
):
    doc = {
        "id": order_id,
        "cancelled": False,
        "line_items": [{
            "master_item_id": ITEM,
            "quantity": quantity,
            "production_process": "print",
            "name": "Karton UAT",
        }],
        "operator_splits": (
            [{"line_idx": 0, "qty_completed": completed}] if completed else []
        ),
    }
    if branch_id:
        doc["branch_id"] = branch_id
    if shipments is not None:
        doc["shipments"] = shipments
    return doc


def test_print_order_with_valid_branch_qty_pesanan_120():
    """UAT recovery positive path: attributed print, not completed → pesanan 120, belum 0."""
    orders = [_print_order("so-attributed", quantity=120, completed=0, branch_id=BRANCH_YGY)]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    assert len(scoped) == 1
    progress = aggregate_operational_orders(scoped, {})
    assert progress[ITEM]["ordered_qty"] == 120.0
    assert progress[ITEM]["completed_qty"] == 0.0
    buckets = operational_availability(
        physical_qty=970,
        ordered_qty=progress[ITEM]["ordered_qty"],
        completed_qty=progress[ITEM]["completed_qty"],
        r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 120.0
    assert buckets["qty_belum_terkirim"] == 0.0
    # Demand is independent of physical book — stamp/attr does not invent stock.
    assert buckets["physical_qty"] == 970.0
    assert buckets["qty_tersedia"] == 850.0


def test_order_without_branch_fail_closed_exclusion():
    """Missing branch_id/cabang_id → excluded; never invent demand onto target cabang.

    Catalog zeros after exclusion are fail-closed empty demand for that branch —
    not a claim that the orphan order was counted as complete demand.
    """
    orphan = _print_order("so-orphan", quantity=120, completed=0, branch_id=None)
    assert "branch_id" not in orphan and "cabang_id" not in orphan

    scoped = _orders_for_branch([orphan], BRANCH_YGY, legacy_settings={})
    assert scoped == []

    progress = aggregate_operational_orders(scoped, {})
    assert ITEM not in progress

    buckets = operational_availability(
        physical_qty=970, ordered_qty=0, completed_qty=0, r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 0.0
    assert buckets["qty_belum_terkirim"] == 0.0

    # Mode B null-branch OFF (empty settings): still excluded — no auto-assign.
    still = _orders_for_branch([orphan], BRANCH_YGY, legacy_settings={"enabled": False})
    assert still == []


def test_operator_partial_completion_moves_pesanan_to_belum():
    orders = [
        _print_order(
            "so-partial",
            quantity=120,
            completed=40,
            branch_id=BRANCH_YGY,
        )
    ]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=0,
    )
    assert progress["ordered_qty"] == 120.0
    assert progress["completed_qty"] == 40.0
    assert buckets["qty_pesanan"] == 80.0
    assert buckets["qty_belum_terkirim"] == 40.0


def test_sj_terkirim_partial_reduces_belum():
    orders = [
        _print_order(
            "so-sj-partial",
            quantity=120,
            completed=80,
            branch_id=BRANCH_YGY,
            shipments=[{
                "id": "ship-partial",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-20T00:00:00+00:00",
                "total_qty": 30,
                "lines": [{"line_idx": 0, "qty": 30}],
            }],
        )
    ]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, [])
    assert delivered[ITEM] == 30.0
    assert prov[ITEM] == "confirmed_shipment"
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=delivered[ITEM],
    )
    assert buckets["qty_pesanan"] == 40.0       # 120 - 80
    assert buckets["qty_belum_terkirim"] == 50.0  # 80 - 30


def test_unknown_delivery_provenance_stays_unverified():
    """Ambiguous multi-SKU shipment → BELUM_TERVERIFIKASI; never invent belum=0."""
    orders = [{
        "id": "so-ambig",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": [
            {"master_item_id": "m1", "quantity": 100, "production_process": "print", "name": "A"},
            {"master_item_id": "m2", "quantity": 100, "production_process": "print", "name": "B"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 80},
            {"line_idx": 1, "qty_completed": 80},
        ],
        "shipments": [{
            "id": "ship-ambig",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"qty": 40}],  # no line_idx → unsafe
        }],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, [])
    assert prov["m1"] == "unproven"
    assert prov["m2"] == "unproven"
    assert delivered.get("m1", 0) == 0

    progress = aggregate_operational_orders(scoped, {})["m1"]
    assert progress["completed_qty"] > 0
    row = compatibility_row(
        master={"id": "m1", "item_code": "M1", "item_name": "A"},
        physical_qty=500,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=0,
        belum_status=BELUM_STATUS_UNVERIFIED,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None
    assert row["qty_tersedia_valid"] is False
    assert row["qty_pesanan"] == 20.0  # 100 - 80
    # Fail-closed tersedia excludes only pesanan — never fabricates belum=0.
    assert row["qty_tersedia"] == 500 - row["qty_pesanan"]


def test_sj_selesai_alone_is_not_delivery_proof():
    """sales_deliveries SELESAI without proven shipment must not reduce Belum."""
    orders = [
        _print_order(
            "so-sj-draft",
            quantity=100,
            completed=80,
            branch_id=BRANCH_YGY,
        )
    ]
    deliveries = [{
        "id": "sj-1",
        "po_id": "so-sj-draft",
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": ITEM, "qty": 80}],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, deliveries)
    assert delivered.get(ITEM, 0) == 0
    assert prov.get(ITEM) is None
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=delivered.get(ITEM, 0),
    )
    assert buckets["qty_belum_terkirim"] == 80.0
    row = compatibility_row(
        master={"id": ITEM, "item_code": "K", "item_name": "Karton"},
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=0,
        belum_status=BELUM_STATUS_READY,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY
    assert row["qty_belum_terkirim"] == 80.0
