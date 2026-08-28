"""Loading / delivery proof eligibility when packing qty is empty."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    apply_loading_confirmation,
    eligible_delivery_qty,
    find_sj_file,
    has_surat_jalan_file,
    qty_packed,
    sj_file_is_active,
    validate_loading_confirmation,
)


def _po(**extra):
    o = {
        "line_items": [{"name": "A", "quantity": 1000, "production_process": "print"}],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 0, "lines": [{"line_idx": 0, "packed_qty": 0}]},
        "surat_jalan_files": extra.pop("surat_jalan_files", [{
            "id": "f1", "name": "sj.pdf", "uploaded_at": "2026-08-28T10:00:00+07:00",
        }]),
        "loading_confirmations": [],
        "shipments": [],
    }
    o.update(extra)
    return o


def test_case1_no_sj_no_packing_loading_rejected():
    o = _po(surat_jalan_files=[])
    assert qty_packed(o) == 0
    assert has_surat_jalan_file(o) is False
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 10}])


def test_case2_empty_packing_valid_sj_loading_allowed():
    o = _po()
    assert qty_packed(o) == 0
    assert has_surat_jalan_file(o) is True
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 100}]) is None
    apply_loading_confirmation(o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 100}], confirmation_id="c1")
    assert qty_packed(o) == 0
    assert o["packing_info"]["qty_packed"] == 0
    assert eligible_delivery_qty(o) == 100


def test_case3_empty_packing_valid_sj_proof_after_loading():
    o = _po()
    assert eligible_delivery_qty(o) == 0
    apply_loading_confirmation(o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 250}], confirmation_id="c1")
    assert eligible_delivery_qty(o) == 250
    assert qty_packed(o) == 0


def test_case4_cancelled_sj_does_not_open_gate():
    o = _po(surat_jalan_files=[{
        "id": "f1", "name": "sj.pdf", "status": "cancelled", "uploaded_at": "2026-08-28T10:00:00+07:00",
    }])
    assert sj_file_is_active(o["surat_jalan_files"][0]) is False
    assert has_surat_jalan_file(o) is False
    assert find_sj_file(o, "f1") is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 10}])


def test_case4_deleted_sj_does_not_open_gate():
    o = _po(surat_jalan_files=[{
        "id": "f1", "name": "sj.pdf", "is_deleted": True, "uploaded_at": "2026-08-28T10:00:00+07:00",
    }])
    assert has_surat_jalan_file(o) is False
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 10}])


def test_case5_normal_packing_unchanged():
    o = _po(
        packing_info={"qty_packed": 400, "lines": [{"line_idx": 0, "packed_qty": 400}]},
        shipments=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 400}]}],
        surat_jalan_files=[],
    )
    assert qty_packed(o) == 400
    assert eligible_delivery_qty(o) == 400
