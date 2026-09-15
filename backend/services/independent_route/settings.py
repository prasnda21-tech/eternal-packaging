"""Independent Route Optimizer settings. Warehouse is never invented."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from services.independent_route.constants import (
    DEFAULT_PRIORITIES,
    END_LAST,
    SETTINGS_KEY,
    START_CURRENT,
    STRATEGY_NEAREST,
)
from services.independent_route.geo import is_valid_coordinate, point_from_snapshot
from services.independent_route.fleet_capacity import CapacityError, parse_capacity_qty


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_settings() -> dict:
    return {
        "warehouse": {
            "configured": False,
            "lat": None,
            "lng": None,
            "name": "",
            "address": "",
            "location_source": None,
        },
        "default_start_mode": START_CURRENT,
        "default_end_mode": END_LAST,
        "default_strategy": STRATEGY_NEAREST,
        "priority_levels": [dict(item) for item in DEFAULT_PRIORITIES],
        "map_defaults": {
            "viewport_lat": -2.5,
            "viewport_lng": 118.0,
            "viewport_zoom": 5,
        },
        "armada_1_capacity_qty_sj": None,
        "armada_2_capacity_qty_sj": None,
    }


def _normalize_priority_levels(raw) -> list[dict]:
    if not raw:
        return [dict(item) for item in DEFAULT_PRIORITIES]
    items = []
    ranks = set()
    ids = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or f"p{index + 1}").strip()
        if pid in ids:
            raise ValueError("Priority id tidak boleh duplikat.")
        try:
            rank = int(row.get("rank"))
        except (TypeError, ValueError):
            raise ValueError("Priority rank harus angka.")
        if rank in ranks:
            raise ValueError("Priority rank tidak boleh duplikat.")
        ids.add(pid)
        ranks.add(rank)
        color = str(row.get("color") or "#64748b").strip()
        label = str(row.get("label") or pid).strip() or pid
        items.append({
            "id": pid,
            "rank": rank,
            "label": label,
            "color": color,
            "enabled": bool(row.get("enabled", True)),
        })
    if not items:
        return [dict(item) for item in DEFAULT_PRIORITIES]
    items.sort(key=lambda item: (item["rank"], item["id"]))
    return items


def normalize_settings(payload: dict | None) -> dict:
    base = default_settings()
    src = payload or {}
    warehouse_in = src.get("warehouse") if isinstance(src.get("warehouse"), dict) else {}
    lat = warehouse_in.get("lat", warehouse_in.get("latitude"))
    lng = warehouse_in.get("lng", warehouse_in.get("longitude"))
    configured = bool(warehouse_in.get("configured")) and is_valid_coordinate(lat, lng)
    if is_valid_coordinate(lat, lng):
        configured = True
        warehouse = {
            "configured": True,
            "lat": float(lat),
            "lng": float(lng),
            "name": str(warehouse_in.get("name") or "").strip(),
            "address": str(warehouse_in.get("address") or "").strip(),
            "location_source": warehouse_in.get("location_source"),
        }
    else:
        warehouse = deepcopy(base["warehouse"])
        warehouse["name"] = str(warehouse_in.get("name") or "").strip()
        warehouse["address"] = str(warehouse_in.get("address") or "").strip()
        warehouse["configured"] = False
    start_mode = str(src.get("default_start_mode") or base["default_start_mode"]).upper()
    end_mode = str(src.get("default_end_mode") or base["default_end_mode"]).upper()
    strategy = str(src.get("default_strategy") or base["default_strategy"]).upper()
    if start_mode not in ("CURRENT_LOCATION", "WAREHOUSE", "MANUAL"):
        start_mode = base["default_start_mode"]
    if end_mode not in ("LAST_STOP", "RETURN_START", "WAREHOUSE", "MANUAL"):
        end_mode = base["default_end_mode"]
    if strategy not in ("NEAREST", "FARTHEST", "SHORTEST"):
        strategy = base["default_strategy"]
    try:
        cap1 = parse_capacity_qty(src.get("armada_1_capacity_qty_sj"), label="Kapasitas Armada 1")
        cap2 = parse_capacity_qty(src.get("armada_2_capacity_qty_sj"), label="Kapasitas Armada 2")
    except CapacityError as exc:
        raise ValueError(exc.message) from exc
    return {
        "warehouse": warehouse,
        "default_start_mode": start_mode,
        "default_end_mode": end_mode,
        "default_strategy": strategy,
        "priority_levels": _normalize_priority_levels(src.get("priority_levels")),
        "map_defaults": dict(base["map_defaults"]),
        "armada_1_capacity_qty_sj": cap1,
        "armada_2_capacity_qty_sj": cap2,
    }


def enabled_priorities(settings: dict) -> list[dict]:
    return [p for p in (settings.get("priority_levels") or []) if p.get("enabled")]


def warehouse_point(settings: dict):
    warehouse = (settings or {}).get("warehouse") or {}
    if not warehouse.get("configured"):
        return None
    return point_from_snapshot(warehouse)


def snapshot_priorities(settings: dict) -> list[dict]:
    return [dict(item) for item in (settings.get("priority_levels") or [])]


async def load_settings(db) -> dict:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    value = (doc or {}).get("value") if doc else None
    if not value and doc:
        value = {k: v for k, v in doc.items() if k != "key"}
    return normalize_settings(value)


async def save_settings(db, payload: dict, user: dict) -> dict:
    existing = await load_settings(db)
    incoming = dict(payload or {})
    merged = {**existing, **incoming}
    cfg = normalize_settings(merged)
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "value": cfg,
                "updated_at": now_iso(),
                "updated_by": {
                    "id": str(user.get("user_id") or user.get("id") or ""),
                    "name": str(user.get("name") or user.get("email") or ""),
                },
            }
        },
        upsert=True,
    )
    return cfg


def armada_capacity_public(settings: dict | None) -> dict:
    src = settings or {}
    return {
        "armada_1_capacity_qty_sj": src.get("armada_1_capacity_qty_sj"),
        "armada_2_capacity_qty_sj": src.get("armada_2_capacity_qty_sj"),
        "capacity_unit": "qty_surat_jalan",
    }


async def save_armada_capacity(db, payload: dict, user: dict) -> dict:
    """Atomically persist both Armada 1 and Armada 2 Qty SJ capacities."""
    src = payload or {}
    try:
        cap1 = parse_capacity_qty(src.get("armada_1_capacity_qty_sj"), label="Kapasitas Armada 1")
        cap2 = parse_capacity_qty(src.get("armada_2_capacity_qty_sj"), label="Kapasitas Armada 2")
    except CapacityError as exc:
        raise ValueError(exc.message) from exc
    if cap1 is None or cap2 is None:
        raise ValueError("Kapasitas Armada 1 dan Armada 2 wajib diisi dan harus lebih dari 0.")
    existing = await load_settings(db)
    existing["armada_1_capacity_qty_sj"] = cap1
    existing["armada_2_capacity_qty_sj"] = cap2
    cfg = await save_settings(db, existing, user)
    return armada_capacity_public(cfg)
