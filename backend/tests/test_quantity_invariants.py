"""Unit tests for quantity invariants, partial flow, legacy PO, concurrency logic.

No live API or Mongo required.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions
from quantity_rules import (
    available_for_packing,
    helper_prepared_print_qty,
    is_fully_delivered,
    non_print_qty_total,
    operator_assigned_qty,
    operator_assignment_cap,
    packing_required_total,
    packing_status_semantic,
    print_available_for_packing,
    print_completed_qty,
    print_qty_total,
    qty_delivered,
    validate_delivery_qty,
    validate_helper_prepared_qty,
    validate_operator_assignment_total,
    validate_packing_qty,
    validate_print_completed_total,
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
    assert available_for_packing(o) == 4000
    assert validate_packing_qty(4000, o) is None
    assert validate_packing_qty(4001, o) is not None


def test_b_no_double_count_packing():
    o = _mixed_po(completed=[1000], packed=1000)
    # Still only 4000 physically available total; packed 1000 is subset
    assert available_for_packing(o) == 4000
    assert validate_packing_qty(1000, o) is None
    assert validate_packing_qty(4000, o) is None
    assert validate_packing_qty(4001, o) is not None


# --- C: partial delivery ---

def test_c_partial_delivery_not_final():
    o = _mixed_po(completed=[1000], packed=1000)
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
    assert validate_delivery_qty(1000, o) is None
    o["packing_info"] = {"qty_packed": 2000}
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
    assert available_for_packing(o) == 6000
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
    assert validate_packing_qty(4000, doc) is None
    doc["packing_info"] = {"qty_packed": 4000}
    assert store.write(token, doc) is True

    # Stale concurrent pack attempt
    assert store.write("v0", {**store.doc, "packing_info": {"qty_packed": 4000}}) is False


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


def test_a_helper_prepared_caps_operator_assignment():
    """TEST A: Helper 2000, Cetak 3000 → assignment max 2000; 2001 ditolak."""
    o = _helper_po(prepared=2000)
    assert print_qty_total(o) == 3000
    assert helper_prepared_print_qty(o) == 2000
    assert operator_assignment_cap(o) == 2000
    assert validate_operator_assignment_total(2000, 3000, 2000) is None
    assert validate_operator_assignment_total(2001, 3000, 2000) is not None


def test_b_packing_print_capped_by_completed_and_helper():
    """TEST B: Helper 2000, Operator completed 1000 → print available packing = 1000."""
    o = _helper_po(prepared=2000, completed=[1000])
    assert print_completed_qty(o) == 1000
    assert print_available_for_packing(o) == 1000
    # Non-cetak 3000 tetap langsung tersedia
    assert non_print_qty_total(o) == 3000
    assert available_for_packing(o) == 4000
    assert validate_packing_qty(4000, o) is None
    assert validate_packing_qty(4001, o) is not None


def test_b_packing_print_never_exceeds_helper_even_if_completed_higher():
    o = _helper_po(prepared=2000)
    o["operator_splits"] = [{"quantity": 3000, "qty_completed": 2500}]
    assert print_completed_qty(o) == 2500
    assert print_available_for_packing(o) == 2000
    assert available_for_packing(o) == 5000  # 3000 non-print + 2000 print


def test_c_helper_increase_unlocks_more_operator_qty():
    """TEST C: Helper 2000 → 2500 membuka tambahan assignment 500."""
    o = _helper_po(prepared=2000)
    assert operator_assignment_cap(o) == 2000
    assert validate_operator_assignment_total(2000, 3000, 2000) is None
    assert validate_operator_assignment_total(2500, 3000, 2000) is not None
    o["helper_prep"]["qty_prepared"] = 2500
    o["helper_prep"]["items"][0]["qty_prepared"] = 2500
    assert operator_assignment_cap(o) == 2500
    assert validate_operator_assignment_total(2500, 3000, 2500) is None
    assert validate_operator_assignment_total(2501, 3000, 2500) is not None


def test_d_delivered_capped_by_packed():
    """TEST D: Packed 1000 → delivered max 1000."""
    o = _helper_po(prepared=2000, completed=[1000], packed=1000)
    assert validate_delivery_qty(1000, o) is None
    assert validate_delivery_qty(1001, o) is not None


def test_e_non_print_skips_operator_and_helper_cap():
    """TEST E: Qty Tidak Cetak tidak masuk Operator dan tetap packing-ready."""
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
    assert available_for_packing(o) == 3000
    assert validate_operator_assignment_total(1, 0, 0) is not None
    assert validate_packing_qty(3000, o) is None


def test_helper_cannot_drop_below_completed_print():
    o = _helper_po(prepared=2000, completed=[1000])
    assert validate_helper_prepared_qty(1000, o) is None
    assert validate_helper_prepared_qty(999, o) is not None


def test_print_completed_rejected_over_helper():
    o = _helper_po(prepared=2000)
    assert validate_print_completed_total(2000, o) is None
    assert validate_print_completed_total(2001, o) is not None


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"ALL {len(tests)} TESTS PASS")


if __name__ == "__main__":
    run_all()
