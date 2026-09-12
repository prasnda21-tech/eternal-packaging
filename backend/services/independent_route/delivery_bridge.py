"""Bridge: Jadwal Pengiriman → independent Route Optimizer.

Creates delivery-linked routes/stops inside independent_route_* collections.
Does NOT revive legacy delivery batches / Rute Saya.
Does NOT mutate Pengiriman / SJ / Packing / Loading / PO workflow data.
"""
from __future__ import annotations

import hashlib
import uuid
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services.delivery_schedule import (
    SCHEDULE_PROJECTION,
    effective_delivery_date,
    schedule_row,
)
from services.delivery_stops import order_location, parse_location
from services.independent_route import store
from services.independent_route.constants import (
    COL_PLANS,
    COL_STOPS,
    LOC_MANUAL,
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_MANAGE,
    PERM_OPTIMIZE,
    REF_ROUTE_ONLY,
    SOURCE_DELIVERY_SCHEDULE,
    OPT_STATUS_NONE,
    OPT_STATUS_OPTIMIZED,
    OPT_STATUS_STALE,
    OPT_STATUS_LABELS,
    PRIORITY_ID_URGENT,
    PRIORITY_ID_NORMAL,
    PRIORITY_RANK_URGENT,
    PRIORITY_RANK_NORMAL,
    FLEET_LABEL_1,
    FLEET_LABEL_2,
    FLEET_PARTITION_VERSION,
    SCHEDULE_FLEET_CREATE_VERSION,
    FLEET_GROUP_ID_PREFIX,
    START_WAREHOUSE,
    START_MANUAL,
    END_LAST,
    STRATEGY_NEAREST,
)
from services.independent_route.geo import is_valid_coordinate
from services.independent_route.settings import now_iso, load_settings, warehouse_point
from services.independent_route.optimizer import optimize_route, OptimizerError
from services.independent_route.fleet_partition import (
    FleetPartitionError,
    assert_partition_invariants,
    partition_into_two_fleets,
)
from services.surat_jalan_files import shipment_is_schedule_ready
from services.delivery_driver_assignment import resolve_eligible_driver
from quantity_rules import shipments_stored
import json

# Extra order fields needed for bridge (additive to schedule projection).
BRIDGE_ORDER_PROJECTION = {
    **SCHEDULE_PROJECTION,
    "customer_id": 1,
    "delivery_outlet_id": 1,
    "delivery_location": 1,
    "address": 1,
    "outlet": 1,
    "loading_checklist.driver_user_id": 1,
    "loading_checklist.driver_name": 1,
    "shipments.id": 1,
    "shipments.number": 1,
    "shipments.sj_number": 1,
    "shipments.file_id": 1,
    "shipments.lines": 1,
    "shipments.accurate_shipment_id": 1,
    "surat_jalan_files.id": 1,
    "surat_jalan_files.name": 1,
    "surat_jalan_files.content_type": 1,
    "surat_jalan_files.uploaded_at": 1,
    "surat_jalan_files.storage_path": 1,
    "surat_jalan_files.data_url": 1,
    "surat_jalan_files.shipment_id": 1,
    "surat_jalan_files.lines": 1,
    "surat_jalan_files.source": 1,
    "priority": 1,
}


def _text(v: Any) -> str:
    return str(v or "").strip()


def _norm_addr(v: Any) -> str:
    text = _text(v).lower()
    text = re.sub(r"\s+", " ", text)
    return text


def address_fingerprint(*, address: str = "", outlet_id: Optional[str] = None, lat=None, lng=None) -> str:
    raw = "|".join([
        _text(outlet_id),
        _norm_addr(address),
        f"{float(lat):.6f}" if lat is not None else "",
        f"{float(lng):.6f}" if lng is not None else "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def canonical_driver_user_id(order: dict) -> Optional[str]:
    """Exact driver user id only — never derive from display name."""
    lc = order.get("loading_checklist") or {}
    uid = _text(lc.get("driver_user_id") or order.get("driver_user_id"))
    return uid or None


def canonical_driver_name(order: dict) -> Optional[str]:
    lc = order.get("loading_checklist") or {}
    name = _text(lc.get("driver_name") or order.get("driver_name"))
    return name or None


def bridge_location(order: dict, outlets_by_id: Optional[Dict[str, dict]] = None) -> dict:
    """Resolve location without customer-name matching.

    Prefer order.delivery_location, then delivery_outlet_id lookup by exact id.
    """
    loc = parse_location(order.get("delivery_location"))
    outlet_id = _text(order.get("delivery_outlet_id")) or None
    if outlet_id:
        loc["outlet_id"] = outlet_id
    if is_valid_coordinate(loc.get("lat"), loc.get("lng")):
        if not loc.get("formatted_address"):
            loc["formatted_address"] = _text(order.get("address")) or None
        return loc
    if outlet_id and outlets_by_id and outlet_id in outlets_by_id:
        out = outlets_by_id[outlet_id]
        out_loc = parse_location(out)
        out_loc["outlet_id"] = outlet_id
        out_loc["outlet_name"] = _text(out.get("name")) or None
        if not out_loc.get("formatted_address"):
            out_loc["formatted_address"] = (
                _text(out.get("formatted_address"))
                or _text(order.get("address"))
                or None
            )
        return out_loc
    # Fallback: order fields only (no name-based outlet resolution).
    bare = order_location(order, outlets_index=None)
    return bare


def planning_snapshot_from_order(order: dict, loc: dict) -> dict:
    driver_id = canonical_driver_user_id(order)
    date_key = effective_delivery_date(order)
    address = _text(loc.get("formatted_address") or order.get("address"))
    outlet_id = _text(loc.get("outlet_id") or order.get("delivery_outlet_id")) or None
    lat = loc.get("lat")
    lng = loc.get("lng")
    return {
        "driver_user_id": driver_id,
        "scheduled_delivery_date": date_key,
        "outlet_id": outlet_id,
        "address": address or None,
        "address_fingerprint": address_fingerprint(
            address=address, outlet_id=outlet_id, lat=lat, lng=lng
        ),
        "lat": lat,
        "lng": lng,
    }


def snapshots_match(current: dict, snap: Optional[dict]) -> bool:
    if not snap:
        return False
    # Route driver is chosen at save-time; order loading_checklist driver must not stale a route.
    if _text(current.get("scheduled_delivery_date")) != _text(snap.get("scheduled_delivery_date")):
        return False
    cur_fp = current.get("address_fingerprint") or address_fingerprint(
        address=current.get("address") or "",
        outlet_id=current.get("outlet_id"),
        lat=current.get("lat"),
        lng=current.get("lng"),
    )
    snap_fp = snap.get("address_fingerprint") or address_fingerprint(
        address=snap.get("address") or "",
        outlet_id=snap.get("outlet_id"),
        lat=snap.get("lat"),
        lng=snap.get("lng"),
    )
    if cur_fp != snap_fp:
        return False
    # Explicit lat/lng compare with rounding tolerance for float noise.
    try:
        if abs(float(current.get("lat")) - float(snap.get("lat"))) > 1e-6:
            return False
        if abs(float(current.get("lng")) - float(snap.get("lng"))) > 1e-6:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _active_route_filter() -> dict:
    return {"$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}


async def _outlets_by_id(db, outlet_ids: Iterable[str]) -> Dict[str, dict]:
    ids = [i for i in {_text(x) for x in outlet_ids} if i]
    if not ids:
        return {}
    docs = await db.delivery_outlets.find({"id": {"$in": ids}}, {"_id": 0}).to_list(2000)
    return {str(d.get("id")): d for d in docs if d.get("id")}


async def _active_delivery_stops_for_orders(db, order_ids: List[str]) -> List[dict]:
    if not order_ids:
        return []
    stops = await db[COL_STOPS].find(
        {
            "source_type": SOURCE_DELIVERY_SCHEDULE,
            "sales_order_id": {"$in": order_ids},
            "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}],
        },
        {"_id": 0},
    ).to_list(5000)
    if not stops:
        return []
    route_ids = list({s.get("route_id") for s in stops if s.get("route_id")})
    routes = await db[COL_PLANS].find(
        {"id": {"$in": route_ids}, **_active_route_filter()},
        {
            "_id": 0,
            "id": 1,
            "status": 1,
            "optimization_stale": 1,
            "current_optimization_version": 1,
            "deleted_at": 1,
            "fleet_group_id": 1,
        },
    ).to_list(2000)
    active_ids = {r["id"] for r in routes}
    route_by_id = {r["id"]: r for r in routes}
    out = []
    for s in stops:
        rid = s.get("route_id")
        if rid not in active_ids:
            continue
        s = dict(s)
        s["_route"] = route_by_id.get(rid)
        out.append(s)
    return out


def derive_opt_status(order: dict, stop: Optional[dict], loc: dict) -> Tuple[str, Optional[str]]:
    """Return (status_code, route_id)."""
    if not stop:
        return OPT_STATUS_NONE, None
    route = stop.get("_route") or {}
    route_id = stop.get("route_id")
    current = planning_snapshot_from_order(order, loc)
    snap = stop.get("planning_snapshot") or {}
    stale_route = bool(route.get("optimization_stale"))
    version = int(route.get("current_optimization_version") or 0)
    optimized = version > 0 and str(route.get("status") or "").upper() in ("OPTIMIZED", "FINAL")
    if not optimized or stale_route or not snapshots_match(current, snap):
        return OPT_STATUS_STALE, route_id
    return OPT_STATUS_OPTIMIZED, route_id


def order_is_urgent(order: dict) -> bool:
    return str((order or {}).get("priority") or "").strip().lower() == "urgent"


def order_has_schedule_ready_sj(order: dict) -> bool:
    for sh in shipments_stored(order):
        if shipment_is_schedule_ready(order, sh):
            return True
    return False


def eligibility_for_order(order: dict, loc: dict, *, existing_stop: Optional[dict] = None) -> dict:
    """Eligibility: SJ ready + coords. Order driver is NOT a gate."""
    oid = _text(order.get("id"))
    delivery_status = _text(order.get("delivery_status"))
    reasons: List[str] = []
    if delivery_status.lower() == "terkirim":
        reasons.append("Sudah terkirim")
    if not order_has_schedule_ready_sj(order):
        reasons.append("Surat Jalan belum siap.")
    address = _text(loc.get("formatted_address") or order.get("address"))
    has_coords = is_valid_coordinate(loc.get("lat"), loc.get("lng"))
    if not address and not has_coords:
        reasons.append("Alamat belum tersedia")
    if not has_coords:
        reasons.append("Koordinat belum tersedia")
    elif not address:
        # coords without address still optimizable; address is display-only
        pass
    opt_status, route_id = derive_opt_status(order, existing_stop, loc)
    if opt_status == OPT_STATUS_OPTIMIZED and existing_stop:
        reasons.append("Already in active valid route")
    eligible = len(reasons) == 0
    return {
        "eligible": eligible,
        "reasons": reasons,
        "reason": reasons[0] if reasons else None,
        "optimization_status": opt_status,
        "optimization_status_label": OPT_STATUS_LABELS.get(opt_status, opt_status),
        "route_id": route_id,
        "existing_stop_id": (existing_stop or {}).get("id"),
    }


async def build_review_rows(db, order_ids: List[str]) -> dict:
    """Pure read: review candidates for exact order IDs (filter scope contract)."""
    ids = [_text(i) for i in order_ids if _text(i)]
    # Preserve caller order; drop empties / dups while keeping first occurrence.
    seen = set()
    ordered_ids: List[str] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        ordered_ids.append(i)
    if not ordered_ids:
        return {
            "rows": [],
            "summary": {
                "selected": 0,
                "eligible": 0,
                "ineligible": 0,
                "drivers": 0,
                "routes_to_create": 0,
            },
            "groups": [],
        }

    orders = await db.orders.find(
        {"id": {"$in": ordered_ids}, "cancelled": {"$ne": True}},
        {"_id": 0, **BRIDGE_ORDER_PROJECTION},
    ).to_list(2000)
    by_id = {str(o.get("id")): o for o in orders}
    outlet_ids = [_text(o.get("delivery_outlet_id")) for o in orders]
    outlets = await _outlets_by_id(db, outlet_ids)
    stops = await _active_delivery_stops_for_orders(db, ordered_ids)
    stop_by_order: Dict[str, dict] = {}
    for s in stops:
        soid = _text(s.get("sales_order_id"))
        if soid and soid not in stop_by_order:
            stop_by_order[soid] = s

    rows = []
    for oid in ordered_ids:
        order = by_id.get(oid)
        if not order:
            rows.append({
                "sales_order_id": oid,
                "delivery_id": oid,
                "order_number": "",
                "customer_name": "",
                "eligible": False,
                "reasons": ["Data tujuan belum valid"],
                "reason": "Data tujuan belum valid",
                "optimization_status": OPT_STATUS_NONE,
                "optimization_status_label": OPT_STATUS_LABELS[OPT_STATUS_NONE],
                "driver_user_id": None,
                "driver_name": None,
            })
            continue
        loc = bridge_location(order, outlets)
        existing = stop_by_order.get(oid)
        elig = eligibility_for_order(order, loc, existing_stop=existing)
        base = schedule_row(order)
        snap = planning_snapshot_from_order(order, loc)
        sj_ids = []
        for sh in order.get("shipments") or []:
            sid = _text(sh.get("id"))
            if sid:
                sj_ids.append(sid)
        rows.append({
            "sales_order_id": oid,
            "delivery_id": oid,
            "delivery_schedule_id": oid,
            "order_number": base.get("order_number") or "",
            "customer_name": base.get("customer_name") or "",
            "customer_id": order.get("customer_id"),
            "sj_numbers": base.get("sj_numbers") or [],
            "sj_label": base.get("sj_label") or "",
            "shipment_ids": sj_ids,
            "qty_total": base.get("qty_total"),
            "qty_packed": base.get("qty_packed"),
            "qty_delivered": base.get("qty_delivered"),
            "driver_user_id": snap.get("driver_user_id"),
            "driver_name": canonical_driver_name(order),
            "effective_delivery_date": snap.get("scheduled_delivery_date"),
            "address": snap.get("address"),
            "outlet_id": snap.get("outlet_id"),
            "lat": snap.get("lat"),
            "lng": snap.get("lng"),
            "urgent": order_is_urgent(order),
            "priority": _text(order.get("priority")) or "normal",
            "eligible": elig["eligible"],
            "reasons": elig["reasons"],
            "reason": elig["reason"],
            "optimization_status": elig["optimization_status"],
            "optimization_status_label": elig["optimization_status_label"],
            "route_id": elig.get("route_id"),
            "existing_stop_id": elig.get("existing_stop_id"),
            "planning_snapshot": snap,
        })

    eligible_rows = [r for r in rows if r.get("eligible")]
    groups_map: Dict[str, list] = {}
    for r in eligible_rows:
        did = _text(r.get("driver_user_id"))
        if not did:
            continue
        groups_map.setdefault(did, []).append(r)
    groups = []
    for driver_id, items in groups_map.items():
        groups.append({
            "driver_user_id": driver_id,
            "driver_name": items[0].get("driver_name"),
            "count": len(items),
            "sales_order_ids": [i["sales_order_id"] for i in items],
        })
    groups.sort(key=lambda g: (g.get("driver_name") or "", g["driver_user_id"]))

    return {
        "rows": rows,
        "summary": {
            "selected": len(rows),
            "eligible": len(eligible_rows),
            "ineligible": len(rows) - len(eligible_rows),
            "drivers": len(groups),
            "routes_to_create": len(groups),
        },
        "groups": groups,
    }


async def optimization_status_map(db, order_ids: List[str]) -> Dict[str, dict]:
    """Pure read map of sales_order_id → status info."""
    review = await build_review_rows(db, order_ids)
    out = {}
    for r in review["rows"]:
        out[r["sales_order_id"]] = {
            "optimization_status": r.get("optimization_status") or OPT_STATUS_NONE,
            "optimization_status_label": r.get("optimization_status_label")
            or OPT_STATUS_LABELS[OPT_STATUS_NONE],
            "route_id": r.get("route_id"),
            "driver_user_id": r.get("driver_user_id"),
        }
    return out


def _route_name(driver_name: Optional[str], date_key: Optional[str], driver_id: str) -> str:
    date_part = date_key or "Tanpa tanggal"
    name_part = _text(driver_name) or driver_id
    return f"Pengiriman {date_part} - {name_part}"


def _preview_fingerprint(eligible_rows: List[dict], fleet_id_lists: List[List[str]], fleet_count: int) -> str:
    payload = {
        "v": FLEET_PARTITION_VERSION,
        "fleet_count": int(fleet_count),
        "eligible": [
            {
                "id": _text(r.get("sales_order_id")),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "urgent": bool(r.get("urgent")),
            }
            for r in sorted(eligible_rows, key=lambda x: _text(x.get("sales_order_id")))
        ],
        "fleets": [list(ids) for ids in fleet_id_lists],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _google_maps_dir_url(
    *,
    ordered_rows: List[dict],
    warehouse: Optional[dict] = None,
) -> str:
    """Build a Google Maps directions URL from optimizer start + ordered stops.

    Mirrors frontend `googleMapsDirUrl` (path form). No silent drop of stops.
    """
    points: List[tuple] = []
    if warehouse and is_valid_coordinate(warehouse.get("lat"), warehouse.get("lng")):
        points.append((float(warehouse["lat"]), float(warehouse["lng"])))
    for r in ordered_rows:
        if is_valid_coordinate(r.get("lat"), r.get("lng")):
            points.append((float(r["lat"]), float(r["lng"])))
    if not points:
        return ""
    if len(points) == 1:
        lat, lng = points[0]
        return f"https://www.google.com/maps?q={lat},{lng}"
    path = "/".join(f"{lat},{lng}" for lat, lng in points)
    return f"https://www.google.com/maps/dir/{path}"


def _order_stops_for_fleet(
    rows: List[dict],
    *,
    warehouse: Optional[dict],
    strategy: str = "NEAREST",
) -> dict:
    if not rows:
        return {
            "ordered_rows": [],
            "ordered_order_ids": [],
            "total_estimated_m": 0.0,
            "segments": [],
            "strategy": strategy,
            "start": None,
            "end": None,
        }
    by_id = {}
    stops_payload = []
    for r in rows:
        oid = _text(r.get("sales_order_id"))
        urgent = bool(r.get("urgent"))
        by_id[oid] = r
        stops_payload.append({
            "stop_id": oid,
            "lat": r.get("lat"),
            "lng": r.get("lng"),
            "priority_rank": PRIORITY_RANK_URGENT if urgent else PRIORITY_RANK_NORMAL,
            "priority_id": PRIORITY_ID_URGENT if urgent else PRIORITY_ID_NORMAL,
            "display_name": r.get("customer_name") or r.get("order_number") or oid,
        })
    start_mode = "WAREHOUSE" if warehouse else "MANUAL"
    start_snapshot = None if warehouse else {"lat": rows[0].get("lat"), "lng": rows[0].get("lng")}
    result = optimize_route(
        stops=stops_payload,
        strategy=strategy,
        start_mode=start_mode,
        start_snapshot=start_snapshot,
        end_mode="LAST_STOP",
        end_snapshot=None,
        warehouse=warehouse,
    )
    ordered_rows = []
    for seq, sid in enumerate(result.ordered_stop_ids, start=1):
        row = dict(by_id[sid])
        row["stop_sequence"] = seq
        row["priority_id"] = PRIORITY_ID_URGENT if row.get("urgent") else PRIORITY_ID_NORMAL
        ordered_rows.append(row)
    return {
        "ordered_rows": ordered_rows,
        "ordered_order_ids": list(result.ordered_stop_ids),
        "total_estimated_m": float(result.total_estimated_m or 0.0),
        "segments": list(result.segments or []),
        "strategy": result.strategy,
        "start": result.start,
        "end": result.end,
    }


def _fleet_panel(label: str, ordered: dict, *, warehouse: Optional[dict] = None) -> dict:
    rows = ordered.get("ordered_rows") or []
    maps_url = _google_maps_dir_url(ordered_rows=rows, warehouse=warehouse)
    return {
        "label": label,
        "fleet_key": "armada_2" if "2" in str(label) else "armada_1",
        "order_ids": [_text(r.get("sales_order_id")) for r in rows],
        "stops": rows,
        "total_orders": len(rows),
        "total_urgent": sum(1 for r in rows if r.get("urgent")),
        "estimated_distance_m": ordered.get("total_estimated_m"),
        "segments": ordered.get("segments") or [],
        "strategy": ordered.get("strategy"),
        "google_maps_url": maps_url,
        "start": ordered.get("start"),
        "end": ordered.get("end"),
    }


async def preview_fleets_from_schedule(
    db,
    order_ids: List[str],
    *,
    fleet_count: int,
) -> dict:
    """Non-persistent 1/2-armada optimization preview. ZERO route/order writes."""
    try:
        fleet_count_n = int(fleet_count)
    except (TypeError, ValueError):
        raise HTTPException(400, "fleet_count harus 1 atau 2.")
    if fleet_count_n not in (1, 2):
        raise HTTPException(400, "fleet_count harus 1 atau 2.")

    review = await build_review_rows(db, order_ids)
    selected = {_text(i) for i in order_ids if _text(i)}
    eligible = [
        r for r in review["rows"]
        if r.get("eligible") and _text(r.get("sales_order_id")) in selected
    ]
    ineligible = [
        r for r in review["rows"]
        if (not r.get("eligible")) and _text(r.get("sales_order_id")) in selected
    ]
    eligible_ids = [_text(r.get("sales_order_id")) for r in eligible]

    if fleet_count_n == 1:
        if len(eligible) < 1:
            raise HTTPException(
                400,
                "Optimasi 1 Armada membutuhkan minimal 1 PO eligible (SJ siap + koordinat valid).",
            )
        fleet_id_lists = [list(eligible_ids)]
    else:
        if len(eligible) < 2:
            raise HTTPException(
                400,
                "Optimasi 2 Armada membutuhkan minimal 2 PO eligible (SJ siap + koordinat valid).",
            )
        partition_input = [
            {
                "id": _text(r.get("sales_order_id")),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "urgent": bool(r.get("urgent")),
            }
            for r in eligible
        ]
        try:
            parted = partition_into_two_fleets(partition_input)
        except FleetPartitionError as exc:
            raise HTTPException(400, exc.message) from exc
        a1_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["armada_1"]]
        a2_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["armada_2"]]
        # Support alternate key names from partition helper
        if not a1_ids and "fleet_1" in parted:
            a1_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["fleet_1"]]
            a2_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["fleet_2"]]
        assert_partition_invariants(eligible_ids, a1_ids, a2_ids)
        fleet_id_lists = [a1_ids, a2_ids]

    by_id = {_text(r.get("sales_order_id")): r for r in eligible}
    settings = await load_settings(db)
    wp = warehouse_point(settings)
    warehouse = {"lat": wp[0], "lng": wp[1]} if wp else None

    labels = [FLEET_LABEL_1] if fleet_count_n == 1 else [FLEET_LABEL_1, FLEET_LABEL_2]
    fleets = []
    for label, ids in zip(labels, fleet_id_lists):
        rows = [by_id[i] for i in ids if i in by_id]
        ordered = _order_stops_for_fleet(rows, warehouse=warehouse)
        fleets.append(_fleet_panel(label, ordered, warehouse=warehouse))

    # Hard invariants
    seen = []
    for f in fleets:
        for oid in f["order_ids"]:
            if oid in seen:
                raise HTTPException(500, "Invariant gagal: PO duplikat antar armada.")
            seen.append(oid)
        if not f["order_ids"]:
            raise HTTPException(500, "Invariant gagal: armada kosong.")
    if set(seen) != set(eligible_ids):
        raise HTTPException(500, "Invariant gagal: PO eligible hilang dari hasil optimasi.")

    fingerprint = _preview_fingerprint(eligible, [f["order_ids"] for f in fleets], fleet_count_n)
    return {
        "ok": True,
        "preview": True,
        "preview_fingerprint": fingerprint,
        "fleet_count": fleet_count_n,
        "eligible_order_ids": eligible_ids,
        "excluded_rows": ineligible,
        "fleets": fleets,
        "review": review,
        "summary": {
            **review["summary"],
            "eligible": len(eligible),
            "fleet_count": fleet_count_n,
            "fleet_order_counts": [f["total_orders"] for f in fleets],
        },
    }





def _schedule_fleet_input_fingerprint(eligible_ids: List[str], fleet_count: int) -> str:
    payload = {
        "v": SCHEDULE_FLEET_CREATE_VERSION,
        "fleet_count": int(fleet_count),
        "eligible": sorted(_text(i) for i in eligible_ids if _text(i)),
        "partition_version": FLEET_PARTITION_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _soft_delete_routes(db, route_ids: List[str], actor: dict, perms: set) -> None:
    for rid in route_ids:
        rid = _text(rid)
        if not rid:
            continue
        try:
            await store.soft_delete_route(db, rid, actor, perms | {PERM_MANAGE, PERM_EDIT_OWN})
        except Exception:
            # Best-effort compensation only for routes created in this attempt.
            pass


async def create_fleets_from_schedule(
    db,
    order_ids: List[str],
    *,
    fleet_count: int,
    actor: dict,
    perms: set,
) -> dict:
    """One-stage driverless create+optimize for 1 or 2 armada from Jadwal Pengiriman.

    Persists exactly fleet_count routes. Never requires/infers/assigns a driver.
    Never mutates source-order driver fields.
    """
    if not (PERM_CREATE in perms or PERM_MANAGE in perms):
        raise HTTPException(403, "Anda tidak boleh membuat rute.")
    if PERM_OPTIMIZE not in perms and PERM_MANAGE not in perms:
        raise HTTPException(403, "Anda tidak boleh mengoptimasi rute.")

    try:
        fleet_count_n = int(fleet_count)
    except (TypeError, ValueError):
        raise HTTPException(400, "fleet_count harus 1 atau 2.")
    if fleet_count_n not in (1, 2):
        raise HTTPException(400, "fleet_count harus 1 atau 2.")

    review = await build_review_rows(db, order_ids)
    selected = {_text(i) for i in order_ids if _text(i)}
    eligible = [
        r for r in review["rows"]
        if r.get("eligible") and _text(r.get("sales_order_id")) in selected
    ]
    ineligible = [
        r for r in review["rows"]
        if (not r.get("eligible")) and _text(r.get("sales_order_id")) in selected
    ]
    eligible_ids = [_text(r.get("sales_order_id")) for r in eligible]

    def _only_active_route_block(row: dict) -> bool:
        reasons = [str(x) for x in (row.get("reasons") or []) if x]
        if not reasons:
            return False
        return all(
            ("already in active" in x.lower())
            or ("rute aktif" in x.lower())
            for x in reasons
        )

    # Idempotency fingerprint uses currently-eligible POs plus POs blocked only because
    # they already sit on an active route (covers successful-create retries).
    fingerprint_ids = sorted({
        _text(r.get("sales_order_id"))
        for r in (eligible + [x for x in ineligible if _only_active_route_block(x)])
        if _text(r.get("sales_order_id"))
    })

    fingerprint = _schedule_fleet_input_fingerprint(fingerprint_ids or eligible_ids, fleet_count_n)
    existing = await store.find_active_routes_by_schedule_fingerprint(db, fingerprint)
    if len(existing) == fleet_count_n and all(int(r.get("stop_count") or 0) > 0 for r in existing):
        # Idempotent reuse of a completed prior create.
        routes_out = []
        for doc in existing:
            full = await store.get_route(db, doc["id"], actor, perms | {PERM_MANAGE, PERM_CREATE, PERM_OPTIMIZE, PERM_EDIT_OWN})
            routes_out.append({
                "route_id": full["id"],
                "label": full.get("name"),
                "fleet_group_id": full.get("fleet_group_id"),
                "order_ids": [
                    _text(s.get("sales_order_id"))
                    for s in (full.get("stops") or [])
                    if _text(s.get("sales_order_id"))
                ],
                "stop_count": int(full.get("stop_count") or len(full.get("stops") or [])),
                "urgent_count": sum(
                    1 for s in (full.get("stops") or [])
                    if str(s.get("priority_id") or "") == PRIORITY_ID_URGENT
                ),
                "estimated_distance_m": full.get("estimated_distance_m"),
                "status": full.get("status"),
                "route": full,
            })
        return {
            "ok": True,
            "created": False,
            "reused": True,
            "fleet_count": fleet_count_n,
            "created_route_count": len(routes_out),
            "fleet_group_id": routes_out[0].get("fleet_group_id") if routes_out else None,
            "schedule_input_fingerprint": fingerprint,
            "eligible_order_ids": eligible_ids,
            "excluded_rows": ineligible,
            "routes": routes_out,
            "review": review,
            "summary": {
                **review["summary"],
                "eligible": len(eligible),
                "fleet_count": fleet_count_n,
                "created_route_count": len(routes_out),
                "reused": True,
            },
        }
    if existing:
        # Incomplete prior attempt — remove only fingerprint-matched orphans.
        await _soft_delete_routes(db, [r.get("id") for r in existing], actor, perms)


    if fleet_count_n == 1:
        if len(eligible) < 1:
            raise HTTPException(
                400,
                "Optimasi 1 Armada membutuhkan minimal 1 PO eligible (SJ siap + koordinat valid).",
            )
        fleet_id_lists = [list(eligible_ids)]
    else:
        if len(eligible) < 2:
            raise HTTPException(
                400,
                "Optimasi 2 Armada membutuhkan minimal 2 PO eligible (SJ siap + koordinat valid).",
            )
        partition_input = [
            {
                "id": _text(r.get("sales_order_id")),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "urgent": bool(r.get("urgent")),
            }
            for r in eligible
        ]
        try:
            parted = partition_into_two_fleets(partition_input)
        except FleetPartitionError as exc:
            raise HTTPException(400, exc.message) from exc
        a1_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["armada_1"]]
        a2_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["armada_2"]]
        if not a1_ids and "fleet_1" in parted:
            a1_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["fleet_1"]]
            a2_ids = [_text(s.get("id") if isinstance(s, dict) else s) for s in parted["fleet_2"]]
        assert_partition_invariants(eligible_ids, a1_ids, a2_ids)
        fleet_id_lists = [a1_ids, a2_ids]

    by_id = {_text(r.get("sales_order_id")): r for r in eligible}
    settings = await load_settings(db)
    wp = warehouse_point(settings)
    if wp:
        start_mode = START_WAREHOUSE
        start_location = None
    else:
        start_mode = START_MANUAL
        first = eligible[0]
        start_location = {
            "lat": first.get("lat"),
            "lng": first.get("lng"),
            "location_source": LOC_MANUAL,
        }

    labels = [FLEET_LABEL_1] if fleet_count_n == 1 else [FLEET_LABEL_1, FLEET_LABEL_2]
    fleet_group_id = None
    if fleet_count_n == 2:
        fleet_group_id = f"{FLEET_GROUP_ID_PREFIX}_{uuid.uuid4().hex}"

    created_ids: List[str] = []
    routes_out = []
    try:
        for label, ids in zip(labels, fleet_id_lists):
            rows = [by_id[i] for i in ids if i in by_id]
            if not rows:
                raise HTTPException(500, "Invariant gagal: armada kosong sebelum persist.")
            # Ensure none of these PO are already on an active delivery stop / route.
            active = await _active_delivery_stops_for_orders(db, [_text(r.get("sales_order_id")) for r in rows])
            if active:
                blocked = sorted({_text(s.get("sales_order_id")) for s in active if _text(s.get("sales_order_id"))})
                raise HTTPException(
                    409,
                    f"PO sudah berada pada rute aktif: {', '.join(blocked)}",
                )
            route = await store.create_route(
                db,
                {
                    "name": label,
                    "notes": "Dibuat dari Jadwal Pengiriman",
                    "start_mode": start_mode,
                    "start_location": start_location,
                    "end_mode": END_LAST,
                    "strategy": STRATEGY_NEAREST,
                    "source_type": SOURCE_DELIVERY_SCHEDULE,
                    "driver_user_id": None,
                    "driver_name": None,
                    "fleet_group_id": fleet_group_id,
                    "schedule_input_fingerprint": fingerprint,
                },
                actor,
            )
            route_id = route["id"]
            created_ids.append(route_id)
            stop_ids = []
            for item in rows:
                snap = item.get("planning_snapshot") or {
                    "lat": item.get("lat"),
                    "lng": item.get("lng"),
                    "address": item.get("address"),
                }
                added = await store.add_stop(
                    db,
                    route_id,
                    {
                        "display_name": item.get("customer_name") or item.get("order_number") or item["sales_order_id"],
                        "address": snap.get("address") or item.get("address") or "",
                        "ref_kind": REF_ROUTE_ONLY,
                        "lat": snap.get("lat") if snap.get("lat") is not None else item.get("lat"),
                        "lng": snap.get("lng") if snap.get("lng") is not None else item.get("lng"),
                        "location_source": LOC_MANUAL,
                        "priority_id": PRIORITY_ID_URGENT if item.get("urgent") else PRIORITY_ID_NORMAL,
                        "source_type": SOURCE_DELIVERY_SCHEDULE,
                        "sales_order_id": item["sales_order_id"],
                        "delivery_id": item["sales_order_id"],
                        "delivery_schedule_id": item["sales_order_id"],
                        "driver_user_id": None,
                        "order_number": item.get("order_number"),
                        "planning_snapshot": snap,
                    },
                    actor,
                    perms | {PERM_EDIT_OWN, PERM_CREATE, PERM_MANAGE},
                )
                stop_ids.append(added["stop"]["id"])
            if not stop_ids:
                raise HTTPException(500, "Invariant gagal: tidak ada stop yang tersimpan.")
            route_doc = await store._route_or_404(db, route_id)
            opt = await store.optimize(
                db,
                route_id,
                {"revision": route_doc.get("revision") or 1},
                actor,
                perms | {PERM_OPTIMIZE, PERM_MANAGE},
            )
            full = opt.get("route") or await store.get_route(
                db, route_id, actor, perms | {PERM_MANAGE, PERM_CREATE, PERM_OPTIMIZE, PERM_EDIT_OWN}
            )
            routes_out.append({
                "route_id": full["id"],
                "label": full.get("name") or label,
                "fleet_group_id": full.get("fleet_group_id") or fleet_group_id,
                "order_ids": [
                    _text(s.get("sales_order_id"))
                    for s in (full.get("stops") or [])
                    if _text(s.get("sales_order_id"))
                ],
                "stop_count": int(full.get("stop_count") or len(full.get("stops") or [])),
                "urgent_count": sum(
                    1 for s in (full.get("stops") or [])
                    if str(s.get("priority_id") or "") == PRIORITY_ID_URGENT
                ),
                "estimated_distance_m": full.get("estimated_distance_m"),
                "status": full.get("status"),
                "route": full,
            })
    except HTTPException:
        await _soft_delete_routes(db, created_ids, actor, perms)
        raise
    except Exception as exc:
        await _soft_delete_routes(db, created_ids, actor, perms)
        raise HTTPException(500, f"Gagal membuat rute armada: {exc}") from exc

    # Final union invariant
    seen = []
    for r in routes_out:
        for oid in r["order_ids"]:
            if oid in seen:
                await _soft_delete_routes(db, created_ids, actor, perms)
                raise HTTPException(500, "Invariant gagal: PO duplikat antar armada.")
            seen.append(oid)
        if not r["order_ids"]:
            await _soft_delete_routes(db, created_ids, actor, perms)
            raise HTTPException(500, "Invariant gagal: armada kosong setelah persist.")
    if set(seen) != set(eligible_ids):
        await _soft_delete_routes(db, created_ids, actor, perms)
        raise HTTPException(500, "Invariant gagal: PO eligible hilang dari rute tersimpan.")

    return {
        "ok": True,
        "created": True,
        "reused": False,
        "fleet_count": fleet_count_n,
        "created_route_count": len(routes_out),
        "fleet_group_id": fleet_group_id,
        "schedule_input_fingerprint": fingerprint,
        "eligible_order_ids": eligible_ids,
        "excluded_rows": ineligible,
        "routes": routes_out,
        "review": review,
        "summary": {
            **review["summary"],
            "eligible": len(eligible),
            "fleet_count": fleet_count_n,
            "created_route_count": len(routes_out),
            "reused": False,
        },
    }


LEGACY_MISSING_DRIVER_REASON = "Belum ada driver"
LEGACY_INVALID_DRIVER_REASON = "Driver pada order tidak valid"


async def _legacy_resolve_order_driver(db, driver_user_id: str) -> Optional[dict]:
    """Validate existing order driver only — never auto-select another driver."""
    uid = _text(driver_user_id)
    if not uid:
        return None
    try:
        return await resolve_eligible_driver(db, uid)
    except HTTPException:
        return None


async def create_and_optimize_from_schedule_legacy(
    db,
    *,
    order_ids: List[str],
    actor: dict,
    perms: set,
) -> dict:
    """Isolated compatibility path for POST /from-schedule/optimize.

    Preserves released `{order_ids}` request + driver-grouped response shape.
    Uses current SJ + coordinate safety. Uses existing order driver only —
    never auto-assigns, never rewrites source-order driver fields, never
    touches fleet_group_id route pairs.
    """
    if not (PERM_CREATE in perms or PERM_MANAGE in perms):
        raise HTTPException(403, "Anda tidak boleh membuat rute.")
    if PERM_OPTIMIZE not in perms and PERM_MANAGE not in perms:
        raise HTTPException(403, "Anda tidak boleh mengoptimasi rute.")

    review = await build_review_rows(db, order_ids)
    selected = {_text(i) for i in order_ids if _text(i)}

    # Response-local review copy: apply legacy driver gate without changing
    # shared eligibility used by non-persistent preview-fleets.
    rows_out: List[dict] = []
    driver_cache: Dict[str, Optional[dict]] = {}
    for r in review["rows"]:
        row = dict(r)
        reasons = list(row.get("reasons") or [])
        if row.get("eligible") and row.get("sales_order_id") in selected:
            did = _text(row.get("driver_user_id"))
            if not did:
                reasons = reasons + [LEGACY_MISSING_DRIVER_REASON]
                row["eligible"] = False
                row["reasons"] = reasons
                row["reason"] = reasons[0] if reasons else LEGACY_MISSING_DRIVER_REASON
            else:
                if did not in driver_cache:
                    driver_cache[did] = await _legacy_resolve_order_driver(db, did)
                if not driver_cache[did]:
                    reasons = reasons + [LEGACY_INVALID_DRIVER_REASON]
                    row["eligible"] = False
                    row["reasons"] = reasons
                    row["reason"] = reasons[0] if reasons else LEGACY_INVALID_DRIVER_REASON
        rows_out.append(row)

    eligible = [
        r for r in rows_out
        if r.get("eligible") and r.get("sales_order_id") in selected
    ]

    groups: Dict[str, List[dict]] = {}
    for r in eligible:
        groups.setdefault(_text(r.get("driver_user_id")), []).append(r)

    results = []
    any_fail = False
    for driver_id, items in groups.items():
        driver_doc = driver_cache.get(driver_id) or await _legacy_resolve_order_driver(db, driver_id)
        if not driver_doc:
            any_fail = True
            results.append({
                "driver_user_id": driver_id,
                "driver_name": items[0].get("driver_name"),
                "requested_order_ids": [i["sales_order_id"] for i in items],
                "ok": False,
                "route_id": None,
                "stop_ids": [],
                "error": LEGACY_INVALID_DRIVER_REASON,
                "skipped_already_optimized": [],
                "reoptimized_order_ids": [],
                "created_order_ids": [],
            })
            continue

        driver_name = _text(driver_doc.get("name")) or items[0].get("driver_name") or driver_id
        group_result = {
            "driver_user_id": driver_id,
            "driver_name": driver_name,
            "requested_order_ids": [i["sales_order_id"] for i in items],
            "ok": False,
            "route_id": None,
            "stop_ids": [],
            "error": None,
            "skipped_already_optimized": [],
            "skipped_fleet_pair": [],
            "reoptimized_order_ids": [],
            "created_order_ids": [],
        }
        try:
            to_create = []
            for item in items:
                status = item.get("optimization_status")
                rid = item.get("route_id")
                # Never mutate or pull stops from two-fleet pair routes.
                if rid:
                    route_meta = await db[COL_PLANS].find_one(
                        {"id": rid, **_active_route_filter()},
                        {"_id": 0, "id": 1, "fleet_group_id": 1},
                    )
                    if _text((route_meta or {}).get("fleet_group_id")):
                        group_result["skipped_fleet_pair"].append(item["sales_order_id"])
                        continue
                if status == OPT_STATUS_OPTIMIZED and item.get("route_id"):
                    group_result["skipped_already_optimized"].append(item["sales_order_id"])
                    continue
                if status == OPT_STATUS_STALE and item.get("route_id") and item.get("existing_stop_id"):
                    old_route_id = item["route_id"]
                    old_stop_id = item["existing_stop_id"]
                    try:
                        await store.delete_stop(
                            db, old_route_id, old_stop_id, actor, perms | {PERM_EDIT_OWN, PERM_MANAGE}
                        )
                    except HTTPException:
                        pass
                    group_result["reoptimized_order_ids"].append(item["sales_order_id"])
                    to_create.append(item)
                else:
                    to_create.append(item)

            if not to_create and (
                group_result["skipped_already_optimized"] or group_result["skipped_fleet_pair"]
            ):
                existing_route = items[0].get("route_id")
                group_result.update({
                    "ok": True,
                    "route_id": existing_route if group_result["skipped_already_optimized"] else None,
                    "message": "Semua pengiriman sudah dioptimasi; tidak ada duplikat.",
                })
                results.append(group_result)
                continue

            if not to_create:
                group_result["ok"] = True
                group_result["message"] = "Tidak ada stop baru."
                results.append(group_result)
                continue

            existing_active = await _active_delivery_stops_for_orders(
                db, [i["sales_order_id"] for i in to_create]
            )
            snap_by_order = {i["sales_order_id"]: i["planning_snapshot"] for i in to_create}
            active_orders = set()
            for s in existing_active:
                soid = _text(s.get("sales_order_id"))
                route = s.get("_route") or {}
                if _text(route.get("fleet_group_id")):
                    active_orders.add(soid)
                    group_result["skipped_fleet_pair"].append(soid)
                    continue
                if route.get("optimization_stale"):
                    continue
                if int(route.get("current_optimization_version") or 0) <= 0:
                    continue
                if snapshots_match(snap_by_order.get(soid) or {}, s.get("planning_snapshot")):
                    active_orders.add(soid)
            to_create = [i for i in to_create if i["sales_order_id"] not in active_orders]

            if not to_create:
                group_result["ok"] = True
                group_result["message"] = "Tidak ada stop baru (idempotent)."
                results.append(group_result)
                continue

            date_keys = sorted({
                i.get("effective_delivery_date") for i in to_create if i.get("effective_delivery_date")
            })
            date_label = date_keys[0] if len(date_keys) == 1 else (date_keys[0] + "+" if date_keys else None)
            settings = await load_settings(db)
            wp = warehouse_point(settings)
            if wp:
                start_mode = "WAREHOUSE"
                start_location = None
            else:
                first = to_create[0]["planning_snapshot"]
                start_mode = "MANUAL"
                start_location = {
                    "lat": first.get("lat"),
                    "lng": first.get("lng"),
                    "location_source": LOC_MANUAL,
                }
            route = await store.create_route(
                db,
                {
                    "name": _route_name(driver_name, date_label, driver_id),
                    "route_date": date_keys[0] if date_keys else None,
                    "notes": "Dibuat dari Jadwal Pengiriman",
                    "start_mode": start_mode,
                    "start_location": start_location,
                    "end_mode": "LAST_STOP",
                    "strategy": "NEAREST",
                    "source_type": SOURCE_DELIVERY_SCHEDULE,
                    "driver_user_id": driver_id,
                    "driver_name": driver_name,
                },
                actor,
            )
            route_id = route["id"]
            stop_ids = []
            for item in to_create:
                again = await _active_delivery_stops_for_orders(db, [item["sales_order_id"]])
                if again:
                    group_result["skipped_already_optimized"].append(item["sales_order_id"])
                    continue
                snap = item["planning_snapshot"]
                added = await store.add_stop(
                    db,
                    route_id,
                    {
                        "display_name": item.get("customer_name") or item.get("order_number") or item["sales_order_id"],
                        "address": snap.get("address") or "",
                        "ref_kind": REF_ROUTE_ONLY,
                        "lat": snap.get("lat"),
                        "lng": snap.get("lng"),
                        "location_source": LOC_MANUAL,
                        "priority_id": "p3",
                        "source_type": SOURCE_DELIVERY_SCHEDULE,
                        "sales_order_id": item["sales_order_id"],
                        "delivery_id": item["sales_order_id"],
                        "delivery_schedule_id": item["sales_order_id"],
                        "driver_user_id": driver_id,
                        "order_number": item.get("order_number"),
                        "planning_snapshot": snap,
                    },
                    actor,
                    perms | {PERM_EDIT_OWN, PERM_CREATE, PERM_MANAGE},
                )
                stop_ids.append(added["stop"]["id"])
                group_result["created_order_ids"].append(item["sales_order_id"])

            if stop_ids:
                opt = await store.optimize(
                    db,
                    route_id,
                    {"revision": (await store._route_or_404(db, route_id)).get("revision") or 1},
                    actor,
                    perms | {PERM_OPTIMIZE, PERM_MANAGE},
                )
                group_result["route_id"] = route_id
                group_result["stop_ids"] = stop_ids
                group_result["ok"] = True
                group_result["optimized"] = True
                group_result["route"] = opt.get("route")
            else:
                await store.soft_delete_route(db, route_id, actor, perms | {PERM_MANAGE, PERM_EDIT_OWN})
                group_result["ok"] = True
                group_result["message"] = "Tidak ada stop baru (idempotent)."
                group_result["route_id"] = None
        except HTTPException as exc:
            any_fail = True
            group_result["ok"] = False
            group_result["error"] = str(exc.detail)
        except Exception as exc:  # noqa: BLE001 — surface group failure without crashing others
            any_fail = True
            group_result["ok"] = False
            group_result["error"] = str(exc)
        results.append(group_result)

    eligible_out = [r for r in rows_out if r.get("eligible")]
    groups_map: Dict[str, list] = {}
    for r in eligible_out:
        did = _text(r.get("driver_user_id"))
        if not did:
            continue
        groups_map.setdefault(did, []).append(r)
    groups_out = []
    for driver_id, items in groups_map.items():
        groups_out.append({
            "driver_user_id": driver_id,
            "driver_name": items[0].get("driver_name"),
            "count": len(items),
            "sales_order_ids": [i["sales_order_id"] for i in items],
        })
    groups_out.sort(key=lambda g: (g.get("driver_name") or "", g["driver_user_id"]))
    review_out = {
        "rows": rows_out,
        "summary": {
            "selected": len(rows_out),
            "eligible": len(eligible_out),
            "ineligible": len(rows_out) - len(eligible_out),
            "drivers": len(groups_out),
            "routes_to_create": len(groups_out),
        },
        "groups": groups_out,
    }

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": (not any_fail) and ok_count == len(results),
        "partial": any_fail and ok_count > 0,
        "summary": {
            **review_out["summary"],
            "groups_attempted": len(results),
            "groups_ok": ok_count,
            "groups_failed": len(results) - ok_count,
        },
        "results": results,
        "review": review_out,
    }


async def create_and_optimize_from_schedule(
    db,
    *,
    order_ids: List[str],
    actor: dict,
    perms: set,
) -> dict:
    """Compatibility alias — isolated legacy optimize only."""
    return await create_and_optimize_from_schedule_legacy(
        db,
        order_ids=order_ids,
        actor=actor,
        perms=perms,
    )
