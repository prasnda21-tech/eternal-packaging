"""Multi-SJ allocation, Loading isolation, and selected-SJ Pengiriman."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    SHIPMENT_SOURCE_MANUAL,
    apply_delivery_from_proof_evidence,
    available_for_delivery_note_line,
    confirm_delivery_block_reason,
    delivery_batch_photo_readiness,
    find_shipment,
    is_fully_delivered,
    qty_delivered,
    qty_shipped_for_line,
    remaining_delivery_note_updates,
    resolve_delivery_shipment,
    shipment_is_delivered,
    validate_loading_confirmation,
    validate_shipment_lines,
)
from services.delivery_history import append_confirmation


def _po2000(*, shipments=None, loading=None, proofs=None, delivered=0, extra_lines=None):
    lines = extra_lines or [{
        "name": "Line A",
        "quantity": 2000,
        "line_id": "LA",
        "accurate_item_id": "item-a",
        "production_process": "print",
    }]
    ships = list(shipments or [])
    return {
        "id": "po-2000",
        "po_number": "PO/2000",
        "line_items": lines,
        "total_quantity": sum(int(li.get("quantity") or 0) for li in lines),
        "packing_info": {
            "qty_packed": 2000 if extra_lines is None else sum(
                int((li.get("quantity") or 0) if str(li.get("production_process")) != "non_print" else 0)
                for li in lines
            ),
            "lines": (
                [{"line_idx": 0, "packed_qty": 2000}]
                if extra_lines is None
                else [{"line_idx": i, "packed_qty": int(li.get("quantity") or 0)} for i, li in enumerate(lines)]
            ),
        },
        "shipments": ships,
        "surat_jalan_files": [
            {
                "id": sh.get("id"),
                "shipment_id": sh.get("id"),
                "sj_number": sh.get("number"),
                "lines": sh.get("lines") or [],
            }
            for sh in ships
        ],
        "loading_confirmations": list(loading or []),
        "delivery_proofs": list(proofs if proofs is not None else [{"id": "p1"}, {"id": "p2"}]),
        "delivery_confirmations": [],
        "qty_delivered": delivered,
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
        "delivery_recipient_signature": {
            "id": "sig-1",
            "recipient_name": "Budi Penerima",
            "signature_storage_path": "sig.png",
            "recipient_signed_at": "t",
            "frozen": False,
        },
        "delivery_driver_signature": {
            "id": "dsig-1",
            "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "t",
            "frozen": False,
        },
    }


def _sj(sid, qty, *, date="2026-09-10", line_idx=0, line_id="LA", name="Line A"):
    return {
        "id": sid,
        "number": f"SJ/{sid}",
        "shipment_index": 1 if sid.endswith("1") else 2,
        "sales_delivery_id": sid,
        "delivery_date": date,
        "lines": [{"line_idx": line_idx, "line_id": line_id, "qty": qty, "name": name}],
        "total_qty": qty,
    }


def test_1_po2000_sj1_1000_remaining_is_1000():
    o = _po2000(shipments=[_sj("sj-1", 1000)])
    assert available_for_delivery_note_line(o, 0) == 1000
    assert remaining_delivery_note_updates(o) == [{"line_idx": 0, "qty": 1000}]


def test_2_sj2_1000_remaining_is_0():
    o = _po2000(shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")])
    assert available_for_delivery_note_line(o, 0) == 0
    assert remaining_delivery_note_updates(o) == []


def test_3_sj2_creation_does_not_mutate_sj1():
    sj1 = _sj("sj-1", 1000)
    o = _po2000(shipments=[sj1])
    err = validate_shipment_lines([{"line_idx": 0, "line_id": "LA", "qty": 1000}], o, source=SHIPMENT_SOURCE_MANUAL)
    assert err is None
    o["shipments"].append(_sj("sj-2", 1000, date="2026-09-12"))
    assert o["shipments"][0]["id"] == "sj-1"
    assert o["shipments"][0]["lines"][0]["qty"] == 1000
    assert o["shipments"][0]["delivery_date"] == "2026-09-10"
    assert o["shipments"][0]["number"] == "SJ/sj-1"


def test_4_and_5_loading_requirement_is_selected_sj_qty():
    o = _po2000(shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")])
    r1 = delivery_batch_photo_readiness(o, o["shipments"][0])
    r2 = delivery_batch_photo_readiness(o, o["shipments"][1])
    assert r1["required_total"] == 1000
    assert r2["required_total"] == 1000
    assert r1["loaded_total"] == 0
    assert r2["loaded_total"] == 0


def test_6_sj1_loaded_leaves_sj2_unloaded():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
    )
    assert delivery_batch_photo_readiness(o, o["shipments"][0])["allowed"] is True
    assert delivery_batch_photo_readiness(o, o["shipments"][1])["allowed"] is False


def test_7_8_pengiriman_shows_only_selected_batch():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
    )
    r1 = delivery_batch_photo_readiness(o, find_shipment(o, "sj-1"))
    r2 = delivery_batch_photo_readiness(o, find_shipment(o, "sj-2"))
    assert r1["shipment_id"] == "sj-1"
    assert r1["batch_qty"] == 1000
    assert [ln["line_idx"] for ln in r1["lines"]] == [0]
    assert r2["shipment_id"] == "sj-2"
    assert r2["loaded_total"] == 0


def test_9_loading_gate_is_per_sj():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        proofs=[
            {"id": "p1", "shipment_id": "sj-1"},
            {"id": "p2", "shipment_id": "sj-1"},
            {"id": "p3", "shipment_id": "sj-2"},
            {"id": "p4", "shipment_id": "sj-2"},
        ],
    )
    o["delivery_recipient_signature"]["shipment_id"] = "sj-1"
    o["delivery_driver_signature"]["shipment_id"] = "sj-1"
    assert confirm_delivery_block_reason(o, o["shipments"][0]) is None
    o["delivery_recipient_signature"]["shipment_id"] = "sj-2"
    o["delivery_driver_signature"]["shipment_id"] = "sj-2"
    blocked = confirm_delivery_block_reason(o, o["shipments"][1])
    assert blocked
    assert "Loading" in blocked or "belum mencukupi" in blocked.lower() or "belum lengkap" in blocked.lower()


def test_10_ship_sj1_leaves_sj2_unshipped():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        proofs=[{"id": "p1", "shipment_id": "sj-1"}, {"id": "p2", "shipment_id": "sj-1"}],
    )
    ev = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    assert ev["qty_delivered"] == 1000
    assert ev["delivery_status"] == "Sebagian Terkirim"
    assert ev["full"] is False
    o["qty_delivered"] = ev["qty_delivered"]
    o["delivery_status"] = ev["delivery_status"]
    o["shipments"][0]["delivered_at"] = "t"
    o["shipments"][0]["delivery_status"] = "Terkirim"
    append_confirmation(o, {"email": "a", "name": "A"}, qty_increment=1000, now="t", shipment=o["shipments"][0])
    assert shipment_is_delivered(o, o["shipments"][0]) is True
    assert shipment_is_delivered(o, o["shipments"][1]) is False
    assert is_fully_delivered(o) is False


def test_11_12_13_ship_both_then_po_fully_delivered():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[
            {"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]},
            {"id": "c2", "surat_jalan_id": "sj-2", "lines": [{"line_idx": 0, "qty": 1000}]},
        ],
        proofs=[{"id": "p1", "shipment_id": "sj-1"}, {"id": "p2", "shipment_id": "sj-1"}],
    )
    ev1 = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    o["qty_delivered"] = ev1["qty_delivered"]
    o["delivery_status"] = ev1["delivery_status"]
    o["shipments"][0]["delivered_at"] = "t1"
    o["shipments"][0]["delivery_status"] = "Terkirim"
    assert ev1["full"] is False
    assert is_fully_delivered(o) is False
    ev2 = apply_delivery_from_proof_evidence(o, o["shipments"][1])
    assert ev2["qty_delivered"] == 2000
    assert ev2["full"] is True
    o["qty_delivered"] = ev2["qty_delivered"]
    o["delivery_status"] = ev2["delivery_status"]
    assert is_fully_delivered(o) is True


def test_14_multi_line_split_isolated():
    lines = [
        {"name": "Line A", "quantity": 1000, "line_id": "LA", "accurate_item_id": "a", "production_process": "print"},
        {"name": "Line B", "quantity": 1000, "line_id": "LB", "accurate_item_id": "b", "production_process": "print"},
    ]
    sj1 = _sj("sj-1", 1000, line_idx=0, line_id="LA", name="Line A")
    sj2 = _sj("sj-2", 1000, date="2026-09-12", line_idx=1, line_id="LB", name="Line B")
    o = _po2000(
        extra_lines=lines,
        shipments=[sj1, sj2],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
    )
    r1 = delivery_batch_photo_readiness(o, sj1)
    r2 = delivery_batch_photo_readiness(o, sj2)
    assert [ln["line_idx"] for ln in r1["lines"]] == [0]
    assert [ln["line_idx"] for ln in r2["lines"]] == [1]
    assert r1["allowed"] is True
    assert r2["allowed"] is False


def test_15_same_name_lines_isolated():
    lines = [
        {"name": "Item X", "quantity": 1000, "line_id": "L0", "accurate_item_id": "AX", "production_process": "print"},
        {"name": "Item X", "quantity": 1000, "line_id": "L1", "accurate_item_id": "AX", "production_process": "print"},
    ]
    sj1 = _sj("sj-1", 1000, line_idx=0, line_id="L0", name="Item X")
    sj2 = _sj("sj-2", 1000, date="2026-09-12", line_idx=1, line_id="L1", name="Item X")
    o = _po2000(extra_lines=lines, shipments=[sj1, sj2], loading=[
        {"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]},
    ])
    r1 = delivery_batch_photo_readiness(o, sj1)
    r2 = delivery_batch_photo_readiness(o, sj2)
    assert r1["lines"][0]["line_idx"] == 0
    assert r2["lines"][0]["line_idx"] == 1
    assert r1["allowed"] is True
    assert r2["allowed"] is False


def test_16_packed_zero_does_not_invent_allocation():
    o = _po2000(shipments=[])
    o["packing_info"] = {"qty_packed": 0, "lines": [{"line_idx": 0, "packed_qty": 0}]}
    assert available_for_delivery_note_line(o, 0) == 0
    err = validate_shipment_lines([{"line_idx": 0, "line_id": "LA", "qty": 1000}], o)
    assert err


def test_17_duplicate_loading_blocked_by_remaining():
    o = _po2000(
        shipments=[_sj("sj-1", 1000)],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
    )
    err = validate_loading_confirmation(o, "sj-1", [{"line_idx": 0, "qty": 1000}])
    assert err


def test_18_duplicate_pengiriman_is_idempotent():
    o = _po2000(
        shipments=[_sj("sj-1", 1000)],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
    )
    ev1 = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    o["qty_delivered"] = ev1["qty_delivered"]
    o["shipments"][0]["delivered_at"] = "t"
    o["shipments"][0]["delivery_status"] = "Terkirim"
    ev2 = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    assert ev2["increment"] == 0
    assert ev2["qty_delivered"] == ev1["qty_delivered"]


def test_19_selected_sj_not_latest_batch():
    o = _po2000(
        shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000, date="2026-09-12")],
        loading=[{"id": "c1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        proofs=[{"id": "p1", "shipment_id": "sj-1"}, {"id": "p2", "shipment_id": "sj-1"}],
    )
    latest, err = resolve_delivery_shipment(o, None)
    assert latest is None
    assert err
    selected, err2 = resolve_delivery_shipment(o, "sj-1")
    assert err2 is None
    assert selected["id"] == "sj-1"
    ev = apply_delivery_from_proof_evidence(o, selected)
    assert ev["shipment_id"] == "sj-1"
    assert ev["qty_delivered"] == 1000
    default = apply_delivery_from_proof_evidence(o)
    assert default is None


def test_20_date_specific_sj_identity_preserved():
    o = _po2000(shipments=[
        _sj("sj-1", 1000, date="2026-09-10"),
        _sj("sj-2", 1000, date="2026-09-12"),
    ])
    assert o["shipments"][0]["delivery_date"] == "2026-09-10"
    assert o["shipments"][1]["delivery_date"] == "2026-09-12"
    assert qty_shipped_for_line(o, 0) == 2000
    assert find_shipment(o, "sj-1")["delivery_date"] == "2026-09-10"


def test_missing_shipment_id_rejected_when_multi_sj():
    o = _po2000(shipments=[_sj("sj-1", 1000), _sj("sj-2", 1000)])
    sh, err = resolve_delivery_shipment(o, "missing")
    assert sh is None
    assert "tidak ditemukan" in err


def test_single_shipment_defaults_without_client_id():
    o = _po2000(shipments=[_sj("sj-1", 1000)])
    sh, err = resolve_delivery_shipment(o, None)
    assert err is None
    assert sh["id"] == "sj-1"


def test_qty_delivered_helper_tracks_po_level():
    o = _po2000(delivered=1000)
    assert qty_delivered(o) == 1000
    assert is_fully_delivered(o) is False
