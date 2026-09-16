"""Delivery confirm requires >= 2 photos and an explicit confirm. Upload does not deliver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    MIN_DELIVERY_PROOFS,
    apply_delivery_from_proof_evidence,
    can_confirm_delivery,
    confirm_delivery_block_reason,
    delivery_proofs_for_upload_limit,
    valid_delivery_proof_count,
    valid_delivery_proofs_for_shipment,
)


def _po(proofs=0, loaded=4000, packed=4000, qty_po=10000, with_signature=False):
    order = {
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
    if with_signature:
        order["delivery_recipient_signature"] = {
            "id": "sig-1",
            "signature_file_id": "sig-1",
            "recipient_name": "Budi Penerima",
            "signature_storage_path": "eternal-packaging/delivery-signature/PO/sig-1.png",
            "recipient_signed_at": "2026-08-26T10:00:00+00:00",
            "frozen": False,
            "shipment_id": "s1",
            "sj_file_id": "s1",
        }
        order["delivery_driver_signature"] = {
            "id": "dsig-1",
            "driver_signature_file_id": "dsig-1",
            "signature_file_id": "dsig-1",
            "driver_signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig-1.png",
            "signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig-1.png",
            "driver_signed_at": "2026-08-26T10:01:00+00:00",
            "driver_name": "Driver Budi",
            "frozen": False,
            "shipment_id": "s1",
            "sj_file_id": "s1",
        }
    return order


def test_min_photos_is_two():
    assert MIN_DELIVERY_PROOFS == 2
    assert can_confirm_delivery(_po(0)) is False
    assert can_confirm_delivery(_po(1)) is False
    assert can_confirm_delivery(_po(2)) is True
    assert can_confirm_delivery(_po(3)) is True
    assert confirm_delivery_block_reason(_po(1))
    # 2 photos alone is not enough anymore — signature + name required
    assert confirm_delivery_block_reason(_po(2)) is not None
    assert confirm_delivery_block_reason(_po(2, with_signature=True)) is None


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


def test_signature_gate_messages():
    assert "foto" in (confirm_delivery_block_reason(_po(1)) or "").lower()
    assert "tanda tangan" in (confirm_delivery_block_reason(_po(2)) or "").lower()
    o = _po(2, with_signature=True)
    o["delivery_recipient_signature"]["recipient_name"] = ""
    assert "nama" in (confirm_delivery_block_reason(o) or "").lower()
    o2 = _po(2, with_signature=True)
    assert confirm_delivery_block_reason(o2) is None
    o3 = _po(2, with_signature=True)
    o3.pop("delivery_driver_signature", None)
    assert "driver" in (confirm_delivery_block_reason(o3) or "").lower()


def _multi_sj_photo_po(proofs=None):
    return {
        "line_items": [
            {"name": "Cup A", "quantity": 1000, "line_id": "LA", "production_process": "print"},
            {"name": "Cup B", "quantity": 800, "line_id": "LB", "production_process": "print"},
            {"name": "Lid C", "quantity": 600, "line_id": "LC", "production_process": "non_print"},
        ],
        "total_quantity": 2400,
        "packing_info": {
            "qty_packed": 1800,
            "lines": [{"line_idx": 0, "packed_qty": 1000}, {"line_idx": 1, "packed_qty": 800}],
        },
        "shipments": [
            {
                "id": "demo-multisj-photo-001",
                "number": "DEMO-MULTISJ-PHOTO-001",
                "lines": [{"line_idx": 0, "line_id": "LA", "qty": 1000}],
                "total_qty": 1000,
            },
            {
                "id": "demo-multisj-photo-002",
                "number": "DEMO-MULTISJ-PHOTO-002",
                "lines": [{"line_idx": 1, "line_id": "LB", "qty": 800}],
                "total_qty": 800,
            },
            {
                "id": "demo-multisj-photo-003",
                "number": "DEMO-MULTISJ-PHOTO-003",
                "lines": [{"line_idx": 2, "line_id": "LC", "qty": 600}],
                "total_qty": 600,
            },
        ],
        "loading_confirmations": [
            {"id": "c1", "surat_jalan_id": "demo-multisj-photo-001", "lines": [{"line_idx": 0, "qty": 1000}]},
            {"id": "c2", "surat_jalan_id": "demo-multisj-photo-002", "lines": [{"line_idx": 1, "qty": 800}]},
            {"id": "c3", "surat_jalan_id": "demo-multisj-photo-003", "lines": [{"line_idx": 2, "qty": 600}]},
        ],
        "delivery_proofs": proofs or [],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
    }


def test_multi_sj_proofs_are_isolated_by_shipment_id():
    order = _multi_sj_photo_po([
        {"id": "p001", "shipment_id": "demo-multisj-photo-001"},
        {"id": "p002", "shipment_id": "demo-multisj-photo-002"},
        {"id": "p003", "shipment_id": "demo-multisj-photo-003"},
    ])
    sj1, sj2, sj3 = order["shipments"]
    assert [p["id"] for p in valid_delivery_proofs_for_shipment(order, sj1)] == ["p001"]
    assert [p["id"] for p in valid_delivery_proofs_for_shipment(order, sj2)] == ["p002"]
    assert [p["id"] for p in valid_delivery_proofs_for_shipment(order, sj3)] == ["p003"]


def test_untagged_legacy_proof_is_not_assigned_to_sibling_sj():
    order = _multi_sj_photo_po([
        {"id": "legacy-untagged"},
        {"id": "p001", "shipment_id": "demo-multisj-photo-001"},
    ])
    sj1, sj2, sj3 = order["shipments"]
    assert [p["id"] for p in valid_delivery_proofs_for_shipment(order, sj1)] == ["p001"]
    assert valid_delivery_proofs_for_shipment(order, sj2) == []
    assert valid_delivery_proofs_for_shipment(order, sj3) == []
    blocked = confirm_delivery_block_reason(order, sj2)
    assert blocked and "foto" in blocked.lower()


def test_single_sj_still_reads_untagged_proofs():
    order = _po(0)
    order["delivery_proofs"] = [{"id": "legacy"}]
    assert [p["id"] for p in valid_delivery_proofs_for_shipment(order, order["shipments"][0])] == ["legacy"]


def test_multi_sj_upload_limit_is_per_shipment_not_po():
    proofs = [{"id": f"p1-{i}", "shipment_id": "demo-multisj-photo-001"} for i in range(5)]
    proofs += [{"id": f"p2-{i}", "shipment_id": "demo-multisj-photo-002"} for i in range(2)]
    order = _multi_sj_photo_po(proofs)
    sj1, sj2, sj3 = order["shipments"]
    assert len(delivery_proofs_for_upload_limit(order, sj1)) == 5
    assert len(delivery_proofs_for_upload_limit(order, sj2)) == 2
    assert len(delivery_proofs_for_upload_limit(order, sj3)) == 0
    assert len(delivery_proofs_for_upload_limit(order, None)) == 7


def test_sibling_proof_does_not_satisfy_current_sj_confirm():
    order = _multi_sj_photo_po([
        {"id": "p001a", "shipment_id": "demo-multisj-photo-001"},
        {"id": "p001b", "shipment_id": "demo-multisj-photo-001"},
    ])
    blocked = confirm_delivery_block_reason(order, order["shipments"][1])
    assert blocked and "foto" in blocked.lower()
    blocked3 = confirm_delivery_block_reason(order, order["shipments"][2])
    assert blocked3 and "foto" in blocked3.lower()
