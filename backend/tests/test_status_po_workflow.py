"""Status PO matrix states come from order evidence, never idx < current_stage."""
from __future__ import annotations

import inspect
from pathlib import Path

from services.status_po import item_line_names, status_po_row, status_po_workflow


SRC = Path(__file__).resolve().parents[1] / "services" / "status_po.py"


def _order(**kw):
    base = {
        "id": "p1",
        "po_number": "PO-1",
        "customer": "Acme",
        "order_date": "2026-08-26",
        "current_stage": 11,
        "cancelled": False,
        "hold": False,
        "line_items": [
            {"name": "Cup 16oz", "quantity": 1000, "production_process": "print"},
            {"name": "Lid 98 mm", "quantity": 1000, "production_process": "print"},
            {"name": "Add On Warna", "quantity": 50, "production_process": "print"},
        ],
    }
    base.update(kw)
    return base


def test_source_has_no_sequential_stage_inference():
    text = SRC.read_text(encoding="utf-8")
    fn = inspect.getsource(status_po_workflow)
    assert "idx <" not in fn
    assert "stageIndex <" not in fn
    assert "current_stage" not in fn or "idx <" not in fn


def test_late_current_stage_does_not_complete_earlier_stages():
    wf = status_po_workflow(_order(
        current_stage=14,
        design_files=[],
        payment_files=[],
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
    ))
    assert wf["design"] == "pending"
    assert wf["payment"] == "pending"
    assert wf["jadwal"] == "pending"
    assert wf["order"] == "completed"


def test_design_and_payment_use_file_and_approval_evidence():
    wf = status_po_workflow(_order(
        design_files=[{"id": "d1"}],
        design_status="Approved",
        payment_files=[{"id": "p1"}],
        payment_status="Waiting",
    ))
    assert wf["design"] == "completed"
    assert wf["appr_design"] == "completed"
    assert wf["payment"] == "completed"
    assert wf["appr_payment"] == "active"


def test_receipt_settlement_marks_payment_without_payment_files():
    """Settlement-effective penerimaan: Payment + Appr. Payment without duplicate payment_files."""
    wf = status_po_workflow(_order(
        design_files=[{"id": "d1"}],
        design_status="Approved",
        payment_files=[],
        payment_status="Approved",
        payment_receipts=[{"receipt_id": "r1", "payment_state": "DP"}],
        payment_fact={"receipt_id": "r1", "payment_state": "DP"},
    ))
    assert wf["payment"] == "completed"
    assert wf["appr_payment"] == "completed"


def test_unsettled_receipt_does_not_complete_payment_stage():
    wf = status_po_workflow(_order(
        payment_files=[],
        payment_status="Waiting",
        payment_receipts=[],
        payment_fact=None,
    ))
    assert wf["payment"] == "pending"
    assert wf["appr_payment"] == "pending"


def test_stale_payment_receipts_without_approved_do_not_complete_payment():
    """Orphan projections must not mark Payment complete (deferred/cancelled race)."""
    from workflow_gates import payment_stage_done, payment_evidence_present

    order = _order(
        payment_files=[],
        payment_status="Waiting",
        payment_receipts=[{"receipt_id": "stale-r1", "payment_state": "DP"}],
        payment_fact={"receipt_id": "stale-r1"},
    )
    assert payment_stage_done(order) is False
    assert payment_evidence_present(order) is False
    wf = status_po_workflow(order)
    assert wf["payment"] == "pending"
    assert wf["appr_payment"] == "pending"


def test_legacy_payment_files_complete_payment_but_not_approval():
    wf = status_po_workflow(_order(
        payment_files=[{"id": "p1"}],
        payment_status="Waiting",
        payment_receipts=[],
    ))
    assert wf["payment"] == "completed"
    assert wf["appr_payment"] == "active"


def test_item_lines_are_names_only_one_po_one_list():
    names = item_line_names(_order())
    assert names == ["Cup 16oz", "Lid 98 mm", "Add On Warna"]
    blob = " ".join(names)
    assert "1000" not in blob
    assert "50" not in blob
    row = status_po_row(_order())
    assert row["item_lines"] == names
    assert "workflow" in row
    assert set(row["workflow"]) >= {
        "order", "design", "packing", "kirim",
    }


def test_cancelled_marks_every_stage():
    wf = status_po_workflow(_order(cancelled=True, design_files=[{"id": "d1"}]))
    assert set(wf.values()) == {"cancelled"}


def test_packing_active_from_semantic_not_from_index():
    wf = status_po_workflow(_order(
        packing_status_semantic="Berjalan",
        packing_info={"qty_packed": 10, "lines": [{"packed_qty": 10}]},
        packing_status="Berjalan",
        print_completed_qty=10,
        operator_splits=[{"qty_completed": 10, "quantity": 1000, "line_idx": 0}],
        sample_approved=True,
        sample_files=[{"id": "s1"}],
        helper_prep={"qty_prepared": 1000},
        screener_info={"screen_qty": 2},
        design_status="Approved",
        payment_status="Approved",
        design_files=[{"id": "d1"}],
        payment_files=[{"id": "p1"}],
    ))
    assert wf["packing"] in ("active", "completed")
    assert wf["design"] == "completed"
    assert wf["sample"] == "completed"
