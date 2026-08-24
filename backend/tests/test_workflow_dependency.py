"""Workflow dependency / auto-progression tests (no Mongo required for unit cases).

Covers TEST A–H from the production workflow revision:
  A/B parallel Design vs Payment
  C Penjadwalan auto-ready after group 2
  D Screener gate vs Helper parallel
  E Operator auto-ready after Screener save
  F Sample upload → Approval Sample ready
  G Sample approved → Packing unlocked
  H Sample rejected → Packing locked, operator can re-upload
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_gates import (
    SCREENER_GATE_OPERATOR_MSG,
    SCREENER_GATE_SAMPLE_MSG,
    compute_compatible_stage,
    design_ok,
    group2_complete,
    helper_done,
    operator_unlocked,
    packing_unlocked,
    payment_ok,
    penjadwalan_done,
    sample_ok,
    sample_uploaded,
    screener_done,
    screener_gate_reason,
    sync_workflow_stage,
    workflow_ready,
)


def _po(**extra):
    order = {
        "current_stage": 0,
        "design_files": [],
        "payment_files": [],
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_bypassed": False,
        "payment_bypassed": False,
        "operator_splits": [],
        "screener_info": None,
        "helper_prep": None,
        "sample_files": [],
        "sample_approved": False,
        "line_items": [{"name": "Kaos", "quantity": 100, "production_process": "print"}],
        "total_quantity": 100,
        **extra,
    }
    return order


# ---------- TEST A / B: parallel Design & Payment ----------

class TestParallelDesignPayment:
    def test_a_payment_first_does_not_complete_design(self):
        o = _po(
            payment_files=[{"id": "p", "name": "p.png"}],
            payment_status="Approved",
            current_stage= 4,
        )
        assert payment_ok(o)
        assert not design_ok(o)
        assert not group2_complete(o)
        ready = workflow_ready(o)
        assert ready["payment"] is True
        assert ready["approval_payment"] is True
        assert ready["penjadwalan"] is False
        assert compute_compatible_stage(o) < 5

    def test_b_design_first_does_not_complete_payment(self):
        o = _po(
            design_files=[{"id": "d", "name": "d.png"}],
            design_status="Approved",
            current_stage= 3,
        )
        assert design_ok(o)
        assert not payment_ok(o)
        assert not group2_complete(o)
        ready = workflow_ready(o)
        assert ready["penjadwalan"] is False
        assert compute_compatible_stage(o) < 5

    def test_siblings_do_not_mark_each_other_done(self):
        o = _po(payment_status="Approved", payment_files=[{"id": "p"}])
        sync_workflow_stage(o)
        assert o["design_status"] != "Approved"
        assert not design_ok(o)


# ---------- TEST C: scheduling auto-ready ----------

class TestSchedulingAutoReady:
    def test_c_both_approvals_unlock_penjadwalan(self):
        o = _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_status="Approved",
            payment_status="Approved",
            current_stage= 4,
        )
        assert group2_complete(o)
        assert workflow_ready(o)["penjadwalan"] is True
        changed = sync_workflow_stage(o)
        assert changed is True
        assert o["current_stage"] == 5

    def test_c_bypass_also_unlocks(self):
        o = _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_bypassed=True,
            payment_bypassed=True,
            current_stage= 0,
        )
        sync_workflow_stage(o)
        assert o["current_stage"] == 5
        assert workflow_ready(o)["penjadwalan"] is True

    def test_sync_idempotent(self):
        o = _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_status="Approved",
            payment_status="Approved",
            current_stage= 5,
        )
        assert sync_workflow_stage(o) is False
        assert o["current_stage"] == 5


# ---------- TEST D / E: screener gate, helper parallel ----------

class TestScreenerGateAndHelperParallel:
    def _scheduled(self, **extra):
        return _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_status="Approved",
            payment_status="Approved",
            operator_splits=[{"id": "s1", "quantity": 100, "qty_completed": 0}],
            current_stage= 6,
            **extra,
        )

    def test_d_helper_can_complete_without_screener(self):
        o = self._scheduled(helper_prep={"qty_prepared": 80})
        assert helper_done(o)
        assert not screener_done(o)
        assert not operator_unlocked(o)
        ready = workflow_ready(o)
        assert ready["helper"] is True
        assert ready["screener"] is True
        assert ready["operator"] is False
        assert ready["sample_upload"] is False
        assert screener_gate_reason(o, for_sample=True) == SCREENER_GATE_SAMPLE_MSG
        assert screener_gate_reason(o, for_sample=False) == SCREENER_GATE_OPERATOR_MSG

    def test_d_screener_does_not_complete_helper(self):
        o = self._scheduled(screener_info={"screen_qty": 2})
        assert screener_done(o)
        assert not helper_done(o)

    def test_e_screener_save_unlocks_operator(self):
        o = self._scheduled()
        o["screener_info"] = {"screen_qty": 4}
        sync_workflow_stage(o)
        assert operator_unlocked(o)
        assert o["current_stage"] == 8
        assert workflow_ready(o)["operator"] is True
        assert workflow_ready(o)["sample_upload"] is True
        assert not helper_done(o)

    def test_helper_save_does_not_unlock_operator(self):
        o = self._scheduled(helper_prep={"qty_prepared": 100})
        sync_workflow_stage(o)
        assert o["current_stage"] == 6
        assert not operator_unlocked(o)


# ---------- TEST F / G / H: sample approval ----------

class TestSampleApprovalFlow:
    def _operator_ready(self, **extra):
        extra.setdefault("current_stage", 8)
        return _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_status="Approved",
            payment_status="Approved",
            operator_splits=[{"id": "s1", "quantity": 100, "qty_completed": 0}],
            screener_info={"screen_qty": 2},
            **extra,
        )

    def test_f_sample_upload_unlocks_approval(self):
        o = self._operator_ready(sample_files=[{"id": "sm", "name": "s.png"}])
        assert sample_uploaded(o)
        assert not sample_ok(o)
        sync_workflow_stage(o)
        assert o["current_stage"] == 9
        assert workflow_ready(o)["sample_approval"] is True
        assert not packing_unlocked(o)

    def test_g_sample_approved_unlocks_packing(self):
        o = self._operator_ready(
            sample_files=[{"id": "sm"}],
            sample_approved=True,
            current_stage= 9,
        )
        sync_workflow_stage(o)
        assert packing_unlocked(o)
        assert o["current_stage"] == 11
        assert workflow_ready(o)["packing"] is True

    def test_h_reject_locks_packing_keeps_sample_files(self):
        o = self._operator_ready(
            sample_files=[{"id": "sm"}],
            sample_approved=False,
            current_stage= 11,
        )
        sync_workflow_stage(o, force=True)
        assert not packing_unlocked(o)
        assert o["current_stage"] == 9
        assert sample_uploaded(o)
        assert workflow_ready(o)["sample_upload"] is True

    def test_sync_does_not_advance_past_packing(self):
        o = self._operator_ready(
            sample_files=[{"id": "sm"}],
            sample_approved=True,
            current_stage= 12,
        )
        assert sync_workflow_stage(o) is False
        assert o["current_stage"] == 12


class TestPenjadwalanDoesNotNeedScreener:
    def test_scheduling_without_screener_is_done(self):
        o = _po(
            design_status="Approved",
            payment_status="Approved",
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            operator_splits=[{"id": "s1", "quantity": 10}],
        )
        assert penjadwalan_done(o)
        assert not screener_done(o)
        ready = workflow_ready(o)
        assert ready["screener"] is True
        assert ready["helper"] is True
        assert ready["operator"] is False
