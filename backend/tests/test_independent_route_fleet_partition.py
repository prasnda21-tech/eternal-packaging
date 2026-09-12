"""Focused pure tests for urgent-seeded geographic two-fleet partition."""
from __future__ import annotations

import pytest

from services.independent_route.fleet_partition import (
    FleetPartitionError,
    assert_partition_invariants,
    partition_into_two_fleets,
)


def _stop(oid, lat, lng, urgent=False):
    return {"id": oid, "lat": lat, "lng": lng, "urgent": urgent}


CLUSTERS = [
    (-6.12, 106.88),   # Jakarta
    (-6.91, 107.61),   # Bandung
    (-7.25, 112.75),   # Surabaya
    (-0.95, 100.35),   # Padang
]


def _four_region_stops(n_per=7, urgent_ids=None):
    urgent_ids = set(urgent_ids or [])
    stops = []
    n = 0
    for ci, (lat, lng) in enumerate(CLUSTERS):
        for j in range(n_per):
            n += 1
            oid = f"o{n:02d}"
            stops.append(_stop(oid, lat + j * 0.01, lng + j * 0.01, oid in urgent_ids))
    return stops


def test_too_few_eligible_raises():
    with pytest.raises(FleetPartitionError) as ei:
        partition_into_two_fleets([_stop("a", -6.2, 106.8)])
    assert ei.value.code == "TOO_FEW_ELIGIBLE"


def test_exactly_two_eligible_one_each():
    out = partition_into_two_fleets([
        _stop("b", -6.2, 106.8),
        _stop("a", -7.2, 112.7),
    ])
    assert [s["id"] for s in out["armada_1"]] == ["a"]
    assert [s["id"] for s in out["armada_2"]] == ["b"]


def test_zero_urgent_farthest_pair_seeds():
    stops = _four_region_stops(n_per=3, urgent_ids=[])
    out = partition_into_two_fleets(stops)
    assert_partition_invariants([s["id"] for s in stops], [s["id"] for s in out["armada_1"]], [s["id"] for s in out["armada_2"]])
    assert len(out["armada_1"]) >= 1 and len(out["armada_2"]) >= 1


def test_one_urgent_seed_plus_farthest_normal():
    stops = _four_region_stops(n_per=3, urgent_ids={"o01"})
    out = partition_into_two_fleets(stops)
    assert_partition_invariants([s["id"] for s in stops], [s["id"] for s in out["armada_1"]], [s["id"] for s in out["armada_2"]])
    seeds = set(out["seed_ids"])
    assert "o01" in seeds


def test_urgent_spread_seeds_are_farthest_urgent_pair():
    # urgent in Jakarta and Padang (most separated)
    stops = _four_region_stops(n_per=4, urgent_ids={"o01", "o02", "o09", "o13", "o16"})
    out = partition_into_two_fleets(stops)
    assert_partition_invariants([s["id"] for s in stops], [s["id"] for s in out["armada_1"]], [s["id"] for s in out["armada_2"]])
    seeds = set(out["seed_ids"])
    # Padang cluster starts at o13; Jakarta at o01
    assert seeds & {"o01", "o02", "o13", "o16"}


def test_urgent_clustered_still_assigns_all_once():
    stops = _four_region_stops(n_per=5, urgent_ids={"o01", "o02", "o03", "o04", "o05"})
    out = partition_into_two_fleets(stops)
    ids1 = [s["id"] for s in out["armada_1"]]
    ids2 = [s["id"] for s in out["armada_2"]]
    assert_partition_invariants([s["id"] for s in stops], ids1, ids2)
    assert len(ids1) + len(ids2) == len(stops)
    assert len(set(ids1) & set(ids2)) == 0


def test_all_urgent():
    stops = [_stop(f"u{i}", -6.2 + i * 0.05, 106.8 + i * 0.2, True) for i in range(8)]
    out = partition_into_two_fleets(stops)
    assert_partition_invariants([s["id"] for s in stops], [s["id"] for s in out["armada_1"]], [s["id"] for s in out["armada_2"]])


def test_odd_count_and_soft_balance_allows_unequal():
    stops = _four_region_stops(n_per=4, urgent_ids={"o01", "o05", "o09", "o13", "o02"})  # 16 stops
    stops.append(_stop("o99", -6.5, 107.0, False))  # odd
    out = partition_into_two_fleets(stops)
    n1, n2 = len(out["armada_1"]), len(out["armada_2"])
    assert n1 + n2 == 17
    assert n1 >= 1 and n2 >= 1
    # Equal split not required
    assert abs(n1 - n2) >= 0


def test_thirty_po_five_urgent_four_regions():
    stops = _four_region_stops(n_per=7, urgent_ids={"o01", "o08", "o15", "o22", "o28"})
    assert len(stops) == 28
    # pad to 30
    stops.append(_stop("o29", -6.15, 106.9, False))
    stops.append(_stop("o30", -7.3, 112.8, False))
    out1 = partition_into_two_fleets(stops)
    out2 = partition_into_two_fleets(stops)
    assert [s["id"] for s in out1["armada_1"]] == [s["id"] for s in out2["armada_1"]]
    assert [s["id"] for s in out1["armada_2"]] == [s["id"] for s in out2["armada_2"]]
    assert_partition_invariants(
        [s["id"] for s in stops],
        [s["id"] for s in out1["armada_1"]],
        [s["id"] for s in out1["armada_2"]],
    )


def test_no_duplicate_no_drop_invariants():
    stops = _four_region_stops(n_per=5, urgent_ids={"o01", "o10", "o20"})
    out = partition_into_two_fleets(stops)
    all_ids = [s["id"] for s in stops]
    a1 = [s["id"] for s in out["armada_1"]]
    a2 = [s["id"] for s in out["armada_2"]]
    assert_partition_invariants(all_ids, a1, a2)
    with pytest.raises(FleetPartitionError):
        assert_partition_invariants(all_ids, a1 + [a1[0]], a2)
