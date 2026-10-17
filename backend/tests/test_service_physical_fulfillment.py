"""SERVICE + physical_fulfillment: packing/shipping without inventory conversion.

OWNER: jasa sablon with customer-owned goods must pack/ship while item_type
stays SERVICE (no Eternal stock/HPP). Process-only SERVICE unchanged.
"""
from __future__ import annotations

from quantity_rules import (
    apply_line_physical_fulfillment_override,
    available_for_packing,
    available_for_packing_line,
    is_process_only_service_line,
    is_service_process_line,
    line_physical_fulfillment,
    packing_line_breakdown,
    packing_required_total,
    packing_shortfall_for_sj_match,
    packing_status_semantic,
    physical_print_qty_total,
    physical_qty_for_loading_line,
    stamp_line_physical_fulfillment,
    validate_packing_lines,
)
from services.inventory_accounting import classify_inventory_item_type
from services.order_line_addon import order_has_unlinked_process_addons
from services.workflow_stage_status import STAGE_PACKING, resolve_stage_chip

ELEMENT_OID = "16518ea4-80f9-42a6-9ace-9fef2431a9c1"
ELEMENT_MID = "9bfd5938-6868-4d1b-bf9b-6212225134a2"
ELEMENT_LINE_ID = "ln-hitam"


def _element_like(*, physical_fulfillment=True, packed=0, helper=10000, completed=10000):
    line = {
        "line_id": ELEMENT_LINE_ID,
        "name": "Add On Warna Sablon HITAM",
        "quantity": 10000,
        "production_process": "print",
        "master_item_id": ELEMENT_MID,
        "item_type": "SERVICE",
    }
    if physical_fulfillment is not None:
        line["physical_fulfillment"] = physical_fulfillment
    order = {
        "id": ELEMENT_OID,
        "po_number": "PO/14/10/2026/000007",
        "cancelled": False,
        "current_stage": 11,
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "requires_printing": True,
        "line_items": [line],
        "operator_splits": [
            {"line_idx": 0, "quantity": 10000, "qty_completed": completed},
        ],
        "helper_prep": {
            "qty_prepared": helper,
            "items": [{"line_idx": 0, "qty_prepared": helper}],
        },
        "packing_info": {},
        "packing_status": "Belum",
    }
    if packed:
        order["packing_info"] = {
            "lines": [{"line_idx": 0, "packed_qty": packed}],
            "qty_packed": packed,
        }
    return order


def _sj(order, qty, sid="sj-el"):
    order = {**order}
    order["surat_jalan_created"] = True
    order["sj_files"] = [{
        "id": sid, "file_id": sid, "has_file": True, "storage_path": "x",
        "total_qty": qty, "lines": [{"line_idx": 0, "qty": qty}],
    }]
    order["shipments"] = [{
        "id": sid, "shipment_id": sid, "status": "open",
        "lines": [{"line_idx": 0, "line_id": "ln-hitam", "qty": qty}],
    }]
    return order


def test_process_only_service_still_skips_packing():
    o = _element_like(physical_fulfillment=False)
    assert is_service_process_line(o["line_items"][0]) is True
    assert is_process_only_service_line(o["line_items"][0]) is True
    assert packing_required_total(o) == 0
    assert physical_print_qty_total(o) == 0
    assert available_for_packing(o) == 0
    assert available_for_packing_line(o, 0) == 0
    assert packing_status_semantic(o) == "Sudah"  # no physical print target
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 100}], o) is not None
    assert physical_qty_for_loading_line(o, 0) == 0


def test_unset_flag_defaults_process_only_historical():
    o = _element_like(physical_fulfillment=None)
    assert "physical_fulfillment" not in o["line_items"][0]
    assert line_physical_fulfillment(o["line_items"][0]) is False
    assert packing_required_total(o) == 0
    assert available_for_packing(o) == 0


def test_element_physical_fulfillment_opens_packing_when_gates_ok():
    o = _element_like(physical_fulfillment=True, packed=0)
    assert is_service_process_line(o["line_items"][0]) is True
    assert is_process_only_service_line(o["line_items"][0]) is False
    assert order_has_unlinked_process_addons(o) is False
    assert packing_required_total(o) == 10000
    assert physical_print_qty_total(o) == 10000
    assert available_for_packing(o) == 10000
    assert available_for_packing_line(o, 0) == 10000
    assert packing_status_semantic(o) == "Berjalan"
    chip = resolve_stage_chip(STAGE_PACKING, o)
    assert chip["label"] == "Menunggu input qty packing"
    assert chip["state"] != "done"
    rows = packing_line_breakdown(o)
    assert len(rows) == 1
    assert rows[0]["available"] == 10000
    assert rows[0]["packed"] == 0
    assert rows[0]["physical_fulfillment"] is True
    assert rows[0]["is_service"] is True


def test_element_incomplete_production_keeps_avail_zero():
    o = _element_like(physical_fulfillment=True, helper=10000, completed=5000)
    assert available_for_packing_line(o, 0) == 5000
    o2 = _element_like(physical_fulfillment=True, helper=0, completed=10000)
    # helper line prepared 0 with items entry
    o2["helper_prep"] = {"qty_prepared": 0, "items": [{"line_idx": 0, "qty_prepared": 0}]}
    assert available_for_packing_line(o2, 0) == 0


def test_packing_zero_blocks_loading_5000():
    o = _sj(_element_like(physical_fulfillment=True, packed=0), 5000)
    assert physical_qty_for_loading_line(o, 0) == 0
    short = packing_shortfall_for_sj_match(o, "sj-el")
    assert short and "Packing belum mencukupi" in short


def test_packing_5000_allows_partial_loading_cap():
    o = _sj(_element_like(physical_fulfillment=True, packed=5000), 5000)
    assert physical_qty_for_loading_line(o, 0) == 5000
    assert packing_shortfall_for_sj_match(o, "sj-el") is None


def test_packing_cannot_exceed_physical_valid():
    o = _element_like(physical_fulfillment=True, packed=0)
    err = validate_packing_lines([{"line_idx": 0, "packed_qty": 10001}], o)
    assert err is not None
    err2 = validate_packing_lines([{"line_idx": 0, "packed_qty": 10000}], o)
    assert err2 is None


def test_service_stays_non_inventory_for_stock_hpp():
    ok, reason = classify_inventory_item_type("SERVICE")
    assert ok is False
    assert reason is not None
    # Flag must not change accounting type contract
    li = {"item_type": "SERVICE", "physical_fulfillment": True}
    assert is_service_process_line(li) is True
    ok2, _ = classify_inventory_item_type(li["item_type"])
    assert ok2 is False


def test_inventory_flow_unchanged_with_neighbor_physical_service():
    order = {
        "id": "po-mix",
        "cancelled": False,
        "current_stage": 11,
        "line_items": [
            {
                "line_id": "ln-cup",
                "name": "Cup",
                "quantity": 1000,
                "production_process": "print",
                "master_item_id": "inv-cup",
                "item_type": "INVENTORY",
            },
            {
                "line_id": "ln-svc",
                "name": "Jasa proses",
                "quantity": 1000,
                "production_process": "print",
                "master_item_id": "svc-proc",
                "item_type": "SERVICE",
                "parent_line_id": "ln-cup",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 1000},
            {"line_idx": 1, "qty_completed": 1000},
        ],
        "helper_prep": {"items": [
            {"line_idx": 0, "qty_prepared": 1000},
            {"line_idx": 1, "qty_prepared": 1000},
        ]},
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": 1000}]},
    }
    assert packing_required_total(order) == 1000
    assert available_for_packing_line(order, 0) == 1000
    assert available_for_packing_line(order, 1) == 0
    assert packing_status_semantic(order) == "Sudah"


def test_name_never_enables_physical_fulfillment():
    assert line_physical_fulfillment({"name": "Add On Warna Sablon HITAM", "item_type": "SERVICE"}) is False


def test_stamp_from_master_only_when_line_key_absent():
    order = {
        "line_items": [
            {"master_item_id": "m1", "item_type": "SERVICE"},
            {"master_item_id": "m1", "item_type": "SERVICE", "physical_fulfillment": False},
        ],
    }
    stamp_line_physical_fulfillment(order, {"m1": True})
    assert order["line_items"][0]["physical_fulfillment"] is True
    assert order["line_items"][1]["physical_fulfillment"] is False


def test_repeated_packing_validate_uses_cumulative_not_double_target():
    o = _element_like(physical_fulfillment=True, packed=3000)
    # cumulative target still 10000; next save of 5000 ok; 10001 not
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 5000}], o) is None
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 10001}], o) is not None
    assert packing_required_total(o) == 10000
    assert available_for_packing(o) == 10000


def test_timeline_done_only_after_stage_or_qty_complete():
    o = _element_like(physical_fulfillment=True, packed=0)
    assert resolve_stage_chip(STAGE_PACKING, o)["label"] == "Menunggu input qty packing"
    o2 = _element_like(physical_fulfillment=True, packed=10000)
    assert packing_status_semantic(o2) == "Sudah"
    o2["current_stage"] = 12
    assert resolve_stage_chip(STAGE_PACKING, o2)["state"] == "done"


def test_po_override_only_one_line_and_idempotent():
    o = _element_like(physical_fulfillment=None)
    o["line_items"].append({
        "line_id": "ln-cup",
        "name": "Cup sibling",
        "quantity": 500,
        "production_process": "print",
        "master_item_id": "inv-cup",
        "item_type": "INVENTORY",
    })
    before, after = apply_line_physical_fulfillment_override(
        o,
        line_id=ELEMENT_LINE_ID,
        master_item_id=ELEMENT_MID,
        physical_fulfillment=True,
    )
    assert before["physical_fulfillment"] is None
    assert after["physical_fulfillment"] is True
    assert o["line_items"][0]["physical_fulfillment"] is True
    assert "physical_fulfillment" not in o["line_items"][1]
    assert o["line_items"][0]["item_type"] == "SERVICE"
    assert o["line_items"][0]["quantity"] == 10000
    # idempotent re-apply
    b2, a2 = apply_line_physical_fulfillment_override(
        o,
        line_id=ELEMENT_LINE_ID,
        master_item_id=ELEMENT_MID,
        physical_fulfillment=True,
    )
    assert b2["physical_fulfillment"] is True
    assert a2["physical_fulfillment"] is True
    assert available_for_packing_line(o, 0) == 10000
    assert available_for_packing_line(o, 1) == 0


def test_po_override_rejects_wrong_master_idx_synthetic_and_inventory():
    o = _element_like(physical_fulfillment=None)
    try:
        apply_line_physical_fulfillment_override(
            o, line_id=ELEMENT_LINE_ID, master_item_id="wrong", physical_fulfillment=True,
        )
        assert False, "expected mismatch"
    except ValueError as e:
        assert "master_item_id" in str(e)
    try:
        apply_line_physical_fulfillment_override(
            o, line_id="idx:0", master_item_id=ELEMENT_MID, physical_fulfillment=True,
        )
        assert False, "expected synthetic reject"
    except ValueError as e:
        assert "sintetis" in str(e)
    inv = {
        "id": "po-inv",
        "line_items": [{
            "line_id": "ln-cup",
            "item_type": "INVENTORY",
            "master_item_id": "inv-1",
            "production_process": "print",
            "quantity": 10,
        }],
    }
    try:
        apply_line_physical_fulfillment_override(
            inv, line_id="ln-cup", master_item_id="inv-1", physical_fulfillment=True,
        )
        assert False, "expected SERVICE-only"
    except ValueError as e:
        assert "SERVICE" in str(e)


def test_master_physical_fulfillment_returns_before_after_audit_shape():
    """Service-layer before/after used by PATCH audit_logs (no secrets)."""
    from tests.test_master_items import FakeDB, _await
    from services.master_items import create_local_master_item, update_master_physical_fulfillment

    db = FakeDB()
    created = _await(create_local_master_item(db, {
        "item_code": "SVC-PF-1",
        "item_name": "Jasa sablon fisik",
        "item_type": "SERVICE",
        "unit": "PCS",
        "is_active": True,
        "production_process": "print",
    }))
    mid = created["id"]
    public, before, after = _await(update_master_physical_fulfillment(
        db, mid, True, user={"email": "owner@test.local"},
    ))
    assert before["master_item_id"] == mid
    assert before["physical_fulfillment"] is False
    assert after["master_item_id"] == mid
    assert after["physical_fulfillment"] is True
    assert after["updated_by"] == "owner@test.local"
    assert public["physical_fulfillment"] is True
    assert "token" not in before and "password" not in after
    # Idempotent second call
    _, b2, a2 = _await(update_master_physical_fulfillment(
        db, mid, True, user={"email": "owner@test.local"},
    ))
    assert b2["physical_fulfillment"] is True
    assert a2["physical_fulfillment"] is True
    # Inventory rejected
    inv = _await(create_local_master_item(db, {
        "item_code": "INV-PF-1",
        "item_name": "Cup",
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    }))
    from fastapi import HTTPException
    try:
        _await(update_master_physical_fulfillment(db, inv["id"], True, user={"email": "x"}))
        assert False
    except HTTPException as e:
        assert e.status_code == 400


def test_other_po_untouched_when_element_overridden():
    element = _element_like(physical_fulfillment=None)
    other = {
        "id": "other-po",
        "line_items": [{
            "line_id": ELEMENT_LINE_ID,  # same line_id string, different PO doc
            "name": "Add On Warna Sablon HITAM",
            "quantity": 10000,
            "production_process": "print",
            "master_item_id": ELEMENT_MID,
            "item_type": "SERVICE",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 10000}],
        "helper_prep": {"items": [{"line_idx": 0, "qty_prepared": 10000}]},
        "packing_info": {},
    }
    apply_line_physical_fulfillment_override(
        element,
        line_id=ELEMENT_LINE_ID,
        master_item_id=ELEMENT_MID,
        physical_fulfillment=True,
    )
    assert element["line_items"][0]["physical_fulfillment"] is True
    assert "physical_fulfillment" not in other["line_items"][0]
    assert available_for_packing(other) == 0
