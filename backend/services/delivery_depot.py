"""Configurable warehouse / depot. Do not invent a real Eternal address."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.delivery_stops import parse_location

SETTINGS_KEY = "delivery_depot"


def empty_depot() -> dict:
    return {
        "name": "",
        "address": "",
        "place_id": None,
        "lat": None,
        "lng": None,
        "configured": False,
    }


def normalize_depot(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    loc = parse_location({
        "lat": src.get("lat"),
        "lng": src.get("lng"),
        "place_id": src.get("place_id"),
        "formatted_address": src.get("address") or src.get("formatted_address"),
    })
    name = str(src.get("name") or "").strip()
    address = loc.get("formatted_address") or str(src.get("address") or "").strip() or ""
    configured = bool(name and loc.get("lat") is not None and loc.get("lng") is not None)
    return {
        "name": name,
        "address": address,
        "place_id": loc.get("place_id"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "configured": configured,
    }


def depot_from_settings_doc(doc: Optional[dict]) -> dict:
    if not doc:
        return empty_depot()
    payload = {k: v for k, v in doc.items() if k not in ("key", "_id")}
    return normalize_depot(payload)
