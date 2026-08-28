"""Selective workflow rollback.

Existing PO-level rollback (`to_stage` without ids) remains available.
When `ids` are supplied, those stable identifiers are the mutation scope
and `to_stage` is the rollback depth. Unselected splits and lines stay
intact. current_stage is not moved on the ids path.

Selective ids are operator_splits.id or line_items.id / line_id /
accurate_line_id / unique item:{accurate_item_id}. Stage slugs are not
selective targets.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set, Tuple

EMPTY_SELECTION = "Pilih minimal satu nomor."
INVALID_ID = "ID tidak valid atau bukan milik PO ini."
INVALID_STAGE = "Stage tujuan tidak valid."
STAGE_NOT_BEFORE_CURRENT = "Stage tujuan harus lebih kecil dari stage saat ini."

# Depth gates — indices match server.py STAGES / frontend constants.js.
STAGE_HELPER = 7
STAGE_CETAK = 10
STAGE_PACKING = 11


class RollbackError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)


def _clean_ids(ids: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in ids or []:
        sid = str(raw or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def line_stable_id(order: dict, idx: int) -> Optional[str]:
    lis = order.get("line_items") or []
    if idx < 0 or idx >= len(lis):
        return None
    li = lis[idx] or {}
    for key in ("id", "line_id", "accurate_line_id"):
        val = str(li.get(key) or "").strip()
        if val:
            return val
    aid = str(li.get("accurate_item_id") or "").strip()
    if not aid:
        return None
    same = [
        i for i, row in enumerate(lis)
        if str((row or {}).get("accurate_item_id") or "").strip() == aid
    ]
    if len(same) == 1:
        return f"item:{aid}"
    return None


def _line_idx_for_stable_id(order: dict, sid: str) -> Optional[int]:
    lis = order.get("line_items") or []
    for idx, _li in enumerate(lis):
        if line_stable_id(order, idx) == sid:
            return idx
    return None


def _split_by_id(order: dict, sid: str) -> Optional[dict]:
    for split in order.get("operator_splits") or []:
        if split and str(split.get("id") or "") == sid:
            return split
    return None


def resolve_target(order: dict, sid: str) -> Optional[Tuple[str, Any]]:
    """Map a stable id to ('split', split) | ('line', idx). Never stage slugs."""
    split = _split_by_id(order, sid)
    if split is not None:
        return ("split", split)
    line_idx = _line_idx_for_stable_id(order, sid)
    if line_idx is not None:
        return ("line", line_idx)
    return None


def reset_split_fill(split: dict, user: dict, now: str) -> None:
    """Empty qty fill on one assignment. Keep operator, allocated qty, work_date, id."""
    entries = list(split.get("progress_entries") or [])
    actor = (user or {}).get("email")
    for entry in entries:
        if not entry or entry.get("cancelled"):
            continue
        entry["cancelled"] = True
        entry["cancelled_at"] = now
        entry["cancelled_by"] = actor
    split["progress_entries"] = entries
    split["qty_completed"] = 0


def _reset_line_splits(order: dict, line_idx: int, user: dict, now: str) -> None:
    for split in order.get("operator_splits") or []:
        if split is None:
            continue
        if split.get("line_idx") is None:
            continue
        try:
            if int(split.get("line_idx")) != int(line_idx):
                continue
        except (TypeError, ValueError):
            continue
        reset_split_fill(split, user, now)


def _line_idxs_for_resolved(resolved: List[Tuple[str, Any]]) -> Set[int]:
    idxs: Set[int] = set()
    for kind, obj in resolved:
        if kind == "split":
            try:
                if obj is not None and obj.get("line_idx") is not None:
                    idxs.add(int(obj.get("line_idx")))
            except (TypeError, ValueError):
                continue
        elif kind == "line":
            try:
                idxs.add(int(obj))
            except (TypeError, ValueError):
                continue
    return idxs


def _reset_helper_lines(order: dict, line_idxs: Set[int]) -> None:
    """Zero helper qty on selected lines only. Skip PO-wide qty_prepared when no items."""
    if not line_idxs:
        return
    hp = order.get("helper_prep")
    if not isinstance(hp, dict):
        return
    items = hp.get("items")
    if not isinstance(items, list) or not items:
        return
    changed = False
    for it in items:
        if not it:
            continue
        try:
            if int(it.get("line_idx", -1)) not in line_idxs:
                continue
        except (TypeError, ValueError):
            continue
        it["qty_prepared"] = 0
        changed = True
    if changed:
        hp["qty_prepared"] = sum(int((row or {}).get("qty_prepared") or 0) for row in items)


def _reset_packing_lines(order: dict, line_idxs: Set[int]) -> None:
    """Zero packed_qty on selected lines only. Skip PO-wide qty_packed when no lines."""
    if not line_idxs:
        return
    pi = order.get("packing_info")
    if not isinstance(pi, dict):
        return
    lines = pi.get("lines")
    if not isinstance(lines, list) or not lines:
        return
    changed = False
    for row in lines:
        if not row:
            continue
        try:
            if int(row.get("line_idx", -1)) not in line_idxs:
                continue
        except (TypeError, ValueError):
            continue
        row["packed_qty"] = 0
        changed = True
    if changed:
        pi["qty_packed"] = sum(int((row or {}).get("packed_qty") or 0) for row in lines)


def validate_rollback_to_stage(
    to_stage: Any,
    from_stage: Any,
    stages_len: int,
    selective: bool = False,
) -> Optional[str]:
    if to_stage is None:
        return INVALID_STAGE
    try:
        n = int(to_stage)
    except (TypeError, ValueError):
        return INVALID_STAGE
    if n < 0 or n >= int(stages_len):
        return INVALID_STAGE
    if selective and n > STAGE_PACKING:
        return INVALID_STAGE
    try:
        current = int(from_stage)
    except (TypeError, ValueError):
        current = 0
    if n >= current:
        return STAGE_NOT_BEFORE_CURRENT
    return None


def apply_selective_rollback(
    order: dict,
    ids: Optional[Iterable[Any]],
    user: dict,
    now: str,
    to_stage: int,
) -> List[str]:
    """Reset only selected stable ids to the given stage depth. Raises RollbackError."""
    cleaned = _clean_ids(ids)
    if not cleaned:
        raise RollbackError(EMPTY_SELECTION)
    resolved: List[Tuple[str, Any]] = []
    for sid in cleaned:
        target = resolve_target(order, sid)
        if target is None:
            raise RollbackError(INVALID_ID)
        resolved.append(target)
    line_idxs = _line_idxs_for_resolved(resolved)
    if int(to_stage) <= STAGE_CETAK:
        for kind, obj in resolved:
            if kind == "split":
                reset_split_fill(obj, user, now)
            elif kind == "line":
                _reset_line_splits(order, obj, user, now)
    if int(to_stage) <= STAGE_HELPER:
        _reset_helper_lines(order, line_idxs)
    if int(to_stage) <= STAGE_PACKING:
        _reset_packing_lines(order, line_idxs)
    return cleaned
