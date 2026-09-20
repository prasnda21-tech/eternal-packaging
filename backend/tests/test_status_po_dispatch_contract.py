"""Canonical Status PO dispatch: badge, boxes 13–15, percent, projection parity."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_loading_gate import (
    MSG_DELIVERY_LOADING_INCOMPLETE,
    new_delivery_block_reason,
)
from services.sales_pwa import _workflow_progress, serialize_list_row
from services.status_po import effective_workflow_status, status_po_row, status_po_workflow
from tests.test_status_po_projection import apply_status_po_projection


def _print_po(**extra):
    o = {
        "id": extra.pop("id", "po-1"),
        "po_number": extra.pop("po_number", "PO/2026/09/07/000005"),
        "customer": extra.pop("customer", "MORE COFFEE"),
        "order_date": "2026-09-07",
        "current_stage": extra.pop("current_stage", 12),
        "cancelled": False,
        "hold": False,
        "line_items": extra.pop("line_items", [
            {"name": "Cup 16oz", "quantity": 1000, "production_process": "print"},
        ]),
        "total_quantity": extra.pop("total_quantity", 1000),
        "packing_required_total": extra.pop("packing_required_total", 1000),
        "design_files": [{"id": "d1"}],
        "design_status": "Approved",
        "payment_files": [{"id": "p1"}],
        "payment_status": "Approved",
        "operator_splits": extra.pop("operator_splits", [
            {"qty_completed": 1000, "line_idx": 0, "quantity": 1000},
        ]),
        "screener_info": {"screen_qty": 2},
        "helper_prep": {"qty_prepared": 1000},
        "sample_files": [{"id": "s1"}],
        "sample_approved": True,
        "packing_status": "Sudah",
        "packing_info": extra.pop("packing_info", {
            "qty_packed": 1000,
            "lines": [{"line_idx": 0, "packed_qty": 1000}],
        }),
        "surat_jalan_created": True,
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "2026-09-08T01:00:00+00:00",
        "delivery_note_created_by": "admin@x.com",
        "shipments": extra.pop("shipments", [{
            "id": "sj1",
            "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 1000}],
        }]),
        "qty_delivered": extra.pop("qty_delivered", 0),
        "delivery_status": extra.pop("delivery_status", "Belum Terkirim"),
    }
    o.update(extra)
    return o


def _badge(row):
    wf = row["workflow"]
    keys = ["order", "design", "appr_design", "payment", "appr_payment",
            "jadwal", "screen", "helper", "operator", "sample",
            "cetak", "packing", "sj", "loading", "kirim"]
    if all(wf.get(k) in ("completed", "skipped", "na", "not_applicable") for k in keys):
        number = 15
    elif row["status"] == "Terkirim":
        number = 15
    elif row["status"] in ("Parsial Terkirim", "Pengiriman"):
        number = 15
    elif row["status"] == "Loading":
        number = 14
    elif row["status"] in ("Surat Jalan", "SJ parsial"):
        number = 13
    else:
        number = None
    return f"{number} {row['status']}"


def test_modern_full_delivery_is_15_terkirim_100():
    o = _print_po(
        current_stage=14,
        qty_delivered=1000,
        delivery_status="Terkirim",
        terkirim_confirmed=True,
        loading_completed=True,
        loading_qty_status="completed",
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sj1",
            "shipment_id": "sj1",
            "lines": [{"line_idx": 0, "qty": 1000}],
        }],
        shipments=[{
            "id": "sj1", "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 1000}],
            "delivery_status": "Terkirim",
            "delivered_at": "2026-09-09T01:00:00+00:00",
        }],
    )
    row = status_po_row(o)
    assert row["status"] == "Terkirim"
    assert row["workflow"]["sj"] == "completed"
    assert row["workflow"]["loading"] == "completed"
    assert row["workflow"]["kirim"] == "completed"
    assert _workflow_progress(row["workflow"]) == 100
    assert _badge(row) == "15 Terkirim"
    assert "13 Terkirim" not in _badge(row)


def test_legacy_full_delivery_skips_loading_without_mutating():
    original = _print_po(
        current_stage=14,
        qty_delivered=1000,
        delivery_status="Terkirim",
        terkirim_confirmed=True,
    )
    snapshot = dict(original)
    row = status_po_row(original)
    assert original == snapshot
    assert row["status"] == "Terkirim"
    assert row["workflow"]["sj"] == "completed"
    assert row["workflow"]["loading"] == "skipped"
    assert row["workflow_reasons"]["loading"] == "legacy_no_loading_evidence"
    assert row["workflow"]["kirim"] == "completed"
    assert _workflow_progress(row["workflow"]) == 100
    assert _badge(row) == "15 Terkirim"


def test_sj_without_loading_cannot_confirm_and_kirim_not_completed():
    o = _print_po()
    assert new_delivery_block_reason(o) == MSG_DELIVERY_LOADING_INCOMPLETE
    wf = status_po_workflow(o)
    assert wf["sj"] == "active"
    assert wf["loading"] == "pending"
    assert wf["kirim"] == "pending"
    row = status_po_row(o)
    assert row["status"] == "Surat Jalan"
    assert _badge(row) == "13 Surat Jalan"
    assert _workflow_progress(wf) < 100


def test_partial_loading_caps_new_delivery():
    o = _print_po(
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sj1",
            "shipment_id": "sj1",
            "lines": [{"line_idx": 0, "qty": 400}],
        }],
        shipments=[{
            "id": "sj1", "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 1000}],
        }],
    )
    assert new_delivery_block_reason(o, o["shipments"][0], requested_qty=1000) == MSG_DELIVERY_LOADING_INCOMPLETE
    assert new_delivery_block_reason(o, o["shipments"][0], requested_qty=400) is None
    row = status_po_row(o)
    assert row["status"] == "Loading"
    assert row["workflow"]["loading"] == "active"
    assert row["workflow"]["kirim"] == "pending"
    assert _workflow_progress(row["workflow"]) < 100


def test_multi_sj_loading_does_not_cover_sibling():
    o = _print_po(
        total_quantity=1000,
        packing_required_total=1000,
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        shipments=[
            {"id": "sj-a", "number": "SJ-A", "lines": [{"line_idx": 0, "qty": 400}]},
            {"id": "sj-b", "number": "SJ-B", "lines": [{"line_idx": 0, "qty": 600}]},
        ],
        loading_confirmations=[{
            "id": "c-a",
            "surat_jalan_id": "sj-a",
            "shipment_id": "sj-a",
            "lines": [{"line_idx": 0, "qty": 400}],
        }],
    )
    assert new_delivery_block_reason(o, o["shipments"][0], requested_qty=400) is None
    assert new_delivery_block_reason(o, o["shipments"][1], requested_qty=600) == MSG_DELIVERY_LOADING_INCOMPLETE


def test_multi_item_lines_validated_separately():
    o = _print_po(
        line_items=[
            {"name": "A", "quantity": 1000, "production_process": "print"},
            {"name": "B", "quantity": 500, "production_process": "print"},
        ],
        total_quantity=1500,
        packing_required_total=1500,
        operator_splits=[
            {"qty_completed": 1000, "line_idx": 0, "quantity": 1000},
            {"qty_completed": 500, "line_idx": 1, "quantity": 500},
        ],
        helper_prep={"qty_prepared": 1500},
        packing_info={"qty_packed": 1500, "lines": [
            {"line_idx": 0, "packed_qty": 1000},
            {"line_idx": 1, "packed_qty": 500},
        ]},
        shipments=[{
            "id": "sj1", "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 1000}, {"line_idx": 1, "qty": 500}],
        }],
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sj1",
            "shipment_id": "sj1",
            "lines": [{"line_idx": 0, "qty": 1000}],
        }],
    )
    assert new_delivery_block_reason(o, o["shipments"][0], requested_qty=1500) == MSG_DELIVERY_LOADING_INCOMPLETE


def test_cancelled_sj_is_ignored():
    o = _print_po(
        shipments=[
            {"id": "sj-old", "number": "SJ-OLD", "status": "cancelled", "lines": [{"line_idx": 0, "qty": 1000}]},
            {"id": "sj1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 1000}]},
        ],
        loading_confirmations=[{
            "id": "c-old",
            "surat_jalan_id": "sj-old",
            "shipment_id": "sj-old",
            "cancelled": True,
            "lines": [{"line_idx": 0, "qty": 1000}],
        }],
    )
    assert new_delivery_block_reason(o, o["shipments"][1], requested_qty=1000) == MSG_DELIVERY_LOADING_INCOMPLETE


def test_non_print_skips_packing_but_loading_still_required():
    o = _print_po(
        line_items=[{"name": "Lid 90", "quantity": 1000, "production_process": "non_print"}],
        operator_splits=[],
        screener_info={},
        helper_prep={},
        sample_files=[],
        sample_approved=False,
        packing_info={},
        packing_status=None,
    )
    wf = status_po_workflow(o)
    assert wf["packing"] == "completed"
    assert wf["loading"] == "pending"
    assert wf["kirim"] == "pending"
    assert new_delivery_block_reason(o) == MSG_DELIVERY_LOADING_INCOMPLETE


def test_partial_delivery_stays_below_100():
    o = _print_po(
        current_stage=12,
        qty_delivered=400,
        delivery_status="Sebagian Terkirim",
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "sj1",
            "shipment_id": "sj1",
            "lines": [{"line_idx": 0, "qty": 400}],
        }],
        shipments=[{
            "id": "sj1", "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 400}],
            "delivery_status": "Terkirim",
            "delivered_at": "t",
        }],
    )
    row = status_po_row(o)
    assert row["status"] == "Parsial Terkirim"
    assert row["workflow"]["kirim"] == "active"
    assert _workflow_progress(row["workflow"]) < 100
    assert _badge(row) == "15 Parsial Terkirim"


def test_projection_parity_for_dispatch_stages():
    cases = [
        _print_po(),
        _print_po(
            current_stage=14, qty_delivered=1000, delivery_status="Terkirim",
            loading_confirmations=[{
                "id": "c1", "surat_jalan_id": "sj1", "shipment_id": "sj1",
                "lines": [{"line_idx": 0, "qty": 1000}],
            }],
        ),
        _print_po(current_stage=14, qty_delivered=1000, delivery_status="Terkirim"),
        _print_po(
            qty_delivered=400, delivery_status="Sebagian Terkirim",
            loading_confirmations=[{
                "id": "c1", "surat_jalan_id": "sj1", "shipment_id": "sj1",
                "lines": [{"line_idx": 0, "qty": 400}],
            }],
            shipments=[
                {"id": "sj1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 400}]},
                {"id": "sj2", "number": "SJ-2", "lines": [{"line_idx": 0, "qty": 600}]},
            ],
        ),
    ]
    for order in cases:
        full = status_po_workflow(order)
        lean = status_po_workflow(apply_status_po_projection(order))
        assert lean["sj"] == full["sj"]
        assert lean["loading"] == full["loading"]
        assert lean["kirim"] == full["kirim"]
        assert effective_workflow_status(apply_status_po_projection(order)) == effective_workflow_status(order)


def test_desktop_and_pwa_share_status_and_progress():
    o = _print_po(current_stage=14, qty_delivered=1000, delivery_status="Terkirim")
    row = status_po_row(o)
    pwa = serialize_list_row(o, [], [])
    assert pwa["status"] == row["status"] == "Terkirim"
    assert pwa["progress_percent"] == _workflow_progress(row["workflow"]) == 100
    assert pwa["workflow"]["loading"] == "skipped"
