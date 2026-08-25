"""Targeted tests for Status Pengiriman Barang percents (no live API)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, derived_nav_permissions, with_nav_defaults
from services.shipment_status import (
    qty_dapat_dikirim,
    qty_terkirim,
    shipment_status_row,
    status_percent,
    status_percent_display,
)
from quantity_rules import qty_shipped


def _po(*, total=10000, print_qty=10000, delivered=0, printed=0, extra_non_print=0, shipments=None):
    lis = [{"name": "Cetak", "quantity": print_qty, "production_process": "print"}]
    if extra_non_print:
        lis.append({"name": "Non", "quantity": extra_non_print, "production_process": "non_print"})
        total = print_qty + extra_non_print
    splits = []
    if printed:
        splits.append({"line_idx": 0, "quantity": printed, "qty_completed": printed})
    return {
        "id": "po-1",
        "po_number": "PO-1",
        "customer": "PT Contoh",
        "order_date": "2026-08-01",
        "deadline": "2026-08-20",
        "total_quantity": total,
        "line_items": lis,
        "qty_delivered": delivered,
        "operator_splits": splits,
        "shipments": shipments or [],
        "current_stage": 11,
        "delivery_status": "Sebagian Terkirim" if delivered else "Belum Terkirim",
    }


def test_case_a_terkirim_50_percent():
    row = shipment_status_row(_po(total=10000, print_qty=10000, delivered=5000))
    assert row["qty_delivered"] == 5000
    assert row["qty_total"] == 10000
    assert row["percent_delivered"] == 50
    assert row["percent_delivered_display"] == 50


def test_case_b_dapat_dikirim_75_percent():
    row = shipment_status_row(_po(total=10000, print_qty=10000, printed=7500))
    assert row["qty_printed_operator"] == 7500
    assert row["print_qty_total"] == 10000
    assert row["percent_ready_to_ship"] == 75
    assert qty_dapat_dikirim(_po(printed=7500)) == 7500


def test_dapat_dikirim_ignores_scheduled_qty():
    o = _po(printed=0)
    o["operator_splits"] = [{"line_idx": 0, "quantity": 8000, "qty_completed": 2000}]
    row = shipment_status_row(o)
    assert row["qty_printed_operator"] == 2000
    assert row["percent_ready_to_ship"] == 20


def test_case_c_multiple_shipments_no_double_count():
    o = _po(
        delivered=5000,
        shipments=[
            {"id": "s1", "lines": [{"line_idx": 0, "qty": 3000}]},
            {"id": "s2", "lines": [{"line_idx": 0, "qty": 2000}]},
        ],
    )
    assert qty_shipped(o) == 5000
    assert qty_terkirim(o) == 5000
    row = shipment_status_row(o)
    assert row["percent_delivered"] == 50
    assert shipment_status_row(o)["percent_delivered"] == 50  # refresh does not double


def test_case_d_partial_line_is_not_full_po():
    o = {
        "po_number": "PO-MULTI",
        "customer": "A",
        "total_quantity": 10000,
        "line_items": [
            {"name": "A", "quantity": 6000, "production_process": "print"},
            {"name": "B", "quantity": 4000, "production_process": "print"},
        ],
        "qty_delivered": 6000,
        "operator_splits": [],
        "shipments": [{"id": "s1", "lines": [{"line_idx": 0, "qty": 6000}]}],
        "current_stage": 12,
        "delivery_status": "Sebagian Terkirim",
    }
    row = shipment_status_row(o)
    assert row["qty_total"] == 10000
    assert row["percent_delivered"] == 60
    assert row["percent_delivered"] != 100


def test_does_not_infer_100_from_stage_terkirim_without_qty():
    o = _po(delivered=0)
    o["current_stage"] = 14
    o["delivery_status"] = "Sebagian Terkirim"
    o.pop("qty_delivered", None)
    row = shipment_status_row(o)
    assert row["percent_delivered"] == 0


def test_visual_cap_does_not_change_raw_percent():
    assert status_percent(12000, 10000) == 120
    assert status_percent_display(12000, 10000) == 100


def test_nav_key_in_catalog():
    assert "nav.reports.shipment_status" in ALL_PERMISSIONS


def test_derived_nav_from_schedule_view():
    nav = derived_nav_permissions({"schedule.view", "po.view"})
    assert "nav.reports.shipment_status" in nav
    assert "nav.production_schedule" in nav


def test_legacy_without_schedule_does_not_gain_shipment_status():
    stored = {"po.view"}
    derived = with_nav_defaults(stored)
    assert "nav.reports.shipment_status" not in derived
