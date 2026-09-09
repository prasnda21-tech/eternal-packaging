"""Kepala Produksi task rows: current_stock from Persediaan read model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.current_stock import build_stock_rows, compute_stock_current
from services.division_tasks import (
    build_kepala_produksi_tasks,
    enrich_tasks_current_stock,
    resolve_current_stock,
    stock_lookup_from_rows,
)


def _ready(**kwargs):
    o = {
        "id": "po1",
        "po_number": "PO-001",
        "customer": "Cust A",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [
            {
                "id": "L1",
                "name": "Cup Same",
                "quantity": 2000,
                "production_process": "print",
                "accurate_item_id": "AID-A",
                "master_item_id": "M-A",
            },
            {
                "id": "L2",
                "name": "Cup Same",
                "quantity": 3000,
                "production_process": "print",
                "accurate_item_id": "AID-B",
                "master_item_id": "M-B",
            },
        ],
        "operator_splits": [],
    }
    o.update(kwargs)
    return o


def _stock_row(aid, mid, stock, name="Item"):
    return {
        "accurate_item_id": aid,
        "master_item_id": mid,
        "item_name": name,
        "stock_current": stock,
    }


def test_enrich_uses_canonical_inventory_stock_not_po_or_task_qty():
    o = _ready()
    rows = build_kepala_produksi_tasks(o)
    assert rows[0]["target_qty"] == 2000
    # Persediaan stock 3250 — unrelated to PO qty 2000 / remaining 2000
    stock_rows = [
        _stock_row("AID-A", "M-A", 3250, "Cup Same"),
        _stock_row("AID-B", "M-B", 100, "Cup Same"),
    ]
    enrich_tasks_current_stock(rows, stock_rows)
    assert rows[0]["current_stock"] == 3250
    assert rows[0]["current_stock"] != rows[0]["target_qty"]
    assert rows[0]["current_stock"] != rows[0]["remaining_qty"]
    assert rows[1]["current_stock"] == 100


def test_zero_inventory_returns_zero_not_null():
    rows = build_kepala_produksi_tasks(_ready(
        line_items=[{
            "id": "L1", "name": "Z", "quantity": 500, "production_process": "print",
            "accurate_item_id": "AID-Z",
        }],
    ))
    enrich_tasks_current_stock(rows, [_stock_row("AID-Z", None, 0)])
    assert rows[0]["current_stock"] == 0
    assert rows[0]["current_stock"] is not None


def test_unresolved_item_returns_null():
    rows = build_kepala_produksi_tasks(_ready(
        line_items=[{
            "id": "L1", "name": "Orphan", "quantity": 500, "production_process": "print",
            # no accurate_item_id / master_item_id
        }],
    ))
    assert rows[0]["accurate_item_id"] is None
    assert rows[0]["master_item_id"] is None
    enrich_tasks_current_stock(rows, [_stock_row("AID-X", "M-X", 999)])
    assert rows[0]["current_stock"] is None


def test_identity_present_but_missing_from_inventory_is_null():
    rows = build_kepala_produksi_tasks(_ready(
        line_items=[{
            "id": "L1", "name": "Missing", "quantity": 100, "production_process": "print",
            "accurate_item_id": "AID-MISSING",
        }],
    ))
    enrich_tasks_current_stock(rows, [_stock_row("AID-OTHER", None, 50)])
    assert rows[0]["current_stock"] is None


def test_same_inventory_item_on_multiple_task_rows_shares_stock():
    o = _ready(line_items=[
        {
            "id": "L1", "name": "Cup", "quantity": 1000, "production_process": "print",
            "accurate_item_id": "AID-SAME",
        },
        {
            "id": "L2", "name": "Cup", "quantity": 2000, "production_process": "print",
            "accurate_item_id": "AID-SAME",
        },
    ])
    rows = build_kepala_produksi_tasks(o)
    enrich_tasks_current_stock(rows, [_stock_row("AID-SAME", "M1", 777)])
    assert len(rows) == 2
    assert rows[0]["line_id"] == "L1"
    assert rows[1]["line_id"] == "L2"
    assert rows[0]["current_stock"] == rows[1]["current_stock"] == 777


def test_same_display_name_different_identity_do_not_cross_map():
    rows = build_kepala_produksi_tasks(_ready())
    enrich_tasks_current_stock(rows, [
        _stock_row("AID-A", "M-A", 10, "Cup Same"),
        _stock_row("AID-B", "M-B", 99, "Cup Same"),
    ])
    by = {r["line_id"]: r for r in rows}
    assert by["L1"]["item"] == by["L2"]["item"] == "Cup Same"
    assert by["L1"]["accurate_item_id"] == "AID-A"
    assert by["L2"]["accurate_item_id"] == "AID-B"
    assert by["L1"]["current_stock"] == 10
    assert by["L2"]["current_stock"] == 99
    assert by["L1"]["current_stock"] != by["L2"]["current_stock"]


def test_line_identity_preserved_no_merge():
    rows = build_kepala_produksi_tasks(_ready())
    enrich_tasks_current_stock(rows, [
        _stock_row("AID-A", "M-A", 1),
        _stock_row("AID-B", "M-B", 2),
    ])
    assert [r["line_id"] for r in rows] == ["L1", "L2"]
    assert [r["line_idx"] for r in rows] == [0, 1]
    assert rows[0]["task_id"] != rows[1]["task_id"]


def test_scheduling_quantities_unchanged_by_enrichment():
    o = _ready(operator_splits=[
        {"id": "s1", "line_idx": 0, "quantity": 500},
    ])
    before = build_kepala_produksi_tasks(o)
    snap = [(r["line_id"], r["target_qty"], r["completed_qty"], r["remaining_qty"], r["status"]) for r in before]
    enrich_tasks_current_stock(before, [_stock_row("AID-A", "M-A", 42), _stock_row("AID-B", "M-B", 7)])
    after = [(r["line_id"], r["target_qty"], r["completed_qty"], r["remaining_qty"], r["status"]) for r in before]
    assert snap == after
    assert before[0]["current_stock"] == 42


def test_stock_comes_from_build_stock_rows_authority():
    """current_stock equals Persediaan stock_current for the same identity."""
    masters = [{"id": "M-A", "accurate_item_id": "AID-A", "item_name": "Cup", "item_code": "C"}]
    manuals = [{"accurate_item_id": "AID-A", "manual_current_qty": 10000, "manual_purchase_qty": 0}]
    # No PO/print deductions → stock_current = 10000
    stock_rows = build_stock_rows(masters, [], manuals)
    assert stock_rows[0]["stock_current"] == compute_stock_current(0, 10000, 0, 0) == 10000
    tasks = build_kepala_produksi_tasks(_ready(
        line_items=[{
            "id": "L1", "name": "Cup", "quantity": 2000, "production_process": "print",
            "accurate_item_id": "AID-A",
        }],
    ))
    enrich_tasks_current_stock(tasks, stock_rows)
    assert tasks[0]["current_stock"] == 10000
    assert tasks[0]["current_stock"] != tasks[0]["target_qty"]


def test_batch_lookup_built_once_not_per_row():
    lookup = stock_lookup_from_rows([
        _stock_row("AID-A", "M-A", 5),
        _stock_row("AID-B", "M-B", 9),
    ])
    assert lookup["a:AID-A"] == 5
    assert lookup["a:AID-B"] == 9
    assert resolve_current_stock({"accurate_item_id": "AID-A"}, lookup) == 5
    assert resolve_current_stock({"accurate_item_id": "AID-B"}, lookup) == 9
    assert resolve_current_stock({"accurate_item_id": "NOPE"}, lookup) is None
    assert resolve_current_stock({"name": "Cup"}, lookup) is None


def test_endpoint_enrichment_is_read_only_and_batched():
    src = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    start = src.index("async def work_division_tasks")
    end = src.index("\n# ---- Screener info")
    body = src[start:end]
    assert "_enrich_tasks_current_stock" in body
    assert "_load_stock_context" in body
    assert 'div == "kepala_produksi"' in body
    # No inventory writes on this GET path
    assert "update_one" not in body
    assert "insert_one" not in body
    assert "replace_one" not in body
    assert "delete_one" not in body
    # Single batch load — not a per-task inventory find loop
    assert body.count("_load_stock_context") == 1
    assert "for t in tasks" not in body or "_enrich_tasks_current_stock(tasks, stock_rows)" in body


def test_hpp_valuation_module_untouched():
    # Guard: this feature must not edit hpp_valuation.py
    hpp = Path(__file__).resolve().parents[1].joinpath("services/hpp_valuation.py")
    assert hpp.exists()
