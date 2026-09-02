"""Deterministic geographic helpers. No network. No invented coordinates."""
from __future__ import annotations

import math
from typing import Optional

from services.independent_route.constants import EARTH_RADIUS_M


def is_finite_number(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def is_valid_coordinate(lat, lng) -> bool:
    if not is_finite_number(lat) or not is_finite_number(lng):
        return False
    lat_n = float(lat)
    lng_n = float(lng)
    if abs(lat_n) < 1e-12 and abs(lng_n) < 1e-12:
        return False
    return -90.0 <= lat_n <= 90.0 and -180.0 <= lng_n <= 180.0


def as_point(lat, lng) -> Optional[tuple[float, float]]:
    if not is_valid_coordinate(lat, lng):
        return None
    return (float(lat), float(lng))


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in meters. Identical points → 0."""
    lat1, lng1 = a
    lat2, lng2 = b
    if lat1 == lat2 and lng1 == lng2:
        return 0.0
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    h = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def point_from_snapshot(snapshot: Optional[dict]) -> Optional[tuple[float, float]]:
    if not isinstance(snapshot, dict):
        return None
    lat = snapshot.get("lat", snapshot.get("latitude"))
    lng = snapshot.get("lng", snapshot.get("longitude"))
    return as_point(lat, lng)
