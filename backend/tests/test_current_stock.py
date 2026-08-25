"""Unit tests for Stock Saat Ini formula, identity, aggregation, filters, export set."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.current_stock import (
    PURCHASE_SOURCE,
    PURCHASE_SOURCE_ACCURATE,
    aggregate_operator_print_qty,
    aggregate_po_qty,
    build_stock_rows,
    compute_stock_current,
    filter_sort_page,
    identity_keys,
    parse_manual_qty,
    qty_num,
)


def test_formula_accurate_6500():
    # Qty Saat Ini 10000 + Pembelian Accurate 3500 - PO 3000 - Cetak 4000 = 6500
    assert compute_stock_current(3000, 10000, 3500, 4000) == 6500


def test_formula_final_5000():
    # (Qty Saat Ini 10000 + Pembelian 2000) - Qty PO 3000 - Qty Cetak 4000 = 5000
    assert compute_stock_current(3000, 10000, 2000, 4000) == 5000


def test_formula_negative_not_clamped():
    # (2000 + 1000) - 4000 - 1000 = -2000
    assert compute_stock_current(4000, 2000, 1000, 1000) == -2000


def test_old_formula_removed():
    # Old (cancelled): 10000 - (2000 + 1000) - 3000 = 4000
    # New: (2000 + 1000) - 10000 - 3000 = -10000
    assert compute_stock_current(10000, 2000, 1000, 3000) == -10000
    assert compute_stock_current(10000, 2000, 1000, 3000) != 4000


def test_formula_never_uses_money():
    # line with amount/price must not affect compute — only qty args
    assert compute_stock_current(10, 0, 0, 0) == -10


def test_parse_manual_rejects_negative_nan():
    assert parse_manual_qty(0) == 0
    assert parse_manual_qty("2000") == 2000
    try:
        parse_manual_qty(-1)
        assert False
    except ValueError:
        pass
    try:
        parse_manual_qty("abc")
        assert False
    except ValueError:
        pass
    try:
        parse_manual_qty("NaN")
        assert False
    except ValueError:
        pass


def test_parse_manual_current_allows_negative():
    assert parse_manual_qty(-1000, allow_negative=True) == -1000
    assert parse_manual_qty("-2500", allow_negative=True) == -2500
    # purchase / other fields still reject negative
    try:
        parse_manual_qty(-1)
        assert False
    except ValueError:
        pass


def test_formula_negative_qty_saat_ini_preserved():
    # Qty Saat Ini -1000 + Pembelian 3000 - PO 500 - Cetak 250 = 1250
    assert compute_stock_current(500, -1000, 3000, 250) == 1250


def _master(aid, name, mid=None, code=None, unit="pcs"):
    return {
        "id": mid or f"m-{aid}",
        "accurate_item_id": str(aid),
        "item_code": code or f"C-{aid}",
        "item_name": name,
        "unit": unit,
    }


def _order(lines, splits=None, cancelled=False):
    return {
        "cancelled": cancelled,
        "line_items": lines,
        "operator_splits": splits or [],
    }


def test_items_independent():
    masters = [_master("A", "Item A"), _master("B", "Item B")]
    orders = [_order([
        {"accurate_item_id": "A", "master_item_id": "m-A", "name": "Item A", "quantity": 10000, "production_process": "print"},
        {"accurate_item_id": "B", "master_item_id": "m-B", "name": "Item B", "quantity": 5000, "production_process": "print"},
    ], [
        {"line_idx": 0, "qty_completed": 3000},
        {"line_idx": 1, "qty_completed": 1000},
    ])]
    manuals = [
        {"accurate_item_id": "A", "master_item_id": "m-A", "manual_current_qty": 2000, "manual_purchase_qty": 1000},
        {"accurate_item_id": "B", "master_item_id": "m-B", "manual_current_qty": 0, "manual_purchase_qty": 0},
    ]
    rows = {r["accurate_item_id"]: r for r in build_stock_rows(masters, orders, manuals)}
    # A: (2000 + 1000) - 10000 - 3000 = -10000
    # B: (0 + 0) - 5000 - 1000 = -6000
    assert rows["A"]["stock_current"] == -10000
    assert rows["B"]["stock_current"] == -6000
    assert rows["A"]["qty_po"] == 10000
    assert rows["B"]["qty_po"] == 5000


def test_same_name_different_accurate_id():
    masters = [
        _master("101", "Cup 12 oz", mid="m1"),
        _master("202", "Cup 12 oz", mid="m2"),
    ]
    orders = [_order([
        {"accurate_item_id": "101", "master_item_id": "m1", "name": "Cup 12 oz", "quantity": 10},
        {"accurate_item_id": "202", "master_item_id": "m2", "name": "Cup 12 oz", "quantity": 99},
    ])]
    rows = build_stock_rows(masters, orders, [])
    assert len(rows) == 2
    by = {r["accurate_item_id"]: r for r in rows}
    assert by["101"]["qty_po"] == 10
    assert by["202"]["qty_po"] == 99
    assert identity_keys(by["101"]) != identity_keys(by["202"])


def test_no_name_matching_orphan_line():
    masters = [_master("101", "Cup")]
    orders = [_order([{"name": "Cup", "quantity": 999}])]  # no IDs
    rows = build_stock_rows(masters, orders, [])
    assert rows[0]["qty_po"] == 0


def test_cancelled_po_excluded():
    masters = [_master("101", "Cup")]
    orders = [
        _order([{"accurate_item_id": "101", "quantity": 50}], cancelled=True),
        _order([{"accurate_item_id": "101", "quantity": 7}]),
    ]
    rows = build_stock_rows(masters, orders, [])
    assert rows[0]["qty_po"] == 7


def test_po_qty_is_line_quantity_not_amount():
    masters = [_master("101", "Cup")]
    orders = [_order([{
        "accurate_item_id": "101",
        "quantity": 12,
        "unit_price": 5000,
        "amount": 60000,
        "subtotal": 60000,
    }])]
    rows = build_stock_rows(masters, orders, [])
    assert rows[0]["qty_po"] == 12
    assert rows[0]["stock_current"] == -12


def test_operator_print_snapshot_no_double_count():
    masters = [_master("101", "Cup")]
    # One split snapshot 3000 — saving again with 3000 still 3000 (single field).
    orders = [_order(
        [{"accurate_item_id": "101", "quantity": 10000, "production_process": "print"}],
        [{"line_idx": 0, "qty_completed": 3000}],
    )]
    rows = build_stock_rows(masters, orders, [])
    assert rows[0]["operator_print_qty"] == 3000
    # two splits on same line = sum of snapshots (two operators), not events
    orders2 = [_order(
        [{"accurate_item_id": "101", "quantity": 10000, "production_process": "print"}],
        [
            {"line_idx": 0, "qty_completed": 2000},
            {"line_idx": 0, "qty_completed": 1000},
        ],
    )]
    rows2 = build_stock_rows(masters, orders2, [])
    assert rows2[0]["operator_print_qty"] == 3000


def test_operator_print_ignores_unassigned_when_multi_line():
    masters = [_master("A", "A"), _master("B", "B")]
    orders = [_order([
        {"accurate_item_id": "A", "quantity": 10, "production_process": "print"},
        {"accurate_item_id": "B", "quantity": 10, "production_process": "print"},
    ], [
        {"line_idx": None, "qty_completed": 99},
    ])]
    rows = {r["accurate_item_id"]: r for r in build_stock_rows(masters, orders, [])}
    assert rows["A"]["operator_print_qty"] == 0
    assert rows["B"]["operator_print_qty"] == 0


def test_purchase_source_is_manual_fallback():
    assert PURCHASE_SOURCE == "MANUAL_FALLBACK"
    rows = build_stock_rows([_master("1", "X")], [], [{"accurate_item_id": "1", "manual_purchase_qty": 5}])
    assert rows[0]["purchase_source"] == "MANUAL_FALLBACK"
    assert rows[0]["purchase_qty"] == 5


def test_accurate_purchase_no_double_count_with_manual():
    masters = [_master("101", "Cup")]
    manuals = [{"accurate_item_id": "101", "manual_current_qty": 10000, "manual_purchase_qty": 1000}]
    receipts = [{
        "accurate_receipt_id": "R1",
        "valid": True,
        "lines": [{"accurate_item_id": "101", "received_qty": 3500}],
    }]
    rows = build_stock_rows(
        masters, [], manuals, receipts, purchase_source=PURCHASE_SOURCE_ACCURATE,
    )
    assert rows[0]["purchase_qty"] == 3500
    assert rows[0]["purchase_qty"] != 4500
    assert rows[0]["stock_current"] == 13500
    assert rows[0]["purchase_source"] == "ACCURATE"


def test_accurate_formula_example_6500():
    masters = [_master("101", "Cup")]
    manuals = [{"accurate_item_id": "101", "manual_current_qty": 10000, "manual_purchase_qty": 1000}]
    orders = [_order(
        [{"accurate_item_id": "101", "quantity": 3000, "production_process": "print"}],
        [{"line_idx": 0, "qty_completed": 4000}],
    )]
    receipts = [
        {"accurate_receipt_id": "R1", "valid": True, "lines": [{"accurate_item_id": "101", "received_qty": 2000}]},
        {"accurate_receipt_id": "R2", "valid": True, "lines": [{"accurate_item_id": "101", "received_qty": 1500}]},
    ]
    rows = build_stock_rows(
        masters, orders, manuals, receipts, purchase_source=PURCHASE_SOURCE_ACCURATE,
    )
    assert rows[0]["purchase_qty"] == 3500
    assert rows[0]["qty_po"] == 3000
    assert rows[0]["operator_print_qty"] == 4000
    assert rows[0]["stock_current"] == 6500


def test_accurate_partial_receipt_not_po_qty():
    masters = [_master("101", "Cup")]
    orders = [_order([{"accurate_item_id": "101", "quantity": 10000}])]
    receipts = [{
        "accurate_receipt_id": "R1", "valid": True,
        "lines": [{"accurate_item_id": "101", "received_qty": 4000}],
    }]
    rows = build_stock_rows(
        masters, orders, [], receipts, purchase_source=PURCHASE_SOURCE_ACCURATE,
    )
    assert rows[0]["qty_po"] == 10000
    assert rows[0]["purchase_qty"] == 4000
    assert rows[0]["purchase_qty"] != 10000


def test_accurate_same_name_different_id():
    masters = [_master("101", "Cup", mid="m1"), _master("202", "Cup", mid="m2")]
    receipts = [{
        "accurate_receipt_id": "R1", "valid": True,
        "lines": [
            {"accurate_item_id": "101", "received_qty": 2000},
            {"accurate_item_id": "202", "received_qty": 5000},
        ],
    }]
    rows = {r["accurate_item_id"]: r for r in build_stock_rows(
        masters, [], [], receipts, purchase_source=PURCHASE_SOURCE_ACCURATE,
    )}
    assert rows["101"]["purchase_qty"] == 2000
    assert rows["202"]["purchase_qty"] == 5000


def test_filter_negative_export_set():
    rows = [
        {"item_name": "Neg", "item_code": "N", "stock_current": -1000, "qty_po": 1},
        {"item_name": "Pos", "item_code": "P", "stock_current": 4, "qty_po": 1},
        {"item_name": "Zero", "item_code": "Z", "stock_current": 0, "qty_po": 1},
    ]
    out = filter_sort_page(rows, stock_filter="negative", page_size=50)
    assert out["total"] == 1
    assert out["items"][0]["item_code"] == "N"
    search = filter_sort_page(rows, q="pos", stock_filter="all")
    assert search["total"] == 1
    assert search["items"][0]["item_code"] == "P"
    assert out["formula"] == "(manual_current_qty + purchase_qty) - po_qty - operator_print_qty"


def test_qty_num_whole_int():
    assert qty_num(4000.0) == 4000
    assert isinstance(qty_num(4000.0), int)


def test_aggregate_po_skips_cancelled_and_nameless():
    bucket, _als = aggregate_po_qty([
        _order([{"accurate_item_id": "1", "quantity": 3}], cancelled=True),
        _order([{"name": "Ghost", "quantity": 9}]),
        _order([{"accurate_item_id": "1", "quantity": 4}]),
    ])
    assert list(bucket.values()) == [4]


def test_operator_aggregate_uses_qty_completed_not_assigned():
    bucket, _ = aggregate_operator_print_qty([_order(
        [{"accurate_item_id": "1", "quantity": 100, "production_process": "print"}],
        [{"line_idx": 0, "quantity": 100, "qty_completed": 40}],
    )])
    assert list(bucket.values()) == [40]
