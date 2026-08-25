"""Partial Surat Jalan, loading per line, Accurate mock import, screener isolation.

No live API / Mongo / Accurate production.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    SHIPMENT_SOURCE_ACCURATE,
    SHIPMENT_SOURCE_MANUAL,
    apply_delivery_from_proof_evidence,
    apply_loading_increments,
    apply_shipment_record,
    available_for_delivery_note_line,
    available_for_loading_line,
    helper_prepared_print_qty,
    loading_cap_for_line,
    loading_status_semantic,
    operator_assignment_cap,
    qty_loaded_for_line,
    qty_shipped,
    qty_shipped_for_line,
    remaining_delivery_note_updates,
    shipment_line_mismatches,
    surat_jalan_status_semantic,
    validate_loading_increments,
    validate_loading_lines,
    validate_screener_qty,
    validate_shipment_lines,
    validate_print_completed_total,
)
from services.accurate import (
    ACCURATE_DELIVERY_ENDPOINT_AVAILABLE,
    fetch_delivery_notes,
    get_mock_delivery_note,
    list_mock_delivery_notes,
)
from workflow_gates import screener_done, operator_unlocked


def _po_a(packed=4000, shipped_lines=None, loaded_lines=None, qty_po=10000):
    order = {
        "line_items": [
            {"name": "Item A", "quantity": qty_po, "production_process": "print", "accurate_item_id": "104"},
        ],
        "total_quantity": qty_po,
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": packed}], "qty_packed": packed},
        "helper_prep": {"qty_prepared": packed},
        "operator_splits": [{"line_idx": 0, "quantity": packed, "qty_completed": packed}],
        "current_stage": 12,
        "shipments": [],
        "loading_info": {},
    }
    if shipped_lines:
        order["shipments"] = shipped_lines
    if loaded_lines:
        order["loading_info"] = {"lines": loaded_lines}
    return order


def _po_ab(packed_a=4000, packed_b=1500):
    return {
        "line_items": [
            {"name": "A", "quantity": 10000, "production_process": "print", "accurate_item_id": "104"},
            {"name": "B", "quantity": 5000, "production_process": "print", "accurate_item_id": "105"},
        ],
        "total_quantity": 15000,
        "packing_info": {
            "lines": [
                {"line_idx": 0, "packed_qty": packed_a},
                {"line_idx": 1, "packed_qty": packed_b},
            ],
            "qty_packed": packed_a + packed_b,
        },
        "helper_prep": {"qty_prepared": packed_a + packed_b},
        "operator_splits": [
            {"line_idx": 0, "quantity": packed_a, "qty_completed": packed_a},
            {"line_idx": 1, "quantity": packed_b, "qty_completed": packed_b},
        ],
        "current_stage": 12,
        "shipments": [],
    }


# --- Scenario 1: Screener independent of prepared ---

def test_screener_qty_accepted_when_helper_prepared_zero():
    o = _po_a(packed=0)
    o["helper_prep"] = {"qty_required": 10000, "qty_prepared": 0}
    o["screener_info"] = {}
    assert validate_screener_qty(25, o) is None
    o["screener_info"] = {"screen_qty": 25}
    assert screener_done(o)
    assert validate_screener_qty(-1, o) is not None
    assert "Prepared" not in (validate_screener_qty(25, o) or "")
    assert "Remain" not in (validate_screener_qty(25, o) or "")


def test_helper_prepared_still_caps_print():
    o = _po_a(packed=0)
    o["helper_prep"] = {"qty_prepared": 1000}
    o["operator_splits"] = [{"quantity": 1000, "qty_completed": 0}]
    o["line_items"][0]["quantity"] = 10000
    o["total_quantity"] = 10000
    assert helper_prepared_print_qty(o) == 1000
    assert operator_assignment_cap(o) == 1000
    o["operator_splits"] = [{"quantity": 1000, "qty_completed": 1001}]
    assert validate_print_completed_total(1001, o) is not None


# --- Scenario 2 / 3: partial packing → SJ ---

def test_partial_packing_opens_sj_max_packed_not_po():
    o = _po_a(packed=4000)
    assert remaining_delivery_note_updates(o) == [{"line_idx": 0, "qty": 4000}]
    assert available_for_delivery_note_line(o, 0) == 4000
    assert validate_shipment_lines([{"line_idx": 0, "qty": 4000}], o) is None
    assert validate_shipment_lines([{"line_idx": 0, "qty": 4001}], o) is not None


def test_second_partial_sj_remaining_3000():
    o = _po_a(packed=7000, shipped_lines=[{
        "id": "s1",
        "accurate_shipment_id": None,
        "source": SHIPMENT_SOURCE_MANUAL,
        "lines": [{"line_idx": 0, "qty": 4000}],
    }])
    assert available_for_delivery_note_line(o, 0) == 3000
    assert validate_shipment_lines([{"line_idx": 0, "qty": 3000}], o) is None
    assert validate_shipment_lines([{"line_idx": 0, "qty": 3001}], o) is not None
    assert surat_jalan_status_semantic(o) == "partial"


# --- Scenario 4 / 5: loading partial + overload ---

def test_loading_partial_remaining_1500():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
        loaded_lines=[{"line_idx": 0, "loaded_qty": 2500}],
    )
    assert available_for_loading_line(o, 0) == 1500
    assert validate_loading_lines([{"line_idx": 0, "loaded_qty": 4000}], o) is None
    assert validate_loading_lines([{"line_idx": 0, "loaded_qty": 4001}], o) is not None


def test_overload_loading_rejected_server_rule():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
    )
    err = validate_loading_lines([{"line_idx": 0, "loaded_qty": 4001}], o)
    assert err is not None
    assert "4001" in err or "packing" in err.lower() or "melebihi" in err.lower()


# --- Scenario 6: multi item independent ---

def test_multi_item_loading_independent():
    o = _po_ab(packed_a=4000, packed_b=1500)
    apply_shipment_record(
        o, number="SJ-1", lines=[{"line_idx": 0, "qty": 4000}, {"line_idx": 1, "qty": 1500}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="s1", created_at="t",
    )
    o["loading_info"] = {"lines": [
        {"line_idx": 0, "loaded_qty": 2000},
        {"line_idx": 1, "loaded_qty": 500},
    ]}
    assert available_for_loading_line(o, 0) == 2000
    assert available_for_loading_line(o, 1) == 1000


# --- Scenario 7–10: Accurate import ---

def test_import_accurate_sj_line_mapping_by_item_id():
    o = _po_ab(packed_a=4000, packed_b=1500)
    o["accurate_id"] = "ACC-SO-2001"
    o["po_number"] = "PO-2001"
    lines = [
        {"accurate_item_id": "104", "qty": 3000},
        {"accurate_item_id": "105", "qty": 1000},
    ]
    assert validate_shipment_lines(lines, o, source=SHIPMENT_SOURCE_ACCURATE) is None
    rec = apply_shipment_record(
        o, number="SJ-2001-01", lines=lines, source=SHIPMENT_SOURCE_ACCURATE,
        accurate_shipment_id="ACC-DO-2001-1", shipment_id="x", created_at="t",
    )
    assert rec["lines"][0]["line_idx"] == 0
    assert rec["lines"][1]["line_idx"] == 1
    assert qty_shipped_for_line(o, 0) == 3000
    assert qty_shipped_for_line(o, 1) == 1000
    assert available_for_loading_line(o, 0) == 3000
    assert available_for_loading_line(o, 1) == 1000
    assert surat_jalan_status_semantic(o) == "partial"


def test_import_idempotent_same_accurate_id():
    o = _po_a(packed=4000)
    lines = [{"accurate_item_id": "104", "qty": 4000}]
    apply_shipment_record(
        o, number="SJ-1", lines=lines, source=SHIPMENT_SOURCE_ACCURATE,
        accurate_shipment_id="ACC-DO-X", shipment_id="a", created_at="t1",
    )
    apply_shipment_record(
        o, number="SJ-1", lines=lines, source=SHIPMENT_SOURCE_ACCURATE,
        accurate_shipment_id="ACC-DO-X", shipment_id="b", created_at="t2",
    )
    assert len(o["shipments"]) == 1
    assert qty_shipped(o) == 4000
    assert available_for_loading_line(o, 0) == 4000


def test_multiple_accurate_shipments_cumulative():
    o = _po_a(packed=10000)
    apply_shipment_record(
        o, number="SJ-1", lines=[{"accurate_item_id": "104", "qty": 4000}],
        source=SHIPMENT_SOURCE_ACCURATE, accurate_shipment_id="DO-1",
        shipment_id="a", created_at="t1",
    )
    apply_shipment_record(
        o, number="SJ-2", lines=[{"accurate_item_id": "104", "qty": 3000}],
        source=SHIPMENT_SOURCE_ACCURATE, accurate_shipment_id="DO-2",
        shipment_id="b", created_at="t2",
    )
    assert len(o["shipments"]) == 2
    assert qty_shipped_for_line(o, 0) == 7000
    assert {s["accurate_shipment_id"] for s in o["shipments"]} == {"DO-1", "DO-2"}


def test_packing_mismatch_does_not_raise_loading_above_packed():
    o = _po_a(packed=2000)
    lines = [{"accurate_item_id": "104", "qty": 3000}]
    assert validate_shipment_lines(lines, o, source=SHIPMENT_SOURCE_ACCURATE) is None
    warns = shipment_line_mismatches(lines, o)
    assert warns
    apply_shipment_record(
        o, number="SJ-MIS", lines=lines, source=SHIPMENT_SOURCE_ACCURATE,
        accurate_shipment_id="DO-MIS", shipment_id="m", created_at="t",
    )
    assert o["packing_info"]["lines"][0]["packed_qty"] == 2000
    assert qty_shipped_for_line(o, 0) == 3000
    assert loading_cap_for_line(o, 0) == 2000
    assert available_for_loading_line(o, 0) == 2000
    assert validate_loading_lines([{"line_idx": 0, "loaded_qty": 3000}], o) is not None
    assert validate_loading_lines([{"line_idx": 0, "loaded_qty": 2000}], o) is None


def test_name_matching_rejected():
    o = _po_a(packed=4000)
    err = validate_shipment_lines(
        [{"qty": 100, "name": "Item A"}], o, source=SHIPMENT_SOURCE_ACCURATE,
    )
    assert err is not None
    assert "accurate_item_id" in err or "line_idx" in err


def test_loading_never_exceeds_sj_when_sj_lines_exist():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 2500}]}],
    )
    assert loading_cap_for_line(o, 0) == 2500
    assert validate_loading_lines([{"line_idx": 0, "loaded_qty": 2501}], o) is not None


def test_loading_increment_cumulative_keeps_events():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
    )
    assert validate_loading_increments([{"line_idx": 0, "qty": 2500}], o) is None
    lines, events = apply_loading_increments(o, [{"line_idx": 0, "qty": 2500}])
    o["loading_info"] = {"lines": lines, "events": events}
    assert qty_loaded_for_line(o, 0) == 2500
    assert available_for_loading_line(o, 0) == 1500
    assert len(events) == 1
    assert validate_loading_increments([{"line_idx": 0, "qty": 1000}], o) is None
    lines2, events2 = apply_loading_increments(o, [{"line_idx": 0, "qty": 1000}])
    o["loading_info"] = {"lines": lines2, "events": events2}
    assert qty_loaded_for_line(o, 0) == 3500
    assert available_for_loading_line(o, 0) == 500
    assert len(events2) == 2
    assert events2[0]["qty"] == 2500
    assert events2[1]["qty"] == 1000


def test_loading_increment_rejects_over_available():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
        loaded_lines=[{"line_idx": 0, "loaded_qty": 3500}],
    )
    err = validate_loading_increments([{"line_idx": 0, "qty": 501}], o)
    assert err is not None
    assert validate_loading_increments([{"line_idx": 0, "qty": 500}], o) is None


def test_loading_increment_independent_lines():
    o = _po_ab(packed_a=4000, packed_b=1500)
    apply_shipment_record(
        o, number="SJ-1", lines=[{"line_idx": 0, "qty": 4000}, {"line_idx": 1, "qty": 1500}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="s1", created_at="t",
    )
    lines, events = apply_loading_increments(o, [{"line_idx": 0, "qty": 2500}])
    o["loading_info"] = {"lines": lines, "events": events}
    lines, events = apply_loading_increments(o, [{"line_idx": 1, "qty": 500}])
    o["loading_info"] = {"lines": lines, "events": events}
    assert qty_loaded_for_line(o, 0) == 2500
    assert qty_loaded_for_line(o, 1) == 500
    assert available_for_loading_line(o, 0) == 1500
    assert available_for_loading_line(o, 1) == 1000
    assert len(o["loading_info"]["events"]) == 2


def test_loading_status_partial_vs_complete():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
    )
    assert loading_status_semantic(o) == "pending"
    lines, events = apply_loading_increments(o, [{"line_idx": 0, "qty": 2500}])
    o["loading_info"] = {"lines": lines, "events": events}
    assert loading_status_semantic(o) == "partial"
    lines, events = apply_loading_increments(o, [{"line_idx": 0, "qty": 1500}])
    o["loading_info"] = {"lines": lines, "events": events}
    assert loading_status_semantic(o) == "completed"


def test_proof_evidence_partial_does_not_mark_full_po():
    o = _po_a(
        packed=4000,
        shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}],
        loaded_lines=[{"line_idx": 0, "loaded_qty": 4000}],
        qty_po=10000,
    )
    ev = apply_delivery_from_proof_evidence(o)
    assert ev is not None
    assert ev["qty_delivered"] == 4000
    assert ev["full"] is False
    assert ev["delivery_status"] == "Sebagian Terkirim"


def test_proof_evidence_none_without_loading():
    o = _po_a(packed=4000, shipped_lines=[{"id": "s1", "lines": [{"line_idx": 0, "qty": 4000}]}])
    assert apply_delivery_from_proof_evidence(o) is None


def test_multi_item_loading_not_fully_complete():
    o = _po_ab(packed_a=4000, packed_b=2000)
    apply_shipment_record(
        o, number="SJ-1", lines=[{"line_idx": 0, "qty": 4000}, {"line_idx": 1, "qty": 2000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="s1", created_at="t",
    )
    lines, events = apply_loading_increments(o, [{"line_idx": 0, "qty": 4000}])
    o["loading_info"] = {"lines": lines, "events": events}
    lines, events = apply_loading_increments(o, [{"line_idx": 1, "qty": 500}])
    o["loading_info"] = {"lines": lines, "events": events}
    assert qty_loaded_for_line(o, 0) == 4000
    assert qty_loaded_for_line(o, 1) == 500
    assert loading_status_semantic(o) == "partial"


def test_accurate_delivery_endpoint_not_invented():
    assert ACCURATE_DELIVERY_ENDPOINT_AVAILABLE is False
    src = (Path(__file__).resolve().parents[1] / "services" / "accurate.py").read_text(encoding="utf-8")
    assert "/accurate/api/delivery-order" not in src
    assert "/accurate/api/sales-delivery" not in src


def test_mock_delivery_catalog_stable_ids():
    note = get_mock_delivery_note(accurate_shipment_id="ACC-DO-2001-1", accurate_so_id="ACC-SO-2001")
    assert note["lines"][0]["accurate_item_id"] == "104"
    assert note["lines"][1]["accurate_item_id"] == "105"
    rows = list_mock_delivery_notes(po_number="PO-2001")
    assert {r["accurate_shipment_id"] for r in rows} == {"ACC-DO-2001-1", "ACC-DO-2001-2"}


def test_live_accurate_delivery_fetch_blocked():
    import asyncio

    class _Live:
        def is_live(self):
            return True

    result = asyncio.run(fetch_delivery_notes(_Live(), po_number="PO-2001"))
    assert result["blocked"] is True
    assert result["notes"] == []
    assert "sales-order" in result["reason"]
