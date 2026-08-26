"""Operator assignment apply / partial cancel. Never mutates Helper Prepared."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from quantity_rules import (
    print_qty_total,
    validate_operator_assignment_total,
)


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def pending_splits_for_user(order: dict, user_email: str) -> List[dict]:
    """Active remaining assignments for one authenticated operator. Never team-wide."""
    email = (user_email or "").strip()
    if not email:
        return []
    out = []
    for s in order.get("operator_splits") or []:
        if split_remaining(s) <= 0:
            continue
        if (s.get("operator_email") or "").strip() != email:
            continue
        out.append(s)
    return out


def split_remaining(split: dict) -> int:
    """Remaining cancellable qty = active scheduled - completed."""
    qty = max(0, _safe_int(split.get("quantity")))
    done = max(0, _safe_int(split.get("qty_completed")))
    return max(0, qty - done)


def helper_prep_snapshot(order: dict) -> Any:
    return order.get("helper_prep")


def assert_helper_unchanged(before: Any, order: dict) -> None:
    if order.get("helper_prep") != before:
        raise ValueError("Penjadwalan tidak boleh mengubah Qty Persiapan Helper.")


def _line_identity_ok(order: dict, line_idx: Optional[int]) -> Optional[str]:
    lis = order.get("line_items") or []
    if line_idx is None:
        return "Setiap assignment wajib terikat line Cetak (line_idx)."
    if line_idx < 0 or line_idx >= len(lis):
        return f"line_idx {line_idx} tidak valid"
    return None


def _cancel_event(split: dict, qty: int, user: dict, now: str, reason: Optional[str]) -> dict:
    return {
        "assignment_id": split.get("id"),
        "operator_email": split.get("operator_email"),
        "operator_name": split.get("operator_name"),
        "cancel_qty": int(qty),
        "at": now,
        "by": (user or {}).get("email"),
        "by_name": (user or {}).get("name"),
        "reason": (reason or "").strip() or None,
    }


def cancel_split_remaining(
    order: dict,
    split_id: str,
    cancel_qty: int,
    user: dict,
    now: str,
    reason: Optional[str] = None,
) -> dict:
    """Cancel remaining scheduled qty on an existing assignment. History kept."""
    helper_before = helper_prep_snapshot(order)
    splits = list(order.get("operator_splits") or [])
    found = next((s for s in splits if s.get("id") == split_id), None)
    if not found:
        raise ValueError("Operator split tidak ditemukan")
    qty = int(cancel_qty)
    remaining = split_remaining(found)
    if qty <= 0:
        raise ValueError("Qty cancel harus lebih dari 0.")
    if qty > remaining:
        raise ValueError(
            f"Qty cancel ({qty}) melebihi sisa terjadwal ({remaining}). "
            "Qty yang sudah completed tidak dapat di-cancel."
        )
    found["quantity"] = max(0, _safe_int(found.get("quantity")) - qty)
    found["qty_cancelled"] = max(0, _safe_int(found.get("qty_cancelled"))) + qty
    found["original_quantity"] = _safe_int(found.get("original_quantity")) or (
        _safe_int(found.get("quantity")) + _safe_int(found.get("qty_cancelled"))
    )
    events = list(found.get("cancel_events") or [])
    events.append(_cancel_event(found, qty, user, now, reason))
    found["cancel_events"] = events
    found["updated_at"] = now
    order["operator_splits"] = splits
    assert_helper_unchanged(helper_before, order)
    return found


def _match_existing(existing: List[dict], payload_row: dict) -> Optional[dict]:
    pid = (payload_row.get("id") or "").strip()
    if pid:
        hit = next((x for x in existing if x.get("id") == pid), None)
        if hit:
            return hit
    email = (payload_row.get("operator_email") or "").strip()
    line_idx = payload_row.get("line_idx")
    return next(
        (
            x for x in existing
            if (x.get("operator_email") or "").strip() == email
            and x.get("line_idx") == line_idx
        ),
        None,
    )


def apply_operator_assignments(
    order: dict,
    payload_splits: List[dict],
    user: dict,
    now: str,
    *,
    new_id_fn,
) -> Tuple[List[dict], List[dict]]:
    """Replace active assignment list without deleting completed history.

    payload_splits: dicts with operator_email, operator_name, quantity, line_idx,
    optional id / work_date.
    Returns (new_splits, cancel_events_this_save).
    """
    helper_before = helper_prep_snapshot(order)
    line_items = order.get("line_items") or []
    print_indices = {
        i for i, li in enumerate(line_items)
        if (li.get("production_process") or "print").strip().lower() != "non_print"
    }
    print_total = print_qty_total(order)
    existing = list(order.get("operator_splits") or [])
    used_ids = set()
    new_splits: List[dict] = []
    cancel_notes: List[dict] = []

    for raw in payload_splits:
        qty = _safe_int(raw.get("quantity"))
        if qty < 0:
            raise ValueError("Qty assignment tidak boleh negatif.")
        line_idx = raw.get("line_idx")
        err = _line_identity_ok(order, line_idx if line_idx is None else int(line_idx))
        if err:
            raise ValueError(err)
        line_idx = int(line_idx)
        if line_idx not in print_indices:
            raise ValueError(
                f"Line item '{line_items[line_idx].get('name')}' bukan proses Cetak — tidak boleh di-assign Operator."
            )
        prev = _match_existing(existing, {**raw, "line_idx": line_idx})
        if prev and prev.get("id") in used_ids:
            prev = None
        completed = max(0, _safe_int(prev.get("qty_completed") if prev else 0))
        if qty < completed:
            raise ValueError(
                f"Qty assignment ({qty}) tidak boleh lebih kecil dari qty completed ({completed}). "
                "Cancel hanya sisa yang belum dikerjakan."
            )
        old_qty = max(0, _safe_int(prev.get("quantity") if prev else 0))
        cancelled_prev = max(0, _safe_int(prev.get("qty_cancelled") if prev else 0))
        extra_cancel = max(0, old_qty - qty) if prev else 0
        events = list((prev.get("cancel_events") if prev else None) or [])
        if extra_cancel:
            ev = _cancel_event(prev or raw, extra_cancel, user, now, None)
            events.append(ev)
            cancel_notes.append(ev)
        sid = (prev.get("id") if prev else None) or new_id_fn()
        used_ids.add(sid)
        work_date = raw.get("work_date")
        if work_date is None and prev:
            work_date = prev.get("work_date")
        original = _safe_int(prev.get("original_quantity") if prev else 0) or max(old_qty, qty)
        row = {
            "id": sid,
            "operator_email": raw.get("operator_email"),
            "operator_name": raw.get("operator_name"),
            "quantity": qty,
            "qty_completed": completed,
            "qty_cancelled": cancelled_prev + extra_cancel,
            "original_quantity": original if original else (qty + cancelled_prev + extra_cancel),
            "progress_entries": list((prev.get("progress_entries") if prev else None) or []),
            "cancel_events": events,
            "stage": (prev.get("stage") if prev else 8) or 8,
            "updated_at": now,
            "line_idx": line_idx,
            "work_date": work_date,
        }
        new_splits.append(row)

    for prev in existing:
        if prev.get("id") in used_ids:
            continue
        completed = max(0, _safe_int(prev.get("qty_completed")))
        remaining = split_remaining(prev)
        if completed <= 0:
            continue
        if remaining > 0:
            ev = _cancel_event(prev, remaining, user, now, "released on reassign")
            events = list(prev.get("cancel_events") or [])
            events.append(ev)
            cancel_notes.append(ev)
            prev = {**prev, "quantity": completed, "qty_cancelled": _safe_int(prev.get("qty_cancelled")) + remaining, "cancel_events": events, "updated_at": now}
            if not prev.get("original_quantity"):
                prev["original_quantity"] = completed + _safe_int(prev.get("qty_cancelled"))
        new_splits.append(prev)
        used_ids.add(prev.get("id"))

    total_split = sum(max(0, _safe_int(s.get("quantity"))) for s in new_splits)
    err = validate_operator_assignment_total(total_split, print_total)
    if err:
        raise ValueError(err)

    per_line: Dict[int, int] = {}
    for s in new_splits:
        idx = s.get("line_idx")
        if idx is None:
            continue
        per_line[int(idx)] = per_line.get(int(idx), 0) + max(0, _safe_int(s.get("quantity")))
    for idx, qty in per_line.items():
        line_qty = _safe_int(line_items[idx].get("quantity"))
        if qty > line_qty:
            name = line_items[idx].get("name")
            raise ValueError(f"Total pembagian untuk '{name}' ({qty}) melebihi qty line ({line_qty}).")

    order["operator_splits"] = new_splits
    assert_helper_unchanged(helper_before, order)
    return new_splits, cancel_notes
