"""Explicit Barang Utama ↔ Add On (process) line relations for Stock Saat Ini.

Schema (backward-compatible, opt-in on line_items):
- line_id: stable identity for the PO line (preferred)
- parent_line_id: Add On process line points at parent line_id
- parent_line_idx: optional integer fallback when line_id absent

Rules:
- Never invent parent links from item names.
- Historical lines without parent_line_id stay fail-closed for siap-kirim gating.
- SERVICE / excluded masters are process work — not physical stock demand.
- With proven parent links, siap qty is the conservative min across parent +
  required process completions (upper bound without per-unit batch IDs).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


def line_identity(line: Optional[dict], idx: int) -> str:
    if not isinstance(line, dict):
        return f"idx:{idx}"
    for key in ("line_id", "id"):
        raw = str(line.get(key) or "").strip()
        if raw:
            return raw
    return f"idx:{idx}"


def line_cancelled(line: Optional[dict]) -> bool:
    if not isinstance(line, dict):
        return True
    return bool(line.get("cancelled") or line.get("is_cancelled"))


def resolve_parent_idx(
    line: dict,
    lines: Sequence[dict],
    *,
    line_ids: Optional[Dict[str, int]] = None,
) -> Optional[int]:
    """Return parent line index when an explicit relation is present and valid."""
    if not isinstance(line, dict):
        return None
    ids = line_ids
    if ids is None:
        ids = {
            line_identity(row, i): i
            for i, row in enumerate(lines)
            if isinstance(row, dict)
        }
    parent_id = str(line.get("parent_line_id") or "").strip()
    if parent_id and parent_id in ids:
        return ids[parent_id]
    raw_idx = line.get("parent_line_idx")
    if raw_idx is None or str(raw_idx).strip() == "":
        return None
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(lines):
        return None
    if not isinstance(lines[idx], dict):
        return None
    return idx


def build_line_id_index(lines: Sequence[dict]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        identity = line_identity(row, i)
        # First wins — duplicate line_id is ambiguous; later resolve fails closed.
        index.setdefault(identity, i)
    return index


def process_children_for_parent(
    parent_idx: int,
    lines: Sequence[dict],
    *,
    process_master_ids: Set[str],
    master_ids: Sequence[str],
    is_print_line,
) -> Tuple[List[int], bool]:
    """Return (linked child idxs, has_unlinked_process_on_order).

    has_unlinked_process_on_order is True when any active process print line
    lacks a valid parent relation (fail-closed for the whole PO).
    """
    ids = build_line_id_index(lines)
    linked: List[int] = []
    unlinked_process = False
    for idx, line in enumerate(lines):
        if not isinstance(line, dict) or line_cancelled(line):
            continue
        mid = master_ids[idx] if idx < len(master_ids) else ""
        if not mid or mid not in process_master_ids:
            continue
        if not is_print_line(line):
            continue
        parent = resolve_parent_idx(line, lines, line_ids=ids)
        if parent is None:
            unlinked_process = True
            continue
        if parent == parent_idx:
            linked.append(idx)
    return linked, unlinked_process


def conservative_full_process_ready(
    *,
    parent_ordered: float,
    parent_completed: float,
    child_ordered_completed: Iterable[Tuple[float, float]],
) -> float:
    """Upper bound of units that finished every required process.

    min(parent, children) — never invents beyond the weakest process progress.
    """
    ready = min(max(parent_ordered, 0.0), max(parent_completed, 0.0))
    for ordered, completed in child_ordered_completed:
        child_ready = min(max(ordered, 0.0), max(completed, 0.0))
        ready = min(ready, child_ready)
    return max(ready, 0.0)


def ensure_line_identity_fields(line: dict, idx: int) -> dict:
    """Return a shallow copy with line_id filled when missing (non-mutating helper)."""
    row = dict(line or {})
    if not str(row.get("line_id") or "").strip():
        row["line_id"] = line_identity(row, idx)
    return row


def validate_parent_relation(
    line: dict,
    lines: Sequence[dict],
    *,
    idx: int,
) -> Optional[str]:
    """Return error message if parent relation is present but invalid; else None."""
    if not isinstance(line, dict):
        return "Line tidak valid."
    has_parent = bool(
        str(line.get("parent_line_id") or "").strip()
        or (line.get("parent_line_idx") is not None and str(line.get("parent_line_idx")).strip() != "")
    )
    if not has_parent:
        return None
    parent = resolve_parent_idx(line, lines)
    if parent is None:
        return "parent_line_id / parent_line_idx tidak merujuk ke line induk yang valid."
    if parent == idx:
        return "Add On tidak boleh merujuk dirinya sendiri sebagai induk."
    return None
