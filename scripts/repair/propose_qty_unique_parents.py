#!/usr/bin/env python3
"""Propose SERVICE→print-parent links when ordered qty uniquely matches one print parent.

Deterministic rule (not name invent):
  Among line_items that are physical print parents (is_print / production_process),
  SERVICE.quantity matches exactly one parent's quantity → propose that parent_line_idx.

Ambiguous (0 or ≥2 matches) → None. C_NO_PRINT stays unresolved.
Does not mutate documents. Used by B32 allowlist + offline tests.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _qty(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_service(line: dict) -> bool:
    return str(line.get("item_type") or "").strip().upper() == "SERVICE"


def _is_print(line: dict) -> bool:
    if line.get("is_print") is True:
        return True
    proc = str(line.get("production_process") or "").strip().lower()
    return proc in {"print", "cetak", "printing"}


def propose_unique_qty_parent(
    lines: List[dict],
    service_line_idx: int,
) -> Optional[Dict[str, Any]]:
    """Return proposal dict or None when ambiguous / invalid."""
    if service_line_idx < 0 or service_line_idx >= len(lines):
        return None
    svc = lines[service_line_idx] if isinstance(lines[service_line_idx], dict) else {}
    if not _is_service(svc):
        return None
    svc_qty = _qty(svc.get("quantity"))
    prints: List[Tuple[int, dict]] = []
    for idx, raw in enumerate(lines):
        if not isinstance(raw, dict):
            continue
        if _is_service(raw):
            continue
        if not _is_print(raw):
            continue
        prints.append((idx, raw))
    matches = [(i, p) for i, p in prints if abs(_qty(p.get("quantity")) - svc_qty) < 1e-9]
    if len(matches) != 1:
        return None
    pidx, parent = matches[0]
    if pidx == service_line_idx:
        return None
    return {
        "service_line_idx": service_line_idx,
        "parent_line_idx": pidx,
        "service_qty": svc_qty,
        "parent_qty": _qty(parent.get("quantity")),
        "parent_master_id": str(parent.get("master_item_id") or "").strip(),
        "print_parent_count": len(prints),
        "rule": "QTY_MATCH_UNIQUE_AMONG_PRINT_PARENTS",
    }


def propose_all_unlinked_services(lines: List[dict]) -> List[Dict[str, Any]]:
    """Propose for every SERVICE line that currently has empty parent fields."""
    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(lines):
        if not isinstance(raw, dict) or not _is_service(raw):
            continue
        parent_id = str(raw.get("parent_line_id") or "").strip()
        parent_idx = raw.get("parent_line_idx")
        if parent_id or parent_idx is not None:
            continue
        prop = propose_unique_qty_parent(lines, idx)
        if prop:
            out.append(prop)
    return out
