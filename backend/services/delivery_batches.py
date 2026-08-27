"""Delivery schedule batches: create, edit priority/closing, publish, driver ready.

Does not replace shipment/delivery workflow. Stops snapshot existing SIAP DIKIRIM qty.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.delivery_stops import (
    LOCATION_LABEL,
    LOCATION_NEED,
    PRIORITY_LABELS,
    normalize_closing_time,
    normalize_priority,
    parse_location,
    snapshot_stop,
)

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_CANCELLED = "dibatalkan"

ACTIVE_BATCH_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)


def actor_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("id") or user.get("sub") or user.get("email") or "").strip()


def actor_name(user: dict) -> str:
    return str(user.get("name") or user.get("email") or "").strip()


def now_iso(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def new_batch_id() -> str:
    return str(uuid.uuid4())


def public_batch(doc: dict) -> dict:
    stops = list(doc.get("stops") or [])
    ready = list(doc.get("driver_readiness") or [])
    return {
        "id": doc.get("id"),
        "delivery_date": doc.get("delivery_date"),
        "status": doc.get("status"),
        "route_plan_id": doc.get("route_plan_id"),
        "stops": stops,
        "stop_count": len(stops),
        "driver_readiness": [
            {
                "driver_user_id": r.get("driver_user_id"),
                "driver_name": r.get("driver_name"),
                "vehicle_id": r.get("vehicle_id"),
                "vehicle_name": r.get("vehicle_name"),
                "vehicle_plate": r.get("vehicle_plate"),
                "capacity_dus": r.get("capacity_dus"),
                "ready": bool(r.get("ready")),
                "ready_at": r.get("ready_at"),
            }
            for r in ready
        ],
        "created_at": doc.get("created_at"),
        "created_by_id": doc.get("created_by_id"),
        "created_by_name": doc.get("created_by_name"),
        "published_at": doc.get("published_at"),
        "published_by_id": doc.get("published_by_id"),
        "published_by_name": doc.get("published_by_name"),
        "notes": doc.get("notes") or "",
    }


def build_batch(
    *,
    delivery_date: str,
    queue_rows: List[dict],
    stop_edits: Optional[Dict[str, dict]] = None,
    user: dict,
    notes: str = "",
    now: Optional[datetime] = None,
) -> dict:
    edits = stop_edits or {}
    stops = []
    for row in queue_rows:
        extra = edits.get(row["stop_id"]) or {}
        stops.append(snapshot_stop(
            row,
            priority=extra.get("priority"),
            outlet_closing_time=extra.get("outlet_closing_time"),
            location=extra.get("location"),
        ))
    ts = now_iso(now)
    return {
        "id": new_batch_id(),
        "delivery_date": delivery_date,
        "status": STATUS_DRAFT,
        "stops": stops,
        "driver_readiness": [],
        "route_plan_id": None,
        "created_at": ts,
        "created_by_id": actor_id(user),
        "created_by_name": actor_name(user),
        "published_at": None,
        "published_by_id": None,
        "published_by_name": None,
        "notes": (notes or "").strip(),
    }


def apply_stop_updates(batch: dict, updates: List[dict]) -> dict:
    by_id = {s["stop_id"]: s for s in batch.get("stops") or []}
    for u in updates or []:
        sid = str((u or {}).get("stop_id") or "").strip()
        if not sid or sid not in by_id:
            continue
        st = dict(by_id[sid])
        if "priority" in u and u.get("priority") is not None:
            pr = normalize_priority(u.get("priority"))
            st["priority"] = pr
            st["priority_label"] = PRIORITY_LABELS[pr]
        if "outlet_closing_time" in u:
            st["outlet_closing_time"] = normalize_closing_time(u.get("outlet_closing_time"))
        if u.get("location"):
            loc = parse_location(u.get("location"))
            st["lat"] = loc.get("lat")
            st["lng"] = loc.get("lng")
            st["place_id"] = loc.get("place_id")
            st["google_place_id"] = loc.get("place_id")
            if loc.get("formatted_address"):
                st["address"] = loc.get("formatted_address")
            if u.get("location", {}).get("outlet_id"):
                st["outlet_id"] = u["location"]["outlet_id"]
            if u.get("location", {}).get("outlet_name"):
                st["outlet_name"] = u["location"]["outlet_name"]
            st["location_status"] = loc.get("location_status")
            st["location_label"] = LOCATION_LABEL.get(loc.get("location_status"), st.get("location_label"))
        by_id[sid] = st
    batch["stops"] = [by_id[s["stop_id"]] for s in batch.get("stops") or [] if s.get("stop_id") in by_id]
    return batch


def validate_for_publish(batch: dict) -> Optional[str]:
    stops = list(batch.get("stops") or [])
    if not stops:
        return "Pilih minimal satu stop SIAP DIKIRIM."
    if not batch.get("delivery_date"):
        return "Tanggal pengiriman wajib diisi."
    return None


def mark_published(batch: dict, user: dict, now: Optional[datetime] = None) -> dict:
    err = validate_for_publish(batch)
    if err:
        raise ValueError(err)
    batch["status"] = STATUS_PUBLISHED
    batch["published_at"] = now_iso(now)
    batch["published_by_id"] = actor_id(user)
    batch["published_by_name"] = actor_name(user)
    return batch


def upsert_driver_ready(
    batch: dict,
    *,
    driver_user_id: str,
    driver_name: str,
    vehicle: dict,
    now: Optional[datetime] = None,
) -> dict:
    """Bind authenticated driver to a vehicle. driver_user_id MUST be the actor."""
    uid = str(driver_user_id or "").strip()
    if not uid:
        raise ValueError("Driver tidak valid.")
    vid = str((vehicle or {}).get("id") or "").strip()
    if not vid:
        raise ValueError("Kendaraan wajib dipilih.")
    plate = str(vehicle.get("plate") or "").strip()
    row = {
        "driver_user_id": uid,
        "driver_name": str(driver_name or "").strip(),
        "vehicle_id": vid,
        "vehicle_name": str(vehicle.get("name") or "").strip(),
        "vehicle_plate": plate,
        "capacity_dus": vehicle.get("capacity_dus"),
        "ready": True,
        "ready_at": now_iso(now),
    }
    ready = [r for r in (batch.get("driver_readiness") or []) if str(r.get("driver_user_id") or "") != uid]
    ready.append(row)
    batch["driver_readiness"] = ready
    return batch


def ready_vehicles(batch: dict) -> List[dict]:
    return [r for r in (batch.get("driver_readiness") or []) if r.get("ready") and r.get("vehicle_id")]


def missing_location_stops(batch: dict) -> List[dict]:
    return [s for s in (batch.get("stops") or []) if (s.get("location_status") == LOCATION_NEED)]
