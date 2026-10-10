"""Unit tests for quantity invariants, partial flow, legacy PO, concurrency logic.

No live API or Mongo required.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions
from quantity_rules import (
    available_for_packing,
    helper_prepared_print_qty,
    is_fully_delivered,
    non_print_qty_total,
    operational_ready_qty_for_line,
    operator_assigned_qty,
    operator_assignment_cap,
    packing_line_breakdown,
    packing_lines_stored,
    packing_required_total,
    packing_status_semantic,
    apply_confirmed_packing_progress,
    physical_qty_for_loading_line,
    print_available_for_packing,
    print_completed_qty,
    print_qty_target,
    print_qty_total,
    print_status,
    qty_delivered,
    qty_packed,
    validate_delivery_qty,
    validate_helper_prepared_qty,
    validate_operator_assignment_total,
    validate_packing_lines,
    validate_packing_qty,
    validate_print_completed_total,
    available_for_packing_line,
    qty_packed_for_line,
    remaining_delivery_note_updates,
)


def _mixed_po(print_qty=3000, non_print_qty=3000, completed=None, packed=0, delivered=None, **extra):
    splits = []
    if completed is not None:
        for i, c in enumerate(completed):
            splits.append({
                "id": f"s{i}",
                "quantity": c,
                "qty_completed": c,
            })
    order = {
        "line_items": [
            {"name": "Cetak", "quantity": print_qty, "production_process": "print"},
            {"name": "Non", "quantity": non_print_qty, "production_process": "non_print"},
        ],
        "total_quantity": print_qty + non_print_qty,
        "operator_splits": splits,
        "packing_info": {"qty_packed": packed} if packed else {},
        "current_stage": 11,
        **extra,
    }
    if delivered is not None:
        order["qty_delivered"] = delivered
    return order


# --- A: operator assignment cap ---

def test_a_operator_assignment_3000_ok_3001_rejected():
    o = _mixed_po()
    assert print_qty_total(o) == 3000
    assert validate_operator_assignment_total(3000, 3000) is None
    assert validate_operator_assignment_total(3001, 3000) is not None
    o["operator_splits"] = [
        {"quantity": 1000, "qty_completed": 0},
        {"quantity": 1000, "qty_completed": 0},
        {"quantity": 1000, "qty_completed": 0},
    ]
    assert operator_assigned_qty(o) == 3000
    assert validate_operator_assignment_total(operator_assigned_qty(o) + 1, print_qty_total(o)) is not None


# --- B: partial packing availability ---

def test_b_print_completed_1000_available_4000():
    o = _mixed_po(completed=[1000])
    assert print_completed_qty(o) == 1000
    assert non_print_qty_total(o) == 3000
    # Non-cetak skip Packing — availability Packing = cetak siap saja
    assert available_for_packing(o) == 1000
    assert validate_packing_qty(1000, o) is None
    assert validate_packing_qty(1001, o) is not None


def test_b_no_double_count_packing():
    o = _mixed_po(completed=[1000], packed=1000)
    # Still only 1000 physically available for Packing (print); packed 1000 is subset
    assert available_for_packing(o) == 1000
    assert validate_packing_qty(1000, o) is None
    assert validate_packing_qty(1001, o) is not None


# --- C: partial delivery ---

def test_c_partial_delivery_not_final():
    o = _mixed_po(completed=[1000], packed=1000)
    o["shipments"] = [{"id": "s1", "lines": [{"line_idx": 0, "qty": 1000}]}]
    assert validate_delivery_qty(1000, o) is None
    assert validate_delivery_qty(1001, o) is not None
    o["qty_delivered"] = 1000
    o["delivery_status"] = "Sebagian Terkirim"
    o["delivered_at"] = "2026-01-01T00:00:00Z"
    o["delivered_by"] = "driver@x.com"
    o["current_stage"] = 13
    assert not is_fully_delivered(o)
    assert qty_delivered(o) == 1000  # explicit, not legacy-inflated


def test_c_delivery_cumulative_when_packed_increases():
    o = _mixed_po(completed=[2000], packed=1000, delivered=1000)
    o["shipments"] = [{"id": "s1", "lines": [{"line_idx": 0, "qty": 1000}]}]
    assert validate_delivery_qty(1000, o) is None
    o["packing_info"] = {"qty_packed": 2000}
    o["shipments"] = [{"id": "s1", "lines": [{"line_idx": 0, "qty": 2000}]}]
    assert validate_delivery_qty(2000, o) is None
    assert validate_delivery_qty(2001, o) is not None


# --- D: full delivery ---

def test_d_full_6000_delivered():
    o = _mixed_po(
        completed=[1000, 1000, 1000],
        packed=6000,
        delivered=6000,
    )
    o["operator_splits"] = [
        {"id": "a", "quantity": 1000, "qty_completed": 1000},
        {"id": "b", "quantity": 1000, "qty_completed": 1000},
        {"id": "c", "quantity": 1000, "qty_completed": 1000},
    ]
    # Packing availability = print only (3000); non-cetak skip Packing
    assert available_for_packing(o) == 3000
    assert is_fully_delivered(o)
    o["delivery_status"] = "Terkirim"
    o["current_stage"] = 14
    assert is_fully_delivered(o)


# --- E: simulated concurrent updates (optimistic lock logic) ---

class _FakeDB:
    """Minimal store simulating updated_at optimistic locking."""

    def __init__(self, doc):
        self.doc = dict(doc)
        self.version = 0

    def read(self):
        return dict(self.doc), self.doc.get("updated_at")

    def write(self, expected_updated, new_doc):
        if expected_updated != self.doc.get("updated_at"):
            return False
        self.version += 1
        new_doc["updated_at"] = f"v{self.version}"
        self.doc = dict(new_doc)
        return True


def test_e_concurrent_assignment_second_fails_or_revalidates():
    store = _FakeDB(_mixed_po())
    store.doc["updated_at"] = "v0"
    store.doc["operator_splits"] = []

    def assign(store_ref, qty_list):
        doc, token = store_ref.read()
        total = sum(qty_list)
        err = validate_operator_assignment_total(total, print_qty_total(doc))
        if err:
            return err
        doc["operator_splits"] = [{"quantity": q, "qty_completed": 0} for q in qty_list]
        ok = store_ref.write(token, doc)
        return ok

    assert assign(store, [1000, 1000, 1000]) is True
    # Concurrent stale write with over-allocation must fail lock
    doc, stale = _mixed_po(), "v0"
    doc["updated_at"] = stale
    assert store.write(stale, {**store.doc, "operator_splits": [{"quantity": 4000}]}) is False
    # Fresh read rejects over-allocation
    doc, token = store.read()
    assert validate_operator_assignment_total(4000, print_qty_total(doc)) is not None


def test_e_concurrent_packing_stale_write_rejected():
    store = _FakeDB(_mixed_po(completed=[1000]))
    store.doc["updated_at"] = "v0"

    doc, token = store.read()
    # Packing availability = print completed only (non-cetak skip Packing)
    assert validate_packing_qty(1000, doc) is None
    assert validate_packing_qty(1001, doc) is not None
    doc["packing_info"] = {"qty_packed": 1000}
    assert store.write(token, doc) is True

    # Stale concurrent pack attempt
    assert store.write("v0", {**store.doc, "packing_info": {"qty_packed": 1000}}) is False


# --- Legacy PO safety ---

def test_legacy_active_po_not_full_delivered():
    o = {
        "total_quantity": 6000,
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
    }
    assert qty_delivered(o) == 0
    assert not is_fully_delivered(o)


def test_legacy_partial_with_delivered_at_not_inflated():
    o = {
        "total_quantity": 6000,
        "line_items": [
            {"quantity": 3000, "production_process": "print"},
            {"quantity": 3000, "production_process": "non_print"},
        ],
        "delivery_status": "Sebagian Terkirim",
        "delivered_at": "2026-01-01",
        "delivered_by": "x@y.com",
        "current_stage": 13,
        "packing_info": {"qty_packed": 1000},
    }
    assert qty_delivered(o) == 0
    assert not is_fully_delivered(o)


def test_legacy_full_terkirim_without_qty_delivered_field():
    o = {
        "total_quantity": 6000,
        "line_items": [
            {"quantity": 3000, "production_process": "print"},
            {"quantity": 3000, "production_process": "non_print"},
        ],
        "delivery_status": "Terkirim",
        "current_stage": 14,
        "delivered_at": "2026-01-01",
        "delivered_by": "x@y.com",
    }
    assert qty_delivered(o) == 6000
    assert is_fully_delivered(o)


def test_legacy_no_line_items_uses_total_quantity():
    o = {"total_quantity": 500, "operator_splits": []}
    assert print_qty_total(o) == 500
    assert packing_required_total(o) == 500


# --- F: Buffer Stock RBAC ---

def test_f_crm_has_buffer_stock_in_builtin_defaults():
    assert "buffer_stock.manage" in ROLE_DEFAULT_PERMISSIONS["crm"]


def test_f_crm_override_topup_simulation():
    """Simulate Mongo role_permissions override missing buffer_stock."""
    override = {"permissions": ["po.view", "customer_approval.view"]}
    perms = effective_permissions("crm", None, {"crm": override["permissions"]})
    assert "buffer_stock.manage" not in perms
    # After idempotent $addToSet migration:
    merged = list(set(override["permissions"]) | {"buffer_stock.manage"})
    perms_after = effective_permissions("crm", None, {"crm": merged})
    assert "buffer_stock.manage" in perms_after


def test_f_operator_denied_buffer_stock():
    perms = effective_permissions("operator", None, None)
    assert "buffer_stock.manage" not in perms


# --- packing semantic ---

def test_packing_semantic_legacy_stage():
    o = {
        "line_items": [{"quantity": 100, "production_process": "print"}],
        "current_stage": 10,
        "packing_status": "Sudah",
        "packing_info": {},
    }
    assert packing_status_semantic(o) == "Belum"


def test_confirmed_packing_opens_downstream_partial():
    o = _mixed_po(completed=[3000], packed=2000)
    o["current_stage"] = 11
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 2000}], "qty_packed": 2000}
    assert apply_confirmed_packing_progress(o) is True
    assert o["current_stage"] == 12
    assert o["packing_status"] == "Berjalan"
    updates = remaining_delivery_note_updates(o)
    # Print packed 2000 + non-cetak PO qty 3000 (skip Packing) = 5000
    by_idx = {int(u["line_idx"]): int(u["qty"]) for u in updates}
    assert by_idx.get(0) == 2000
    assert by_idx.get(1) == 3000
    assert sum(by_idx.values()) == 5000


def test_confirmed_packing_zero_does_not_mutate():
    o = _mixed_po(completed=[3000], packed=0)
    o["current_stage"] = 11
    o["packing_info"] = {}
    snap = dict(o)
    assert apply_confirmed_packing_progress(o) is False
    assert o["current_stage"] == 11
    assert o.get("packing_status") == snap.get("packing_status")


def test_confirmed_packing_idempotent_same_stage():
    o = _mixed_po(completed=[3000], packed=2000)
    o["current_stage"] = 12
    o["packing_status"] = "Berjalan"
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 2000}], "qty_packed": 2000}
    assert apply_confirmed_packing_progress(o) is False
    assert o["current_stage"] == 12


def test_no_negative_or_over_allocation():
    assert validate_operator_assignment_total(-1, 100) is not None
    o = _mixed_po(completed=[1000], packed=500, delivered=400)
    assert validate_packing_qty(300, o) is not None
    assert validate_delivery_qty(350, o) is not None


# --- Helper prepared qty is hard cap for CETAK ---

def _helper_po(prepared=2000, completed=None, packed=0, delivered=None, print_qty=3000, non_print_qty=3000):
    o = _mixed_po(print_qty=print_qty, non_print_qty=non_print_qty, completed=completed, packed=packed, delivered=delivered)
    o["helper_prep"] = {
        "qty_required": print_qty,
        "qty_prepared": prepared,
        "items": [
            {"line_idx": 0, "qty_required": print_qty, "qty_prepared": prepared},
        ],
    }
    return o


def test_a_scheduling_not_capped_by_helper_prepared():
    """TEST A: Helper 2000, Cetak 3000 → assignment boleh 3000; execution tetap cap Helper."""
    o = _helper_po(prepared=2000)
    assert print_qty_total(o) == 3000
    assert helper_prepared_print_qty(o) == 2000
    assert operator_assignment_cap(o) == 3000
    assert validate_operator_assignment_total(3000, 3000, 2000) is None
    assert validate_operator_assignment_total(3001, 3000, 2000) is not None
    assert validate_print_completed_total(2000, o) is None
    assert validate_print_completed_total(2001, o) is not None


def test_b_packing_print_capped_by_completed_and_helper():
    """TEST B: Helper 2000, Operator completed 1000 → print available packing = 1000."""
    o = _helper_po(prepared=2000, completed=[1000])
    assert print_completed_qty(o) == 1000
    assert print_available_for_packing(o) == 1000
    assert non_print_qty_total(o) == 3000
    # Non-cetak skip Packing
    assert available_for_packing(o) == 1000
    assert validate_packing_qty(1000, o) is None
    assert validate_packing_qty(1001, o) is not None


def test_b_packing_print_never_exceeds_helper_even_if_completed_higher():
    o = _helper_po(prepared=2000)
    o["operator_splits"] = [{"quantity": 3000, "qty_completed": 2500}]
    assert print_completed_qty(o) == 2500
    assert print_available_for_packing(o) == 2000
    assert available_for_packing(o) == 2000  # print only; non-cetak skip Packing


def test_c_helper_increase_unlocks_more_operator_execution():
    """TEST C: Helper 2000 → 2500 membuka tambahan actual print, bukan scheduling."""
    o = _helper_po(prepared=2000)
    assert operator_assignment_cap(o) == 3000
    assert validate_operator_assignment_total(3000, 3000, 2000) is None
    assert validate_print_completed_total(2000, o) is None
    assert validate_print_completed_total(2500, o) is not None
    o["helper_prep"]["qty_prepared"] = 2500
    o["helper_prep"]["items"][0]["qty_prepared"] = 2500
    assert operator_assignment_cap(o) == 3000
    assert validate_print_completed_total(2500, o) is None
    assert validate_print_completed_total(2501, o) is not None


def test_d_delivered_capped_by_packed():
    """TEST D: Confirmed/SJ 1000 → delivered max 1000."""
    o = _helper_po(prepared=2000, completed=[1000], packed=1000)
    o["shipments"] = [{"id": "s1", "lines": [{"line_idx": 0, "qty": 1000}]}]
    assert validate_delivery_qty(1000, o) is None
    assert validate_delivery_qty(1001, o) is not None


def test_e_non_print_skips_operator_and_helper_cap():
    """TEST E: Qty Tidak Cetak tidak masuk Operator dan skip Packing."""
    o = _mixed_po(print_qty=0, non_print_qty=3000, completed=None)
    o["line_items"] = [
        {"name": "Non", "quantity": 3000, "production_process": "non_print"},
    ]
    o["total_quantity"] = 3000
    o["operator_splits"] = []
    o["helper_prep"] = {"qty_required": 0, "qty_prepared": 0}
    assert print_qty_total(o) == 0
    assert non_print_qty_total(o) == 3000
    assert operator_assignment_cap(o) == 0
    assert available_for_packing(o) == 0
    assert packing_status_semantic(o) == "Sudah"
    assert validate_operator_assignment_total(1, 0, 0) is not None
    assert validate_packing_qty(3000, o) is not None


def test_helper_cannot_drop_below_completed_print():
    o = _helper_po(prepared=2000, completed=[1000])
    assert validate_helper_prepared_qty(1000, o) is None
    assert validate_helper_prepared_qty(999, o) is not None


def test_print_completed_rejected_over_helper():
    o = _helper_po(prepared=2000)
    assert validate_print_completed_total(2000, o) is None
    assert validate_print_completed_total(2001, o) is not None


def test_packing_line_breakdown_reads_stored_lines_not_total():
    o = _mixed_po(completed=[1000], packed=0)
    o["operator_splits"][0]["line_idx"] = 0
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 500}]}
    lines = packing_line_breakdown(o)
    # Non-cetak excluded from Packing breakdown
    assert len(lines) == 1
    print_row = lines[0]
    assert print_row["is_print"] is True
    assert print_row["line_idx"] == 0
    assert print_row["print_completed"] == 1000
    assert print_row["print_remaining"] == 2000
    assert print_row["available"] == 1000
    assert print_row["packed"] == 500
    assert print_row["remaining"] == 500
    assert qty_packed(o) == 500


def test_packing_line_breakdown_legacy_total_does_not_fill_lines():
    o = _mixed_po(completed=[1000], packed=4000)
    o["operator_splits"][0]["line_idx"] = 0
    lines = packing_line_breakdown(o)
    assert packing_lines_stored(o) is None
    assert qty_packed(o) == 4000
    assert len(lines) == 1
    assert lines[0]["packed"] == 0
    assert lines[0]["is_print"] is True


def test_packing_line_breakdown_helper_caps_print_available():
    o = _mixed_po(completed=[1000], packed=0)
    o["operator_splits"][0]["line_idx"] = 0
    o["helper_prep"] = {"qty_prepared": 500, "items": [{"line_idx": 0, "qty_required": 3000, "qty_prepared": 500}]}
    lines = packing_line_breakdown(o)
    assert len(lines) == 1
    assert lines[0]["helper_prepared"] == 500
    assert lines[0]["available"] == 500
    assert sum(r["available"] for r in lines) == available_for_packing(o) == 500


def _four_line_po():
    return {
        "line_items": [
            {"name": "A", "quantity": 1000, "production_process": "print"},
            {"name": "B", "quantity": 1000, "production_process": "non_print"},
            {"name": "C", "quantity": 250, "production_process": "print"},
            {"name": "D", "quantity": 250, "production_process": "non_print"},
        ],
        "total_quantity": 2500,
        "operator_splits": [
            {"line_idx": 0, "quantity": 400, "qty_completed": 400},
        ],
        "helper_prep": {
            "qty_prepared": 600,
            "items": [
                {"line_idx": 0, "qty_required": 1000, "qty_prepared": 600},
                {"line_idx": 2, "qty_required": 250, "qty_prepared": 0},
            ],
        },
        "packing_info": {},
        "current_stage": 11,
    }


def test_a_line_a_pack_300_of_available_400():
    o = _four_line_po()
    assert available_for_packing_line(o, 0) == 400
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 300}], o) is None
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 300}]}
    assert qty_packed_for_line(o, 0) == 300
    assert packing_line_breakdown(o)[0]["remaining"] == 100


def test_b_line_a_additional_150_rejected():
    o = _four_line_po()
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 300}]}
    # 300 + 150 = 450 cumulative
    err = validate_packing_lines([{"line_idx": 0, "packed_qty": 450}], o)
    assert err is not None
    assert qty_packed_for_line(o, 0) == 300


def test_c_non_print_b_rejected_from_packing():
    o = _four_line_po()
    o["packing_info"] = {"lines": [{"line_idx": 0, "packed_qty": 300}]}
    err = validate_packing_lines([{"line_idx": 1, "packed_qty": 500}], o)
    assert err is not None
    assert available_for_packing_line(o, 1) == 0


def test_d_total_packed_is_sum_of_lines():
    o = _four_line_po()
    o["packing_info"] = {
        "lines": [
            {"line_idx": 0, "packed_qty": 300},
            {"line_idx": 1, "packed_qty": 500},
        ]
    }
    assert qty_packed(o) == 800
    assert qty_packed_for_line(o, 0) == 300
    assert qty_packed_for_line(o, 1) == 500


def test_e_one_line_complete_po_not_fully_packed():
    o = _four_line_po()
    o["packing_info"] = {"lines": [{"line_idx": 1, "packed_qty": 1000}]}
    assert packing_status_semantic(o) != "Sudah"
    assert qty_packed(o) == 1000
    assert packing_required_total(o) == 2500


def test_f_all_print_lines_packed_marks_complete():
    o = _four_line_po()
    o["operator_splits"] = [
        {"line_idx": 0, "quantity": 1000, "qty_completed": 1000},
        {"line_idx": 2, "quantity": 250, "qty_completed": 250},
    ]
    o["helper_prep"] = {
        "qty_prepared": 1250,
        "items": [
            {"line_idx": 0, "qty_required": 1000, "qty_prepared": 1000},
            {"line_idx": 2, "qty_required": 250, "qty_prepared": 250},
        ],
    }
    # Non-cetak lines are not packed — only print lines
    lines_payload = [
        {"line_idx": 0, "packed_qty": 1000},
        {"line_idx": 2, "packed_qty": 250},
    ]
    assert validate_packing_lines(lines_payload, o) is None
    assert validate_packing_lines([{"line_idx": 1, "packed_qty": 1000}], o) is not None
    o["packing_info"] = {"lines": lines_payload}
    assert qty_packed(o) == 1250
    assert packing_status_semantic(o) == "Sudah"


def test_print_target_follows_penjadwalan_not_po_total():
    """PO 6000, Penjadwalan 3000 → target cetak 3000."""
    o = _mixed_po(print_qty=6000, non_print_qty=0)
    o["line_items"] = [{"name": "Cetak", "quantity": 6000, "production_process": "print"}]
    o["total_quantity"] = 6000
    o["operator_splits"] = [
        {"id": "a", "quantity": 1000, "qty_completed": 0},
        {"id": "b", "quantity": 1000, "qty_completed": 0},
        {"id": "c", "quantity": 1000, "qty_completed": 0},
    ]
    assert print_qty_total(o) == 6000
    assert print_qty_target(o) == 3000
    assert validate_print_completed_total(3000, o) is None
    assert validate_print_completed_total(3001, o) is not None


def test_print_status_partial_and_complete():
    from quantity_rules import PRINT_STATUS_DONE, PRINT_STATUS_IN_PROGRESS, PRINT_STATUS_NOT_STARTED
    o = _mixed_po(print_qty=3000, non_print_qty=3000)
    o["operator_splits"] = [
        {"id": "a", "quantity": 1000, "qty_completed": 0},
        {"id": "b", "quantity": 1000, "qty_completed": 0},
        {"id": "c", "quantity": 1000, "qty_completed": 0},
    ]
    assert print_status(o) == PRINT_STATUS_NOT_STARTED
    o["operator_splits"][0]["qty_completed"] = 1000
    assert print_completed_qty(o) == 1000
    assert print_status(o) == PRINT_STATUS_IN_PROGRESS
    o["operator_splits"][1]["qty_completed"] = 1000
    o["operator_splits"][2]["qty_completed"] = 1000
    assert print_completed_qty(o) == 3000
    assert print_status(o) == PRINT_STATUS_DONE


def test_partial_print_available_for_packing_while_not_complete():
    o = _mixed_po(print_qty=3000, non_print_qty=3000)
    o["helper_prep"] = {"qty_prepared": 3000}
    o["operator_splits"] = [
        {"id": "a", "quantity": 1000, "qty_completed": 1000},
        {"id": "b", "quantity": 1000, "qty_completed": 0},
        {"id": "c", "quantity": 1000, "qty_completed": 0},
    ]
    from quantity_rules import PRINT_STATUS_IN_PROGRESS
    assert print_status(o) == PRINT_STATUS_IN_PROGRESS
    assert print_available_for_packing(o) == 1000
    assert available_for_packing(o) == 1000
    assert validate_packing_qty(1000, o) is None
    assert validate_packing_qty(1001, o) is not None


def test_jadwal_total_qty_progress_not_po_total():
    """Jadwal Produksi Total Qty = print_completed / print_qty_total, bukan total PO."""
    o = {
        "total_quantity": 15000,
        "line_items": [
            {"name": "A", "quantity": 6000, "production_process": "print", "accurate_item_id": "A"},
            {"name": "B", "quantity": 4000, "production_process": "print", "accurate_item_id": "B"},
            {"name": "C", "quantity": 5000, "production_process": "non_print", "accurate_item_id": "C"},
        ],
        "operator_splits": [
            {"id": "sA", "line_idx": 0, "qty_completed": 4000, "quantity": 6000},
            {"id": "sB", "line_idx": 1, "qty_completed": 1000, "quantity": 4000},
        ],
    }
    assert print_qty_total(o) == 10000
    assert print_qty_total(o) != o["total_quantity"]
    assert print_completed_qty(o) == 5000
    assert packing_required_total(o) == 15000


def test_jadwal_total_qty_zero_actual_keeps_denominator():
    o = _mixed_po(print_qty=10000, non_print_qty=5000, completed=None)
    o["total_quantity"] = 15000
    o["line_items"][0]["quantity"] = 10000
    o["line_items"][1]["quantity"] = 5000
    assert print_qty_total(o) == 10000
    assert print_completed_qty(o) == 0


def test_jadwal_total_qty_complete_print():
    o = _mixed_po(print_qty=10000, non_print_qty=5000, completed=[10000])
    assert print_qty_total(o) == 10000
    assert print_completed_qty(o) == 10000


def test_jadwal_total_qty_no_double_count_on_resave():
    """qty_completed is a snapshot; re-saving the same value must not double."""
    o = _mixed_po(print_qty=10000, non_print_qty=5000)
    o["operator_splits"] = [{"id": "s1", "line_idx": 0, "quantity": 10000, "qty_completed": 5000}]
    assert print_completed_qty(o) == 5000
    o["operator_splits"][0]["qty_completed"] = 5000
    assert print_completed_qty(o) == 5000
    o["operator_splits"][0]["qty_completed"] = 5000
    assert print_completed_qty(o) == 5000


def test_jadwal_total_qty_no_printable_items():
    o = _mixed_po(print_qty=0, non_print_qty=15000, completed=None)
    o["line_items"] = [{"name": "C", "quantity": 15000, "production_process": "non_print"}]
    o["total_quantity"] = 15000
    o["operator_splits"] = []
    assert print_qty_total(o) == 0
    assert print_completed_qty(o) == 0


def test_jadwal_total_qty_over_target_not_clamped():
    o = _mixed_po(print_qty=10000, non_print_qty=5000)
    o["operator_splits"] = [{"id": "s1", "quantity": 12000, "qty_completed": 12000}]
    assert print_completed_qty(o) == 12000
    assert print_qty_total(o) == 10000
    assert print_completed_qty(o) > print_qty_total(o)


def _two_print_packing_po(
    *,
    lines,
    delivered,
    line_qty=(2000, 500),
    completed=None,
    helper_items=None,
    helper_total=None,
    non_print=2000,
    legacy_qty_packed=None,
):
    """Dummy dua line cetak. `lines=None` means no packing_info.lines."""
    done = completed if completed is not None else line_qty
    items = helper_items if helper_items is not None else [
        {"line_idx": 0, "qty_prepared": done[0]},
        {"line_idx": 1, "qty_prepared": done[1]},
    ]
    prepared = helper_total if helper_total is not None else (done[0] + done[1])
    lis = [
        {"name": "12 oz Datar 8 gr SA", "quantity": line_qty[0], "production_process": "print"},
        {"name": "Paper Cup Double Wall 8 oz Putih", "quantity": line_qty[1], "production_process": "print"},
    ]
    if non_print:
        lis.append({"name": "Lid", "quantity": non_print, "production_process": "non_print"})
    if lines is None:
        packing = {"qty_packed": 0 if legacy_qty_packed is None else legacy_qty_packed}
    else:
        packing = {
            "qty_packed": sum(max(0, int(row.get("packed_qty") or 0)) for row in lines),
            "lines": lines,
        }
    order = {
        "line_items": lis,
        "total_quantity": sum(li["quantity"] for li in lis),
        "operator_splits": [
            {"line_idx": 0, "quantity": line_qty[0], "qty_completed": done[0]},
            {"line_idx": 1, "quantity": line_qty[1], "qty_completed": done[1]},
        ],
        "helper_prep": {"qty_prepared": prepared, "items": items},
        "packing_info": packing,
        "current_stage": 13,
        "qty_delivered": delivered,
    }
    return order


def test_packing_lines_plus_500_monotonic_does_not_mutate_order():
    lines = [{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}]
    order = _two_print_packing_po(lines=lines, delivered=4000)
    snapshot = copy.deepcopy(order)
    updates = [{"line_idx": 1, "packed_qty": 500}]
    updates_snapshot = copy.deepcopy(updates)
    assert validate_packing_lines(updates, order) is None
    assert order == snapshot
    assert updates == updates_snapshot


def test_packing_lines_unchanged_baseline_passes_when_delivered_is_higher():
    order = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
    )
    assert validate_packing_lines([{"line_idx": 0, "packed_qty": 2000}], order) is None


def test_packing_lines_line_drop_hidden_by_other_increase_stays_rejected():
    order = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
    )
    err = validate_packing_lines(
        [{"line_idx": 0, "packed_qty": 1600}, {"line_idx": 1, "packed_qty": 500}],
        order,
    )
    assert err == (
        "Qty Packing (2100) tidak boleh lebih kecil dari qty sudah dikirim (4000)."
    )


def test_packing_lines_decrease_still_allowed_when_total_meets_delivered():
    order = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 500}],
        delivered=2000,
    )
    assert validate_packing_lines([{"line_idx": 1, "packed_qty": 0}], order) is None


def test_packing_lines_empty_baseline_legacy_qty_keeps_delivered_guard():
    order = _two_print_packing_po(lines=None, delivered=4000, legacy_qty_packed=2000)
    assert packing_lines_stored(order) is None
    err = validate_packing_lines([{"line_idx": 1, "packed_qty": 500}], order)
    assert err == (
        "Qty Packing (500) tidak boleh lebih kecil dari qty sudah dikirim (4000)."
    )


def test_packing_lines_other_guards_keep_their_messages():
    base = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
    )
    assert "non-cetak" in validate_packing_lines([{"line_idx": 2, "packed_qty": 10}], base)
    assert validate_packing_lines([{"line_idx": 9, "packed_qty": 1}], base) == (
        "line_idx 9 tidak valid."
    )
    assert validate_packing_lines([{"line_idx": 1, "packed_qty": -1}], base) == (
        "Qty Packing tidak boleh negatif."
    )
    over_target = validate_packing_lines([{"line_idx": 1, "packed_qty": 501}], base)
    assert over_target == (
        "Qty Packing 'Paper Cup Double Wall 8 oz Putih' (501) melebihi qty PO (500)."
    )
    short = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
        completed=(2000, 100),
    )
    per_line = validate_packing_lines([{"line_idx": 1, "packed_qty": 500}], short)
    assert per_line == (
        "Qty Packing 'Paper Cup Double Wall 8 oz Putih' (500) melebihi qty tersedia "
        "untuk packing line tersebut (100)."
    )
    capped = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 500}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
        line_qty=(1000, 1000),
        completed=(1000, 1000),
        helper_items=[
            {"line_idx": 0, "qty_prepared": 1000},
            {"line_idx": 1, "qty_prepared": 1000},
        ],
        helper_total=1500,
    )
    total_avail = validate_packing_lines(
        [{"line_idx": 0, "packed_qty": 1000}, {"line_idx": 1, "packed_qty": 1000}],
        capped,
    )
    assert total_avail == (
        "Total qty packing Cetak (2000) melebihi qty Cetak tersedia untuk packing (1500)."
    )


def test_validate_packing_qty_below_delivered_still_rejected():
    order = _two_print_packing_po(
        lines=[{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        delivered=4000,
    )
    err = validate_packing_qty(2500, order)
    assert err == (
        "Qty Packing (2500) tidak boleh lebih kecil dari qty sudah dikirim (4000)."
    )


def test_operational_ready_and_loading_physical_print_vs_non_print():
    order = {
        "line_items": [
            {"name": "Cetak", "quantity": 100, "production_process": "print"},
            {"name": "Langsung", "quantity": 80, "production_process": "non_print"},
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 40}],
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": 30}]},
    }
    assert operational_ready_qty_for_line(order, 0) == 40
    assert operational_ready_qty_for_line(order, 1) == 80
    assert physical_qty_for_loading_line(order, 0) == 30
    assert physical_qty_for_loading_line(order, 1) == 80
    # Non-print readiness must not invent operator_splits / packing rows.
    assert order["operator_splits"] == [{"line_idx": 0, "qty_completed": 40}]


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"ALL {len(tests)} TESTS PASS")


if __name__ == "__main__":
    run_all()
