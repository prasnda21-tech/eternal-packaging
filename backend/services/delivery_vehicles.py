"""Master kendaraan for route optimization. Plate is a display field; vehicle_id is stable."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional


def _safe_int(v) -> Optional[int]:
    if v is None or str(v).strip() == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def normalize_vehicle(raw: dict, *, existing: Optional[dict] = None) -> dict:
    prev = existing or {}
    name = str(raw.get("name") or prev.get("name") or "").strip()
    plate = str(raw.get("plate") or raw.get("vehicle_plate") or prev.get("plate") or "").strip()
    vid = str(raw.get("id") or prev.get("id") or "").strip() or str(uuid.uuid4())
    cap = raw.get("capacity_dus")
    if cap is None:
        cap = prev.get("capacity_dus")
    active = raw.get("active")
    if active is None:
        active = prev.get("active", True)
    return {
        "id": vid,
        "name": name or plate or "Kendaraan",
        "plate": plate,
        "capacity_dus": _safe_int(cap),
        "active": bool(active),
    }


def public_vehicle(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name") or "",
        "plate": doc.get("plate") or "",
        "capacity_dus": doc.get("capacity_dus"),
        "active": bool(doc.get("active", True)),
    }
