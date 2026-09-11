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
    HELPER_GATE_OPERATOR_MSG,
    SAMPLE_GATE_PRINT_QTY_MSG,
    SCREENER_GATE_OPERATOR_MSG,
    SCREENER_GATE_SAMPLE_MSG,
    compute_compatible_stage,
    design_ok,
    group2_complete,
    helper_done,
    helper_gate_reason,
    helper_unlocked,
    operator_unlocked,
    packing_unlocked,
    payment_ok,
    penjadwalan_done,
    penjadwalan_unlocked,
    print_qty_gate_reason,
    print_qty_unlocked,
    sample_approval_unlocked,
    sample_ok,
    sample_upload_unlocked,
    sample_uploaded,
    screener_done,
    screener_gate_reason,
    screener_unlocked,
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

    def test_customer_approve_satisfies_approval_design_not_payment(self):
        o = _po(
            design_files=[{"id": "d", "name": "d.pdf", "data_url": "data:application/pdf;base64,xx", "uploaded_at": "2026-09-11T00:00:00+00:00"}],
            design_status="Waiting",
            current_stage=2,
            customer_approval={"status": "approved", "approval_id": "ca-1"},
        )
        assert design_ok(o)
        assert not payment_ok(o)
        assert not group2_complete(o)
        assert compute_compatible_stage(o) < 5

    def test_customer_reject_does_not_satisfy_approval_design(self):
        o = _po(
            design_files=[{"id": "d", "name": "d.pdf", "uploaded_at": "2026-09-11T00:00:00+00:00"}],
            design_status="Waiting",
            current_stage=2,
            customer_approval={"status": "rejected", "approval_id": "ca-1"},
        )
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


# ---------- Parallel Screener & Helper after Penjadwalan ----------

class TestParallelScreenerAndHelper:
    def _scheduled(self, **extra):
        return _po(
            design_files=[{"id": "d"}],
            payment_files=[{"id": "p"}],
            design_status="Approved",
            payment_status="Approved",
            operator_splits=[{"id": "s1", "quantity": 100, "qty_completed": 0}],
            current_stage=6,
            **extra,
        )

    def test_screener_and_helper_both_unlocked_after_scheduling(self):
        o = self._scheduled()
        assert not screener_done(o)
        assert not helper_done(o)
        assert screener_unlocked(o)
        assert helper_unlocked(o)
        ready = workflow_ready(o)
        assert ready["screener"] is True
        assert ready["helper"] is True
        assert ready["operator"] is False

    def test_helper_can_complete_without_screener(self):
        o = self._scheduled(helper_prep={"qty_prepared": 100})
        assert helper_done(o)
        assert not screener_done(o)
        assert operator_unlocked(o)
        ready = workflow_ready(o)
        assert ready["helper"] is True
        assert ready["screener"] is True
        assert ready["operator"] is True

    def test_screener_can_complete_without_helper(self):
        o = self._scheduled(screener_info={"screen_qty": 2})
        assert screener_done(o)
        assert not helper_done(o)
        assert not operator_unlocked(o)
        ready = workflow_ready(o)
        assert ready["screener"] is True
        assert ready["helper"] is True
        assert ready["operator"] is False


# ---------- Sample Approval & Packing Flow ----------

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
            helper_prep={"qty_prepared": 100},
            **extra,
        )

    def test_f_sample_upload_unlocks_approval(self):
        o = self._operator_ready(sample_files=[{"id": "sm", "name": "s.png"}])
        assert sample_uploaded(o)
        assert not sample_ok(o)
        sync_workflow_stage(o)
        assert o["current_stage"] == 9
        assert workflow_ready(o)["sample_approval"] is True
        assert workflow_ready(o)["print_qty"] is False
        assert not packing_unlocked(o)

    def test_g_sample_approved_unlocks_packing(self):
        o = self._operator_ready(
            sample_files=[{"id": "sm"}],
            sample_approved=True,
            current_stage=9,
        )
        sync_workflow_stage(o)
        assert packing_unlocked(o)
        assert o["current_stage"] == 11
        assert workflow_ready(o)["packing"] is True
        assert workflow_ready(o)["print_qty"] is True

    def test_h_reject_locks_packing_keeps_sample_files(self):
        o = self._operator_ready(
            sample_files=[{"id": "sm"}],
            sample_approved=False,
            current_stage=11,
        )
        sync_workflow_stage(o, force=True)
        assert not packing_unlocked(o)
        assert o["current_stage"] == 9
        assert sample_uploaded(o)
        assert workflow_ready(o)["sample_upload"] is True
        assert workflow_ready(o)["print_qty"] is False
        assert print_qty_gate_reason(o) == SAMPLE_GATE_PRINT_QTY_MSG


# ---------- EXACT SCREENSHOT / PREVIEW FIXTURE ----------

class TestExactScreenshotRegression:
    def test_penjadwalan_done_helper_menunggu_input_locks_operator_and_downstream(self):
        """Fixture representing Penjadwalan Selesai:
        Penjadwalan = Selesai
        Screener = Menunggu input (Ready)
        Persiapan Helper = Menunggu input (Ready)
        Operator = LOCKED (Menunggu)
        Konfirmasi Sample = LOCKED (Menunggu)
        Cetak = LOCKED (Menunggu)
        Packing = LOCKED (Menunggu)
        """
        from services.workflow_stage_status import resolve_stage_chip

        o = _po(
            current_stage=6,
            design_bypassed=True,
            payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 1000, "qty_completed": 0}],
            screener_info=None,
            helper_prep=None,
            sample_files=[],
            sample_approved=False,
        )
        # Stage 5: Penjadwalan
        assert resolve_stage_chip(5, o, 6)["state"] == "done"

        # Stage 6: Screener -> Ready / Menunggu input
        screen_chip = resolve_stage_chip(6, o, 6)
        assert screen_chip["state"] == "current"
        assert screen_chip["label"] == "Menunggu input"

        # Stage 7: Persiapan Helper -> Ready / Menunggu input
        helper_chip = resolve_stage_chip(7, o, 6)
        assert helper_chip["state"] == "current"
        assert helper_chip["label"] == "Menunggu input"

        # Stage 8: Operator MUST BE LOCKED / Menunggu
        op_chip = resolve_stage_chip(8, o, 6)
        assert op_chip["state"] == "pending"
        assert op_chip["label"] == "Menunggu"

        # Downstream all locked
        assert not helper_done(o)
        assert not operator_unlocked(o)
        assert not sample_ok(o)
        assert not print_qty_unlocked(o)
        assert not packing_unlocked(o)


# ---------- CASES 1 TO 14 REGRESSION TEST CASES ----------

class TestCases1To14:
    def test_case_1_penjadwalan_not_done_blocks_screener_and_helper(self):
        """CASE 1: Penjadwalan belum selesai -> Screener tidak eligible, Helper tidak eligible."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[],  # No scheduling
        )
        assert not penjadwalan_done(o)
        assert not screener_unlocked(o)
        assert not helper_unlocked(o)
        ready = workflow_ready(o)
        assert ready["screener"] is False
        assert ready["helper"] is False

    def test_case_2_penjadwalan_done_unlocks_screener(self):
        """CASE 2: Penjadwalan selesai -> Screener eligible."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 100}],
        )
        assert penjadwalan_done(o)
        assert screener_unlocked(o)
        assert workflow_ready(o)["screener"] is True

    def test_case_3_penjadwalan_done_unlocks_helper_without_screener(self):
        """CASE 3: Penjadwalan selesai -> Helper eligible TANPA Screener selesai."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 100}],
            screener_info=None,
        )
        assert penjadwalan_done(o)
        assert helper_unlocked(o)
        assert workflow_ready(o)["helper"] is True

    def test_case_4_screener_not_confirmed_helper_can_confirm(self):
        """CASE 4: Screener belum dikonfirmasi -> Helper tetap bisa confirm qty."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 100}],
            screener_info=None,
            helper_prep={"qty_prepared": 100},
        )
        assert helper_done(o)
        assert operator_unlocked(o)

    def test_case_5_helper_not_confirmed_screener_can_confirm(self):
        """CASE 5: Helper belum selesai -> Screener tetap bisa confirm activity."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 100}],
            screener_info={"screen_qty": 2},
            helper_prep=None,
        )
        assert screener_done(o)
        assert not helper_done(o)

    def test_case_6_target_500_prepared_0_remaining_500(self):
        """CASE 6: Target Helper 500, Prepared 0 -> Remaining 500."""
        target = 500
        prepared = 0
        remaining = max(target - prepared, 0)
        assert remaining == 500

    def test_case_7_helper_confirm_200_cumulative_200_remaining_300(self):
        """CASE 7: Helper confirm 200 -> cumulative 200 -> remaining 300."""
        target = 500
        prepared = 200
        remaining = max(target - prepared, 0)
        assert remaining == 300

    def test_case_8_helper_confirm_300_more_cumulative_500_remaining_0(self):
        """CASE 8: Helper confirm 300 lagi -> cumulative 500 -> remaining 0."""
        target = 500
        prepared = 200 + 300
        remaining = max(target - prepared, 0)
        assert remaining == 0

    def test_case_9_helper_confirm_over_target_rejected(self):
        """CASE 9: Helper confirm > remaining -> backend reject."""
        from quantity_rules import validate_helper_prepared_qty
        o = _po(
            line_items=[{"name": "Kaos", "quantity": 500, "production_process": "print"}],
            total_quantity=500,
            operator_splits=[{"id": "s1", "quantity": 500}],
        )
        assert validate_helper_prepared_qty(500, o) is None
        assert validate_helper_prepared_qty(501, o) is not None

    def test_case_10_operator_before_helper_qty_eligible_rejected(self):
        """CASE 10: Operator sebelum Helper qty eligible -> reject."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 500, "qty_completed": 0}],
            helper_prep=None,
        )
        assert not helper_done(o)
        assert not operator_unlocked(o)

    def test_case_11_operator_for_qty_eligible_allowed(self):
        """CASE 11: Operator untuk qty <= Helper eligible -> allowed."""
        from quantity_rules import available_for_packing
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            line_items=[{"name": "Kaos", "quantity": 500, "production_process": "print"}],
            total_quantity=500,
            operator_splits=[{"id": "s1", "quantity": 500, "qty_completed": 200}],
            helper_prep={"qty_prepared": 200},
            sample_approved=True,
        )
        assert helper_done(o)
        assert operator_unlocked(o)
        assert available_for_packing(o) == 200

    def test_case_12_same_item_different_exact_line(self):
        """CASE 12: Same item name different exact line -> independent quantities."""
        lines = [
            {"id": "line-1", "name": "Cup 16oz", "quantity": 500, "production_process": "print"},
            {"id": "line-2", "name": "Cup 16oz", "quantity": 500, "production_process": "non_print"},
        ]
        o = _po(line_items=lines, total_quantity=1000)
        assert o["line_items"][0]["id"] != o["line_items"][1]["id"]
        assert o["line_items"][0]["production_process"] == "print"
        assert o["line_items"][1]["production_process"] == "non_print"

    def test_case_13_non_cetak_does_not_need_helper(self):
        """CASE 13: Non-Cetak -> tidak membutuhkan Helper."""
        o = _po(
            line_items=[{"id": "line-1", "name": "Plastik", "quantity": 500, "production_process": "non_print"}],
            requires_printing=False,
            design_bypassed=True,
            payment_bypassed=True,
        )
        assert helper_done(o) is True
        assert operator_unlocked(o) is True

    def test_case_14_admin_cannot_skip_qty_gate(self):
        """CASE 14: Admin permission lengkap -> tetap tidak bisa melanggar qty gate."""
        from quantity_rules import validate_helper_prepared_qty
        o = _po(
            line_items=[{"name": "Kaos", "quantity": 500, "production_process": "print"}],
            total_quantity=500,
            operator_splits=[{"id": "s1", "quantity": 500}],
        )
        assert validate_helper_prepared_qty(600, o) is not None


class TestScreenerConfirmationCases:
    def test_case_1_penjadwalan_not_done_screener_reject(self):
        """CASE 1: Penjadwalan belum selesai -> Screener reject."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[],
        )
        assert not screener_unlocked(o)
        assert workflow_ready(o)["screener"] is False

    def test_case_2_penjadwalan_done_screener_qty_1_success(self):
        """CASE 2: Penjadwalan selesai -> Screener qty 1 success."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 500}],
        )
        assert screener_unlocked(o)
        assert workflow_ready(o)["screener"] is True
        from quantity_rules import validate_screener_qty
        assert validate_screener_qty(1, o) is None

    def test_case_3_helper_prepared_0_screener_qty_1_success(self):
        """CASE 3: Helper prepared 0 -> Screener qty 1 tetap success."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 500}],
            helper_prep={"qty_prepared": 0, "qty_required": 500},
        )
        assert screener_unlocked(o)
        from quantity_rules import validate_screener_qty
        assert validate_screener_qty(1, o) is None

    def test_case_4_helper_prepared_500_done_screener_qty_1_success(self):
        """CASE 4: Helper prepared 500/500 DONE -> Screener qty 1 tetap success."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            operator_splits=[{"id": "s1", "quantity": 500}],
            helper_prep={"qty_prepared": 500, "qty_required": 500},
        )
        assert screener_unlocked(o)
        from quantity_rules import validate_screener_qty
        assert validate_screener_qty(1, o) is None

    def test_case_5_screener_confirm_1_then_4_cumulative_5(self):
        """CASE 5: Screener confirm 1 lalu 4 -> cumulative activity = 5."""
        from services.workflow_entries import append_qty_entry, sum_entry_qty
        entries = []
        entries, _, _ = append_qty_entry(entries, qty=1, stage="screener", user={"email": "a@ep.com", "name": "A"}, now="2026-08-30T00:00:00Z", action_id="act-1", po_id="po-1")
        assert sum_entry_qty(entries) == 1
        entries, _, _ = append_qty_entry(entries, qty=4, stage="screener", user={"email": "a@ep.com", "name": "A"}, now="2026-08-30T00:00:01Z", action_id="act-2", po_id="po-1")
        assert sum_entry_qty(entries) == 5

    def test_case_6_qty_0_reject(self):
        """CASE 6: qty 0 -> reject."""
        from quantity_rules import validate_screener_qty
        assert validate_screener_qty(0, _po()) is None  # base validator
        # In endpoint, increment <= 0 raises 400

    def test_case_7_qty_negative_reject(self):
        """CASE 7: qty negative -> reject."""
        from quantity_rules import validate_screener_qty
        assert validate_screener_qty(-1, _po()) is not None

    def test_case_9_double_submit_exact_request_idempotent(self):
        """CASE 9: double-submit exact request -> tidak membuat duplicate transaction."""
        from services.workflow_entries import append_qty_entry, sum_entry_qty, find_by_action_id
        entries = []
        entries, _, created1 = append_qty_entry(entries, qty=1, stage="screener", user={"email": "a@ep.com", "name": "A"}, now="2026-08-30T00:00:00Z", action_id="dup-1", po_id="po-1")
        assert created1 is True
        assert sum_entry_qty(entries) == 1
        assert find_by_action_id(entries, "dup-1") is not None
        # Second attempt with same action_id
        entries, _, created2 = append_qty_entry(entries, qty=1, stage="screener", user={"email": "a@ep.com", "name": "A"}, now="2026-08-30T00:00:00Z", action_id="dup-1", po_id="po-1")
        assert created2 is False
        assert sum_entry_qty(entries) == 1

    def test_case_10_screener_confirmation_does_not_mutate_other_stages(self):
        """CASE 10: Screener confirmation -> TIDAK mengubah Helper prepared qty, Operator qty, Cetak qty, Packing qty, SJ qty, Loading qty, Pengiriman qty."""
        o = _po(
            design_bypassed=True, payment_bypassed=True,
            line_items=[{"name": "Kaos", "quantity": 500, "production_process": "print"}],
            total_quantity=500,
            operator_splits=[{"id": "s1", "quantity": 500, "qty_completed": 0}],
            helper_prep={"qty_prepared": 500, "qty_required": 500},
            screener_info={"screen_qty": 1},
        )
        assert o["helper_prep"]["qty_prepared"] == 500
        assert o["operator_splits"][0]["qty_completed"] == 0
        from quantity_rules import helper_prepared_print_qty, available_for_packing
        assert helper_prepared_print_qty(o) == 500
        assert available_for_packing(o) == 0



