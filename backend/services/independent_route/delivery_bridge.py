"""Bridge: Jadwal Pengiriman → independent Route Optimizer.

Creates delivery-linked routes/stops inside independent_route_* collections.
Does NOT revive legacy delivery batches / Rute Saya.
Does NOT mutate Pengiriman / SJ / Packing / Loading / PO workflow data.
"""
from __future__ import annotations

import hashlib
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
)
from services.independent_route.geo import is_valid_coordinate
from services.independent_route.settings import now_iso

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
    if _text(current.get("driver_user_id")) != _text(snap.get("driver_user_id")):
        return False
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
        {"_id": 0, "id": 1, "status": 1, "optimization_stale": 1, "current_optimization_version": 1, "deleted_at": 1},
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


def eligibility_for_order(order: dict, loc: dict, *, existing_stop: Optional[dict] = None) -> dict:
    """Return eligibility without inventing coords or drivers."""
    oid = _text(order.get("id"))
    driver_id = canonical_driver_user_id(order)
    delivery_status = _text(order.get("delivery_status"))
    reasons: List[str] = []
    if delivery_status.lower() == "terkirim":
        reasons.append("Sudah terkirim")
    if not driver_id:
        reasons.append("Belum ada driver")
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
        groups_map.setdefault(r["driver_user_id"], []).append(r)
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


async def create_and_optimize_from_schedule(
    db,
    *,
    order_ids: List[str],
    actor: dict,
    perms: set,
) -> dict:
    """Mutation: create one route per driver group and optimize via existing store."""
    if not (PERM_CREATE in perms or PERM_MANAGE in perms):
        raise HTTPException(403, "Anda tidak boleh membuat rute.")
    if PERM_OPTIMIZE not in perms and PERM_MANAGE not in perms:
        raise HTTPException(403, "Anda tidak boleh mengoptimasi rute.")

    review = await build_review_rows(db, order_ids)
    # Only process rows that were requested AND currently eligible.
    selected = {_text(i) for i in order_ids if _text(i)}
    eligible = [r for r in review["rows"] if r.get("eligible") and r["sales_order_id"] in selected]

    # Group by exact driver_user_id
    groups: Dict[str, List[dict]] = {}
    for r in eligible:
        groups.setdefault(r["driver_user_id"], []).append(r)

    results = []
    any_fail = False
    for driver_id, items in groups.items():
        group_result = {
            "driver_user_id": driver_id,
            "driver_name": items[0].get("driver_name"),
            "requested_order_ids": [i["sales_order_id"] for i in items],
            "ok": False,
            "route_id": None,
            "stop_ids": [],
            "error": None,
            "skipped_already_optimized": [],
            "reoptimized_order_ids": [],
            "created_order_ids": [],
        }
        try:
            # Idempotency: separate already-valid from stale/missing
            to_create = []
            for item in items:
                status = item.get("optimization_status")
                if status == OPT_STATUS_OPTIMIZED and item.get("route_id"):
                    group_result["skipped_already_optimized"].append(item["sales_order_id"])
                    continue
                if status == OPT_STATUS_STALE and item.get("route_id") and item.get("existing_stop_id"):
                    # Controlled re-optimization: remove old stop linkage then recreate in this group route.
                    old_route_id = item["route_id"]
                    old_stop_id = item["existing_stop_id"]
                    try:
                        await store.delete_stop(db, old_route_id, old_stop_id, actor, perms | {PERM_EDIT_OWN, PERM_MANAGE})
                    except HTTPException:
                        # If cannot edit old route (ownership), fall through to create new — but still protect duplicates via check.
                        pass
                    group_result["reoptimized_order_ids"].append(item["sales_order_id"])
                    to_create.append(item)
                else:
                    to_create.append(item)

            if not to_create and group_result["skipped_already_optimized"]:
                # All already optimized — success with existing route ids
                existing_route = items[0].get("route_id")
                group_result.update({
                    "ok": True,
                    "route_id": existing_route,
                    "message": "Semua pengiriman sudah dioptimasi; tidak ada duplikat.",
                })
                results.append(group_result)
                continue

            if not to_create:
                group_result["ok"] = True
                group_result["message"] = "Tidak ada stop baru."
                results.append(group_result)
                continue

            # Final duplicate guard before insert (exact active valid linkage).
            existing_active = await _active_delivery_stops_for_orders(
                db, [i["sales_order_id"] for i in to_create]
            )
            snap_by_order = {i["sales_order_id"]: i["planning_snapshot"] for i in to_create}
            active_orders = set()
            for s in existing_active:
                soid = _text(s.get("sales_order_id"))
                route = s.get("_route") or {}
                if route.get("optimization_stale"):
                    continue
                if int(route.get("current_optimization_version") or 0) <= 0:
                    continue
                if snapshots_match(snap_by_order.get(soid) or {}, s.get("planning_snapshot")):
                    active_orders.add(soid)
            to_create = [i for i in to_create if i["sales_order_id"] not in active_orders]

            date_keys = sorted({i.get("effective_delivery_date") for i in to_create if i.get("effective_delivery_date")})
            date_label = date_keys[0] if len(date_keys) == 1 else (date_keys[0] + "+" if date_keys else None)
            # Prefer warehouse when configured; otherwise MANUAL start at first stop coords.
            settings = await store.load_settings(db) if hasattr(store, "load_settings") else None
            from services.independent_route.settings import load_settings, warehouse_point
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
                    "name": _route_name(items[0].get("driver_name"), date_label, driver_id),
                    "route_date": date_keys[0] if date_keys else None,
                    "notes": "Dibuat dari Jadwal Pengiriman",
                    "start_mode": start_mode,
                    "start_location": start_location,
                    "end_mode": "LAST_STOP",
                    "strategy": "NEAREST",
                    "source_type": SOURCE_DELIVERY_SCHEDULE,
                    "driver_user_id": driver_id,
                    "driver_name": items[0].get("driver_name"),
                },
                actor,
            )
            route_id = route["id"]
            stop_ids = []
            for item in to_create:
                # Race-safe: skip if another active stop appeared
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
                # Created empty route — soft-delete it to avoid orphans
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

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": (not any_fail) and ok_count == len(results),
        "partial": any_fail and ok_count > 0,
        "summary": {
            **review["summary"],
            "groups_attempted": len(results),
            "groups_ok": ok_count,
            "groups_failed": len(results) - ok_count,
        },
        "results": results,
        "review": review,
    }
