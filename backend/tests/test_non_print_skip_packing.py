"""Non-cetak skip Packing: SJ + Loading use PO qty; cetak still requires packed.

Does not invent packed_qty. SJ stage order preserved. Pengiriman untouched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    apply_shipment_record,
    available_for_delivery_note_line,
    available_for_loading_confirm_line,
    available_for_packing,
    available_for_packing_line,
    loading_confirm_cap_for_line,
    packing_line_breakdown,
    packing_status_semantic,
    physical_qty_for_loading_line,
    qty_packed_for_line,
    remaining_delivery_note_updates,
    SHIPMENT_SOURCE_MANUAL,
    validate_loading_confirmation,
    validate_packing_lines,
    validate_shipment_lines,
)
from services.division_tasks import build_packing_tasks, build_loading_tasks
from workflow_gates import packing_qty_input


def _mixed(*, print_qty=500, non_print_qty=300, packed_print=0, sj=True):
    o = {
        "id": "ord-mixed",
        "po_number": "PO/MIX",
        "cancelled": False,
        "current_stage": 12,
        "line_items": [
            {"name": "Cup Cetak", "quantity": print_qty, "production_process": "print"},
            {"name": "Sedotan", "quantity": non_print_qty, "production_process": "non_print"},
        ],
        "total_quantity": print_qty + non_print_qty,
        "operator_splits": [
            {"line_idx": 0, "quantity": print_qty, "qty_completed": print_qty},
        ],
        "packing_info": {
            "qty_packed": packed_print,
            "lines": ([{"line_idx": 0, "packed_qty": packed_print}] if packed_print else []),
        },
        "surat_jalan_files": [{"id": "f1", "name": "sj.pdf"}] if sj else [],
        "shipments": [],
        "loading_confirmations": [],
        "loading_info": {},
    }
    return o


def _non_print_only(*, qty=400, sj=False, stage=11):
    return {
        "id": "ord-np",
        "po_number": "PO/NP",
        "cancelled": False,
        "current_stage": stage,
        "line_items": [
            {"name": "Box Polos", "quantity": qty, "production_process": "non_print"},
        ],
        "total_quantity": qty,
        "packing_info": {},
        "surat_jalan_files": [{"id": "f1", "name": "sj.pdf"}] if sj else [],
        "shipments": [],
        "loading_confirmations": [],
        "loading_info": {},
    }


def test_non_print_excluded_from_packing_breakdown_and_tasks():
    o = _mixed(packed_print=0)
    rows = packing_line_breakdown(o)
    assert all(r["is_print"] for r in rows)
    assert all(r["line_idx"] == 0 for r in rows)
    assert available_for_packing_line(o, 1) == 0
    assert available_for_packing(o) == 500
    tasks = build_packing_tasks(o)
    assert all(t.get("line_idx") != 1 for t in tasks)


def test_non_print_packed0_can_allocate_sj():
    """E2E gate 1: non-cetak packing=0 boleh SJ tanpa invent packed_qty."""
    o = _non_print_only(qty=400, sj=False, stage=12)
    assert qty_packed_for_line(o, 0) == 0
    assert available_for_delivery_note_line(o, 0) == 400
    rem = remaining_delivery_note_updates(o)
    assert rem == [{"line_idx": 0, "qty": 400}]
    assert validate_shipment_lines([{"line_idx": 0, "qty": 400}], o) is None
    assert validate_shipment_lines([{"line_idx": 0, "qty": 401}], o)
    assert packing_qty_input(o) is True
    assert packing_status_semantic(o) == "Sudah"
    # still no fake packed rows
    assert not (o.get("packing_info") or {}).get("lines")


def test_non_print_sj_then_loading_e2e():
    """E2E: packed=0 → SJ line qty → Loading confirm, still no packed_qty."""
    o = _non_print_only(qty=400, sj=False, stage=12)
    assert validate_shipment_lines([{"line_idx": 0, "qty": 250}], o) is None
    apply_shipment_record(
        o,
        number="SJ-NP-1",
        lines=[{"line_idx": 0, "qty": 250}],
        source=SHIPMENT_SOURCE_MANUAL,
        actor={"email": "sj@x.com"},
        shipment_id="sh1",
        created_at="2026-01-01T00:00:00Z",
    )
    # Attach SJ file identity used by Loading confirm
    o["surat_jalan_files"] = [{
        "id": "f1",
        "name": "sj.pdf",
        "shipment_id": "sh1",
        "lines": [{"line_idx": 0, "qty": 250}],
        "total_qty": 250,
    }]
    assert qty_packed_for_line(o, 0) == 0
    assert not (o.get("packing_info") or {}).get("lines")
    assert physical_qty_for_loading_line(o, 0) == 400
    assert available_for_loading_confirm_line(o, 0) == 400
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 250}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 251}])
    rows = build_loading_tasks(o)
    assert rows and rows[0]["remaining_qty"] > 0


def test_print_packed0_sj_and_loading_still_blocked():
    o = _mixed(packed_print=0, sj=True)
    assert available_for_delivery_note_line(o, 0) == 0
    assert validate_shipment_lines([{"line_idx": 0, "qty": 1}], o)
    assert loading_confirm_cap_for_line(o, 0) == 0
    err = validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 1}])
    assert err
    assert "packing" in err.lower() or "Packing" in err


def test_mixed_non_print_sj_while_print_unpacked():
    o = _mixed(packed_print=0, sj=False)
    o["current_stage"] = 12
    assert available_for_delivery_note_line(o, 0) == 0
    assert available_for_delivery_note_line(o, 1) == 300
    assert validate_shipment_lines([{"line_idx": 1, "qty": 300}], o) is None
    assert validate_shipment_lines([{"line_idx": 0, "qty": 1}, {"line_idx": 1, "qty": 300}], o)


def test_mixed_order_print_packed_non_print_direct():
    o = _mixed(packed_print=200)
    assert loading_confirm_cap_for_line(o, 0) == 200
    assert loading_confirm_cap_for_line(o, 1) == 300
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 200}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 1, "qty": 300}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 201}])
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 1, "qty": 301}])
    assert validate_packing_lines([{"line_idx": 1, "packed_qty": 10}], o)


def test_over_sj_and_over_loading_rejected():
    o = _non_print_only(qty=100, sj=True, stage=12)
    assert validate_shipment_lines([{"line_idx": 0, "qty": 101}], o)
    err = validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 101}])
    assert err
    assert "101" in err


def test_no_fake_packed_qty_after_sj_loading_helpers():
    o = _non_print_only(qty=50, sj=True)
    _ = available_for_delivery_note_line(o, 0)
    _ = physical_qty_for_loading_line(o, 0)
    _ = loading_confirm_cap_for_line(o, 0)
    assert qty_packed_for_line(o, 0) == 0
    assert o.get("packing_info", {}) == {}
