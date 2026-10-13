"""Dependency / gating helpers for production workflow.

Evidence-based: a process is done only from its own data, never from
`current_stage` alone. Parallel siblings do not complete each other.

Stage index compatibility (unchanged STAGES list):
  0 Order Masuk
  1 Design / 2 Approval Design / 3 Payment / 4 Approval Payment   (group 2, parallel)
  5 Penjadwalan
  6 Screener / 7 Persiapan Helper                                 (group 4, parallel)
  8 Operator + Cetak Sample
  9 Approval Cetak Sample
  10 Cetak (qty progress; not a linear gate)
  11 Packing
  12+ Surat Jalan / Loading / Terkirim — existing action-driven flow
"""
from __future__ import annotations

from quantity_rules import requires_printing, qty_packed

SCREENER_GATE_SAMPLE_MSG = "Qty Screener harus diisi sebelum Cetak Sample."
SCREENER_GATE_OPERATOR_MSG = "Qty Screener harus diisi sebelum pekerjaan Operator."
SCREENER_GATE_SCREEN_MSG = "Qty Screen belum diinput."
HELPER_GATE_OPERATOR_MSG = "Persiapan Helper harus diisi sebelum pekerjaan Operator."
SAMPLE_GATE_PRINT_QTY_MSG = "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi."


def _has_value(v) -> bool:
    return v is not None and str(v).strip() != ""


def customer_design_approved(order: dict) -> bool:
    """True when the customer approval snapshot is an authoritative approve.

    Customer Approval is an alternate path for the Approval Design gate,
    not a second mandatory internal gate. Reject/expired/revoked do not count.
    """
    snap = order.get("customer_approval") or {}
    if not isinstance(snap, dict):
        return False
    return str(snap.get("status") or "").strip().lower() == "approved"


def design_ok(order: dict) -> bool:
    """Approval Design complete: internal approve, official bypass, or customer approve."""
    return (
        bool(order.get("design_bypassed"))
        or (order.get("design_status") == "Approved")
        or customer_design_approved(order)
    )


def payment_ok(order: dict) -> bool:
    return bool(order.get("payment_bypassed")) or (order.get("payment_status") == "Approved")


def _has_payment_file_records(files) -> bool:
    if not isinstance(files, list) or not files:
        return False
    return any(isinstance(f, dict) and f for f in files)


def payment_stage_done(order: dict) -> bool:
    """Payment stage complete without requiring a duplicate payment_files upload.

    Canonical after settlement-effective Penerimaan: ``payment_status == Approved``
    (written only by create-settle / sync_order_payment_projections from counting receipts).

    Do NOT treat raw ``payment_receipts`` / ``payment_fact`` alone as complete — those
    fields can be stale if a deferred/cancelled receipt left orphan projections.
    Legacy: payment_files upload completes Payment (Approval still needs Approved/bypass).
    """
    order = order or {}
    if bool(order.get("payment_bypassed")):
        return True
    if order.get("payment_status") == "Approved":
        return True
    if _has_payment_file_records(order.get("payment_files")):
        return True
    return False


def payment_evidence_present(order: dict) -> bool:
    """Evidence waiting for Approval Payment (legacy upload path).

    Receipt settlement auto-approves via payment_status; orphan payment_receipts /
    payment_fact must not unlock Approval UI or mark Payment complete.
    """
    order = order or {}
    if bool(order.get("payment_bypassed")):
        return True
    if _has_payment_file_records(order.get("payment_files")):
        return True
    return False


def group2_complete(order: dict) -> bool:
    """Design + Payment branches both satisfied (approve or official bypass)."""
    return design_ok(order) and payment_ok(order)


def penjadwalan_unlocked(order: dict) -> bool:
    return group2_complete(order)


def penjadwalan_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    return penjadwalan_unlocked(order) and bool(order.get("operator_splits"))


def screener_unlocked(order: dict) -> bool:
    return group2_complete(order) and penjadwalan_done(order)


def screener_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    si = order.get("screener_info") or {}
    return _has_value(si.get("screen_qty"))


def helper_unlocked(order: dict) -> bool:
    return group2_complete(order) and penjadwalan_done(order)


def helper_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    hp = order.get("helper_prep") or {}
    return _has_value(hp.get("qty_prepared"))


def sample_uploaded(order: dict) -> bool:
    return bool(order.get("sample_files"))


def operator_unlocked(order: dict) -> bool:
    """Workflow gate: Operator work requires Persiapan Helper (and Penjadwalan)."""
    if not requires_printing(order):
        return penjadwalan_done(order)
    return penjadwalan_done(order) and helper_done(order)


def sample_upload_unlocked(order: dict) -> bool:
    if not requires_printing(order):
        return False
    return operator_unlocked(order)


def sample_approval_unlocked(order: dict) -> bool:
    if not requires_printing(order):
        return False
    return operator_unlocked(order) and sample_uploaded(order)


def sample_ok(order: dict) -> bool:
    if not requires_printing(order):
        return True
    return operator_unlocked(order) and bool(order.get("sample_approved"))


def print_qty_input(order: dict) -> bool:
    for s in (order.get("operator_splits") or []):
        v = s.get("qty_completed")
        if _has_value(v) and int(v) > 0:
            return True
    return False


def packing_qty_input(order: dict) -> bool:
    """True when Packing evidence exists, or Packing is not required (full non-print)."""
    if not requires_printing(order):
        return True
    return qty_packed(order) > 0


def print_qty_unlocked(order: dict) -> bool:
    """Qty Selesai Cetak hanya setelah Helper valid DAN sample APPROVED."""
    if not requires_printing(order):
        return False
    return sample_ok(order)


def print_qty_gate_reason(order: dict) -> str | None:
    if not requires_printing(order):
        return None
    helper_reason = helper_gate_reason(order)
    if helper_reason:
        return helper_reason
    if not sample_ok(order):
        return SAMPLE_GATE_PRINT_QTY_MSG
    return None


def packing_unlocked(order: dict) -> bool:
    """Packing stage becomes available after sample approval (print)
    or after scheduling (full non-print). Quantity caps stay separate.
    """
    if not requires_printing(order):
        return group2_complete(order) and penjadwalan_done(order)
    return sample_ok(order)


def screener_gate_reason(order: dict, *, for_sample: bool = False) -> str | None:
    if not requires_printing(order):
        return None
    if not penjadwalan_done(order):
        return "Penjadwalan harus selesai terlebih dahulu."
    if screener_done(order):
        return None
    return SCREENER_GATE_SAMPLE_MSG if for_sample else SCREENER_GATE_OPERATOR_MSG


def helper_gate_reason(order: dict) -> str | None:
    if not requires_printing(order):
        return None
    if not penjadwalan_done(order):
        return "Penjadwalan harus selesai terlebih dahulu."
    if not helper_done(order):
        return HELPER_GATE_OPERATOR_MSG
    return None


def compute_compatible_stage(order: dict) -> int:
    """Furthest unlocked stage (0..11) from evidence. Never skips prerequisites.

    Does not advance past Packing (11); SJ/Loading/Terkirim stay action-driven.
    """
    target = 0
    if group2_complete(order):
        target = 5
    if penjadwalan_done(order) and group2_complete(order):
        target = max(target, 6)
    if screener_done(order):
        target = max(target, 7)
    if helper_done(order):
        target = max(target, 8)
    if sample_uploaded(order) and operator_unlocked(order):
        target = max(target, 9)
    if sample_ok(order):
        target = max(target, 10)
    if packing_unlocked(order):
        target = max(target, 11)
    return target


def sync_workflow_stage(order: dict, *, force: bool = False) -> bool:
    """Idempotent current_stage bump for compatibility/navigation.

    Increase-only unless force=True (sample reject / re-upload reset).
    Never writes timeline. Never advances past 11. Leaves 12+ untouched.
    Returns True if current_stage changed.
    """
    cs = int(order.get("current_stage") or 0)
    if cs >= 12:
        return False
    target = compute_compatible_stage(order)
    if force:
        if target != cs:
            order["current_stage"] = target
            return True
        return False
    if target > cs:
        order["current_stage"] = target
        return True
    return False


def workflow_ready(order: dict) -> dict:
    """UI/API flags: which cards may accept their real action (Simpan/Upload/Approve)."""
    g2 = group2_complete(order)
    req_print = requires_printing(order)
    scheduled = penjadwalan_done(order)
    screen_ok = screener_done(order)
    help_ok = helper_done(order)
    op_unlocked = operator_unlocked(order)
    uploaded = sample_uploaded(order)
    s_ok = sample_ok(order)
    return {
        "design": True,
        "approval_design": bool(order.get("design_files")) or bool(order.get("design_bypassed")),
        "payment": True,
        "approval_payment": payment_evidence_present(order),
        "penjadwalan": g2,
        "screener": screener_unlocked(order),
        "helper": helper_unlocked(order),
        "operator": op_unlocked,
        "sample_upload": op_unlocked if req_print else False,
        "sample_approval": uploaded and op_unlocked if req_print else False,
        "print_qty": print_qty_unlocked(order),
        "packing": packing_unlocked(order),
    }
