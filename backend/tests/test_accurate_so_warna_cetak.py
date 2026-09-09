"""Accurate SO line color mapping from structured Warna (charField1).

Free-text helpers remain unit-tested but are not used for SO import color.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    _detect_colors,
    _extract_warna_cetak_label_value,
    _normalize_accurate_so,
)


def test_warna_cetak_label_variants():
    assert _extract_warna_cetak_label_value("Warna Cetak: Merah") == "Merah"
    assert _extract_warna_cetak_label_value("Warna Cetak : Merah") == "Merah"
    assert _extract_warna_cetak_label_value("WARNA CETAK: Merah") == "Merah"
    assert _extract_warna_cetak_label_value("Warna: Merah") == "Merah"
    assert _extract_warna_cetak_label_value("SABLON WARNA HITAM") is None


def test_case1_structured_warna_merah():
    raw = {
        "id": 101,
        "number": "SO-COLOR-1",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "charField1": "Merah",
                "detailNotes": "Warna Cetak: ignored",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "Merah"
    assert so["line_items"][0]["accurate_line_id"] == "1"
    assert so["line_items"][0].get("color_needs_verify") is None


def test_case2_multiple_colors_preserved_in_full():
    raw = {
        "id": 102,
        "number": "SO-COLOR-2",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "charField1": "Merah, Hitam, Putih",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "Merah, Hitam, Putih"
    assert so["line_items"][0].get("color_needs_verify") is not True


def test_case2_structured_multi_color_keeps_appearance_order():
    raw = {
        "id": 1022,
        "number": "SO-COLOR-2B",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 1,
                "quantity": 10,
                "charField1": "PUTIH, HIJAU, PINK",
                "item": {"id": 9, "name": "Preview Cup"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "PUTIH, HIJAU, PINK"
    assert _detect_colors("SABLON WARNA MERAH / HITAM / PUTIH") == ["MERAH", "HITAM", "PUTIH"]


def test_case3_same_name_lines_keep_distinct_colors():
    raw = {
        "id": 103,
        "number": "SO-COLOR-3",
        "customer": {"name": "A"},
        "detailItem": [
            {
                "id": 11,
                "quantity": 100,
                "charField1": "Merah",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 12,
                "quantity": 200,
                "charField1": "Biru",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert len(so["line_items"]) == 2
    assert so["line_items"][0]["name"] == so["line_items"][1]["name"] == "Preview Cup"
    assert so["line_items"][0]["color"] == "Merah"
    assert so["line_items"][1]["color"] == "Biru"
    assert so["line_items"][0]["accurate_line_id"] == "11"
    assert so["line_items"][1]["accurate_line_id"] == "12"


def test_case4_structured_beats_header_and_notes():
    raw = {
        "id": 104,
        "number": "SO-COLOR-4",
        "customer": {"name": "A"},
        "description": "Warna Cetak: Putih",
        "detailItem": [
            {
                "id": 21,
                "quantity": 10,
                "charField1": "Merah",
                "detailNotes": "Warna Cetak: Hitam",
                "item": {"id": 1, "name": "Preview Cup"},
            },
            {
                "id": 22,
                "quantity": 10,
                "charField1": "Biru",
                "item": {"id": 1, "name": "Preview Cup"},
            },
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] == "Merah"
    assert so["line_items"][1]["color"] == "Biru"
    assert so["notes"] == "Warna Cetak: Putih"


def test_case5_no_structured_warna_stays_empty():
    raw = {
        "id": 105,
        "number": "SO-COLOR-5",
        "customer": {"name": "A"},
        "description": "SABLON WARNA HITAM",
        "detailItem": [
            {
                "id": 31,
                "quantity": 10,
                "detailNotes": "Warna Cetak: Merah",
                "item": {"id": 7, "name": "Paper bag medium"},
            }
        ],
    }
    so = _normalize_accurate_so(raw)
    assert so["line_items"][0]["color"] is None
    assert so["line_items"][0].get("color_source") is None
    assert so["line_items"][0].get("color_needs_verify") is None
