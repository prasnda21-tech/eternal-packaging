"""Loading confirm qty is capped at packed physical qty, not remaining PO qty.

Does not change Packing, Surat Jalan, Bukti Pengiriman, or permission rules.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    SHIPMENT_SOURCE_MANUAL,
    available_for_loading_confirm_line,
    eligible_delivery_qty,
    loading_confirm_cap_for_line,
    shipment_line_breakdown,
    validate_loading_confirmation,
    validate_loading_increments,
    validate_packing_lines,
    validate_shipment_lines,
)
from services.division_tasks import build_division_tasks
from permissions import ROLE_DEFAULT_PERMISSIONS


def _po(*, packed=0, qty_po=1000, print_completed=0, **extra):
    o = {
        "id": extra.pop("id", "ord-load"),
        "po_number": "PO/LOAD",
        "customer": "Cust",
        "cancelled": False,
        "current_stage": 12,
        "line_items": [{
            "name": "Karton",
            "quantity": qty_po,
            "production_process": "print",
        }],
        "total_quantity": qty_po,
        "operator_splits": (
            [{"line_idx": 0, "quantity": print_completed, "qty_completed": print_completed}]
            if print_completed else []
        ),
        "packing_info": {
            "qty_packed": packed,
            "lines": [{"line_idx": 0, "packed_qty": packed}] if packed else [],
        },
        "surat_jalan_files": extra.pop("surat_jalan_files", [{
            "id": "f1",
            "name": "sj.pdf",
            "shipment_id": "f1",
            "sj_number": "SJ-1",
        }]),
        "shipments": extra.pop("shipments", [{
            "id": "f1",
            "number": "SJ-1",
        }]),
        "loading_confirmations": extra.pop("loading_confirmations", []),
        "loading_info": extra.pop("loading_info", {}),
    }
    o.update(extra)
    return o


def test_loading_blocked_when_packed_qty_empty():
    o = _po(packed=0, qty_po=1000)
    assert loading_confirm_cap_for_line(o, 0) == 0
    assert available_for_loading_confirm_line(o, 0) == 0
    err = validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 1}])
    assert err
    assert "fisik" in err.lower() or "packing" in err.lower()


def test_print_completed_without_packing_does_not_unlock_loading():
    o = _po(packed=0, qty_po=1000, print_completed=800)
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 100}])


def test_sj_file_does_not_bypass_packing_for_loading():
    o = _po(packed=0, qty_po=1000)
    o["surat_jalan_created"] = True
    o["delivery_note_number"] = "SJ-1"
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 10}])


def test_loading_capped_at_packed_not_po_qty():
    o = _po(packed=400, qty_po=1000)
    assert loading_confirm_cap_for_line(o, 0) == 400
    assert available_for_loading_confirm_line(o, 0) == 400
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 400}]) is None
    err = validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 401}])
    assert err
    assert "401" in err
    assert "400" in err


def test_loading_remaining_subtracts_already_loaded():
    o = _po(
        packed=500,
        qty_po=1000,
        loading_confirmations=[{"id": "c1", "surat_jalan_id": "f1", "lines": [{"line_idx": 0, "qty": 300}]}],
    )
    assert available_for_loading_confirm_line(o, 0) == 200
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 200}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 201}])


def test_loading_cap_is_per_line_not_po_total():
    o = {
        "line_items": [
            {"name": "A", "quantity": 1000, "production_process": "print"},
            {"name": "B", "quantity": 800, "production_process": "print"},
        ],
        "total_quantity": 1800,
        "packing_info": {
            "qty_packed": 700,
            "lines": [
                {"line_idx": 0, "packed_qty": 500},
                {"line_idx": 1, "packed_qty": 200},
            ],
        },
        "surat_jalan_files": [{"id": "f1", "name": "sj.pdf", "shipment_id": "f1", "sj_number": "SJ-1"}],
        "loading_confirmations": [],
        "loading_info": {},
        "shipments": [{"id": "f1", "number": "SJ-1"}],
    }
    assert loading_confirm_cap_for_line(o, 0) == 500
    assert loading_confirm_cap_for_line(o, 1) == 200
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 500}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 501}])
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 1, "qty": 200}]) is None
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 1, "qty": 201}])


def test_shipment_lines_snapshot_uses_packed_cap():
    o = _po(packed=400, qty_po=1000)
    rows = shipment_line_breakdown(o)
    assert rows[0]["loading_cap"] == 400
    assert rows[0]["available_loading"] == 400
    assert rows[0]["qty_po"] == 1000
    assert rows[0]["packed"] == 400


def test_loading_task_not_siap_when_physical_qty_missing():
    o = _po(packed=0)
    rows = build_division_tasks([o], "loading", {"email": "a@x.com"}, mine_only=False)
    assert len(rows) == 1
    assert rows[0]["remaining_qty"] == 0
    assert rows[0]["status"] != "siap"


def test_nonreg_sj_qty_rule_still_requires_packed():
    o = _po(packed=0, qty_po=1000)
    err = validate_shipment_lines([{"line_idx": 0, "qty": 100}], o, source=SHIPMENT_SOURCE_MANUAL)
    assert err
    packed = _po(packed=400, qty_po=1000)
    assert validate_shipment_lines([{"line_idx": 0, "qty": 100}], packed, source=SHIPMENT_SOURCE_MANUAL) is None


def test_nonreg_packing_rule_unchanged():
    o = _po(packed=0, qty_po=1000, print_completed=400)
    o["helper_prep"] = {"qty_prepared": 400}
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 400}], o) is None
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 401}], o) is not None


def test_nonreg_historical_sj_delivery_without_loading_confirm():
    o = _po(
        packed=400,
        shipments=[{"id": "sj-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 400}]}],
        packing_info={"qty_packed": 400, "lines": [{"line_idx": 0, "packed_qty": 400}]},
    )
    assert eligible_delivery_qty(o) == 400


def test_nonreg_legacy_loading_increment_still_uses_packed_shipped_cap():
    o = _po(
        packed=400,
        qty_po=1000,
        shipments=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 400}]}],
    )
    assert validate_loading_increments([{"line_idx": 0, "qty": 400}], o) is None
    assert validate_loading_increments([{"line_idx": 0, "qty": 401}], o) is not None


def test_nonreg_loading_permission_catalog_unchanged():
    assert "loading.update" in ROLE_DEFAULT_PERMISSIONS["driver"]
    assert "loading.update" not in ROLE_DEFAULT_PERMISSIONS["helper"]
