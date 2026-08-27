"""Deterministic local mock optimizer.

Used by automated tests and local preview when Google is not configured.
Never called as a silent production fallback.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from services.route_optimizer.base import RouteOptimizerProvider
from services.route_optimizer.types import (
    AssignedStop,
    OptimizeRequest,
    OptimizeResult,
    UnassignedStop,
    VehicleRoute,
)

WIB = timezone(timedelta(hours=7))
SPEED_KMH = 30.0
SERVICE_SECONDS = 5 * 60
PRIORITY_RANK = {"mendesak": 0, "prioritas": 1, "normal": 2}


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _road_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return haversine_m(a[0], a[1], b[0], b[1]) * 1.3


def _travel_s(meters: float) -> int:
    km = meters / 1000.0
    return int(km / SPEED_KMH * 3600) if km > 0 else 0


def _parse_hhmm(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value or len(value) < 5:
        return None
    try:
        return int(value[:2]), int(value[3:5])
    except ValueError:
        return None


def _day_start(date_key: str) -> datetime:
    try:
        d = datetime.strptime(date_key[:10], "%Y-%m-%d").date()
    except ValueError:
        d = datetime.now(WIB).date()
    return datetime(d.year, d.month, d.day, 8, 0, tzinfo=WIB)


class LocalMockRouteOptimizer(RouteOptimizerProvider):
    name = "mock"

    def is_configured(self) -> bool:
        return True

    def optimize(self, request: OptimizeRequest) -> OptimizeResult:
        unassigned: List[UnassignedStop] = []
        loc_ok = []
        for st in request.stops:
            if not st.has_location():
                unassigned.append(UnassignedStop(
                    stop_id=st.stop_id,
                    reason="Lokasi outlet belum diatur",
                    code="missing_location",
                ))
            else:
                loc_ok.append(st)

        vehicles = [v for v in request.vehicles if v.vehicle_id]
        if not vehicles:
            for st in loc_ok:
                unassigned.append(UnassignedStop(
                    stop_id=st.stop_id,
                    reason="Tidak ada kendaraan",
                    code="no_vehicle",
                ))
            return OptimizeResult(routes=[], unassigned=unassigned, provider=self.name)

        depot_pt = None
        if request.depot.has_location():
            depot_pt = (float(request.depot.lat), float(request.depot.lng))

        loc_ok.sort(key=lambda s: (
            PRIORITY_RANK.get(s.priority or "normal", 9),
            s.outlet_closing_time or "99:99",
            s.stop_id,
        ))
        vehicles = sorted(vehicles, key=lambda v: (v.vehicle_id, v.driver_user_id))

        buckets = [{
            "vehicle": v,
            "stops": [],
            "dus": 0,
            "qty": 0,
            "meters": 0,
            "seconds": 0,
            "cursor": depot_pt or (float(loc_ok[0].lat), float(loc_ok[0].lng)) if loc_ok else None,
            "t": _day_start(request.delivery_date),
            "geometry": list(
                [{"lat": depot_pt[0], "lng": depot_pt[1]}] if depot_pt else []
            ),
        } for v in vehicles]

        def pick_bucket(stop, demand: Optional[int]):
            best = None
            best_key = None
            for b in buckets:
                cap = b["vehicle"].capacity_dus
                if demand is not None and cap is not None and (b["dus"] + demand) > cap:
                    continue
                dist = 0.0
                if b["cursor"] and stop.has_location():
                    dist = _road_m(b["cursor"], (float(stop.lat), float(stop.lng)))
                key = (len(b["stops"]), dist, b["vehicle"].vehicle_id)
                if best_key is None or key < best_key:
                    best = b
                    best_key = key
            return best

        for st in loc_ok:
            demand = st.package_count
            bucket = pick_bucket(st, demand)
            if bucket is None:
                unassigned.append(UnassignedStop(
                    stop_id=st.stop_id,
                    reason="Kapasitas tidak cukup",
                    code="capacity",
                ))
                continue
            dest = (float(st.lat), float(st.lng))
            travel_m = _road_m(bucket["cursor"], dest) if bucket["cursor"] else 0.0
            travel_s = _travel_s(travel_m)
            arrive = bucket["t"] + timedelta(seconds=travel_s)
            closing = _parse_hhmm(st.outlet_closing_time)
            infeasible = False
            infeasible_reason = None
            if closing:
                close_at = _day_start(request.delivery_date).replace(
                    hour=closing[0], minute=closing[1], second=0, microsecond=0,
                )
                if arrive >= close_at:
                    unassigned.append(UnassignedStop(
                        stop_id=st.stop_id,
                        reason="Jam tutup tidak feasible",
                        code="closing_time",
                    ))
                    continue
            seq = len(bucket["stops"]) + 1
            bucket["stops"].append(AssignedStop(
                stop_id=st.stop_id,
                sequence=seq,
                eta=arrive.isoformat(),
                infeasible=infeasible,
                infeasible_reason=infeasible_reason,
            ))
            bucket["dus"] += int(demand or 0)
            bucket["qty"] += int(st.qty or 0)
            bucket["meters"] += int(travel_m)
            bucket["seconds"] += travel_s + SERVICE_SECONDS
            bucket["cursor"] = dest
            bucket["t"] = arrive + timedelta(seconds=SERVICE_SECONDS)
            bucket["geometry"].append({"lat": dest[0], "lng": dest[1]})

        routes: List[VehicleRoute] = []
        for b in buckets:
            if depot_pt and b["cursor"]:
                back_m = _road_m(b["cursor"], depot_pt)
                b["meters"] += int(back_m)
                b["seconds"] += _travel_s(back_m)
                b["geometry"].append({"lat": depot_pt[0], "lng": depot_pt[1]})
            v = b["vehicle"]
            routes.append(VehicleRoute(
                vehicle_id=v.vehicle_id,
                driver_user_id=v.driver_user_id,
                driver_name=v.driver_name,
                plate=v.plate,
                vehicle_name=v.name,
                stops=b["stops"],
                distance_meters=int(b["meters"]),
                duration_seconds=int(b["seconds"]),
                total_qty=int(b["qty"]),
                total_dus=b["dus"] if any(s.package_count is not None for s in loc_ok) else None,
                geometry=b["geometry"],
            ))

        seen = set()
        dup = []
        for r in routes:
            for s in r.stops:
                if s.stop_id in seen:
                    dup.append(s.stop_id)
                seen.add(s.stop_id)
        warnings = []
        if dup:
            warnings.append("Duplicate assignment detected in mock (should not happen).")
        return OptimizeResult(
            routes=routes,
            unassigned=unassigned,
            provider=self.name,
            warnings=warnings,
        )
