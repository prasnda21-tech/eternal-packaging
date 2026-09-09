"""Warna Cetak read-model enrichment + persistence regression.

Covers import mapping (new), same-line notes enrichment for legacy rows
(color null but notes still hold Warna Cetak), and API-shaped read output.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    _normalize_accurate_so,
    enrich_line_item_color_from_own_notes,
    enrich_order_line_item_colors,
)


def test_case1_single_color_persisted_exactly():
    raw = {
        "id": 201,
        "number": "SO-SYNC-1",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "detailNotes": "Warna Cetak: RED",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED"


def test_case2_same_name_lines_independent_red_blue():
    raw = {
        "id": 202,
        "number": "SO-SYNC-2",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 11,
                "quantity": 100,
                "detailNotes": "Warna Cetak: RED",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 12,
                "quantity": 200,
                "detailNotes": "Warna Cetak: BLUE",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["name"] == so["line_items"][1]["name"] == "Preview Cup"
    assert so["line_items"][0]["color"] == "RED"
    assert so["line_items"][1]["color"] == "BLUE"
    assert so["line_items"][0]["accurate_line_id"] == "11"
    assert so["line_items"][1]["accurate_line_id"] == "12"


def test_case3_multi_color_preserves_order():
    raw = {
        "id": 203,
        "number": "SO-SYNC-3",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "detailNotes": "Warna Cetak: RED / WHITE / BLACK",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED / WHITE / BLACK"


def test_case4_line_level_beats_header():
    raw = {
        "id": 204,
        "number": "SO-SYNC-4",
        "customer": {"name": "A"},
        "description": "Warna Cetak: WHITE",
        "detailItem": [
            {
                "id": 21,
                "quantity": 10,
                "detailNotes": "Warna Cetak: RED",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 22,
                "quantity": 10,
                "detailNotes": "Warna Cetak: BLUE",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED"
    assert so["line_items"][1]["color"] == "BLUE"


def test_case5_missing_color_stays_empty_no_cross_line():
    raw = {
        "id": 205,
        "number": "SO-SYNC-5",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 31,
                "quantity": 10,
                "detailNotes": "Warna Cetak: RED",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 32,
                "quantity": 10,
                "item": {"id": 7, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "RED"
    assert so["line_items"][1]["color"] is None


def test_case6_api_read_model_returns_exact_line_colors():
    """Simulate GET /orders/{id} payload after persist + enrichment."""
    order = {
        "id": "task-so-color-sync-01",
        "po_number": "TASK-SO-COLOR-SYNC-01",
        "line_items": [
            {
                "name": "Preview Cup",
                "color": "RED",
                "notes": "Warna Cetak: RED",
                "accurate_line_id": "sync-1",
            },
            {
                "name": "Preview Cup",
                "color": "BLUE",
                "notes": "Warna Cetak: BLUE",
                "accurate_line_id": "sync-2",
            },
            {
                "name": "Preview Cup",
                "color": "RED / WHITE / BLACK",
                "notes": "Warna Cetak: RED / WHITE / BLACK",
                "accurate_line_id": "sync-3",
            },
        ],
    }
    enrich_order_line_item_colors(order)
    assert [li["color"] for li in order["line_items"]] == [
        "RED",
        "BLUE",
        "RED / WHITE / BLACK",
    ]


def test_legacy_null_color_enriched_from_own_notes_only():
    """Pre-#178 style: color null, Warna Cetak still in notes → read enrichment."""
    legacy = {
        "name": "Preview Cup",
        "color": None,
        "notes": "Warna Cetak: Merah, Hitam, Putih",
        "accurate_line_id": "legacy-1",
    }
    other = {
        "name": "Preview Cup",
        "color": None,
        "notes": "Warna Cetak: Biru",
        "accurate_line_id": "legacy-2",
    }
    empty = {
        "name": "Preview Cup",
        "color": None,
        "notes": "",
        "accurate_line_id": "legacy-3",
    }
    assert enrich_line_item_color_from_own_notes(legacy)["color"] == "Merah, Hitam, Putih"
    assert enrich_line_item_color_from_own_notes(other)["color"] == "Biru"
    assert enrich_line_item_color_from_own_notes(empty)["color"] is None


def test_enrichment_does_not_override_persisted_color():
    li = {
        "name": "Preview Cup",
        "color": "RED",
        "notes": "Warna Cetak: BLUE",
    }
    assert enrich_line_item_color_from_own_notes(li)["color"] == "RED"


def test_enrichment_never_copies_sibling_line_color():
    order = {
        "line_items": [
            {"name": "Preview Cup", "color": "RED", "notes": "Warna Cetak: RED"},
            {"name": "Preview Cup", "color": None, "notes": ""},
        ]
    }
    enrich_order_line_item_colors(order)
    assert order["line_items"][0]["color"] == "RED"
    assert order["line_items"][1]["color"] is None
