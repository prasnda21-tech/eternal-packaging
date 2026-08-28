"""Selective rollback: selected ids reset, unselected preserved."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from services.workflow_rollback import (
    EMPTY_SELECTION,
    INVALID_ID,
    RollbackError,
    STAGE_PACKING,
    apply_selective_rollback,
    line_stable_id,
    validate_rollback_to_stage,
)


USER = {"email": "admin@eternalpackaging.com", "name": "Admin"}
NOW = "2026-08-29T01:00:00+00:00"
TO_OPERATOR = 8
TO_PACKING = STAGE_PACKING


def _split(sid, line_idx, qty_done, entries=None, operator="Budi"):
    return {
        "id": sid,
        "line_idx": line_idx,
        "operator_email": f"{operator.lower()}@x.com",
        "operator_name": operator,
        "quantity": 10000,
        "qty_completed": qty_done,
        "work_date": "2026-08-20",
        "updated_at": "2026-08-21T08:00:00+00:00",
        "progress_entries": entries or [
            {
                "id": f"e-{sid}",
                "qty": qty_done,
                "created_at": "2026-08-21T08:00:00+00:00",
                "created_by_name": operator,
                "cancelled": False,
            }
        ],
    }


def _order(**extra):
    order = {
        "id": "po-1",
        "po_number": "PO-RB-1",
        "current_stage": 8,
        "design_status": "Approved",
        "design_approved_by": "a@x.com",
        "design_approved_by_name": "A",
        "design_approved_at": "2026-08-10T00:00:00+00:00",
        "design_files": [{"id": "d1", "name": "d.png"}],
        "payment_status": "Approved",
        "payment_approved_by": "a@x.com",
        "payment_approved_by_name": "A",
        "payment_approved_at": "2026-08-10T00:00:00+00:00",
        "payment_files": [{"id": "p1", "name": "p.png"}],
        "line_items": [
            {"id": "line-a", "name": "Cup A", "quantity": 10000, "accurate_item_id": "101"},
            {"id": "line-b", "name": "Cup B", "quantity": 8000, "accurate_item_id": "202"},
            {"id": "line-c", "name": "Cup C", "quantity": 5000, "accurate_item_id": "303"},
        ],
        "operator_splits": [
            _split("split-1", 0, 3000, operator="Budi"),
            _split("split-2", 1, 4000, operator="Andi"),
            _split("split-3", 2, 2000, operator="Cici"),
        ],
        "rollback_history": [],
        "design_approval_history": [],
        "payment_approval_history": [],
    }
    order.update(extra)
    return order


def test_rollback_only_nomor_2_empties_selected_preserves_others():
    order = _order()
    before_1 = copy.deepcopy(order["operator_splits"][0])
    before_3 = copy.deepcopy(order["operator_splits"][2])
    apply_selective_rollback(order, ["split-2"], USER, NOW, TO_OPERATOR)
    s1, s2, s3 = order["operator_splits"]
    assert s2["qty_completed"] == 0
    assert all(e.get("cancelled") for e in s2["progress_entries"])
    assert s1["qty_completed"] == before_1["qty_completed"]
    assert s1["operator_name"] == before_1["operator_name"]
    assert s1["updated_at"] == before_1["updated_at"]
    assert s1["progress_entries"] == before_1["progress_entries"]
    assert s3["qty_completed"] == before_3["qty_completed"]
    assert s3["progress_entries"] == before_3["progress_entries"]
    assert order["current_stage"] == 8
    assert order["design_status"] == "Approved"
    assert order["payment_status"] == "Approved"


def test_rollback_nomor_1_and_3_keeps_2():
    order = _order()
    apply_selective_rollback(order, ["split-1", "split-3"], USER, NOW, TO_OPERATOR)
    s1, s2, s3 = order["operator_splits"]
    assert s1["qty_completed"] == 0
    assert s3["qty_completed"] == 0
    assert s2["qty_completed"] == 4000
    assert s2["progress_entries"][0]["cancelled"] is False


def test_selective_to_stage_rejects_surat_jalan_and_beyond():
    assert validate_rollback_to_stage(11, 14, 15, selective=True) is None
    assert validate_rollback_to_stage(12, 14, 15, selective=True)
    assert validate_rollback_to_stage(13, 14, 15, selective=True)
    assert validate_rollback_to_stage(14, 14, 15, selective=True)
    assert validate_rollback_to_stage(12, 14, 15, selective=False) is None


def test_empty_ids_rejected():
    order = _order()
    with pytest.raises(RollbackError, match=EMPTY_SELECTION):
        apply_selective_rollback(order, [], USER, NOW, TO_OPERATOR)
    with pytest.raises(RollbackError, match=EMPTY_SELECTION):
        apply_selective_rollback(order, ["", "  "], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][1]["qty_completed"] == 4000


def test_invalid_id_rejected_does_not_mutate():
    order = _order()
    snapshot = copy.deepcopy(order)
    with pytest.raises(RollbackError, match=INVALID_ID):
        apply_selective_rollback(order, ["split-2", "not-on-this-po"], USER, NOW, TO_OPERATOR)
    assert order == snapshot


def test_duplicate_ids_are_idempotent():
    order = _order()
    apply_selective_rollback(order, ["split-2", "split-2"], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][1]["qty_completed"] == 0
    apply_selective_rollback(order, ["split-2"], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][1]["qty_completed"] == 0
    assert order["operator_splits"][0]["qty_completed"] == 3000


def test_stage_slug_is_not_a_selective_target():
    order = _order()
    snapshot = copy.deepcopy(order)
    with pytest.raises(RollbackError, match=INVALID_ID):
        apply_selective_rollback(order, ["approval_design"], USER, NOW, TO_OPERATOR)
    assert order == snapshot
    assert order["design_status"] == "Approved"
    assert order["operator_splits"][1]["qty_completed"] == 4000


def test_line_stable_id_resets_only_that_line_splits():
    order = _order()
    assert line_stable_id(order, 1) == "line-b"
    apply_selective_rollback(order, ["line-b"], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][0]["qty_completed"] == 3000
    assert order["operator_splits"][1]["qty_completed"] == 0
    assert order["operator_splits"][2]["qty_completed"] == 2000


def test_helper_and_packing_untouched():
    order = _order()
    order["helper_prep"] = {"qty_prepared": 100, "items": [{"line_idx": 0, "qty_prepared": 100}]}
    order["packing_info"] = {"qty_packed": 50, "lines": [{"line_idx": 0, "packed_qty": 50}]}
    apply_selective_rollback(order, ["split-2"], USER, NOW, TO_OPERATOR)
    assert order["helper_prep"]["qty_prepared"] == 100
    assert order["packing_info"]["qty_packed"] == 50
    assert order["operator_splits"][1]["qty_completed"] == 0
    assert order["operator_splits"][0]["qty_completed"] == 3000


def test_splits_without_id_reset_via_unique_item_key():
    order = _order()
    order["operator_splits"][1]["id"] = None
    apply_selective_rollback(order, ["line-b"], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][1]["qty_completed"] == 0
    assert order["operator_splits"][0]["qty_completed"] == 3000
    assert order["operator_splits"][2]["qty_completed"] == 2000


def test_endpoint_requires_perm_then_selective_ids():
    src = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    start = src.find("async def rollback_stage")
    end = src.find("async def cancel_po", start)
    fn = src[start:end]
    idx_perm = fn.find('await _require_perm(user, "workflow.rollback")')
    idx_sel = fn.find("apply_selective_rollback")
    assert idx_perm != -1 and idx_sel != -1
    assert idx_perm < idx_sel
    assert "validate_rollback_to_stage" in fn
    assert "AMBIGUOUS_PAYLOAD" not in fn


def _packing_lines():
    return {
        "qty_packed": 60,
        "lines": [
            {"line_idx": 0, "packed_qty": 10},
            {"line_idx": 1, "packed_qty": 20},
            {"line_idx": 2, "packed_qty": 30},
        ],
    }


def test_case_a_to_packing_resets_only_b():
    order = _order(current_stage=14, packing_info=_packing_lines())
    before_a = copy.deepcopy(order["operator_splits"][0])
    before_c = copy.deepcopy(order["operator_splits"][2])
    pack_a = copy.deepcopy(order["packing_info"]["lines"][0])
    pack_c = copy.deepcopy(order["packing_info"]["lines"][2])
    apply_selective_rollback(order, ["split-2"], USER, NOW, TO_PACKING)
    assert order["operator_splits"][0] == before_a
    assert order["operator_splits"][2] == before_c
    assert order["operator_splits"][1]["qty_completed"] == 4000
    assert order["operator_splits"][1]["operator_name"] == "Andi"
    assert order["packing_info"]["lines"][1]["packed_qty"] == 0
    assert order["packing_info"]["lines"][0] == pack_a
    assert order["packing_info"]["lines"][2] == pack_c
    assert order["packing_info"]["qty_packed"] == 40
    assert order["current_stage"] == 14
    assert order["design_status"] == "Approved"


def test_case_b_to_operator_resets_a_and_c_keeps_b():
    order = _order(current_stage=14, packing_info=_packing_lines())
    before_b = copy.deepcopy(order["operator_splits"][1])
    pack_b = copy.deepcopy(order["packing_info"]["lines"][1])
    apply_selective_rollback(order, ["split-1", "split-3"], USER, NOW, TO_OPERATOR)
    assert order["operator_splits"][0]["qty_completed"] == 0
    assert order["operator_splits"][2]["qty_completed"] == 0
    assert order["operator_splits"][1] == before_b
    assert order["packing_info"]["lines"][0]["packed_qty"] == 0
    assert order["packing_info"]["lines"][2]["packed_qty"] == 0
    assert order["packing_info"]["lines"][1] == pack_b
    assert order["current_stage"] == 14


def test_case_c_all_ids_processed():
    order = _order()
    apply_selective_rollback(order, ["split-1", "split-2", "split-3"], USER, NOW, TO_OPERATOR)
    assert [s["qty_completed"] for s in order["operator_splits"]] == [0, 0, 0]
    assert order["operator_splits"][1]["operator_name"] == "Andi"


def test_case_h_unselected_snapshot_identical():
    order = _order(
        current_stage=14,
        packing_info=_packing_lines(),
        helper_prep={
            "qty_prepared": 100,
            "items": [
                {"line_idx": 0, "qty_prepared": 40},
                {"line_idx": 1, "qty_prepared": 35},
                {"line_idx": 2, "qty_prepared": 25},
            ],
        },
    )
    before_a = copy.deepcopy(order["operator_splits"][0])
    before_c = copy.deepcopy(order["operator_splits"][2])
    pack_a = copy.deepcopy(order["packing_info"]["lines"][0])
    pack_c = copy.deepcopy(order["packing_info"]["lines"][2])
    help_a = copy.deepcopy(order["helper_prep"]["items"][0])
    help_c = copy.deepcopy(order["helper_prep"]["items"][2])
    apply_selective_rollback(order, ["split-2"], USER, NOW, TO_PACKING)
    assert order["operator_splits"][0] == before_a
    assert order["operator_splits"][2] == before_c
    assert order["packing_info"]["lines"][0] == pack_a
    assert order["packing_info"]["lines"][2] == pack_c
    assert order["helper_prep"]["items"][0] == help_a
    assert order["helper_prep"]["items"][2] == help_c
    assert order["current_stage"] == 14

