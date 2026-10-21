"""Focused audit: Qty Pesanan / Belum Terkirim / Tersedia — no double-count, fail-closed.

Locks the formulas used by compatibility + warehouse/R2 catalogs so OWNER can
see when "Belum terverifikasi" is intentional vs a calculation bug.

Does NOT claim live production numbers are correct without a READ-ONLY data audit.
Does NOT convert company legacy balances into per-warehouse stock.
"""
from __future__ import annotations

from services.physical_stock import (
    aggregate_operational_orders,
    operational_availability,
)
from services.stock_compatibility_read import (
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
    aggregate_legacy_delivered_qty,
    compatibility_row,
)


def test_operational_availability_formula_no_double_subtraction():
    """Qty Tersedia = physical − pesanan − belum; delivery reduces belum only once.

    physical is book stock (already net of posted SALES_DELIVERY in R2/standalone).
    ordered/completed/delivered are demand buckets — never subtract delivery from
    physical again inside this helper.
    """
    # ordered 100, completed 60, delivered 20 → pesanan 40, belum 40, tersedia 100-40-40
    buckets = operational_availability(
        physical_qty=100, ordered_qty=100, completed_qty=60, r2_delivered_qty=20,
    )
    assert buckets == {
        "physical_qty": 100.0,
        "qty_pesanan": 40.0,
        "qty_belum_terkirim": 40.0,
        "qty_tersedia": 20.0,
    }
    # Delivered cannot exceed completed/ordered floor — no negative belum.
    over = operational_availability(
        physical_qty=100, ordered_qty=50, completed_qty=50, r2_delivered_qty=80,
    )
    assert over["qty_pesanan"] == 0.0
    assert over["qty_belum_terkirim"] == 0.0
    assert over["qty_tersedia"] == 100.0
    # Negative book stays informational (not clamped to invent readiness).
    neg = operational_availability(
        physical_qty=-10, ordered_qty=5, completed_qty=0, r2_delivered_qty=0,
    )
    assert neg["qty_pesanan"] == 5.0
    assert neg["qty_belum_terkirim"] == 0.0
    assert neg["qty_tersedia"] == -15.0


def test_qty_pesanan_from_order_status_and_operator_completion():
    """Pesanan = ordered − completed; cancelled / incomplete process stay fail-closed."""
    owners = {}
    orders = [
        {
            "id": "so-1",
            "cancelled": False,
            "line_items": [{
                "master_item_id": "m1",
                "quantity": 100,
                "production_process": "print",
                "name": "Cup",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 40}],
        },
        {
            "id": "so-canc",
            "cancelled": True,
            "line_items": [{
                "master_item_id": "m1",
                "quantity": 999,
                "production_process": "print",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 999}],
        },
    ]
    progress = aggregate_operational_orders(orders, owners)
    assert progress["m1"]["ordered_qty"] == 100.0
    assert progress["m1"]["completed_qty"] == 40.0
    assert progress["m1"]["completion_unproven"] is False
    buckets = operational_availability(
        physical_qty=500,
        ordered_qty=progress["m1"]["ordered_qty"],
        completed_qty=progress["m1"]["completed_qty"],
        r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 60.0
    assert buckets["qty_belum_terkirim"] == 40.0


def test_belum_terkirim_uses_confirmed_delivery_not_sj_allocation():
    """SJ SELESAI alone is not customer proof — Belum stays = completed."""
    orders = [{
        "id": "so-sj",
        "cancelled": False,
        "line_items": [{
            "master_item_id": "m1",
            "quantity": 100,
            "production_process": "print",
            "name": "Lid",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 80}],
        # No proven shipment / qty_delivered.
    }]
    deliveries = [{
        "id": "sj-1",
        "po_id": "so-sj",
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": "m1", "qty": 80}],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, deliveries)
    assert delivered.get("m1", 0) == 0
    assert prov.get("m1") is None
    progress = aggregate_operational_orders(orders, {})
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["m1"]["ordered_qty"],
        completed_qty=progress["m1"]["completed_qty"],
        r2_delivered_qty=delivered.get("m1", 0),
    )
    assert buckets["qty_belum_terkirim"] == 80.0


def test_proven_shipment_reduces_belum_deterministically():
    orders = [{
        "id": "so-ok",
        "cancelled": False,
        "line_items": [{
            "master_item_id": "m1",
            "quantity": 100,
            "production_process": "print",
            "name": "Lid",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 80}],
        "shipments": [{
            "id": "ship-1",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-01-02T00:00:00+00:00",
            "total_qty": 30,
            "lines": [{"line_idx": 0, "qty": 30}],
        }],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert delivered["m1"] == 30.0
    assert prov["m1"] == "confirmed_shipment"
    progress = aggregate_operational_orders(orders, {})
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["m1"]["ordered_qty"],
        completed_qty=progress["m1"]["completed_qty"],
        r2_delivered_qty=delivered["m1"],
    )
    assert buckets["qty_pesanan"] == 20.0
    assert buckets["qty_belum_terkirim"] == 50.0  # 80 - 30


def test_unproven_multi_line_attribution_stays_belum_terverifikasi_not_zero():
    """Ambiguous multi-SKU confirmation → BELUM_TERVERIFIKASI; never invent 0 belum."""
    orders = [{
        "id": "so-ambig",
        "cancelled": False,
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
            "delivered_at": "2026-01-03T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"qty": 40}],  # no line_idx → unsafe on multi-line PO
        }],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert prov["m1"] == "unproven"
    assert prov["m2"] == "unproven"
    # Catalog path: completed > 0 and provenance unproven → status unverified, qty null.
    for mid in ("m1", "m2"):
        progress = aggregate_operational_orders(orders, {})[mid]
        assert progress["completed_qty"] > 0
        row = compatibility_row(
            master={"id": mid, "item_code": mid, "item_name": mid},
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
        # Fail-closed tersedia excludes only pesanan — never fabricates belum=0.
        assert row["qty_tersedia"] == 500 - row["qty_pesanan"]


def test_single_line_qty_delivered_fallback_is_ready():
    orders = [{
        "id": "so-one",
        "cancelled": False,
        "qty_delivered": 25,
        "line_items": [{
            "master_item_id": "m1",
            "quantity": 100,
            "production_process": "print",
            "name": "One",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 60}],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(orders, [])
    assert delivered["m1"] == 25.0
    assert prov["m1"] == "order_qty_delivered"
    progress = aggregate_operational_orders(orders, {})["m1"]
    row = compatibility_row(
        master={"id": "m1", "item_code": "M1", "item_name": "One"},
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=delivered["m1"],
        belum_status=BELUM_STATUS_READY,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY
    assert row["qty_belum_terkirim"] == 35.0  # min(60,100) - 25
    assert row["qty_pesanan"] == 40.0


def test_unlinked_process_addon_marks_completion_unproven():
    """Unlinked SERVICE print process → completion_unproven (fail-closed Belum)."""
    from services.inventory_accounting import ITEM_TYPE_SERVICE

    orders = [{
        "id": "so-addon",
        "cancelled": False,
        "line_items": [
            {
                "master_item_id": "inv-a",
                "quantity": 100,
                "production_process": "print",
                "name": "Parent",
            },
            {
                "master_item_id": "svc-warna",
                "quantity": 100,
                "production_process": "print",
                "item_type": ITEM_TYPE_SERVICE,
                "name": "Warna",
                # intentionally no parent_line_id / parent_line_idx
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
    }]
    progress = aggregate_operational_orders(
        orders, {}, exclude_master_ids={"svc-warna"},
    )
    assert "svc-warna" not in progress
    assert progress["inv-a"]["ordered_qty"] == 100.0
    assert progress["inv-a"]["completion_unproven"] is True


def test_compatibility_vs_standalone_mode_contract_notes():
    """Document mode differences for OWNER — no silent company→warehouse conversion."""
    # OWNER screenshot shape (compatibility): large legacy physical, open pesanan,
    # Belum terverifikasi when completed>0 but delivery attribution unproven.
    # ordered 24500 / completed 12250 → pesanan 12250; belum stays null (not 0).
    row = compatibility_row(
        master={"id": "m1", "item_code": "X", "item_name": "12 oz PET Datar 11 gr SJP"},
        physical_qty=156247,
        ordered_qty=24500,
        completed_qty=12250,
        delivered_qty=0,
        belum_status=BELUM_STATUS_UNVERIFIED,
        updated_at="2026-01-01T00:00:00+00:00",
        base_unit="PCS",
    )
    assert row["stock_read_mode"] == "compatibility"
    assert row["physical_qty_scope"] == "COMPANY"
    assert row["physical_qty_warehouse_valid"] is False
    assert row["qty_tersedia_valid"] is False
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None
    assert row["physical_qty"] == 156247.0
    assert row["qty_pesanan"] == 12250.0
    assert row["qty_tersedia"] == 156247.0 - 12250.0
    # Standalone/R2 catalogs use physical_stock_balances per warehouse and
    # confirmed SALES_DELIVERY — they never invent warehouse opening from
    # product_stock_state.total_qty (covered in warehouse standalone suite).
