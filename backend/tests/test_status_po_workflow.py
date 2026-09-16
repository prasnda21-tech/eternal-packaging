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
