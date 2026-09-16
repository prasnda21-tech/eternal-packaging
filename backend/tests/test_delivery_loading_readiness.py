"""Delivery readiness = Loading vs selected SJ (per line). Packing gates Loading only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    MSG_PHOTO_LOADING_INSUFFICIENT,
    MSG_PHOTO_LOADING_ITEM_MISMATCH,
    MSG_PHOTO_SJ_NOT_READY,
    REASON_LOADING_INCOMPLETE,
    REASON_LOADING_ITEM_MISMATCH,
    REASON_SJ_NOT_READY,
    apply_delivery_from_proof_evidence,
    delivery_batch_photo_readiness,
    validate_delivery_qty,
    validate_loading_confirmation,
)


def _cup_lid(
    *,
    packed_cup=5000,
    sj_cup=5000,
    sj_lid=5000,
    loaded_cup=5000,
    loaded_lid=5000,
    shipment_id="s1",
    other_sj_loading=None,
    cancelled_loading=False,
    duplicate_loading=False,
):
    confs = []
    if loaded_cup or loaded_lid:
        confs.append({
            "id": "c1",
            "surat_jalan_id": shipment_id,
            "cancelled": bool(cancelled_loading),
            "lines": [
                {"line_idx": 0, "qty": loaded_cup},
                {"line_idx": 1, "qty": loaded_lid},
            ],
        })
    if duplicate_loading:
        confs.append({
            "id": "c-dup",
            "surat_jalan_id": shipment_id,
            "lines": [
                {"line_idx": 0, "qty": loaded_cup},
                {"line_idx": 1, "qty": loaded_lid},
            ],
        })
    if other_sj_loading:
        confs.append(other_sj_loading)
    return {
        "id": "ord-argo",
        "po_number": "PO/TEST/CUP-LID",
        "line_items": [
            {
                "name": "12 oz Oval Inject Bening Klir",
                "quantity": 5000,
                "line_id": "L0",
                "production_process": "print",
                "accurate_item_id": "cup-1",
            },
            {
                "name": "LID Inject Flip Bening Victory",
                "quantity": 5000,
                "line_id": "L1",
                "production_process": "non_print",
                "accurate_item_id": "lid-1",
            },
        ],
        "total_quantity": 10000,
        "packing_info": {
            "qty_packed": packed_cup,
            "lines": [{"line_idx": 0, "packed_qty": packed_cup}],
        },
        "shipments": [{
            "id": shipment_id,
            "number": "SJ-1",
            "sales_delivery_id": shipment_id,
            "lines": [
                {"line_idx": 0, "qty": sj_cup},
                {"line_idx": 1, "qty": sj_lid},
            ],
            "total_qty": sj_cup + sj_lid,
        }],
        "surat_jalan_files": [{
            "id": shipment_id,
            "shipment_id": shipment_id,
            "sj_number": "SJ-1",
            "lines": [
                {"line_idx": 0, "qty": sj_cup},
                {"line_idx": 1, "qty": sj_lid},
            ],
            "total_qty": sj_cup + sj_lid,
        }],
        "loading_confirmations": confs,
        "qty_delivered": 0,
        "delivery_proofs": [],
        "delivery_status": "Belum Terkirim",
    }


def test_single_item_sj_fully_loaded_ready():
    o = {
        "line_items": [{"name": "Cup", "quantity": 3000, "production_process": "print"}],
        "packing_info": {"qty_packed": 3000, "lines": [{"line_idx": 0, "packed_qty": 3000}]},
        "shipments": [{
            "id": "s1", "number": "SJ-1", "sales_delivery_id": "s1",
            "lines": [{"line_idx": 0, "qty": 3000}], "total_qty": 3000,
        }],
        "loading_confirmations": [{
            "id": "c1", "surat_jalan_id": "s1",
            "lines": [{"line_idx": 0, "qty": 3000}],
        }],
    }
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is True
    assert r["allowed"] is True
    assert r["reason_code"] is None


def test_multi_item_print_fully_loaded_ready():
    o = {
        "line_items": [
            {"name": "A", "quantity": 2000, "production_process": "print"},
            {"name": "B", "quantity": 3000, "production_process": "print"},
        ],
        "packing_info": {
            "qty_packed": 5000,
            "lines": [{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 3000}],
        },
        "shipments": [{
            "id": "s1", "sales_delivery_id": "s1",
            "lines": [{"line_idx": 0, "qty": 2000}, {"line_idx": 1, "qty": 3000}],
            "total_qty": 5000,
        }],
        "loading_confirmations": [{
            "id": "c1", "surat_jalan_id": "s1",
            "lines": [{"line_idx": 0, "qty": 2000}, {"line_idx": 1, "qty": 3000}],
        }],
    }
    assert delivery_batch_photo_readiness(o)["delivery_ready"] is True


def test_cup_lid_fully_loaded_packing_excludes_lid_ready():
    """Incident shape: Packing cup only; Loading cup+LID; Delivery must be ready."""
    o = _cup_lid()
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is True
    assert r["allowed"] is True
    assert r["required_total"] == 10000
    assert r["loaded_total"] == 10000
    assert r["packed_available"] == 5000  # diagnostic only — not a gate
    assert r["reason"] is None
    assert all(ln["shortage"] == 0 for ln in r["lines"])
    assert validate_delivery_qty(10000, o) is None
    ev = apply_delivery_from_proof_evidence(o)
    assert ev is not None
    assert ev["qty_delivered"] == 10000


def test_one_loaded_item_missing_blocks_with_detail():
    o = _cup_lid(loaded_cup=5000, loaded_lid=0)
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE
    assert "LID" in (r["reason"] or "")
    lid = next(ln for ln in r["lines"] if ln["line_idx"] == 1)
    assert lid["shortage"] == 5000


def test_aggregate_equal_wrong_composition_mismatch():
    """Overload cup, underload LID — totals can look fine but per-line fails."""
    o = _cup_lid(loaded_cup=10000, loaded_lid=0, sj_cup=5000, sj_lid=5000)
    # loaded_total via SJ line caps in readiness uses per-line loaded amounts
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    short = [ln for ln in r["lines"] if ln["shortage"] > 0]
    assert short and short[0]["line_idx"] == 1
    assert r["reason_code"] in (REASON_LOADING_INCOMPLETE, REASON_LOADING_ITEM_MISMATCH)


def test_incomplete_loading_blocks():
    o = _cup_lid(loaded_cup=2000, loaded_lid=2000)
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE


def test_zero_packing_blocks_loading_for_print_item():
    o = _cup_lid(packed_cup=0, loaded_cup=0, loaded_lid=0)
    err = validate_loading_confirmation(o, "s1", [{"line_idx": 0, "qty": 1}])
    assert err
    assert "Packing" in err or "packing" in err.lower()


def test_loading_cannot_exceed_packing_for_print():
    o = _cup_lid(packed_cup=1000, loaded_cup=0, loaded_lid=0)
    err = validate_loading_confirmation(o, "s1", [{"line_idx": 0, "qty": 1001}])
    assert err


def test_inventory_only_lid_follows_non_print_loading_rule():
    o = _cup_lid(packed_cup=5000, loaded_cup=0, loaded_lid=0)
    assert validate_loading_confirmation(o, "s1", [{"line_idx": 1, "qty": 5000}]) is None
    err = validate_loading_confirmation(o, "s1", [{"line_idx": 1, "qty": 5001}])
    assert err


def test_loading_from_another_sj_does_not_count():
    o = _cup_lid(
        loaded_cup=0,
        loaded_lid=0,
        other_sj_loading={
            "id": "c-other",
            "surat_jalan_id": "other-sj",
            "lines": [{"line_idx": 0, "qty": 5000}, {"line_idx": 1, "qty": 5000}],
        },
    )
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["loaded_total"] == 0


def test_cancelled_loading_does_not_count():
    o = _cup_lid(cancelled_loading=True)
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["loaded_total"] == 0


def test_duplicate_loading_does_not_overcount_readiness_gate():
    """Duplicate confirmations may inflate loaded_total; readiness still per-line >= required."""
    o = _cup_lid(duplicate_loading=True)
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is True
    assert r["loaded_total"] >= 10000


def test_multiple_sj_uses_active_final_batch():
    o = _cup_lid(shipment_id="s1", loaded_cup=5000, loaded_lid=5000)
    o["shipments"].append({
        "id": "s2",
        "number": "SJ-2",
        "sales_delivery_id": "s2",
        "lines": [{"line_idx": 0, "qty": 1000}, {"line_idx": 1, "qty": 1000}],
        "total_qty": 2000,
    })
    # Active batch = latest shipment s2 — not fully loaded
    r = delivery_batch_photo_readiness(o)
    assert r["shipment_id"] == "s2"
    assert r["delivery_ready"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE


def test_no_sj_sj_not_ready():
    o = _cup_lid()
    o["shipments"] = []
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["reason_code"] == REASON_SJ_NOT_READY
    assert r["reason"] == MSG_PHOTO_SJ_NOT_READY


def test_readiness_calculation_does_not_mutate_order():
    o = _cup_lid()
    before = repr(o)
    _ = delivery_batch_photo_readiness(o)
    assert repr(o) == before


def test_confirm_revalidates_physical_not_raw_packing_aggregate():
    """Packed cup 5k only must not block confirm of loaded 10k cup+LID."""
    o = _cup_lid()
    assert validate_delivery_qty(10000, o) is None
    # Corrupt print overload still blocked by physical ceiling
    bad = _cup_lid(packed_cup=1000, loaded_cup=5000, loaded_lid=5000)
    err = validate_delivery_qty(10000, bad)
    assert err is not None
    assert "1000" in err or "fisik" in err.lower() or "eligible" in err.lower()


def test_partial_sj_line_loading_not_enough_for_photos():
    o = _cup_lid(loaded_cup=5000, loaded_lid=4999)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is False
    assert MSG_PHOTO_LOADING_ITEM_MISMATCH.split()[0] in (r["reason"] or "") or r["reason_code"] == REASON_LOADING_INCOMPLETE


def test_non_print_loading_bounded_by_selected_sj_not_po_total():
    """PO may hold LID 10k; selected SJ requiring 5k caps Loading at 5k."""
    o = _cup_lid(sj_lid=5000, loaded_cup=0, loaded_lid=0)
    o["line_items"][1]["quantity"] = 10000
    o["total_quantity"] = 15000
    assert validate_loading_confirmation(o, "s1", [{"line_idx": 1, "qty": 5000}]) is None
    err = validate_loading_confirmation(o, "s1", [{"line_idx": 1, "qty": 6000}])
    assert err and "5000" in err
    err2 = validate_loading_confirmation(o, "s1", [{"line_idx": 1, "qty": 5001}])
    assert err2 and "5000" in err2


def test_other_sj_loading_does_not_authorize_selected_sj():
    o = _cup_lid(loaded_cup=0, loaded_lid=0)
    o["loading_confirmations"] = [{
        "id": "c-other",
        "surat_jalan_id": "other-sj",
        "lines": [{"line_idx": 0, "qty": 5000}, {"line_idx": 1, "qty": 5000}],
    }]
    r = delivery_batch_photo_readiness(o)
    assert r["delivery_ready"] is False
    assert r["loaded_total"] == 0


def test_wrong_non_print_item_cannot_substitute():
    o = _cup_lid(loaded_cup=0, loaded_lid=0)
    o["line_items"].append({
        "name": "SEDOTAN", "quantity": 5000, "production_process": "non_print", "line_id": "L2",
    })
    err = validate_loading_confirmation(o, "s1", [{"line_idx": 2, "qty": 5000}])
    assert err  # SJ has no line 2 allocation


def test_legacy_no_sj_lines_multi_item_blocks_aggregate_bypass():
    o = {
        "line_items": [
            {"name": "Cup", "quantity": 5000, "production_process": "print"},
            {"name": "LID", "quantity": 5000, "production_process": "non_print"},
        ],
        "packing_info": {"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        "shipments": [{"id": "s1", "number": "SJ-1", "sales_delivery_id": "s1", "total_qty": 10000}],
        "loading_confirmations": [{
            "id": "c1", "surat_jalan_id": "s1",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        "delivery_proofs": [{"id": "p1"}, {"id": "p2"}],
    }
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is False
    assert r["reason_code"] == REASON_LOADING_ITEM_MISMATCH
    from quantity_rules import confirm_delivery_block_reason, apply_delivery_from_proof_evidence
    assert confirm_delivery_block_reason(o)
    assert apply_delivery_from_proof_evidence(o) is None


def test_legacy_no_sj_lines_single_item_still_supported():
    o = {
        "line_items": [{"name": "Cup", "quantity": 3000, "production_process": "print"}],
        "packing_info": {"qty_packed": 3000, "lines": [{"line_idx": 0, "packed_qty": 3000}]},
        "shipments": [{"id": "s1", "number": "SJ-1", "sales_delivery_id": "s1", "total_qty": 3000}],
        "loading_confirmations": [{
            "id": "c1", "surat_jalan_id": "s1",
            "lines": [{"line_idx": 0, "qty": 3000}],
        }],
    }
    assert delivery_batch_photo_readiness(o)["allowed"] is True


def test_confirm_independently_revalidates_loading_vs_sj():
    from quantity_rules import confirm_delivery_block_reason, apply_delivery_from_proof_evidence

    def _with_proofs(o):
        o = {**o}
        o["delivery_proofs"] = [{"id": "p1"}, {"id": "p2"}]
        o["delivery_recipient_signature"] = {
            "id": "sig",
            "recipient_name": "Budi Penerima",
            "signature_storage_path": "eternal-packaging/delivery-signature/PO/sig.png",
            "recipient_signed_at": "2026-09-07T10:00:00+00:00",
            "frozen": False,
        }
        o["delivery_driver_signature"] = {
            "id": "dsig",
            "driver_signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig.png",
            "signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig.png",
            "driver_signed_at": "2026-09-07T10:01:00+00:00",
            "frozen": False,
        }
        return o

    full = _with_proofs(_cup_lid())
    assert apply_delivery_from_proof_evidence(full) is not None
    assert confirm_delivery_block_reason(full) is None

    incomplete = _with_proofs(_cup_lid(loaded_lid=0))
    assert apply_delivery_from_proof_evidence(incomplete) is None
    blocked = confirm_delivery_block_reason(incomplete)
    assert blocked
    assert "Loading" in blocked or "LID" in blocked

    # Stale page: Loading removed after open → Confirm re-reads and rejects
    stale = _with_proofs(_cup_lid())
    assert apply_delivery_from_proof_evidence(stale) is not None
    stale["loading_confirmations"] = []
    assert apply_delivery_from_proof_evidence(stale) is None
    assert confirm_delivery_block_reason(stale)


def test_legacy_loading_info_makes_selected_sj_ready_for_pengiriman():
    """SJ/2026/09/10/000020: historical 2.000 Loading unlocks Delivery without rewriting physical qty."""
    from quantity_rules import (
        apply_delivery_from_proof_evidence as apply_ev,
        confirm_delivery_block_reason,
        loaded_qty_for_sj_line,
        loading_verification_status,
        qty_loaded,
    )
    shipment_id = "ship-2026-09-10-000020"
    sj_number = "SJ/2026/09/10/000020"
    o = {
        "id": "po-sep10",
        "po_number": "PO/2026/09/10/000020",
        "line_items": [{"name": "Cup", "quantity": 2000, "production_process": "print", "line_id": "L0"}],
        "packing_info": {"qty_packed": 2000, "lines": [{"line_idx": 0, "line_id": "L0", "packed_qty": 2000}]},
        "shipments": [{
            "id": shipment_id,
            "number": sj_number,
            "sales_delivery_id": shipment_id,
            "lines": [{"line_idx": 0, "line_id": "L0", "qty": 2000}],
            "total_qty": 2000,
        }],
        "surat_jalan_files": [{
            "id": "file-000020",
            "shipment_id": shipment_id,
            "sj_number": sj_number,
            "storage_path": "/sj/000020.pdf",
            "uploaded_at": "2026-09-10T08:00:00Z",
            "lines": [{"line_idx": 0, "line_id": "L0", "qty": 2000}],
            "total_qty": 2000,
        }],
        "loading_confirmations": [],
        "loading_info": {"lines": [{"line_idx": 0, "loaded_qty": 2000}]},
        "qty_delivered": 0,
        "delivery_proofs": [{"id": "p1"}, {"id": "p2"}],
        "delivery_recipient_signature": {
            "id": "sig",
            "recipient_name": "Budi Penerima",
            "signature_storage_path": "sig.png",
            "recipient_signed_at": "t",
            "frozen": False,
        },
        "delivery_driver_signature": {
            "id": "dsig",
            "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "t",
            "frozen": False,
        },
    }
    assert qty_loaded(o) == 2000
    assert loaded_qty_for_sj_line(o, shipment_id, 0) == 2000
    assert loaded_qty_for_sj_line(o, sj_number, 0) == 2000
    r = delivery_batch_photo_readiness(o, o["shipments"][0])
    assert r["delivery_ready"] is True
    assert r["loaded_total"] == 2000
    assert loading_verification_status(o, shipment_id).get("code") == "loading_complete"
    assert confirm_delivery_block_reason(o, o["shipments"][0]) is None
    ev = apply_ev(o, o["shipments"][0])
    assert ev is not None
    assert ev["qty_delivered"] == 2000
