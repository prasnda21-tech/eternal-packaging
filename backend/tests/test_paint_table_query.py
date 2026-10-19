from services.paint_table_query import (
    apply_filters,
    parse_header_filters,
    parse_sort,
    sort_rows,
)


def test_parse_sort_whitelist():
    field, direction = parse_sort("qty", "asc", ("name", "qty"), "name")
    assert field == "qty"
    assert direction == 1
    field2, direction2 = parse_sort("hack", "desc", ("name",), "name")
    assert field2 == "name"
    assert direction2 == -1


def test_filter_before_sort_stable():
    rows = [
        {"id": "a", "notes": "alpha", "quantity_grams": 30},
        {"id": "b", "notes": "beta", "quantity_grams": 10},
        {"id": "c", "notes": "alpha-2", "quantity_grams": 20},
    ]
    hf = parse_header_filters(["notes:contains:alpha"], ["notes", "quantity_grams"])
    filtered = apply_filters(rows, hf)
    assert len(filtered) == 2
    sorted_rows = sort_rows(filtered, sort_by="quantity_grams", sort_dir=1, tie_break="id")
    assert [r["id"] for r in sorted_rows] == ["c", "a"]
