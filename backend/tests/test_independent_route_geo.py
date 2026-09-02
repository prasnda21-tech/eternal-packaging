"""Haversine / coordinate validation — no network."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.independent_route.geo import as_point, haversine_m, is_valid_coordinate


def test_identical_points_are_zero():
    assert haversine_m((-6.2, 106.8), (-6.2, 106.8)) == 0.0


def test_known_relative_points_jakarta_bandung():
    jakarta = (-6.2088, 106.8456)
    bandung = (-6.9175, 107.6191)
    dist = haversine_m(jakarta, bandung)
    assert 110_000 < dist < 160_000


def test_invalid_coordinates_rejected():
    assert is_valid_coordinate(None, 106) is False
    assert is_valid_coordinate(-6, None) is False
    assert is_valid_coordinate("x", 106) is False
    assert is_valid_coordinate(91, 106) is False
    assert is_valid_coordinate(-6, 200) is False
    assert is_valid_coordinate(0, 0) is False
    assert is_valid_coordinate(0.0, 0.0) is False
    assert as_point(0, 0) is None
    assert is_valid_coordinate(-6.2, 106.8) is True


def test_nan_rejected():
    assert is_valid_coordinate(float("nan"), 106) is False
    assert is_valid_coordinate(-6, float("inf")) is False


def test_haversine_is_symmetric():
    a = (-6.1, 106.7)
    b = (-6.3, 106.9)
    assert math.isclose(haversine_m(a, b), haversine_m(b, a), rel_tol=1e-12)
