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

from quantity_rules import requires_printing

SCREENER_GATE_SAMPLE_MSG = "Qty Screener harus diisi sebelum Cetak Sample."
SCREENER_GATE_OPERATOR_MSG = "Qty Screener harus diisi sebelum pekerjaan Operator."
SCREENER_GATE_SCREEN_MSG = "Qty Screen belum diinput."


def _has_value(v) -> bool:
    return v is not None and str(v).strip() != ""


def design_ok(order: dict) -> bool:
    return bool(order.get("design_bypassed")) or (order.get("design_status") == "Approved")


def payment_ok(order: dict) -> bool:
    return bool(order.get("payment_bypassed")) or (order.get("payment_status") == "Approved")


def group2_complete(order: dict) -> bool:
    """Design + Payment branches both satisfied (approve or official bypass)."""
    return design_ok(order) and payment_ok(order)


def penjadwalan_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    return bool(order.get("operator_splits"))


def screener_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    si = order.get("screener_info") or {}
    return _has_value(si.get("screen_qty"))


def helper_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    hp = order.get("helper_prep") or {}
    return _has_value(hp.get("qty_prepared"))


def sample_uploaded(order: dict) -> bool:
    return bool(order.get("sample_files"))


def sample_ok(order: dict) -> bool:
    return bool(order.get("sample_approved"))


def print_qty_input(order: dict) -> bool:
    for s in (order.get("operator_splits") or []):
        v = s.get("qty_completed")
        if _has_value(v) and int(v) > 0:
            return True
    return False


def packing_qty_input(order: dict) -> bool:
    pi = order.get("packing_info") or {}
    v = pi.get("qty_packed")
    if not _has_value(v):
        return False
    try:
        return int(v) > 0
    except (ValueError, TypeError):
        return False


def operator_unlocked(order: dict) -> bool:
    """Workflow gate: Operator / Cetak Sample need Penjadwalan + Screener.

    Helper prepared qty is a quantity cap, not this unlock.
    """
    if not requires_printing(order):
        return penjadwalan_done(order)
    return penjadwalan_done(order) and screener_done(order)


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
    if screener_done(order):
        return None
    return SCREENER_GATE_SAMPLE_MSG if for_sample else SCREENER_GATE_OPERATOR_MSG


def compute_compatible_stage(order: dict) -> int:
    """Furthest unlocked stage (0..11) from evidence. Never infers sibling done.

    Does not advance past Packing (11); SJ/Loading/Terkirim stay action-driven.
    """
    target = 0
    if group2_complete(order):
        target = 5
    if penjadwalan_done(order) and group2_complete(order):
        target = max(target, 6)
    if operator_unlocked(order) and group2_complete(order):
        target = max(target, 8)
    if sample_uploaded(order) and operator_unlocked(order):
        target = max(target, 9)
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
    scheduled = penjadwalan_done(order)
    screen = screener_done(order)
    req_print = requires_printing(order)
    op = operator_unlocked(order)
    uploaded = sample_uploaded(order)
    return {
        "design": True,
        "approval_design": bool(order.get("design_files")) or bool(order.get("design_bypassed")),
        "payment": True,
        "approval_payment": bool(order.get("payment_files")) or bool(order.get("payment_bypassed")),
        "penjadwalan": g2,
        "screener": g2 and scheduled,
        "helper": g2 and scheduled,
        "operator": op,
        "sample_upload": op if req_print else False,
        "sample_approval": uploaded and op if req_print else False,
        "packing": packing_unlocked(order),
    }
