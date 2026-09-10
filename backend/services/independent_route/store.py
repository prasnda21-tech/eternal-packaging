"""Mongo persistence for independent route plans, stops, and versions."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.independent_route.constants import (
    COL_PLANS,
    COL_STOPS,
    COL_VERSIONS,
    END_LAST,
    END_MODES,
    LOC_GOOGLE,
    LOC_MANUAL,
    LOCATION_SOURCES,
    REF_DELIVERY_OUTLET,
    REF_KINDS,
    REF_ROUTE_ONLY,
    REF_SALES_OUTLET,
    SEQ_MANUAL,
    SEQ_OPTIMIZED,
    SOURCE_DELIVERY_SCHEDULE,
    SOURCE_MANUAL,
    SOURCE_TYPES,
    START_CURRENT,
    START_MODES,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_OPTIMIZED,
    STATUSES,
    STRATEGIES,
)
from services.independent_route.geo import is_valid_coordinate
from services.independent_route.optimizer import (
    OptimizerError,
    optimize_route,
    validate_priority_order,
)
from services.independent_route.places import google_maps_url
from services.independent_route.settings import (
    enabled_priorities,
    load_settings,
    now_iso,
    snapshot_priorities,
    warehouse_point,
)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def actor_from_user(user: dict) -> dict:
    return {
        "id": str(user.get("user_id") or user.get("id") or user.get("sub") or "").strip(),
        "name": str(user.get("name") or user.get("email") or "").strip(),
        "email": str(user.get("email") or "").strip(),
        "role": str(user.get("role") or "").strip(),
    }


def can_manage(perms: set) -> bool:
    return "route_optimizer.manage" in perms


def can_view_route(route: dict, actor: dict, perms: set) -> bool:
    if can_manage(perms) or "route_optimizer.view" in perms:
        if can_manage(perms):
            return True
        return str((route.get("created_by") or {}).get("id") or "") == actor["id"] or "route_optimizer.view" in perms
    return False


def owns_route(route: dict, actor: dict) -> bool:
    return str((route.get("created_by") or {}).get("id") or "") == actor["id"]


def can_edit_route(route: dict, actor: dict, perms: set) -> bool:
    if can_manage(perms):
        return True
    return "route_optimizer.edit_own" in perms and owns_route(route, actor)


def can_optimize_route(route: dict, actor: dict, perms: set) -> bool:
    if "route_optimizer.optimize" not in perms:
        return False
    return can_manage(perms) or owns_route(route, actor)


async def ensure_indexes(db) -> None:
    await db[COL_PLANS].create_index("id", unique=True)
    await db[COL_PLANS].create_index("created_by.id")
    await db[COL_PLANS].create_index("created_at")
    await db[COL_PLANS].create_index("status")
    await db[COL_PLANS].create_index("source_type")
    await db[COL_PLANS].create_index("deleted_at")
    await db[COL_STOPS].create_index("id", unique=True)
    await db[COL_STOPS].create_index("route_id")
    await db[COL_STOPS].create_index([("route_id", 1), ("id", 1)], unique=True)
    await db[COL_STOPS].create_index("sales_order_id")
    await db[COL_STOPS].create_index([("source_type", 1), ("sales_order_id", 1)])
    await db[COL_VERSIONS].create_index("id", unique=True)
    await db[COL_VERSIONS].create_index("route_id")
    await db[COL_VERSIONS].create_index([("route_id", 1), ("optimization_version", 1)], unique=True)


def _active_plan_query(extra: dict | None = None) -> dict:
    q = {"$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}
    if extra:
        q = {"$and": [q, extra]}
    return q


def _normalize_source_type(raw) -> str:
    val = str(raw or SOURCE_MANUAL).strip().lower()
    return val if val in SOURCE_TYPES else SOURCE_MANUAL


def _location_snapshot(payload: dict | None, *, required: bool = False, label: str = "Lokasi") -> Optional[dict]:
    if not payload:
        if required:
            raise HTTPException(400, f"{label} belum diisi.")
        return None
    lat = payload.get("lat", payload.get("latitude"))
    lng = payload.get("lng", payload.get("longitude"))
    if lat is None and lng is None:
        if required:
            raise HTTPException(400, f"{label} belum diisi.")
        return None
    if not is_valid_coordinate(lat, lng):
        raise HTTPException(400, f"{label} tidak valid. Koordinat 0,0 atau di luar rentang ditolak.")
    source = str(payload.get("location_source") or payload.get("source") or LOC_MANUAL).upper()
    if source not in LOCATION_SOURCES:
        source = LOC_MANUAL
    snap = {
        "lat": float(lat),
        "lng": float(lng),
        "location_source": source,
    }
    accuracy = payload.get("accuracy", payload.get("accuracy_m"))
    if accuracy is not None:
        try:
            snap["accuracy_m"] = float(accuracy)
        except (TypeError, ValueError):
            pass
    if payload.get("captured_at"):
        snap["captured_at"] = str(payload.get("captured_at"))
    place_id = str(payload.get("google_place_id") or payload.get("place_id") or "").strip()
    place_name = str(payload.get("place_name") or payload.get("display_name") or "").strip()
    formatted = str(payload.get("formatted_address") or payload.get("address") or "").strip()
    if source == LOC_GOOGLE:
        if place_id:
            snap["google_place_id"] = place_id
        if place_name:
            snap["place_name"] = place_name
        if formatted:
            snap["formatted_address"] = formatted
        snap["google_maps_url"] = str(payload.get("google_maps_url") or "").strip() or google_maps_url(
            place_id=place_id or None,
            lat=snap["lat"],
            lng=snap["lng"],
            name=place_name,
        )
        snap["selected_at"] = now_iso()
    return snap


def _actor_stamp(actor: dict | None) -> dict | None:
    if not actor:
        return None
    return {
        "id": actor.get("id") or actor.get("user_id"),
        "name": actor.get("name") or "",
        "email": actor.get("email") or "",
    }


def _stamp_location_actor(loc: dict | None, actor: dict | None) -> dict | None:
    if loc and loc.get("location_source") == LOC_GOOGLE:
        loc = dict(loc)
        loc["selected_by"] = _actor_stamp(actor)
    return loc


def _apply_location_to_stop(target: dict, loc: dict | None, actor: dict | None) -> None:
    if not loc:
        return
    target["lat"] = loc["lat"]
    target["lng"] = loc["lng"]
    target["location_source"] = loc["location_source"]
    if loc.get("location_source") == LOC_GOOGLE:
        target["google_place_id"] = loc.get("google_place_id")
        target["place_name"] = loc.get("place_name") or ""
        if loc.get("formatted_address"):
            target["formatted_address"] = loc["formatted_address"]
            if not str(target.get("address") or "").strip():
                target["address"] = loc["formatted_address"]
        target["google_maps_url"] = loc.get("google_maps_url") or ""
        target["selected_at"] = loc.get("selected_at") or now_iso()
        target["selected_by"] = _actor_stamp(actor)
        return
    target["google_place_id"] = None
    target["place_name"] = None
    target["formatted_address"] = None
    target["google_maps_url"] = None
    target["selected_at"] = None
    target["selected_by"] = None


def public_stop(doc: dict, settings: dict | None = None) -> dict:
    priority_id = doc.get("priority_id")
    rank = doc.get("priority_rank")
    label = doc.get("priority_label")
    color = doc.get("priority_color")
    if settings:
        match = next((p for p in settings.get("priority_levels") or [] if p["id"] == priority_id), None)
        if match:
            rank = match["rank"]
            label = match["label"]
            color = match["color"]
    lat = doc.get("lat")
    lng = doc.get("lng")
    has_location = is_valid_coordinate(lat, lng)
    return {
        "id": doc.get("id"),
        "route_id": doc.get("route_id"),
        "ref_kind": doc.get("ref_kind") or REF_ROUTE_ONLY,
        "ref_id": doc.get("ref_id"),
        "display_name": doc.get("display_name"),
        "address": doc.get("address") or "",
        "notes": doc.get("notes") or "",
        "lat": lat if has_location else None,
        "lng": lng if has_location else None,
        "location_source": doc.get("location_source"),
        "google_place_id": doc.get("google_place_id"),
        "place_name": doc.get("place_name") or "",
        "formatted_address": doc.get("formatted_address") or doc.get("address") or "",
        "google_maps_url": doc.get("google_maps_url") or "",
        "selected_at": doc.get("selected_at"),
        "selected_by": doc.get("selected_by"),
        "location_set": has_location,
        "priority_id": priority_id,
        "priority_rank": rank,
        "priority_label": label,
        "priority_color": color,
        "manual_order": doc.get("manual_order"),
        "optimized_order": doc.get("optimized_order"),
        "display_order": doc.get("display_order"),
        "source_type": _normalize_source_type(doc.get("source_type")),
        "sales_order_id": doc.get("sales_order_id"),
        "delivery_id": doc.get("delivery_id"),
        "delivery_schedule_id": doc.get("delivery_schedule_id"),
        "driver_user_id": doc.get("driver_user_id"),
        "order_number": doc.get("order_number"),
        "planning_snapshot": doc.get("planning_snapshot"),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def public_route(doc: dict, stops: list[dict], settings: dict | None = None) -> dict:
    pub_stops = [public_stop(s, settings) for s in stops]
    pub_stops.sort(key=lambda s: (
        s.get("display_order") is None,
        s.get("display_order") if s.get("display_order") is not None else 10**9,
        s.get("priority_rank") or 10**9,
        s.get("id") or "",
    ))
    missing = [s for s in pub_stops if not s.get("location_set")]
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "route_date": doc.get("route_date"),
        "notes": doc.get("notes") or "",
        "status": doc.get("status") or STATUS_DRAFT,
        "start_mode": doc.get("start_mode"),
        "start_location": doc.get("start_location"),
        "end_mode": doc.get("end_mode"),
        "end_location": doc.get("end_location"),
        "strategy": doc.get("strategy"),
        "current_optimization_version": doc.get("current_optimization_version") or 0,
        "optimization_stale": bool(doc.get("optimization_stale")),
        "sequence_overridden": bool(doc.get("sequence_overridden")),
        "sequence_source": doc.get("sequence_source") or SEQ_MANUAL,
        "estimated_distance_m": doc.get("estimated_distance_m"),
        "last_optimized_at": doc.get("last_optimized_at"),
        "last_optimized_by": doc.get("last_optimized_by"),
        "revision": doc.get("revision") or 1,
        "stop_count": len(pub_stops),
        "missing_location_count": len(missing),
        "source_type": _normalize_source_type(doc.get("source_type")),
        "driver_user_id": doc.get("driver_user_id"),
        "driver_name": doc.get("driver_name"),
        "deleted_at": doc.get("deleted_at"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "stops": pub_stops,
    }


def public_route_list_item(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "route_date": doc.get("route_date"),
        "status": doc.get("status") or STATUS_DRAFT,
        "start_mode": doc.get("start_mode"),
        "end_mode": doc.get("end_mode"),
        "strategy": doc.get("strategy"),
        "stop_count": int(doc.get("stop_count") or 0),
        "estimated_distance_m": doc.get("estimated_distance_m"),
        "last_optimized_at": doc.get("last_optimized_at"),
        "current_optimization_version": doc.get("current_optimization_version") or 0,
        "optimization_stale": bool(doc.get("optimization_stale")),
        "sequence_overridden": bool(doc.get("sequence_overridden")),
        "source_type": _normalize_source_type(doc.get("source_type")),
        "driver_user_id": doc.get("driver_user_id"),
        "driver_name": doc.get("driver_name"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
    }


async def _stops_for(db, route_id: str) -> list[dict]:
    return await db[COL_STOPS].find({"route_id": route_id}, {"_id": 0}).to_list(2000)


async def _route_or_404(db, route_id: str) -> dict:
    doc = await db[COL_PLANS].find_one({"id": route_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Rute tidak ditemukan.")
    return doc


def _assert_not_final(route: dict) -> None:
    if route.get("status") == STATUS_FINAL:
        raise HTTPException(409, "Rute FINAL tidak bisa diubah. Buka ulang dulu jika berwenang.")


def _resolve_priority(settings: dict, priority_id: str | None, fallback_rank=None) -> dict:
    levels = enabled_priorities(settings) or settings.get("priority_levels") or []
    if priority_id:
        match = next((p for p in levels if p["id"] == priority_id), None)
        if match:
            return match
    if fallback_rank is not None:
        try:
            rank = int(fallback_rank)
        except (TypeError, ValueError):
            rank = None
        if rank is not None:
            match = next((p for p in levels if p["rank"] == rank), None)
            if match:
                return match
    if not levels:
        raise HTTPException(400, "Tidak ada tingkat prioritas yang aktif.")
    return levels[-1] if levels else None


async def list_routes(db, actor: dict, perms: set) -> list[dict]:
    query = _active_plan_query()
    if not can_manage(perms):
        query = _active_plan_query({"created_by.id": actor["id"]})
    docs = await db[COL_PLANS].find(query, {"_id": 0}).sort("updated_at", -1).to_list(500)
    return [public_route_list_item(d) for d in docs]


async def get_route(db, route_id: str, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if route.get("deleted_at"):
        raise HTTPException(404, "Rute tidak ditemukan.")
    if not can_manage(perms) and not owns_route(route, actor):
        raise HTTPException(403, "Anda tidak punya akses ke rute ini.")
    settings = await load_settings(db)
    stops = await _stops_for(db, route_id)
    return public_route(route, stops, settings)


async def create_route(db, payload: dict, actor: dict) -> dict:
    settings = await load_settings(db)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Nama rute wajib diisi.")
    start_mode = str(payload.get("start_mode") or settings["default_start_mode"]).upper()
    end_mode = str(payload.get("end_mode") or settings["default_end_mode"]).upper()
    strategy = str(payload.get("strategy") or settings["default_strategy"]).upper()
    if start_mode not in START_MODES:
        raise HTTPException(400, "Start mode tidak valid.")
    if end_mode not in END_MODES:
        raise HTTPException(400, "End mode tidak valid.")
    if strategy not in STRATEGIES:
        raise HTTPException(400, "Strategi tidak valid.")
    now = now_iso()
    start_location = _stamp_location_actor(
        _location_snapshot(payload.get("start_location"), required=False, label="Titik awal"),
        actor,
    )
    end_location = _stamp_location_actor(
        _location_snapshot(payload.get("end_location"), required=False, label="Titik akhir"),
        actor,
    )
    route_id = new_id("iro")
    source_type = _normalize_source_type(payload.get("source_type"))
    doc = {
        "id": route_id,
        "name": name,
        "route_date": str(payload.get("route_date") or "").strip() or None,
        "notes": str(payload.get("notes") or "").strip(),
        "status": STATUS_DRAFT,
        "start_mode": start_mode,
        "start_location": start_location,
        "end_mode": end_mode,
        "end_location": end_location,
        "strategy": strategy,
        "current_optimization_version": 0,
        "optimization_stale": False,
        "sequence_overridden": False,
        "sequence_source": SEQ_MANUAL,
        "estimated_distance_m": None,
        "last_optimized_at": None,
        "last_optimized_by": None,
        "stop_count": 0,
        "revision": 1,
        "source_type": source_type,
        "driver_user_id": str(payload.get("driver_user_id") or "").strip() or None,
        "driver_name": str(payload.get("driver_name") or "").strip() or None,
        "deleted_at": None,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    await db[COL_PLANS].insert_one(doc)
    return public_route(doc, [], settings)


async def update_route(db, route_id: str, payload: dict, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if not can_edit_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh mengubah rute ini.")
    _assert_not_final(route)
    expected = payload.get("revision", route.get("revision") or 1)
    settings = await load_settings(db)
    updates = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Nama rute wajib diisi.")
        updates["name"] = name
    if "route_date" in payload:
        updates["route_date"] = str(payload.get("route_date") or "").strip() or None
    if "notes" in payload:
        updates["notes"] = str(payload.get("notes") or "").strip()
    if "start_mode" in payload:
        start_mode = str(payload.get("start_mode") or "").upper()
        if start_mode not in START_MODES:
            raise HTTPException(400, "Start mode tidak valid.")
        updates["start_mode"] = start_mode
    if "end_mode" in payload:
        end_mode = str(payload.get("end_mode") or "").upper()
        if end_mode not in END_MODES:
            raise HTTPException(400, "End mode tidak valid.")
        updates["end_mode"] = end_mode
    if "strategy" in payload:
        strategy = str(payload.get("strategy") or "").upper()
        if strategy not in STRATEGIES:
            raise HTTPException(400, "Strategi tidak valid.")
        updates["strategy"] = strategy
    if "start_location" in payload:
        updates["start_location"] = _stamp_location_actor(
            _location_snapshot(payload.get("start_location"), required=False, label="Titik awal"),
            actor,
        )
    if "end_location" in payload:
        updates["end_location"] = _stamp_location_actor(
            _location_snapshot(payload.get("end_location"), required=False, label="Titik akhir"),
            actor,
        )
    if "status" in payload:
        status = str(payload.get("status") or "").upper()
        if status not in STATUSES:
            raise HTTPException(400, "Status rute tidak valid.")
        if status == STATUS_FINAL and not can_edit_route(route, actor, perms):
            raise HTTPException(403, "Anda tidak boleh menandai rute FINAL.")
        if route.get("status") == STATUS_FINAL and status != STATUS_FINAL and not can_manage(perms):
            raise HTTPException(403, "Hanya pengelola yang dapat membuka ulang rute FINAL.")
        updates["status"] = status
    setup_keys = {"start_mode", "end_mode", "strategy", "start_location", "end_location"}
    if setup_keys.intersection(updates) and route.get("current_optimization_version"):
        updates["optimization_stale"] = True
    now = now_iso()
    updates["updated_at"] = now
    updates["updated_by"] = actor
    updates["revision"] = int(route.get("revision") or 1) + 1
    result = await db[COL_PLANS].find_one_and_update(
        {"id": route_id, "revision": expected},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(409, "Rute sudah diubah orang lain. Muat ulang lalu coba lagi.")
    result.pop("_id", None)
    stops = await _stops_for(db, route_id)
    return public_route(result, stops, settings)


async def add_stop(db, route_id: str, payload: dict, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if not can_edit_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh menambah stop pada rute ini.")
    _assert_not_final(route)
    settings = await load_settings(db)
    name = str(payload.get("display_name") or payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Nama stop wajib diisi.")
    ref_kind = str(payload.get("ref_kind") or REF_ROUTE_ONLY).upper()
    if ref_kind not in REF_KINDS:
        raise HTTPException(400, "Jenis referensi stop tidak valid.")
    ref_id = str(payload.get("ref_id") or "").strip() or None
    if ref_kind != REF_ROUTE_ONLY and not ref_id:
        raise HTTPException(400, "Referensi customer/outlet wajib jika memilih data master.")
    loc = _location_snapshot(payload, required=False, label="Titik lokasi")
    priority = _resolve_priority(settings, payload.get("priority_id"), payload.get("priority_rank"))
    existing = await _stops_for(db, route_id)
    next_order = (max((s.get("manual_order") or 0) for s in existing) + 1) if existing else 1
    now = now_iso()
    stop = {
        "id": new_id("irs"),
        "route_id": route_id,
        "ref_kind": ref_kind,
        "ref_id": ref_id,
        "display_name": name,
        "address": str(payload.get("address") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "lat": loc["lat"] if loc else None,
        "lng": loc["lng"] if loc else None,
        "location_source": loc["location_source"] if loc else None,
        "google_place_id": None,
        "place_name": None,
        "formatted_address": None,
        "google_maps_url": None,
        "selected_at": None,
        "selected_by": None,
        "priority_id": priority["id"],
        "priority_rank": priority["rank"],
        "priority_label": priority["label"],
        "priority_color": priority["color"],
        "manual_order": next_order,
        "optimized_order": None,
        "display_order": next_order,
        "source_type": _normalize_source_type(payload.get("source_type") or route.get("source_type")),
        "sales_order_id": str(payload.get("sales_order_id") or "").strip() or None,
        "delivery_id": str(payload.get("delivery_id") or "").strip() or None,
        "delivery_schedule_id": str(payload.get("delivery_schedule_id") or "").strip() or None,
        "driver_user_id": str(payload.get("driver_user_id") or "").strip() or None,
        "order_number": str(payload.get("order_number") or "").strip() or None,
        "planning_snapshot": payload.get("planning_snapshot") if isinstance(payload.get("planning_snapshot"), dict) else None,
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
    }
    _apply_location_to_stop(stop, loc, actor)
    await db[COL_STOPS].insert_one(stop)
    await db[COL_PLANS].update_one(
        {"id": route_id},
        {
            "$set": {
                "stop_count": len(existing) + 1,
                "optimization_stale": True if route.get("current_optimization_version") else False,
                "updated_at": now,
                "updated_by": actor,
                "revision": int(route.get("revision") or 1) + 1,
            }
        },
    )
    route = await _route_or_404(db, route_id)
    stops = await _stops_for(db, route_id)
    return {"route": public_route(route, stops, settings), "stop": public_stop(stop, settings)}


async def update_stop(db, route_id: str, stop_id: str, payload: dict, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if not can_edit_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh mengubah stop ini.")
    _assert_not_final(route)
    stop = await db[COL_STOPS].find_one({"id": stop_id, "route_id": route_id}, {"_id": 0})
    if not stop:
        raise HTTPException(404, "Stop tidak ditemukan.")
    settings = await load_settings(db)
    updates = {}
    if "display_name" in payload or "name" in payload:
        name = str(payload.get("display_name") or payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Nama stop wajib diisi.")
        updates["display_name"] = name
    if "address" in payload:
        updates["address"] = str(payload.get("address") or "").strip()
    if "notes" in payload:
        updates["notes"] = str(payload.get("notes") or "").strip()
    if any(k in payload for k in ("lat", "lng", "latitude", "longitude", "location_source", "google_place_id", "place_id")):
        loc = _location_snapshot(payload, required=True, label="Titik lokasi")
        _apply_location_to_stop(updates, loc, actor)
    if "priority_id" in payload or "priority_rank" in payload:
        if not can_manage(perms) and not owns_route(route, actor):
            raise HTTPException(403, "Anda tidak boleh mengubah prioritas stop ini.")
        priority = _resolve_priority(settings, payload.get("priority_id"), payload.get("priority_rank"))
        updates["priority_id"] = priority["id"]
        updates["priority_rank"] = priority["rank"]
        updates["priority_label"] = priority["label"]
        updates["priority_color"] = priority["color"]
    if not updates:
        raise HTTPException(400, "Tidak ada perubahan stop.")
    updates["updated_at"] = now_iso()
    await db[COL_STOPS].update_one({"id": stop_id, "route_id": route_id}, {"$set": updates})
    await db[COL_PLANS].update_one(
        {"id": route_id},
        {
            "$set": {
                "optimization_stale": True if route.get("current_optimization_version") else False,
                "updated_at": updates["updated_at"],
                "updated_by": actor,
                "revision": int(route.get("revision") or 1) + 1,
            }
        },
    )
    route = await _route_or_404(db, route_id)
    stops = await _stops_for(db, route_id)
    updated = next((s for s in stops if s.get("id") == stop_id), stop)
    return {"route": public_route(route, stops, settings), "stop": public_stop(updated, settings)}


async def delete_stop(db, route_id: str, stop_id: str, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if route.get("deleted_at"):
        raise HTTPException(404, "Rute tidak ditemukan.")
    if not can_edit_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh menghapus stop ini.")
    _assert_not_final(route)
    result = await db[COL_STOPS].delete_one({"id": stop_id, "route_id": route_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Stop tidak ditemukan.")
    now = now_iso()
    remaining = await _stops_for(db, route_id)
    # Membership change invalidates prior optimized sequence.
    stale = True if int(route.get("current_optimization_version") or 0) > 0 else False
    await db[COL_PLANS].update_one(
        {"id": route_id},
        {
            "$set": {
                "stop_count": len(remaining),
                "optimization_stale": stale,
                "updated_at": now,
                "updated_by": actor,
                "revision": int(route.get("revision") or 1) + 1,
            }
        },
    )
    settings = await load_settings(db)
    route = await _route_or_404(db, route_id)
    return {
        "route": public_route(route, remaining, settings),
        "deleted_stop_id": stop_id,
        "source_untouched": True,
    }


async def soft_delete_route(db, route_id: str, actor: dict, perms: set) -> dict:
    """Deactivate a route by exact id. Never touches Pengiriman/SJ/Packing/Loading/PO."""
    route = await _route_or_404(db, route_id)
    if route.get("deleted_at"):
        raise HTTPException(404, "Rute tidak ditemukan.")
    if not (can_manage(perms) or (can_edit_route(route, actor, perms))):
        raise HTTPException(403, "Anda tidak boleh menghapus rute ini.")
    now = now_iso()
    # Soft-delete plan + hard-delete owned stops only (optimizer data).
    await db[COL_PLANS].update_one(
        {"id": route_id},
        {
            "$set": {
                "deleted_at": now,
                "deleted_by": actor,
                "updated_at": now,
                "updated_by": actor,
                "optimization_stale": True,
                "revision": int(route.get("revision") or 1) + 1,
            }
        },
    )
    stops = await _stops_for(db, route_id)
    stop_ids = [s.get("id") for s in stops if s.get("id")]
    if stop_ids:
        await db[COL_STOPS].delete_many({"route_id": route_id, "id": {"$in": stop_ids}})
    return {
        "deleted": True,
        "route_id": route_id,
        "deleted_stop_ids": stop_ids,
        "source_untouched": True,
        "message": (
            "Rute optimasi dihapus. Pengiriman, Surat Jalan, Packing, Loading, "
            "dan PO tidak dihapus/diubah."
        ),
    }


def _stop_optimize_payload(stops: list[dict]) -> list[dict]:
    return [
        {
            "stop_id": s["id"],
            "lat": s.get("lat"),
            "lng": s.get("lng"),
            "priority_rank": s.get("priority_rank"),
            "priority_id": s.get("priority_id"),
            "display_name": s.get("display_name"),
        }
        for s in stops
    ]


async def optimize(db, route_id: str, payload: dict, actor: dict, perms: set) -> dict:
    if not payload:
        payload = {}
    route = await _route_or_404(db, route_id)
    if not can_optimize_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh mengoptimasi rute ini.")
    _assert_not_final(route)
    expected = payload.get("revision", route.get("revision") or 1)
    settings = await load_settings(db)
    stops = await _stops_for(db, route_id)
    start_mode = str(payload.get("start_mode") or route.get("start_mode") or START_CURRENT).upper()
    end_mode = str(payload.get("end_mode") or route.get("end_mode") or END_LAST).upper()
    strategy = str(payload.get("strategy") or route.get("strategy") or "NEAREST").upper()
    start_location = route.get("start_location")
    if "start_location" in payload:
        start_location = _location_snapshot(payload.get("start_location"), required=False, label="Titik awal")
    end_location = route.get("end_location")
    if "end_location" in payload:
        end_location = _location_snapshot(payload.get("end_location"), required=False, label="Titik akhir")
    warehouse = None
    wp = warehouse_point(settings)
    if wp:
        warehouse = {"lat": wp[0], "lng": wp[1], "configured": True}
    try:
        result = optimize_route(
            stops=_stop_optimize_payload(stops),
            strategy=strategy,
            start_mode=start_mode,
            start_snapshot=start_location,
            end_mode=end_mode,
            end_snapshot=end_location,
            warehouse=warehouse,
        )
    except OptimizerError as exc:
        raise HTTPException(400, exc.message) from exc

    coord_before = {s["id"]: (s.get("lat"), s.get("lng"), s.get("priority_id"), s.get("priority_rank")) for s in stops}
    now = now_iso()
    claimed = await db[COL_PLANS].find_one_and_update(
        {"id": route_id, "revision": expected, "status": {"$ne": STATUS_FINAL}},
        {"$inc": {"revision": 1, "current_optimization_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        raise HTTPException(409, "Optimasi bersamaan terdeteksi. Muat ulang lalu Optimize Ulang.")
    version_no = int(claimed.get("current_optimization_version") or 1)
    order_map = {sid: idx + 1 for idx, sid in enumerate(result.ordered_stop_ids)}
    for stop in stops:
        new_order = order_map.get(stop["id"])
        await db[COL_STOPS].update_one(
            {"id": stop["id"], "route_id": route_id},
            {"$set": {
                "optimized_order": new_order,
                "display_order": new_order,
                "updated_at": now,
            }},
        )
    after_stops = await _stops_for(db, route_id)
    for stop in after_stops:
        before = coord_before[stop["id"]]
        if (stop.get("lat"), stop.get("lng"), stop.get("priority_id"), stop.get("priority_rank")) != before:
            raise HTTPException(500, "Optimize mengubah data stop. Operasi dibatalkan.")

    version = {
        "id": new_id("irv"),
        "route_id": route_id,
        "optimization_version": version_no,
        "strategy": strategy,
        "start_mode": start_mode,
        "start_snapshot": start_location,
        "end_mode": end_mode,
        "end_snapshot": end_location,
        "warehouse_snapshot": warehouse,
        "priority_snapshot": snapshot_priorities(settings),
        "stop_snapshots": [
            {
                "stop_id": s["id"],
                "display_name": s.get("display_name"),
                "lat": s.get("lat"),
                "lng": s.get("lng"),
                "priority_id": s.get("priority_id"),
                "priority_rank": s.get("priority_rank"),
                "optimized_order": order_map.get(s["id"]),
            }
            for s in after_stops
        ],
        "ordered_stop_ids": result.ordered_stop_ids,
        "segments": result.segments,
        "total_estimated_m": result.total_estimated_m,
        "distance_model": result.distance_model,
        "optimized_at": now,
        "optimized_by": actor,
        "sequence_source": SEQ_OPTIMIZED,
    }
    await db[COL_VERSIONS].insert_one(version)
    updated = await db[COL_PLANS].find_one_and_update(
        {"id": route_id, "revision": claimed.get("revision")},
        {"$set": {
            "start_mode": start_mode,
            "start_location": start_location,
            "end_mode": end_mode,
            "end_location": end_location,
            "strategy": strategy,
            "status": STATUS_OPTIMIZED,
            "optimization_stale": False,
            "sequence_overridden": False,
            "sequence_source": SEQ_OPTIMIZED,
            "estimated_distance_m": result.total_estimated_m,
            "last_optimized_at": now,
            "last_optimized_by": actor,
            "updated_at": now,
            "updated_by": actor,
            "stop_count": len(after_stops),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        updated = await db[COL_PLANS].find_one({"id": route_id}, {"_id": 0})
    if updated:
        updated.pop("_id", None)
    version.pop("_id", None)
    return {
        "route": public_route(updated, after_stops, settings),
        "version": {k: version[k] for k in version if k != "_id"},
        "result": {
            "ordered_stop_ids": result.ordered_stop_ids,
            "total_estimated_m": result.total_estimated_m,
            "segments": result.segments,
            "distance_model": result.distance_model,
            "start": result.start,
            "end": result.end,
        },
    }


async def reorder_stops(db, route_id: str, payload: dict, actor: dict, perms: set) -> dict:
    route = await _route_or_404(db, route_id)
    if not can_edit_route(route, actor, perms):
        raise HTTPException(403, "Anda tidak boleh mengubah urutan rute ini.")
    _assert_not_final(route)
    ordered_ids = [str(x) for x in (payload.get("ordered_stop_ids") or payload.get("stop_ids") or [])]
    if not ordered_ids:
        raise HTTPException(400, "Urutan stop wajib diisi.")
    settings = await load_settings(db)
    stops = await _stops_for(db, route_id)
    try:
        validate_priority_order(_stop_optimize_payload(stops), ordered_ids)
    except OptimizerError as exc:
        raise HTTPException(400, exc.message) from exc
    expected = payload.get("revision", route.get("revision") or 1)
    now = now_iso()
    for index, stop_id in enumerate(ordered_ids, start=1):
        await db[COL_STOPS].update_one(
            {"id": stop_id, "route_id": route_id},
            {"$set": {"display_order": index, "manual_order": index, "updated_at": now}},
        )
    updated = await db[COL_PLANS].find_one_and_update(
        {"id": route_id, "revision": expected},
        {"$set": {
            "sequence_overridden": True,
            "sequence_source": SEQ_MANUAL,
            "optimization_stale": False,
            "updated_at": now,
            "updated_by": actor,
            "revision": int(route.get("revision") or 1) + 1,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "Rute sudah diubah orang lain. Muat ulang lalu coba lagi.")
    updated.pop("_id", None)
    after = await _stops_for(db, route_id)
    return {"route": public_route(updated, after, settings)}


async def list_versions(db, route_id: str, actor: dict, perms: set) -> list[dict]:
    route = await _route_or_404(db, route_id)
    if not can_manage(perms) and not owns_route(route, actor):
        raise HTTPException(403, "Anda tidak punya akses ke riwayat rute ini.")
    docs = await db[COL_VERSIONS].find({"route_id": route_id}, {"_id": 0}).sort("optimization_version", -1).to_list(200)
    return docs


async def search_stop_candidates(db, q: str) -> list[dict]:
    needle = str(q or "").strip()
    if len(needle) < 2:
        return []
    regex = {"$regex": needle, "$options": "i"}
    results = []
    sales = await db.sales_outlets.find(
        {"$or": [{"name": regex}, {"address": regex}, {"code": regex}]},
        {"_id": 0, "id": 1, "name": 1, "address": 1, "code": 1, "maps_url": 1},
    ).limit(20).to_list(20)
    for row in sales:
        results.append({
            "ref_kind": REF_SALES_OUTLET,
            "ref_id": row.get("id"),
            "display_name": row.get("name") or row.get("code") or "Outlet",
            "address": row.get("address") or "",
            "source_label": "Outlet Sales",
            "lat": None,
            "lng": None,
        })
    delivery = await db.delivery_outlets.find(
        {"$or": [
            {"customer_name": regex},
            {"outlet_name": regex},
            {"customer_key": regex},
            {"formatted_address": regex},
        ]},
        {
            "_id": 0, "id": 1, "customer_name": 1, "outlet_name": 1,
            "formatted_address": 1, "lat": 1, "lng": 1,
        },
    ).limit(20).to_list(20)
    for row in delivery:
        lat, lng = row.get("lat"), row.get("lng")
        hint = is_valid_coordinate(lat, lng)
        results.append({
            "ref_kind": REF_DELIVERY_OUTLET,
            "ref_id": row.get("id"),
            "display_name": row.get("outlet_name") or row.get("customer_name") or "Outlet",
            "address": row.get("formatted_address") or "",
            "source_label": "Outlet Pengiriman (referensi)",
            "lat": float(lat) if hint else None,
            "lng": float(lng) if hint else None,
            "coord_hint": bool(hint),
        })
    return results[:30]
