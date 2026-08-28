"""Unit tests for Accurate SO path audit walker. No live Accurate calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate_so_audit import (
    filter_target_hits,
    interesting_scalars,
    is_secret_key,
    unique_key_names,
    walk_scalar_paths,
)


SAMPLE = {
    "number": "PO/2026/08/28/000004",
    "authorization": "should-not-appear",
    "access_token": "secret",
    "shipment": {"id": 7, "name": "OUTLET"},
    "branch": {"id": 2},
    "detailItem": [
        {
            "detailNotes": "SABLON WARNA HITAM",
            "item": {"id": 418, "no": "100418", "name": "Plastik PE 15"},
            "dataClassification1Name": "JS",
        }
    ],
}


def test_walker_redacts_secret_keys_and_finds_scalars():
    scalars = walk_scalar_paths(SAMPLE)
    paths = [p for p, _ in scalars]
    assert "authorization" not in paths
    assert "access_token" not in paths
    assert ("shipment.name", "OUTLET") in scalars
    assert ("detailItem[0].detailNotes", "SABLON WARNA HITAM") in scalars
    assert ("detailItem[0].dataClassification1Name", "JS") in scalars
    assert ("branch.id", "2") in scalars


def test_filter_finds_js_hitam_outlet():
    hits = filter_target_hits(walk_scalar_paths(SAMPLE), ("JS", "HITAM", "OUTLET"))
    matched = {(path, target) for path, _value, target in hits}
    assert ("detailItem[0].dataClassification1Name", "JS") in matched
    assert ("detailItem[0].detailNotes", "HITAM") in matched
    assert ("shipment.name", "OUTLET") in matched


def test_secret_key_detector():
    assert is_secret_key("Authorization")
    assert is_secret_key("refresh_token")
    assert is_secret_key("X-Api-Signature")
    assert not is_secret_key("shipmentName")


def test_interesting_scalars_include_branch_and_notes():
    rows = interesting_scalars(walk_scalar_paths(SAMPLE))
    paths = {p for p, _ in rows}
    assert "branch.id" in paths
    assert "detailItem[0].detailNotes" in paths
    assert "shipment.name" in paths


def test_unique_keys_omit_secrets():
    names = unique_key_names(SAMPLE)
    assert "number" in names
    assert "detailNotes" in names
    assert "authorization" not in names
    assert "access_token" not in names
