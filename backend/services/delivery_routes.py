"""Route plan lifecycle: draft → edit → confirm → start. Append-safe versions.

Optimization is always at delivery-batch level (all stops + all ready vehicles).
Confirmed assignments are official driver routes. Start does not begin GPS.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.delivery_batches import actor_id, actor_name, now_iso, ready_vehicles
from services.delivery_depot import normalize_depot
from services.delivery_stops import LOCATION_NEED
from services.route_optimizer.types import (
    OptimizeRequest,
    OptimizeResult,
    OptimizerDepot,
    OptimizerStop,
    OptimizerVehicle,
)

ROUTE_DRAFT = "draft"
ROUTE_TERJADWAL = "terjadwal"
ROUTE_BERJALAN = "berjalan"
ROUTE_SELESAI = "selesai"
ROUTE_DIBATALKAN = "dibatalkan"

ROUTE_STATUS_LABELS = {
    ROUTE_DRAFT: "DRAFT",
    ROUTE_TERJADWAL: "TERJADWAL",
    ROUTE_BERJALAN: "BERJALAN",
    ROUTE_SELESAI: "SELESAI",
    ROUTE_DIBATALKAN: "DIBATALKAN",
}

KIND_OPTIMIZED_DRAFT = "optimized_draft"
KIND_MANUAL_EDIT = "manual_edit"
KIND_REOPTIMIZED = "reoptimized"
KIND_CONFIRMED = "confirmed"
KIND_STARTED = "started"

ASSIGNMENT_SCHEDULED = "terjadwal"
ASSIGNMENT_RUNNING = "berjalan"
ASSIGNMENT_DONE = "selesai"

STOP_QUEUED = "queued"
STOP_ASSIGNED = "assigned"
STOP_UNASSIGNED = "unassigned"
STOP_DELIVERED = "delivered"
STOP_REMOVED = "removed"
STOP_RESCHEDULED = "rescheduled"


class RouteError(ValueError):
    pass


def _stop_map(batch: dict) -> Dict[str, dict]:
    return {str(s.get("stop_id")): s for s in (batch.get("stops") or []) if s.get("stop_id")}


def build_optimize_request(batch: dict, depot: dict, remaining_only: bool = False) -> OptimizeRequest:
    dep = normalize_depot(depot)
    stops = []
    for st in batch.get("stops") or []:
        status = str(st.get("status") or STOP_QUEUED)
        if remaining_only and status in (STOP_DELIVERED, STOP_REMOVED, STOP_RESCHEDULED):
            continue
        stops.append(OptimizerStop(
            stop_id=str(st.get("stop_id")),
            lat=st.get("lat"),
            lng=st.get("lng"),
            place_id=st.get("place_id"),
            qty=int(st.get("qty") or 0),
            package_count=st.get("package_count"),
            priority=st.get("priority") or "normal",
            outlet_closing_time=st.get("outlet_closing_time"),
            deadline=st.get("deadline"),
            customer=st.get("customer") or "",
            address=st.get("address") or "",
        ))
    vehicles = []
    for r in ready_vehicles(batch):
        vehicles.append(OptimizerVehicle(
            vehicle_id=str(r.get("vehicle_id")),
            driver_user_id=str(r.get("driver_user_id")),
            driver_name=str(r.get("driver_name") or ""),
            plate=str(r.get("vehicle_plate") or ""),
            name=str(r.get("vehicle_name") or ""),
            capacity_dus=r.get("capacity_dus"),
            start_lat=dep.get("lat"),
            start_lng=dep.get("lng"),
            end_lat=dep.get("lat"),
            end_lng=dep.get("lng"),
        ))
    return OptimizeRequest(
        batch_id=str(batch.get("id") or ""),
        delivery_date=str(batch.get("delivery_date") or ""),
        depot=OptimizerDepot(
            name=dep.get("name") or "",
            lat=dep.get("lat"),
            lng=dep.get("lng"),
            place_id=dep.get("place_id"),
            address=dep.get("address") or "",
        ),
        stops=stops,
        vehicles=vehicles,
    )


def _assignment_from_vehicle_route(vr, stop_lookup: dict) -> dict:
    stops = []
    for ast in vr.stops:
        src = dict(stop_lookup.get(ast.stop_id) or {})
        src["status"] = STOP_ASSIGNED
        src["sequence"] = ast.sequence
        src["eta"] = ast.eta
        src["infeasible"] = bool(ast.infeasible)
        src["infeasible_reason"] = ast.infeasible_reason
        src["unassigned_reason"] = None
        stops.append(src)
    return {
        "assignment_id": str(uuid.uuid4()),
        "driver_user_id": vr.driver_user_id,
        "driver_name": vr.driver_name,
        "vehicle_id": vr.vehicle_id,
        "vehicle_plate": vr.plate,
        "vehicle_name": vr.vehicle_name,
        "capacity_dus": None,
        "stops": stops,
        "distance_meters": int(vr.distance_meters or 0),
        "duration_seconds": int(vr.duration_seconds or 0),
        "total_qty": int(vr.total_qty or 0),
        "total_dus": vr.total_dus,
        "geometry": list(vr.geometry or []),
        "polyline": vr.polyline,
        "status": ROUTE_DRAFT,
        "started_at": None,
        "started_by_id": None,
        "current_stop_index": 0,
    }


def apply_optimize_result(
    batch: dict,
    result: OptimizeResult,
    *,
    user: dict,
    kind: str = KIND_OPTIMIZED_DRAFT,
    existing_plan: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    lookup = _stop_map(batch)
    assigned_ids = set()
    routes = []
    for vr in result.routes:
        row = _assignment_from_vehicle_route(vr, lookup)
        for s in row["stops"]:
            assigned_ids.add(s["stop_id"])
        routes.append(row)

    unassigned = []
    seen_un = set()
    for u in result.unassigned:
        src = dict(lookup.get(u.stop_id) or {"stop_id": u.stop_id})
        src["status"] = STOP_UNASSIGNED
        src["unassigned_reason"] = u.reason
        src["sequence"] = None
        unassigned.append(src)
        seen_un.add(u.stop_id)

    for st in batch.get("stops") or []:
        sid = st.get("stop_id")
        if sid in assigned_ids or sid in seen_un:
            continue
        src = dict(st)
        if src.get("location_status") == LOCATION_NEED:
            src["status"] = STOP_UNASSIGNED
            src["unassigned_reason"] = "Belum Ada Lokasi"
        else:
            src["status"] = STOP_UNASSIGNED
            src["unassigned_reason"] = "Tidak dapat di-assign"
        unassigned.append(src)
        seen_un.add(sid)

    totals = {
        "distance_meters": sum(int(r.get("distance_meters") or 0) for r in routes),
        "duration_seconds": sum(int(r.get("duration_seconds") or 0) for r in routes),
        "stop_count": len(assigned_ids),
        "qty": sum(int(r.get("total_qty") or 0) for r in routes),
        "dus": sum(int(r.get("total_dus") or 0) for r in routes) or None,
        "vehicle_count": len([r for r in routes if r.get("stops")]),
    }
    ts = now_iso(now)
    version_no = int((existing_plan or {}).get("version") or 0) + 1
    snapshot = {
        "routes": routes,
        "unassigned": unassigned,
        "totals": totals,
        "provider": result.provider,
        "warnings": list(result.warnings or []),
    }
    history = list((existing_plan or {}).get("versions") or [])
    history.append({
        "version": version_no,
        "kind": kind,
        "created_at": ts,
        "created_by_id": actor_id(user),
        "created_by_name": actor_name(user),
        "snapshot": snapshot,
    })
    plan_id = str((existing_plan or {}).get("id") or uuid.uuid4())
    status = ROUTE_DRAFT
    if existing_plan and existing_plan.get("status") in (ROUTE_TERJADWAL, ROUTE_BERJALAN) and kind == KIND_REOPTIMIZED:
        status = existing_plan.get("status")
    return {
        "id": plan_id,
        "batch_id": batch.get("id"),
        "delivery_date": batch.get("delivery_date"),
        "status": status,
        "status_label": ROUTE_STATUS_LABELS.get(status, status),
        "version": version_no,
        "versions": history,
        "routes": routes,
        "unassigned": unassigned,
        "totals": totals,
        "provider": result.provider,
        "warnings": list(result.warnings or []),
        "confirmed_at": (existing_plan or {}).get("confirmed_at"),
        "confirmed_by_id": (existing_plan or {}).get("confirmed_by_id"),
        "confirmed_by_name": (existing_plan or {}).get("confirmed_by_name"),
        "updated_at": ts,
    }


def _append_version(plan: dict, kind: str, user: dict, now: Optional[datetime] = None) -> None:
    ts = now_iso(now)
    n = int(plan.get("version") or 0) + 1
    plan["version"] = n
    hist = list(plan.get("versions") or [])
    hist.append({
        "version": n,
        "kind": kind,
        "created_at": ts,
        "created_by_id": actor_id(user),
        "created_by_name": actor_name(user),
        "snapshot": {
            "routes": plan.get("routes"),
            "unassigned": plan.get("unassigned"),
            "totals": plan.get("totals"),
        },
    })
    plan["versions"] = hist
    plan["updated_at"] = ts


def _recompute_totals(plan: dict) -> None:
    routes = plan.get("routes") or []
    assigned = 0
    qty = 0
    dus = 0
    has_dus = False
    for r in routes:
        r["total_qty"] = sum(int(s.get("qty") or 0) for s in r.get("stops") or [])
        dvals = [s.get("package_count") for s in r.get("stops") or [] if s.get("package_count") is not None]
        r["total_dus"] = sum(int(x) for x in dvals) if dvals else None
        assigned += len(r.get("stops") or [])
        qty += int(r.get("total_qty") or 0)
        if r.get("total_dus") is not None:
            has_dus = True
            dus += int(r["total_dus"])
    plan["totals"] = {
        "distance_meters": sum(int(r.get("distance_meters") or 0) for r in routes),
        "duration_seconds": sum(int(r.get("duration_seconds") or 0) for r in routes),
        "stop_count": assigned,
        "qty": qty,
        "dus": dus if has_dus else None,
        "vehicle_count": len([r for r in routes if r.get("stops")]),
    }


def edit_draft(plan: dict, payload: dict, user: dict) -> dict:
    """Reorder / move / remove / assign unassigned. Never mutates delivered history stops."""
    if plan.get("status") not in (ROUTE_DRAFT,):
        raise RouteError("Hanya draf rute yang bisa diubah. Jadwal terkonfirmasi tidak diubah diam-diam.")
    routes = list(plan.get("routes") or [])
    unassigned = list(plan.get("unassigned") or [])
    by_assign = {r.get("assignment_id"): r for r in routes}

    def find_stop(stop_id: str) -> Tuple[Optional[dict], Optional[str]]:
        for r in routes:
            for s in r.get("stops") or []:
                if s.get("stop_id") == stop_id:
                    return s, r.get("assignment_id")
        for s in unassigned:
            if s.get("stop_id") == stop_id:
                return s, None
        return None, None

    for move in payload.get("moves") or []:
        sid = str(move.get("stop_id") or "").strip()
        target = str(move.get("assignment_id") or "").strip() or None
        st, src_aid = find_stop(sid)
        if not st:
            continue
        if st.get("status") == STOP_DELIVERED:
            raise RouteError("Stop yang sudah terkirim tidak dapat diubah.")
        if src_aid:
            src = by_assign.get(src_aid)
            src["stops"] = [s for s in (src.get("stops") or []) if s.get("stop_id") != sid]
        else:
            unassigned = [s for s in unassigned if s.get("stop_id") != sid]
        if not target:
            st = dict(st)
            st["status"] = STOP_UNASSIGNED
            st["unassigned_reason"] = move.get("reason") or "Dilepas dari draf"
            st["sequence"] = None
            unassigned.append(st)
            continue
        dest = by_assign.get(target)
        if not dest:
            raise RouteError("Kendaraan tujuan tidak ditemukan.")
        st = dict(st)
        st["status"] = STOP_ASSIGNED
        st["unassigned_reason"] = None
        dest.setdefault("stops", []).append(st)

    for reorder in payload.get("reorder") or []:
        aid = str(reorder.get("assignment_id") or "").strip()
        order = [str(x) for x in (reorder.get("stop_ids") or [])]
        dest = by_assign.get(aid)
        if not dest:
            continue
        current = {s.get("stop_id"): s for s in dest.get("stops") or []}
        next_stops = []
        for i, sid in enumerate(order, start=1):
            if sid not in current:
                continue
            s = dict(current[sid])
            if s.get("status") == STOP_DELIVERED:
                next_stops.append(current[sid])
                continue
            s["sequence"] = i
            next_stops.append(s)
        for sid, s in current.items():
            if sid not in order:
                next_stops.append(s)
        for i, s in enumerate(next_stops, start=1):
            s["sequence"] = i
        dest["stops"] = next_stops

    for r in routes:
        for i, s in enumerate(r.get("stops") or [], start=1):
            s["sequence"] = i
    plan["routes"] = routes
    plan["unassigned"] = unassigned
    _recompute_totals(plan)
    _append_version(plan, KIND_MANUAL_EDIT, user)
    return plan


def confirm_plan(plan: dict, user: dict, now: Optional[datetime] = None) -> dict:
    if plan.get("status") != ROUTE_DRAFT:
        raise RouteError("Hanya draf yang dapat dikonfirmasi menjadi jadwal.")
    ts = now_iso(now)
    plan["status"] = ROUTE_TERJADWAL
    plan["status_label"] = ROUTE_STATUS_LABELS[ROUTE_TERJADWAL]
    plan["confirmed_at"] = ts
    plan["confirmed_by_id"] = actor_id(user)
    plan["confirmed_by_name"] = actor_name(user)
    for r in plan.get("routes") or []:
        r["status"] = ASSIGNMENT_SCHEDULED
    _append_version(plan, KIND_CONFIRMED, user, now)
    return plan


def start_assignment(plan: dict, *, driver_user_id: str, user: dict, now: Optional[datetime] = None) -> dict:
    uid = str(driver_user_id or "").strip()
    if plan.get("status") not in (ROUTE_TERJADWAL, ROUTE_BERJALAN):
        raise RouteError("Rute belum dikonfirmasi.")
    found = None
    for r in plan.get("routes") or []:
        if str(r.get("driver_user_id") or "") == uid:
            found = r
            break
    if not found:
        raise RouteError("Anda tidak memiliki assignment pada rute ini.")
    if found.get("status") == ASSIGNMENT_RUNNING:
        return plan
    ts = now_iso(now)
    found["status"] = ASSIGNMENT_RUNNING
    found["started_at"] = ts
    found["started_by_id"] = uid
    plan["status"] = ROUTE_BERJALAN
    plan["status_label"] = ROUTE_STATUS_LABELS[ROUTE_BERJALAN]
    _append_version(plan, KIND_STARTED, user, now)
    return plan


def assignment_for_driver(plan: dict, driver_user_id: str) -> Optional[dict]:
    uid = str(driver_user_id or "").strip()
    for r in plan.get("routes") or []:
        if str(r.get("driver_user_id") or "") == uid:
            return r
    return None


def public_plan(plan: dict, *, driver_user_id: Optional[str] = None) -> dict:
    """If driver_user_id set, only that assignment is returned."""
    routes = list(plan.get("routes") or [])
    if driver_user_id:
        routes = [r for r in routes if str(r.get("driver_user_id") or "") == str(driver_user_id)]
    return {
        "id": plan.get("id"),
        "batch_id": plan.get("batch_id"),
        "delivery_date": plan.get("delivery_date"),
        "status": plan.get("status"),
        "status_label": plan.get("status_label") or ROUTE_STATUS_LABELS.get(plan.get("status") or "", plan.get("status")),
        "version": plan.get("version"),
        "routes": routes,
        "unassigned": [] if driver_user_id else list(plan.get("unassigned") or []),
        "totals": plan.get("totals") if not driver_user_id else _driver_totals(routes),
        "provider": plan.get("provider"),
        "warnings": plan.get("warnings") or [],
        "confirmed_at": plan.get("confirmed_at"),
        "depot": plan.get("depot"),
        "updated_at": plan.get("updated_at"),
    }


def _driver_totals(routes: List[dict]) -> dict:
    r = routes[0] if routes else {}
    return {
        "distance_meters": int(r.get("distance_meters") or 0),
        "duration_seconds": int(r.get("duration_seconds") or 0),
        "stop_count": len(r.get("stops") or []),
        "qty": int(r.get("total_qty") or 0),
        "dus": r.get("total_dus"),
        "vehicle_count": 1 if routes else 0,
    }


def remaining_stops(assignment: dict) -> List[dict]:
    out = []
    for s in assignment.get("stops") or []:
        if s.get("status") in (STOP_DELIVERED, STOP_REMOVED, STOP_RESCHEDULED):
            continue
        out.append(s)
    return out


def current_stop(assignment: dict) -> Optional[dict]:
    rem = remaining_stops(assignment)
    return rem[0] if rem else None


def mark_stop_delivered(plan: dict, *, order_id: str, shipment_id: Optional[str] = None) -> bool:
    changed = False
    oid = str(order_id or "")
    sid = str(shipment_id or "").strip() or None
    all_done = True
    any_running = False
    for r in plan.get("routes") or []:
        for s in r.get("stops") or []:
            if str(s.get("order_id") or "") != oid:
                continue
            if sid and str(s.get("shipment_id") or "") not in ("", sid):
                continue
            if s.get("status") != STOP_DELIVERED:
                s["status"] = STOP_DELIVERED
                changed = True
        rem = remaining_stops(r)
        if rem:
            all_done = False
            if r.get("status") == ASSIGNMENT_RUNNING:
                any_running = True
            r["current_stop_index"] = next(
                (i for i, s in enumerate(r.get("stops") or []) if s.get("status") not in (STOP_DELIVERED, STOP_REMOVED, STOP_RESCHEDULED)),
                len(r.get("stops") or []),
            )
        else:
            if r.get("status") != ASSIGNMENT_DONE:
                r["status"] = ASSIGNMENT_DONE
                changed = True
    if all_done and (plan.get("routes") or []):
        if plan.get("status") != ROUTE_SELESAI:
            plan["status"] = ROUTE_SELESAI
            plan["status_label"] = ROUTE_STATUS_LABELS[ROUTE_SELESAI]
            changed = True
    elif any_running:
        plan["status"] = ROUTE_BERJALAN
        plan["status_label"] = ROUTE_STATUS_LABELS[ROUTE_BERJALAN]
    return changed


def mark_stop_rescheduled(plan: dict, *, order_id: str, shipment_id: Optional[str] = None, route_started: bool) -> bool:
    """Rescheduled stop stays visible. If route not started, return to unassigned queue semantics."""
    changed = False
    oid = str(order_id or "")
    sid = str(shipment_id or "").strip() or None
    for r in plan.get("routes") or []:
        keep = []
        for s in r.get("stops") or []:
            match = str(s.get("order_id") or "") == oid and (
                not sid or str(s.get("shipment_id") or "") in ("", sid)
            )
            if not match:
                keep.append(s)
                continue
            if s.get("status") == STOP_DELIVERED:
                keep.append(s)
                continue
            s = dict(s)
            s["status"] = STOP_RESCHEDULED
            if not route_started:
                s["unassigned_reason"] = "Dijadwalkan ulang"
                plan.setdefault("unassigned", []).append(s)
            else:
                keep.append(s)
            changed = True
        if changed:
            r["stops"] = keep
            for i, s in enumerate(r.get("stops") or [], start=1):
                s["sequence"] = i
    if changed:
        _recompute_totals(plan)
    return changed


def my_route_view(plan: dict, assignment: dict, user: dict) -> dict:
    stops = list(assignment.get("stops") or [])
    remaining = remaining_stops(assignment)
    cur = current_stop(assignment)
    return {
        "plan_id": plan.get("id"),
        "batch_id": plan.get("batch_id"),
        "assignment_id": assignment.get("assignment_id"),
        "status": assignment.get("status") or plan.get("status"),
        "status_label": ROUTE_STATUS_LABELS.get(assignment.get("status") or plan.get("status") or "", ""),
        "delivery_date": plan.get("delivery_date"),
        "driver_user_id": assignment.get("driver_user_id"),
        "driver_name": user.get("name") or assignment.get("driver_name"),
        "vehicle_name": assignment.get("vehicle_name"),
        "vehicle_plate": assignment.get("vehicle_plate"),
        "stop_count": len(stops),
        "remaining_count": len(remaining),
        "distance_meters": assignment.get("distance_meters"),
        "duration_seconds": assignment.get("duration_seconds"),
        "current_stop_id": (cur or {}).get("stop_id"),
        "started_at": assignment.get("started_at"),
        "stops": [
            {
                "stop_id": s.get("stop_id"),
                "sequence": s.get("sequence"),
                "customer": s.get("customer"),
                "address": s.get("address"),
                "order_number": s.get("order_number"),
                "order_id": s.get("order_id"),
                "shipment_id": s.get("shipment_id"),
                "qty": s.get("qty"),
                "outlet_closing_time": s.get("outlet_closing_time"),
                "priority": s.get("priority"),
                "priority_label": s.get("priority_label"),
                "status": s.get("status"),
                "lat": s.get("lat"),
                "lng": s.get("lng"),
                "place_id": s.get("place_id"),
                "eta": s.get("eta"),
            }
            for s in stops
        ],
    }


def confirm_summary(plan: dict, batch: dict) -> dict:
    t = plan.get("totals") or {}
    routes = [r for r in (plan.get("routes") or []) if r.get("stops")]
    return {
        "delivery_date": plan.get("delivery_date") or batch.get("delivery_date"),
        "vehicle_count": t.get("vehicle_count") if t.get("vehicle_count") is not None else len(routes),
        "stop_count": t.get("stop_count") or 0,
        "qty": t.get("qty") or 0,
        "dus": t.get("dus"),
        "distance_meters": t.get("distance_meters") or 0,
        "duration_seconds": t.get("duration_seconds") or 0,
        "unassigned_count": len(plan.get("unassigned") or []),
    }
