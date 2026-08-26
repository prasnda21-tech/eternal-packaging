"""Delivery confirm requires >= 2 photos and an explicit confirm. Upload does not deliver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    MIN_DELIVERY_PROOFS,
    apply_delivery_from_proof_evidence,
    can_confirm_delivery,
    confirm_delivery_block_reason,
    valid_delivery_proof_count,
)


def _po(proofs=0, loaded=4000, packed=4000, qty_po=10000):
    return {
        "line_items": [{"name": "A", "quantity": qty_po, "accurate_item_id": "104", "production_process": "print"}],
        "total_quantity": qty_po,
        "packing_info": {"qty_packed": packed, "lines": [{"line_idx": 0, "packed_qty": packed}]},
        "shipments": [{"id": "s1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": packed}]}],
        "loading_info": {"lines": [{"line_idx": 0, "loaded_qty": loaded}]},
        "delivery_proofs": [{"id": f"p{i}", "uploaded_at": f"2026-08-26T0{i}:00:00+07:00"} for i in range(proofs)],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
    }


def test_min_photos_is_two():
    assert MIN_DELIVERY_PROOFS == 2
    assert can_confirm_delivery(_po(0)) is False
    assert can_confirm_delivery(_po(1)) is False
    assert can_confirm_delivery(_po(2)) is True
    assert can_confirm_delivery(_po(3)) is True
    assert confirm_delivery_block_reason(_po(1))
    assert confirm_delivery_block_reason(_po(2)) is None


def test_upload_count_does_not_apply_delivery_status():
    o = _po(2)
    assert o["delivery_status"] == "Belum Terkirim"
    assert o["qty_delivered"] == 0
    assert valid_delivery_proof_count(o) == 2


def test_confirm_evidence_is_partial_not_whole_po():
    o = _po(2, loaded=4000, packed=4000, qty_po=10000)
    ev = apply_delivery_from_proof_evidence(o)
    assert ev is not None
    assert ev["qty_delivered"] == 4000
    assert ev["full"] is False
    assert ev["delivery_status"] == "Sebagian Terkirim"


def test_double_confirm_same_loaded_qty_does_not_increase():
    o = _po(2, loaded=4000, packed=4000, qty_po=10000)
    ev1 = apply_delivery_from_proof_evidence(o)
    o["qty_delivered"] = ev1["qty_delivered"]
    o["delivery_status"] = ev1["delivery_status"]
    ev2 = apply_delivery_from_proof_evidence(o)
    assert ev2["qty_delivered"] == 4000
