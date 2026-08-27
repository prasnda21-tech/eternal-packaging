"""Google Route Optimization adapter.

Official endpoint (do not invent):
  POST https://routeoptimization.googleapis.com/v1/projects/{project}:optimizeTours

Request/response mapping follows:
  https://developers.google.com/maps/documentation/route-optimization/reference/rest/v1/projects/optimizeTours

This module MUST NOT be called from automated tests against the live API.
Business services consume OptimizeResult, never raw Google JSON.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from services.route_optimizer.base import (
    RouteOptimizerError,
    RouteOptimizerNotConfigured,
    RouteOptimizerProvider,
)
from services.route_optimizer.types import (
    AssignedStop,
    OptimizeRequest,
    OptimizeResult,
    UnassignedStop,
    VehicleRoute,
)

# Official REST host + RPC from Google Maps Route Optimization docs.
GOOGLE_OPTIMIZE_HOST = "https://routeoptimization.googleapis.com"
GOOGLE_OPTIMIZE_RPC = "optimizeTours"

WIB = timezone(timedelta(hours=7))

# penaltyCost is the official skip-cost field on Shipment.
# Higher penalty = more important to serve. We map display priority onto it
# rather than inventing a non-documented "priority" ranking field.
PRIORITY_PENALTY = {
    "normal": 1000.0,
    "prioritas": 8000.0,
    "mendesak": 25000.0,
}

SKIP_REASON_ID = {
    "CODE_UNSPECIFIED": "Tidak dapat di-assign",
    "NO_VEHICLE": "Tidak ada kendaraan",
    "DEMAND_EXCEEDS_VEHICLE_CAPACITY": "Kapasitas tidak cukup",
    "CANNOT_BE_PERFORMED_WITHIN_VEHICLE_DISTANCE_LIMIT": "Jarak kendaraan tidak feasible",
    "CANNOT_BE_PERFORMED_WITHIN_VEHICLE_DURATION_LIMIT": "Durasi kendaraan tidak feasible",
    "CANNOT_BE_PERFORMED_WITHIN_VEHICLE_TRAVEL_DURATION_LIMIT": "Waktu tempuh tidak feasible",
    "CANNOT_BE_PERFORMED_WITHIN_VEHICLE_TIME_WINDOWS": "Jam tutup tidak feasible",
    "VEHICLE_NOT_ALLOWED": "Kendaraan tidak diizinkan",
}


def google_project_id() -> str:
    return (
        os.environ.get("GOOGLE_ROUTE_OPTIMIZATION_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()


def google_api_key() -> str:
    return (
        os.environ.get("GOOGLE_ROUTE_OPTIMIZATION_API_KEY")
        or os.environ.get("GOOGLE_MAPS_API_KEY")
        or ""
    ).strip()


def is_google_configured() -> bool:
    return bool(google_project_id() and google_api_key())


def official_optimize_url(project: Optional[str] = None) -> str:
    pid = (project or google_project_id()).strip()
    if not pid:
        raise RouteOptimizerNotConfigured("GOOGLE_ROUTE_OPTIMIZATION_PROJECT belum diisi.")
    base = f"{GOOGLE_OPTIMIZE_HOST}/v1/projects/{pid}:{GOOGLE_OPTIMIZE_RPC}"
    key = google_api_key()
    if key:
        return f"{base}?{urlencode({'key': key})}"
    return base


def _latlng(lat: float, lng: float) -> dict:
    # Official LatLng: https://developers.google.com/maps/documentation/route-optimization/reference/rest/Shared.Types/LatLng
    return {"latitude": float(lat), "longitude": float(lng)}


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.isoformat().replace("+00:00", "Z")


def _day_bounds(date_key: str) -> tuple[datetime, datetime]:
    try:
        d = datetime.strptime(str(date_key)[:10], "%Y-%m-%d").date()
    except ValueError:
        d = datetime.now(WIB).date()
    start = datetime(d.year, d.month, d.day, 6, 0, tzinfo=WIB)
    end = datetime(d.year, d.month, d.day, 22, 0, tzinfo=WIB)
    return start, end


def _closing_end(date_key: str, hhmm: str) -> Optional[datetime]:
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
        d = datetime.strptime(str(date_key)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return datetime(d.year, d.month, d.day, h, m, tzinfo=WIB)


def build_google_request(req: OptimizeRequest) -> dict:
    """Map domain OptimizeRequest → official OptimizeToursRequest JSON.

    Fields used (official ShipmentModel / Shipment / Vehicle / VisitRequest):
      model.shipments[].label
      model.shipments[].deliveries[].arrivalLocation
      model.shipments[].deliveries[].duration
      model.shipments[].deliveries[].timeWindows[].endTime   (only if closing time set)
      model.shipments[].loadDemands.dus.amount               (only if package_count set)
      model.shipments[].penaltyCost                          (priority mapping)
      model.vehicles[].label
      model.vehicles[].startLocation / endLocation
      model.vehicles[].loadLimits.dus.maxLoad                (only if capacity set)
      model.vehicles[].costPerKilometer
      model.globalStartTime / globalEndTime
      populatePolylines
    """
    start, end = _day_bounds(req.delivery_date)
    depot_lat = req.depot.lat
    depot_lng = req.depot.lng
    shipments = []
    for st in req.stops:
        if not st.has_location():
            continue
        delivery: Dict[str, Any] = {
            "arrivalLocation": _latlng(st.lat, st.lng),
            "duration": "300s",
        }
        if st.outlet_closing_time:
            close_at = _closing_end(req.delivery_date, st.outlet_closing_time)
            if close_at is not None:
                # Official TimeWindow.endTime — do not invent a startTime.
                delivery["timeWindows"] = [{"endTime": _rfc3339(close_at)}]
        shipment: Dict[str, Any] = {
            "label": st.stop_id,
            "deliveries": [delivery],
            "penaltyCost": PRIORITY_PENALTY.get(st.priority or "normal", 1000.0),
        }
        if st.package_count is not None:
            shipment["loadDemands"] = {"dus": {"amount": str(int(st.package_count))}}
        shipments.append(shipment)

    vehicles = []
    for v in req.vehicles:
        slat = v.start_lat if v.start_lat is not None else depot_lat
        slng = v.start_lng if v.start_lng is not None else depot_lng
        elat = v.end_lat if v.end_lat is not None else depot_lat
        elng = v.end_lng if v.end_lng is not None else depot_lng
        veh: Dict[str, Any] = {
            "label": v.vehicle_id,
            "costPerKilometer": 1.0,
        }
        if slat is not None and slng is not None:
            veh["startLocation"] = _latlng(slat, slng)
        if elat is not None and elng is not None:
            veh["endLocation"] = _latlng(elat, elng)
        if v.capacity_dus is not None:
            veh["loadLimits"] = {"dus": {"maxLoad": str(int(v.capacity_dus))}}
        vehicles.append(veh)

    return {
        "model": {
            "shipments": shipments,
            "vehicles": vehicles,
            "globalStartTime": _rfc3339(start),
            "globalEndTime": _rfc3339(end),
        },
        "populatePolylines": True,
        "considerRoadTraffic": False,
        "label": req.batch_id,
    }


def _parse_duration_s(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if text.endswith("s"):
        try:
            return int(float(text[:-1]))
        except ValueError:
            return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_google_response(payload: dict, req: OptimizeRequest) -> OptimizeResult:
    """Map official OptimizeToursResponse → OptimizeResult."""
    stop_by_id = {s.stop_id: s for s in req.stops}
    veh_by_id = {v.vehicle_id: v for v in req.vehicles}
    routes: List[VehicleRoute] = []
    assigned: set = set()
    warnings: List[str] = []

    for gr in payload.get("routes") or []:
        label = str(gr.get("vehicleLabel") or "")
        idx = gr.get("vehicleIndex")
        vehicle = None
        if label and label in veh_by_id:
            vehicle = veh_by_id[label]
        elif isinstance(idx, int) and 0 <= idx < len(req.vehicles):
            vehicle = req.vehicles[idx]
        if not vehicle:
            continue
        visits = []
        seq = 0
        for vis in gr.get("visits") or []:
            if vis.get("isPickup"):
                continue
            sid = str(vis.get("shipmentLabel") or "")
            if not sid:
                sidx = vis.get("shipmentIndex")
                google_shipments = [s for s in req.stops if s.has_location()]
                if isinstance(sidx, int) and 0 <= sidx < len(google_shipments):
                    sid = google_shipments[sidx].stop_id
            if not sid:
                continue
            seq += 1
            assigned.add(sid)
            src = stop_by_id.get(sid)
            closing = src.outlet_closing_time if src else None
            infeasible = bool(gr.get("hasTrafficInfeasibilities") or vis.get("isDetour"))
            reason = None
            start_time = vis.get("startTime")
            if closing and start_time:
                close_at = _closing_end(req.delivery_date, closing)
                try:
                    arrive = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
                    if close_at is not None and arrive.astimezone(WIB) > close_at:
                        infeasible = True
                        reason = "Jam tutup tidak feasible"
                        warnings.append(f"Stop {sid} ETA beyond outlet closing time.")
                except ValueError:
                    pass
            visits.append(AssignedStop(
                stop_id=sid,
                sequence=seq,
                eta=str(start_time) if start_time else None,
                infeasible=infeasible,
                infeasible_reason=reason,
            ))
        metrics = gr.get("metrics") or {}
        dist = int(metrics.get("travelDistanceMeters") or gr.get("travelDistanceMeters") or 0)
        dur = _parse_duration_s(metrics.get("travelDuration") or gr.get("vehicleEndTime"))
        poly = None
        route_poly = gr.get("routePolyline") or {}
        if isinstance(route_poly, dict):
            poly = route_poly.get("points")
        geometry = []
        if req.depot.has_location():
            geometry.append({"lat": float(req.depot.lat), "lng": float(req.depot.lng)})
        for vis in visits:
            src = stop_by_id.get(vis.stop_id)
            if src and src.has_location():
                geometry.append({"lat": float(src.lat), "lng": float(src.lng)})
        if req.depot.has_location():
            geometry.append({"lat": float(req.depot.lat), "lng": float(req.depot.lng)})
        qty = 0
        dus_vals = []
        for vis in visits:
            src_stop = stop_by_id.get(vis.stop_id)
            if not src_stop:
                continue
            qty += int(src_stop.qty or 0)
            if src_stop.package_count is not None:
                dus_vals.append(src_stop.package_count)
        routes.append(VehicleRoute(
            vehicle_id=vehicle.vehicle_id,
            driver_user_id=vehicle.driver_user_id,
            driver_name=vehicle.driver_name,
            plate=vehicle.plate,
            vehicle_name=vehicle.name,
            stops=visits,
            distance_meters=dist,
            duration_seconds=dur,
            total_qty=qty,
            total_dus=sum(dus_vals) if dus_vals else None,
            geometry=geometry,
            polyline=poly,
        ))

    unassigned: List[UnassignedStop] = []
    for skipped in payload.get("skippedShipments") or []:
        sid = str(skipped.get("label") or "")
        reasons = skipped.get("reasons") or []
        code = "unassigned"
        reason_text = "Tidak dapat di-assign"
        if reasons:
            raw_code = str((reasons[0] or {}).get("code") or "")
            code = raw_code or code
            reason_text = SKIP_REASON_ID.get(raw_code, reason_text)
        if not sid:
            continue
        assigned.discard(sid)
        unassigned.append(UnassignedStop(stop_id=sid, reason=reason_text, code=code))

    for st in req.stops:
        if st.stop_id in assigned:
            continue
        if any(u.stop_id == st.stop_id for u in unassigned):
            continue
        if not st.has_location():
            unassigned.append(UnassignedStop(
                stop_id=st.stop_id, reason="Lokasi outlet belum diatur", code="missing_location",
            ))
        else:
            unassigned.append(UnassignedStop(
                stop_id=st.stop_id, reason="Tidak dapat di-assign", code="unassigned",
            ))

    return OptimizeResult(
        routes=routes,
        unassigned=unassigned,
        provider="google",
        warnings=warnings,
        raw_meta={"skipped": len(payload.get("skippedShipments") or [])},
    )


class GoogleRouteOptimizer(RouteOptimizerProvider):
    name = "google"

    def is_configured(self) -> bool:
        return is_google_configured()

    def optimize(self, request: OptimizeRequest) -> OptimizeResult:
        if not self.is_configured():
            raise RouteOptimizerNotConfigured(
                "Google Route Optimization belum dikonfigurasi "
                "(GOOGLE_ROUTE_OPTIMIZATION_PROJECT + API key)."
            )
        body = build_google_request(request)
        url = official_optimize_url()
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=body)
        except httpx.HTTPError as e:
            raise RouteOptimizerError(f"Gagal memanggil Google Route Optimization: {e}") from e
        if resp.status_code >= 400:
            raise RouteOptimizerError(
                f"Google Route Optimization error {resp.status_code}: {resp.text[:400]}"
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise RouteOptimizerError("Google Route Optimization mengembalikan non-JSON.") from e
        return parse_google_response(payload if isinstance(payload, dict) else {}, request)
