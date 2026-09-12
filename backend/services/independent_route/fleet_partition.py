"""Deterministic urgent-seeded geographic partition into exactly two fleets.

Pure functions only — no DB, no HTTP, no Google routing.
Uses haversine meters from independent_route.geo.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.independent_route.geo import haversine_m, is_valid_coordinate

PartitionStop = Dict[str, Any]


class FleetPartitionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(v: Any) -> str:
    return str(v or "").strip()


def _stop_id(stop: PartitionStop) -> str:
    return _text(stop.get("id") or stop.get("sales_order_id") or stop.get("order_id"))


def _urgent(stop: PartitionStop) -> bool:
    if "urgent" in stop:
        return bool(stop.get("urgent"))
    return str(stop.get("priority") or "").lower() == "urgent"


def _point(stop: PartitionStop) -> Tuple[float, float]:
    lat = stop.get("lat", stop.get("latitude"))
    lng = stop.get("lng", stop.get("longitude"))
    if not is_valid_coordinate(lat, lng):
        raise FleetPartitionError(
            "INVALID_COORDINATE",
            f"Stop {_stop_id(stop) or '?'} tidak punya koordinat valid.",
        )
    return (float(lat), float(lng))


def _dist(a: PartitionStop, b: PartitionStop) -> float:
    return haversine_m(_point(a), _point(b))


def _sorted_stops(stops: Sequence[PartitionStop]) -> List[PartitionStop]:
    return sorted(stops, key=lambda s: _stop_id(s))


def _farthest_pair(stops: Sequence[PartitionStop]) -> Tuple[PartitionStop, PartitionStop]:
    ordered = _sorted_stops(stops)
    if len(ordered) < 2:
        raise FleetPartitionError("TOO_FEW", "Butuh minimal dua stop untuk pasangan seed.")
    best: Optional[Tuple[float, str, str, PartitionStop, PartitionStop]] = None
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            d = _dist(a, b)
            key = (d, _stop_id(a), _stop_id(b), a, b)
            if best is None or key[:3] > best[:3]:
                best = key
    assert best is not None
    # Deterministic armada assignment: lower id → Armada 1 seed.
    a, b = best[3], best[4]
    if _stop_id(a) <= _stop_id(b):
        return a, b
    return b, a


def _farthest_from(seed: PartitionStop, candidates: Sequence[PartitionStop]) -> PartitionStop:
    ordered = _sorted_stops(candidates)
    if not ordered:
        raise FleetPartitionError("TOO_FEW", "Tidak ada kandidat seed kedua.")
    best = ordered[0]
    best_key = (_dist(seed, best), _stop_id(best))
    for c in ordered[1:]:
        key = (_dist(seed, c), _stop_id(c))
        if key > best_key:
            best = c
            best_key = key
    return best


def _nearest_member_dist(fleet: Sequence[PartitionStop], stop: PartitionStop) -> float:
    return min(_dist(stop, m) for m in fleet)


def _fleet_span(fleet: Sequence[PartitionStop]) -> float:
    """Crude route-length proxy: sum of nearest-neighbour chain from first seed."""
    if len(fleet) <= 1:
        return 0.0
    ordered = list(fleet)
    used = { _stop_id(ordered[0]) }
    total = 0.0
    cur = ordered[0]
    while len(used) < len(ordered):
        nxt = None
        nxt_d = None
        for s in ordered:
            sid = _stop_id(s)
            if sid in used:
                continue
            d = _dist(cur, s)
            key = (d, sid)
            if nxt is None or key < (nxt_d, _stop_id(nxt)):
                nxt = s
                nxt_d = d
        assert nxt is not None and nxt_d is not None
        total += nxt_d
        used.add(_stop_id(nxt))
        cur = nxt
    return total


def _choose_fleet(
    stop: PartitionStop,
    fleet1: List[PartitionStop],
    fleet2: List[PartitionStop],
) -> int:
    """Return 1 or 2. Soft-balance by estimated span, not PO count."""
    c1 = _nearest_member_dist(fleet1, stop)
    c2 = _nearest_member_dist(fleet2, stop)
    span1 = _fleet_span(fleet1)
    span2 = _fleet_span(fleet2)
    # Soft balance: if one fleet is already much longer, prefer the lighter when costs close.
    balance_bias = 0.0
    if span1 + span2 > 1.0:
        if span1 > span2 * 1.25:
            balance_bias = (span1 - span2) * 0.05
            c1 += balance_bias
        elif span2 > span1 * 1.25:
            balance_bias = (span2 - span1) * 0.05
            c2 += balance_bias
    if abs(c1 - c2) < 1e-6:
        # Tie-break: prefer currently lighter span, then Armada 1, then id.
        if abs(span1 - span2) > 1e-6:
            return 1 if span1 < span2 else 2
        return 1
    return 1 if c1 < c2 else 2


def _soft_rebalance(fleet1: List[PartitionStop], fleet2: List[PartitionStop]) -> None:
    """Optional single-pass moves to ease extreme span imbalance (mutates lists)."""
    for _ in range(min(8, len(fleet1) + len(fleet2))):
        if len(fleet1) <= 1 or len(fleet2) <= 1:
            return
        s1 = _fleet_span(fleet1)
        s2 = _fleet_span(fleet2)
        if s1 <= s2 * 1.35 and s2 <= s1 * 1.35:
            return
        heavy, light = (fleet1, fleet2) if s1 > s2 else (fleet2, fleet1)
        # Candidate: heavy member closest to light fleet (excluding seed at index 0).
        movers = _sorted_stops(heavy[1:])
        if not movers:
            return
        best = None
        best_key = None
        for m in movers:
            gain = _nearest_member_dist(light, m) - _nearest_member_dist(heavy, m)
            key = (gain, _stop_id(m))
            if best is None or key < best_key:
                best = m
                best_key = key
        if best is None or best_key is None or best_key[0] > 0:
            return
        heavy.remove(best)
        light.append(best)
        light.sort(key=_stop_id)
        # Keep seed at index 0
        seed_h = heavy[0]
        heavy[:] = [seed_h] + [s for s in _sorted_stops(heavy) if _stop_id(s) != _stop_id(seed_h)]
        seed_l = light[0]
        light[:] = [seed_l] + [s for s in _sorted_stops(light) if _stop_id(s) != _stop_id(seed_l)]


def partition_into_two_fleets(stops: Sequence[PartitionStop]) -> Dict[str, Any]:
    """Partition eligible stops into armada_1 and armada_2.

    Each input stop must include id + valid lat/lng. Optional urgent/priority.
    Returns dict with armada_1 / armada_2 lists (same stop dict references preserved via copy of ids).
    """
    cleaned: List[PartitionStop] = []
    seen = set()
    for raw in stops:
        sid = _stop_id(raw)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        _point(raw)  # validate
        cleaned.append(raw)

    if len(cleaned) < 2:
        raise FleetPartitionError(
            "TOO_FEW_ELIGIBLE",
            "Optimasi Armada membutuhkan minimal 2 PO eligible.",
        )

    if len(cleaned) == 2:
        a, b = _sorted_stops(cleaned)
        return {
            "armada_1": [a],
            "armada_2": [b],
            "seed_ids": [_stop_id(a), _stop_id(b)],
        }

    urgents = [s for s in cleaned if _urgent(s)]
    if len(urgents) >= 2:
        seed1, seed2 = _farthest_pair(urgents)
    elif len(urgents) == 1:
        seed1 = urgents[0]
        others = [s for s in cleaned if _stop_id(s) != _stop_id(seed1)]
        seed2 = _farthest_from(seed1, others)
        if _stop_id(seed1) > _stop_id(seed2):
            # Keep geographic seeds but normalize labels only when both non-urgent path;
            # with one urgent, urgent stays Armada 1 seed for interpretability.
            pass
    else:
        seed1, seed2 = _farthest_pair(cleaned)

    fleet1: List[PartitionStop] = [seed1]
    fleet2: List[PartitionStop] = [seed2]
    assigned = {_stop_id(seed1), _stop_id(seed2)}

    # Remaining: urgents first (stable by id), then normals by id.
    remaining_u = _sorted_stops([s for s in cleaned if _urgent(s) and _stop_id(s) not in assigned])
    remaining_n = _sorted_stops([s for s in cleaned if not _urgent(s) and _stop_id(s) not in assigned])
    for stop in remaining_u + remaining_n:
        choice = _choose_fleet(stop, fleet1, fleet2)
        if choice == 1:
            fleet1.append(stop)
        else:
            fleet2.append(stop)

    _soft_rebalance(fleet1, fleet2)

    if not fleet1 or not fleet2:
        raise FleetPartitionError("EMPTY_FLEET", "Partition menghasilkan armada kosong.")

    ids1 = {_stop_id(s) for s in fleet1}
    ids2 = {_stop_id(s) for s in fleet2}
    if ids1 & ids2:
        raise FleetPartitionError("DUPLICATE", "PO terduplikasi antar armada.")
    all_ids = {_stop_id(s) for s in cleaned}
    if ids1 | ids2 != all_ids:
        raise FleetPartitionError("DROP", "PO eligible hilang dari partition.")

    return {
        "armada_1": fleet1,
        "armada_2": fleet2,
        "seed_ids": [_stop_id(seed1), _stop_id(seed2)],
    }


def assert_partition_invariants(
    eligible_ids: Iterable[str],
    armada_1_ids: Sequence[str],
    armada_2_ids: Sequence[str],
) -> None:
    e = {_text(i) for i in eligible_ids if _text(i)}
    a1 = [_text(i) for i in armada_1_ids if _text(i)]
    a2 = [_text(i) for i in armada_2_ids if _text(i)]
    if len(a1) != len(set(a1)) or len(a2) != len(set(a2)):
        raise FleetPartitionError("DUPLICATE", "ID duplikat di dalam armada.")
    s1, s2 = set(a1), set(a2)
    if s1 & s2:
        raise FleetPartitionError("DUPLICATE", "ID muncul di kedua armada.")
    if s1 | s2 != e:
        raise FleetPartitionError("MISMATCH", "Gabungan armada ≠ himpunan eligible.")
    if not s1 or not s2:
        raise FleetPartitionError("EMPTY_FLEET", "Armada tidak boleh kosong.")
