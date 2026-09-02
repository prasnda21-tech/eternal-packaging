"""Priority-first geographic route optimizer.

Priority rank is a hard constraint: every stop of rank N is visited before
any stop of rank N+1. Strategies only reorder inside a rank group.

Uses Haversine distance. Deterministic: distance ties break on stop_id.
Does not mutate coordinates, priorities, or master data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from services.independent_route.constants import (
    END_LAST,
    END_MANUAL,
    END_RETURN_START,
    END_WAREHOUSE,
    START_CURRENT,
    START_MANUAL,
    START_WAREHOUSE,
    STRATEGY_FARTHEST,
    STRATEGY_NEAREST,
    STRATEGY_SHORTEST,
)
from services.independent_route.geo import as_point, haversine_m, is_valid_coordinate, point_from_snapshot


class OptimizerError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class StopInput:
    stop_id: str
    lat: float
    lng: float
    priority_rank: int
    priority_id: str = ""


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    kind: str = ""


@dataclass
class OptimizeResult:
    ordered_stop_ids: list[str]
    total_estimated_m: float
    segments: list[dict] = field(default_factory=list)
    start: dict = field(default_factory=dict)
    end: dict = field(default_factory=dict)
    strategy: str = ""
    distance_model: str = "haversine_estimate"


def _require_point(lat, lng, label: str) -> tuple[float, float]:
    point = as_point(lat, lng)
    if point is None:
        raise OptimizerError("INVALID_COORDINATE", f"{label} tidak memiliki koordinat valid.")
    return point


def _stop_point(stop: StopInput) -> tuple[float, float]:
    return _require_point(stop.lat, stop.lng, f"Stop {stop.stop_id}")


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_m(a, b)


def _pick_by_distance(current: tuple[float, float], remaining: list[StopInput], *, farthest: bool) -> StopInput:
    scored = []
    for stop in remaining:
        dist = _distance(current, _stop_point(stop))
        scored.append((dist, stop.stop_id, stop))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=farthest)
    if farthest:
        # Stable: farthest distance first, then smallest stop_id among ties.
        best_dist = scored[0][0]
        ties = [row for row in scored if abs(row[0] - best_dist) < 1e-9]
        ties.sort(key=lambda row: row[1])
        return ties[0][2]
    return scored[0][2]


def _greedy_group(current: tuple[float, float], group: list[StopInput], *, farthest: bool) -> list[StopInput]:
    remaining = list(group)
    ordered = []
    pos = current
    while remaining:
        chosen = _pick_by_distance(pos, remaining, farthest=farthest)
        ordered.append(chosen)
        remaining = [s for s in remaining if s.stop_id != chosen.stop_id]
        pos = _stop_point(chosen)
    return ordered


def _path_length(start: tuple[float, float], ordered: list[StopInput], end: Optional[tuple[float, float]]) -> float:
    total = 0.0
    pos = start
    for stop in ordered:
        nxt = _stop_point(stop)
        total += _distance(pos, nxt)
        pos = nxt
    if end is not None and ordered:
        total += _distance(pos, end)
    elif end is not None and not ordered:
        total += _distance(start, end)
    return total


def _two_opt_group(
    start: tuple[float, float],
    group: list[StopInput],
    end: Optional[tuple[float, float]],
) -> list[StopInput]:
    """Improve a group's order. Never worse than the nearest-neighbor seed."""
    if len(group) < 3:
        return list(group)
    seed = _greedy_group(start, group, farthest=False)
    best = list(seed)
    best_cost = _path_length(start, best, end)
    improved = True
    while improved:
        improved = False
        candidate = None
        candidate_cost = best_cost
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                trial = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                cost = _path_length(start, trial, end)
                if cost + 1e-9 < candidate_cost:
                    candidate = trial
                    candidate_cost = cost
                    improved = True
        if candidate is not None:
            best = candidate
            best_cost = candidate_cost
    return best


def _resolve_start(start_mode: str, start_snapshot: Optional[dict], warehouse) -> Point:
    mode = str(start_mode or "").strip().upper()
    if mode == START_CURRENT:
        point = point_from_snapshot(start_snapshot)
        if point is None:
            raise OptimizerError(
                "START_GPS_UNAVAILABLE",
                "Start = Lokasi Saya, tetapi titik GPS belum tersedia.",
            )
        return Point(point[0], point[1], START_CURRENT)
    if mode == START_WAREHOUSE:
        point = point_from_snapshot(warehouse)
        if point is None:
            raise OptimizerError(
                "WAREHOUSE_NOT_CONFIGURED",
                "Lokasi Gudang belum dikonfigurasi.",
            )
        return Point(point[0], point[1], START_WAREHOUSE)
    if mode == START_MANUAL:
        point = point_from_snapshot(start_snapshot)
        if point is None:
            raise OptimizerError(
                "START_MANUAL_MISSING",
                "Start = Titik Manual, tetapi koordinat belum diisi.",
            )
        return Point(point[0], point[1], START_MANUAL)
    raise OptimizerError("INVALID_START_MODE", f"Start mode tidak dikenal: {start_mode}")


def _resolve_end(
    end_mode: str,
    start: Point,
    end_snapshot: Optional[dict],
    warehouse,
) -> Optional[Point]:
    mode = str(end_mode or "").strip().upper()
    if mode == END_LAST:
        return None
    if mode == END_RETURN_START:
        return Point(start.lat, start.lng, END_RETURN_START)
    if mode == END_WAREHOUSE:
        point = point_from_snapshot(warehouse)
        if point is None:
            raise OptimizerError(
                "WAREHOUSE_NOT_CONFIGURED",
                "Lokasi Gudang belum dikonfigurasi.",
            )
        return Point(point[0], point[1], END_WAREHOUSE)
    if mode == END_MANUAL:
        point = point_from_snapshot(end_snapshot)
        if point is None:
            raise OptimizerError(
                "END_MANUAL_MISSING",
                "End = Titik Manual, tetapi koordinat belum diisi.",
            )
        return Point(point[0], point[1], END_MANUAL)
    raise OptimizerError("INVALID_END_MODE", f"End mode tidak dikenal: {end_mode}")


def validate_stops_for_optimize(stops: list[dict]) -> list[StopInput]:
    missing = []
    parsed = []
    for raw in stops:
        stop_id = str(raw.get("stop_id") or raw.get("id") or "").strip()
        lat = raw.get("lat", raw.get("latitude"))
        lng = raw.get("lng", raw.get("longitude"))
        if not stop_id:
            raise OptimizerError("INVALID_STOP", "Stop tanpa identitas.")
        if not is_valid_coordinate(lat, lng):
            missing.append({
                "stop_id": stop_id,
                "display_name": raw.get("display_name") or raw.get("name") or stop_id,
            })
            continue
        rank = raw.get("priority_rank", raw.get("rank"))
        try:
            rank_n = int(rank)
        except (TypeError, ValueError):
            raise OptimizerError("INVALID_PRIORITY", f"Stop {stop_id} tidak memiliki priority rank.")
        parsed.append(StopInput(
            stop_id=stop_id,
            lat=float(lat),
            lng=float(lng),
            priority_rank=rank_n,
            priority_id=str(raw.get("priority_id") or ""),
        ))
    if missing:
        raise OptimizerError(
            "STOPS_MISSING_LOCATION",
            f"{len(missing)} customer belum memiliki titik lokasi.",
            {"missing": missing},
        )
    if not parsed:
        raise OptimizerError("NO_STOPS", "Rute belum memiliki stop yang bisa dioptimasi.")
    return parsed


def validate_priority_order(stops: list[dict], ordered_ids: list[str]) -> None:
    by_id = {str(s.get("stop_id") or s.get("id")): s for s in stops}
    if set(ordered_ids) != set(by_id):
        raise OptimizerError("REORDER_MISMATCH", "Urutan manual harus mencakup semua stop yang ada.")
    last_rank = None
    for stop_id in ordered_ids:
        stop = by_id[stop_id]
        try:
            rank = int(stop.get("priority_rank", stop.get("rank")))
        except (TypeError, ValueError):
            raise OptimizerError("INVALID_PRIORITY", f"Stop {stop_id} tidak memiliki priority rank.")
        if last_rank is not None and rank < last_rank:
            raise OptimizerError(
                "PRIORITY_VIOLATION",
                "Urutan manual tidak boleh mendahului prioritas yang lebih tinggi.",
                {"stop_id": stop_id, "rank": rank, "blocked_by_rank": last_rank},
            )
        last_rank = rank


def optimize_route(
    *,
    stops: list[dict],
    strategy: str,
    start_mode: str,
    start_snapshot: Optional[dict],
    end_mode: str,
    end_snapshot: Optional[dict],
    warehouse: Optional[dict],
) -> OptimizeResult:
    parsed = validate_stops_for_optimize(stops)
    start = _resolve_start(start_mode, start_snapshot, warehouse)
    end = _resolve_end(end_mode, start, end_snapshot, warehouse)
    strategy_key = str(strategy or "").strip().upper()
    if strategy_key not in (STRATEGY_NEAREST, STRATEGY_FARTHEST, STRATEGY_SHORTEST):
        raise OptimizerError("INVALID_STRATEGY", f"Strategi tidak dikenal: {strategy}")

    groups: dict[int, list[StopInput]] = {}
    for stop in parsed:
        groups.setdefault(stop.priority_rank, []).append(stop)
    for rank in groups:
        groups[rank].sort(key=lambda s: s.stop_id)

    ordered: list[StopInput] = []
    pos = (start.lat, start.lng)
    ranks = sorted(groups)
    for index, rank in enumerate(ranks):
        group = groups[rank]
        is_last_group = index == len(ranks) - 1
        group_end = (end.lat, end.lng) if (is_last_group and end is not None) else None
        if strategy_key == STRATEGY_NEAREST:
            sequenced = _greedy_group(pos, group, farthest=False)
        elif strategy_key == STRATEGY_FARTHEST:
            sequenced = _greedy_group(pos, group, farthest=True)
        else:
            sequenced = _two_opt_group(pos, group, group_end)
        ordered.extend(sequenced)
        if sequenced:
            last = sequenced[-1]
            pos = _stop_point(last)

    segments = []
    total = 0.0
    cursor = (start.lat, start.lng)
    cursor_label = "START"
    for seq, stop in enumerate(ordered, start=1):
        nxt = _stop_point(stop)
        dist = _distance(cursor, nxt)
        total += dist
        segments.append({
            "from": cursor_label,
            "to": stop.stop_id,
            "sequence": seq,
            "estimated_m": dist,
        })
        cursor = nxt
        cursor_label = stop.stop_id
    if end is not None:
        dist = _distance(cursor, (end.lat, end.lng))
        total += dist
        segments.append({
            "from": cursor_label,
            "to": "END",
            "sequence": len(ordered) + 1,
            "estimated_m": dist,
        })

    return OptimizeResult(
        ordered_stop_ids=[s.stop_id for s in ordered],
        total_estimated_m=total,
        segments=segments,
        start={"lat": start.lat, "lng": start.lng, "kind": start.kind},
        end={"lat": end.lat, "lng": end.lng, "kind": end.kind} if end else {"kind": END_LAST},
        strategy=strategy_key,
    )
