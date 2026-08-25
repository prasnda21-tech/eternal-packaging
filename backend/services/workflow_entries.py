"""Append-only confirmed workflow entries.

Confirmed entries are immutable. Additional work appends a new row.
Totals = sum of valid entries. Idempotent via action_id.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple


def new_entry_id() -> str:
    return str(uuid.uuid4())


def valid_entries(entries: Optional[List[dict]]) -> List[dict]:
    out = []
    for e in entries or []:
        if not e or e.get("cancelled") or e.get("replaced"):
            continue
        out.append(e)
    return out


def sum_entry_qty(entries: Optional[List[dict]]) -> int:
    total = 0
    for e in valid_entries(entries):
        try:
            total += int(e.get("qty") or 0)
        except (TypeError, ValueError):
            continue
    return total


def find_by_action_id(entries: Optional[List[dict]], action_id: Optional[str]) -> Optional[dict]:
    if not action_id:
        return None
    for e in valid_entries(entries):
        if e.get("action_id") and e.get("action_id") == action_id:
            return e
    return None


def make_entry(
    *,
    stage: str,
    user: dict,
    qty: Optional[int] = None,
    action_id: Optional[str] = None,
    now: str,
    po_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    split_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    entry = {
        "id": new_entry_id(),
        "stage": stage,
        "qty": None if qty is None else int(qty),
        "created_at": now,
        "confirmed_at": now,
        "created_by": user.get("email") or user.get("id"),
        "created_by_name": user.get("name") or user.get("email") or "",
        "action_id": action_id or new_entry_id(),
        "locked": True,
    }
    if po_id:
        entry["po_id"] = po_id
    if line_idx is not None:
        entry["line_idx"] = int(line_idx)
    if split_id:
        entry["split_id"] = split_id
    if shipment_id:
        entry["shipment_id"] = shipment_id
    if extra:
        entry.update(extra)
    return entry


def seed_legacy(
    entries: Optional[List[dict]],
    *,
    snapshot_qty: Any,
    stage: str,
    now: str,
    at: Optional[str] = None,
    by: Optional[str] = None,
    by_name: Optional[str] = None,
    po_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    split_id: Optional[str] = None,
) -> List[dict]:
    current = list(entries or [])
    if valid_entries(current):
        return current
    try:
        qty = int(snapshot_qty or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return current
    current.append({
        "id": "legacy",
        "stage": stage,
        "qty": qty,
        "created_at": at or now,
        "confirmed_at": at or now,
        "created_by": by or "",
        "created_by_name": by_name or by or "",
        "action_id": f"legacy-{stage}-{split_id or line_idx or 'po'}",
        "locked": True,
        "legacy": True,
        "po_id": po_id,
        "line_idx": line_idx,
        "split_id": split_id,
    })
    return current


def append_qty_entry(
    entries: Optional[List[dict]],
    *,
    qty: int,
    stage: str,
    user: dict,
    now: str,
    action_id: Optional[str] = None,
    po_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    split_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[List[dict], dict, bool]:
    """Append one confirmed qty entry. Duplicate action_id returns existing (no mutation)."""
    current = list(entries or [])
    existing = find_by_action_id(current, action_id)
    if existing:
        return current, existing, False
    entry = make_entry(
        stage=stage,
        user=user,
        qty=int(qty),
        action_id=action_id,
        now=now,
        po_id=po_id,
        line_idx=line_idx,
        split_id=split_id,
        shipment_id=shipment_id,
        extra=extra,
    )
    current.append(entry)
    return current, entry, True
