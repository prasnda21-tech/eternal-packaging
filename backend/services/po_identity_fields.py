"""Batch attach canonical PO identity flags onto list rows that already carry po_id.

Read-only. Keys by orders.id only — never ambiguous po_number matching.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


async def order_identity_by_ids(db, po_ids: Iterable[str]) -> Dict[str, dict]:
    ids = sorted({str(pid) for pid in po_ids if pid})
    if not ids:
        return {}
    out: Dict[str, dict] = {}
    cursor = db.orders.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "priority": 1, "buffer_stock": 1},
    )
    async for doc in cursor:
        oid = str(doc.get("id") or "")
        if not oid:
            continue
        out[oid] = {
            "priority": doc.get("priority") or "normal",
            "urgent": str(doc.get("priority") or "").lower() == "urgent",
            "buffer_stock": bool(doc.get("buffer_stock")),
        }
    return out


def apply_identity_fields(row: dict, identity: Optional[dict]) -> dict:
    """Mutate row with priority / urgent / buffer_stock (defaults when missing)."""
    if not isinstance(row, dict):
        return row
    if identity:
        row["priority"] = identity.get("priority") or "normal"
        row["urgent"] = bool(identity.get("urgent"))
        row["buffer_stock"] = bool(identity.get("buffer_stock"))
    else:
        row.setdefault("priority", "normal")
        row.setdefault("urgent", False)
        row.setdefault("buffer_stock", False)
    return row


async def attach_po_identity_fields(db, rows: List[dict], *, po_id_key: str = "po_id") -> List[dict]:
    """Attach identity onto an already-scoped page. One query for all po_ids."""
    if not rows:
        return rows
    ids = [str(r.get(po_id_key) or "") for r in rows if r.get(po_id_key)]
    by_id = await order_identity_by_ids(db, ids)
    for row in rows:
        pid = str(row.get(po_id_key) or "")
        apply_identity_fields(row, by_id.get(pid) if pid else None)
    return rows
