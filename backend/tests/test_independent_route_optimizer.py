"""Independent optimizer cases — plain fixtures, no network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.independent_route.geo import haversine_m
from services.independent_route.optimizer import (
    OptimizerError,
    optimize_route,
    validate_priority_order,
)


ORIGIN = {"lat": -6.175392, "lng": 106.827153}
WAREHOUSE = {"lat": -6.200000, "lng": 106.800000, "configured": True}
MANUAL_START = {"lat": -6.180000, "lng": 106.820000}
MANUAL_END = {"lat": -6.250000, "lng": 106.900000}

# A far P1, B near P2, C far-ish P1, D mid P2
STOPS_MIXED = [
    {"stop_id": "A", "lat": -6.4000, "lng": 106.8500, "priority_rank": 1, "priority_id": "p1"},
    {"stop_id": "B", "lat": -6.1800, "lng": 106.8300, "priority_rank": 3, "priority_id": "p3"},
    {"stop_id": "C", "lat": -6.3500, "lng": 107.0000, "priority_rank": 1, "priority_id": "p1"},
    {"stop_id": "D", "lat": -6.2200, "lng": 106.8600, "priority_rank": 2, "priority_id": "p2"},
]

SAME_PRI = [
    {"stop_id": "N1", "lat": -6.1760, "lng": 106.8280, "priority_rank": 3, "priority_id": "p3"},
    {"stop_id": "N2", "lat": -6.1900, "lng": 106.8400, "priority_rank": 3, "priority_id": "p3"},
    {"stop_id": "N3", "lat": -6.2300, "lng": 106.9000, "priority_rank": 3, "priority_id": "p3"},
]


def _run(stops, strategy, start_mode="MANUAL", start=ORIGIN, end_mode="LAST_STOP", end=None, warehouse=WAREHOUSE):
    return optimize_route(
        stops=stops,
        strategy=strategy,
        start_mode=start_mode,
        start_snapshot=start,
        end_mode=end_mode,
        end_snapshot=end,
        warehouse=warehouse,
    )


def test_case1_p1_far_before_p2_near():
    result = _run(STOPS_MIXED, "NEAREST")
    ranks = {s["stop_id"]: s["priority_rank"] for s in STOPS_MIXED}
    ordered_ranks = [ranks[sid] for sid in result.ordered_stop_ids]
    assert ordered_ranks == sorted(ordered_ranks)
    assert result.ordered_stop_ids[0] in {"A", "C"}
    assert result.ordered_stop_ids[-1] == "B"


def test_case2_same_priority_nearest_first():
    result = _run(SAME_PRI, "NEAREST")
    assert result.ordered_stop_ids == ["N1", "N2", "N3"]


def test_case3_same_priority_farthest_first():
    result = _run(SAME_PRI, "FARTHEST")
    assert result.ordered_stop_ids[0] == "N3"
    assert set(result.ordered_stop_ids) == {"N1", "N2", "N3"}


def test_case4_shortest_deterministic_and_not_worse_than_seed():
    first = _run(SAME_PRI, "SHORTEST")
    second = _run(SAME_PRI, "SHORTEST")
    assert first.ordered_stop_ids == second.ordered_stop_ids
    seed = _run(SAME_PRI, "NEAREST")
    assert first.total_estimated_m <= seed.total_estimated_m + 1e-6


def test_case5_mixed_priorities_never_cross():
    result = _run(STOPS_MIXED, "FARTHEST")
    ranks = {s["stop_id"]: s["priority_rank"] for s in STOPS_MIXED}
    seen = []
    for sid in result.ordered_stop_ids:
        rank = ranks[sid]
        assert all(prev <= rank for prev in seen)
        seen.append(rank)
    assert seen == [1, 1, 2, 3]


def test_case6_start_current_location_uses_snapshot():
    snap = {"lat": -6.111, "lng": 106.111, "accuracy_m": 12, "captured_at": "2026-09-03T00:00:00Z"}
    result = _run(SAME_PRI, "NEAREST", start_mode="CURRENT_LOCATION", start=snap)
    assert result.start["lat"] == -6.111
    assert result.start["lng"] == 106.111
    assert result.start["kind"] == "CURRENT_LOCATION"


def test_case7_start_warehouse_uses_configured_point():
    result = _run(SAME_PRI, "NEAREST", start_mode="WAREHOUSE", start=None, warehouse=WAREHOUSE)
    assert result.start["lat"] == WAREHOUSE["lat"]
    assert result.start["kind"] == "WAREHOUSE"


def test_case8_start_manual_uses_manual_point():
    result = _run(SAME_PRI, "NEAREST", start_mode="MANUAL", start=MANUAL_START)
    assert result.start["lat"] == MANUAL_START["lat"]
    assert result.start["kind"] == "MANUAL"


def test_case9_end_last_customer_no_return_leg():
    result = _run(SAME_PRI, "NEAREST", end_mode="LAST_STOP")
    assert result.end["kind"] == "LAST_STOP"
    assert all(seg["to"] != "END" for seg in result.segments)


def test_case10_end_return_start_includes_return_leg():
    result = _run(SAME_PRI, "NEAREST", end_mode="RETURN_START")
    end_seg = result.segments[-1]
    assert end_seg["to"] == "END"
    last_stop = next(s for s in SAME_PRI if s["stop_id"] == result.ordered_stop_ids[-1])
    expected = haversine_m((last_stop["lat"], last_stop["lng"]), (ORIGIN["lat"], ORIGIN["lng"]))
    assert abs(end_seg["estimated_m"] - expected) < 1e-6


def test_case11_end_warehouse_includes_warehouse_leg():
    result = _run(SAME_PRI, "NEAREST", end_mode="WAREHOUSE")
    end_seg = result.segments[-1]
    last_stop = next(s for s in SAME_PRI if s["stop_id"] == result.ordered_stop_ids[-1])
    expected = haversine_m((last_stop["lat"], last_stop["lng"]), (WAREHOUSE["lat"], WAREHOUSE["lng"]))
    assert abs(end_seg["estimated_m"] - expected) < 1e-6


def test_case12_end_manual_includes_manual_leg():
    result = _run(SAME_PRI, "NEAREST", end_mode="MANUAL", end=MANUAL_END)
    end_seg = result.segments[-1]
    last_stop = next(s for s in SAME_PRI if s["stop_id"] == result.ordered_stop_ids[-1])
    expected = haversine_m((last_stop["lat"], last_stop["lng"]), (MANUAL_END["lat"], MANUAL_END["lng"]))
    assert abs(end_seg["estimated_m"] - expected) < 1e-6


def test_case13_stop_without_coordinate_rejected():
    stops = SAME_PRI + [{"stop_id": "X", "lat": None, "lng": None, "priority_rank": 3}]
    with pytest.raises(OptimizerError) as exc:
        _run(stops, "NEAREST")
    assert exc.value.code == "STOPS_MISSING_LOCATION"
    assert "1 customer" in exc.value.message


def test_case14_manual_reorder_priority_violation_rejected():
    with pytest.raises(OptimizerError) as exc:
        validate_priority_order(STOPS_MIXED, ["B", "A", "C", "D"])
    assert exc.value.code == "PRIORITY_VIOLATION"


def test_case15_manual_reorder_same_priority_allowed():
    validate_priority_order(STOPS_MIXED, ["C", "A", "D", "B"])


def test_optimize_does_not_mutate_input_stops():
    stops = [dict(s) for s in STOPS_MIXED]
    before = [(s["lat"], s["lng"], s["priority_rank"]) for s in stops]
    _run(stops, "SHORTEST")
    after = [(s["lat"], s["lng"], s["priority_rank"]) for s in stops]
    assert before == after


def test_start_current_without_gps_rejected():
    with pytest.raises(OptimizerError) as exc:
        _run(SAME_PRI, "NEAREST", start_mode="CURRENT_LOCATION", start=None)
    assert exc.value.code == "START_GPS_UNAVAILABLE"


def test_warehouse_missing_rejected():
    with pytest.raises(OptimizerError) as exc:
        _run(SAME_PRI, "NEAREST", start_mode="WAREHOUSE", start=None, warehouse=None)
    assert exc.value.code == "WAREHOUSE_NOT_CONFIGURED"


def test_zero_zero_coordinate_rejected():
    stops = [{"stop_id": "Z", "lat": 0, "lng": 0, "priority_rank": 1}]
    with pytest.raises(OptimizerError):
        _run(stops, "NEAREST")


def test_tie_break_is_stable_by_stop_id():
    stops = [
        {"stop_id": "B-id", "lat": -6.176, "lng": 106.828, "priority_rank": 1},
        {"stop_id": "A-id", "lat": -6.176, "lng": 106.828, "priority_rank": 1},
    ]
    result = _run(stops, "NEAREST")
    assert result.ordered_stop_ids[0] == "A-id"
