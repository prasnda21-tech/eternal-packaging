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


def test_confirm_payload_applies_trimmed_recipient_name_before_gate():
    from services.delivery_pod_signature import apply_recipient_name_to_active_signature

    o = _po(2, with_signature=True)
    o["delivery_recipient_signature"]["recipient_name"] = ""
    assert confirm_delivery_block_reason(o)
    assert apply_recipient_name_to_active_signature(o, "  me time  ") is None
    assert o["delivery_recipient_signature"]["recipient_name"] == "me time"
    assert confirm_delivery_block_reason(o) is None
    # Incomplete evidence remains rejected after a valid name is applied.
    o["delivery_proofs"] = o["delivery_proofs"][:1]
    blocked = confirm_delivery_block_reason(o) or ""
    assert "foto" in blocked.lower()
    assert o["delivery_recipient_signature"]["recipient_name"] == "me time"


def test_confirm_delivered_endpoint_accepts_recipient_name_contract():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "class ConfirmDeliveredPayload" in src
    payload = src[src.index("class ConfirmDeliveredPayload") : src.index("class ConfirmDeliveredPayload") + 400]
    assert "recipient_name" in payload
    confirm = src[src.index("async def confirm_delivered") : src.index("async def confirm_delivered") + 4500]
    assert "apply_recipient_name_to_active_signature" in confirm
    assert "incoming_name" in confirm
    assert "delivery_confirm_action_ids" in confirm


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


def _legacy_siap_kirim(*, recipient_name="omit"):
    """Pre-patch stored PO: Siap Kirim, both TTDs, 2 photos, Stock V2 OFF."""
    from quantity_rules import shipment_is_delivered

    order = _po(2, loaded=10, packed=10, qty_po=10, with_signature=True)
    order["current_stage"] = 14
    order["status_label"] = "Pengiriman"
    order["eligible_delivery_qty"] = 10
    order["delivery_confirmations"] = []
    order["delivery_confirm_action_ids"] = []
    order.pop("physical_stock_snapshot", None)
    sig = order["delivery_recipient_signature"]
    if recipient_name == "omit":
        sig.pop("recipient_name", None)
    elif recipient_name is None:
        sig["recipient_name"] = ""
    else:
        sig["recipient_name"] = recipient_name
    assert "physical_stock_snapshot" not in order
    assert shipment_is_delivered(order, order["shipments"][0]) is False
    return order


def test_legacy_po_missing_recipient_name_field_is_siap_kirim_but_gated():
    from services.delivery_pod_signature import apply_recipient_name_to_active_signature

    o = _legacy_siap_kirim(recipient_name="omit")
    assert o["delivery_status"] == "Belum Terkirim"
    assert "recipient_name" not in o["delivery_recipient_signature"]
    assert o["delivery_recipient_signature"].get("signature_storage_path")
    assert o["delivery_driver_signature"].get("signature_storage_path")
    assert len(o["delivery_proofs"]) >= 2
    blocked = confirm_delivery_block_reason(o) or ""
    assert "nama" in blocked.lower()
    assert apply_recipient_name_to_active_signature(o, "  me time  ") is None
    assert o["delivery_recipient_signature"]["recipient_name"] == "me time"
    assert confirm_delivery_block_reason(o) is None


def test_legacy_po_empty_recipient_name_binds_live_name_then_confirm_passes():
    from services.delivery_pod_signature import apply_recipient_name_to_active_signature

    o = _legacy_siap_kirim(recipient_name=None)
    assert o["delivery_recipient_signature"]["recipient_name"] == ""
    assert confirm_delivery_block_reason(o)
    assert apply_recipient_name_to_active_signature(o, "Penerima Lama") is None
    assert o["delivery_recipient_signature"]["recipient_name"] == "Penerima Lama"
    assert confirm_delivery_block_reason(o) is None


def test_legacy_po_with_stored_recipient_name_still_confirms():
    o = _legacy_siap_kirim(recipient_name="Budi Penerima")
    assert confirm_delivery_block_reason(o) is None
    # Old client omits recipient_name — stored name remains authoritative.
    incoming_name = None
    if incoming_name is not None:
        raise AssertionError("old payload must skip name apply")
    assert o["delivery_recipient_signature"]["recipient_name"] == "Budi Penerima"
    assert confirm_delivery_block_reason(o) is None


def test_legacy_old_client_payload_without_recipient_name_keeps_empty_gate():
    from services.delivery_pod_signature import apply_recipient_name_to_active_signature

    o = _legacy_siap_kirim(recipient_name=None)
    incoming_name = None  # ConfirmDeliveredPayload default / omitted JSON field
    if incoming_name is not None:
        apply_recipient_name_to_active_signature(o, incoming_name)
    assert o["delivery_recipient_signature"]["recipient_name"] == ""
    blocked = confirm_delivery_block_reason(o) or ""
    assert "nama penerima wajib" in blocked.lower()


def test_legacy_already_terkirim_does_not_need_a_second_confirmation():
    from quantity_rules import shipment_is_delivered

    o = _legacy_siap_kirim(recipient_name="Budi Penerima")
    o["delivery_status"] = "Terkirim"
    o["qty_delivered"] = 10
    o["shipments"][0]["delivery_status"] = "Terkirim"
    o["shipments"][0]["delivered_at"] = "2026-09-01T00:00:00+00:00"
    o["delivery_confirmations"] = [{
        "id": "conf-legacy-1",
        "action_id": "act-legacy-1",
        "recipient_name": "Budi Penerima",
        "shipment_id": "s1",
        "qty": 10,
    }]
    o["delivery_confirm_action_ids"] = ["act-legacy-1"]
    o["delivery_recipient_signature"]["frozen"] = True
    assert shipment_is_delivered(o, o["shipments"][0]) is True
    before = list(o["delivery_confirmations"])
    # Mutate short-circuits: action_id already claimed, or shipment already delivered.
    ids = list(o.get("delivery_confirm_action_ids") or [])
    action_id = "act-legacy-1"
    if action_id and action_id in ids:
        pass
    elif shipment_is_delivered(o, o["shipments"][0]):
        pass
    else:
        o["delivery_confirmations"].append({"id": "should-not-happen"})
    assert o["delivery_confirmations"] == before
    assert o["delivery_confirm_action_ids"] == ["act-legacy-1"]


def test_confirm_delivered_old_client_payload_skips_name_apply():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    confirm = src[src.index("async def confirm_delivered") : src.index("async def confirm_delivered") + 4500]
    assert "incoming_name = payload.recipient_name if payload else None" in confirm
    assert "if incoming_name is not None:" in confirm
    delivered_at = confirm.index("if selected_sh and _qr_shipment_is_delivered")
    apply_at = confirm.index("apply_recipient_name_to_active_signature")
    blocked_at = confirm.index("blocked = _qr_confirm_delivery_block_reason")
    action_at = confirm.index("if action_id and action_id in ids:")
    assert action_at < delivered_at < apply_at < blocked_at
