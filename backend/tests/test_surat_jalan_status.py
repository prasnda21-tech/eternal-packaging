"""Surat Jalan status follows Loading fulfillment per PO line."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    apply_loading_confirmation,
    loading_fulfills_all_required_lines,
    surat_jalan_created,
    surat_jalan_status_semantic,
)
from services.customer_tracking import customer_workflow, public_payload
from services.status_po import STAGES
from services.workflow_stage_status import resolve_stage_chip, workflow_stage_rows


def _sj_po(**extra):
    o = {
        "id": "ord-sj-st",
        "po_number": "PO/SJ-ST",
        "current_stage": 13,
        "line_items": [
            {"name": "A", "quantity": 1000, "production_process": "print"},
        ],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "surat_jalan_files": [{
            "id": "f-sj-1",
            "name": "sj.pdf",
            "storage_path": "eternal-packaging/surat-jalan/x/f-sj-1.pdf",
            "uploaded_at": "2026-08-29T04:00:00+00:00",
            "uploaded_by": "crm@x.com",
            "uploaded_by_name": "Sinta",
            "shipment_id": "f-sj-1",
            "sj_number": "SJ-1",
        }],
        "shipments": [{"id": "f-sj-1", "number": "SJ-1"}],
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "2026-08-29T04:00:00+00:00",
        "delivery_note_created_by": "crm@x.com",
        "loading_confirmations": [],
        **extra,
    }
    return o


def _stage(rows, name):
    return next(r for r in rows if r["stage"] == name)


def test_no_sj_not_completed():
    o = _sj_po(surat_jalan_files=[], delivery_note_number=None, delivery_note_created_at=None, delivery_note_created_by=None)
    assert surat_jalan_created(o) is False
    assert surat_jalan_status_semantic(o) == "pending"


def test_sj_file_loading_short_is_partial():
    o = _sj_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 400}], confirmation_id="c1")
    assert surat_jalan_status_semantic(o) == "partial"
    chip = resolve_stage_chip(12, o, 13)
    assert "parsial" in chip["label"].lower()
    assert chip["state"] == "current"


def test_sj_file_loading_full_is_completed():
    o = _sj_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 1000}], confirmation_id="c1")
    assert loading_fulfills_all_required_lines(o)
    assert surat_jalan_status_semantic(o) == "completed"
    chip = resolve_stage_chip(12, o, 13)
    assert chip == {"state": "done", "label": "Selesai"}
    loading = resolve_stage_chip(13, o, 13)
    assert loading["label"] == "Selesai"
    rows = workflow_stage_rows(o)
    assert _stage(rows, "Surat Jalan")["label"] == "Selesai"
    assert _stage(rows, "Loading")["label"] == "Selesai"


def test_multi_line_one_short_not_completed():
    o = _sj_po(
        line_items=[
            {"name": "A", "quantity": 1000, "production_process": "print"},
            {"name": "B", "quantity": 500, "production_process": "print"},
        ],
        total_quantity=1500,
        packing_info={"qty_packed": 1500, "lines": [
            {"line_idx": 0, "packed_qty": 1000},
            {"line_idx": 1, "packed_qty": 500},
        ]},
    )
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 1000}], confirmation_id="c1")
    assert loading_fulfills_all_required_lines(o) is False
    assert surat_jalan_status_semantic(o) == "partial"
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 1, "qty": 500}], confirmation_id="c2")
    assert loading_fulfills_all_required_lines(o)
    assert surat_jalan_status_semantic(o) == "completed"


def test_grand_total_cannot_hide_line_shortfall():
    o = _sj_po(
        line_items=[
            {"name": "A", "quantity": 1000, "production_process": "print"},
            {"name": "B", "quantity": 500, "production_process": "print"},
        ],
        total_quantity=1500,
        packing_info={"qty_packed": 1500, "lines": [
            {"line_idx": 0, "packed_qty": 1000},
            {"line_idx": 1, "packed_qty": 500},
        ]},
        loading_confirmations=[{
            "id": "c1",
            "surat_jalan_id": "f-sj-1",
            "lines": [{"line_idx": 0, "qty": 1500}],
        }],
    )
    # Line A over, line B zero — remaining on B keeps SJ incomplete.
    assert surat_jalan_status_semantic(o) != "completed"


def test_live_tracking_fifteen_stages_match_alur():
    o = _sj_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 1000}], confirmation_id="c1")
    rows = workflow_stage_rows(o)
    assert [r["stage"] for r in rows] == list(STAGES)
    assert len(rows) == 15
    wf = customer_workflow(o)
    assert [r["stage"] for r in wf] == list(STAGES)
    assert _stage(wf, "Surat Jalan")["label"] == resolve_stage_chip(12, o)["label"]
    assert _stage(wf, "Loading")["label"] == resolve_stage_chip(13, o)["label"]
    assert _stage(wf, "Packing")["label"] == "Selesai"
    terkirim = _stage(wf, "Pengiriman")
    assert terkirim["label"] == "Menunggu bukti pengiriman"


def test_public_payload_keeps_sj_label():
    o = _sj_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 1000}], confirmation_id="c1")
    payload = public_payload(o)
    sj = next(r for r in payload["workflow"] if r["stage"] == "Surat Jalan")
    assert sj["label"] == "Selesai"
    assert sj["status"] == "completed"
