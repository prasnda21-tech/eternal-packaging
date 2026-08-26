"""Unit tests for Status PO filters, columns, and delivered percent."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.status_po import (
    build_status_po,
    effective_workflow_status,
    filter_orders_for_view,
    matches_status_po_filters,
    status_po_row,
)
from services.shipment_status import status_percent


def _po(**kw):
    base = {
        "id": kw.get("id", "p1"),
        "po_number": kw.get("po_number", "PO-1"),
        "customer": kw.get("customer", "Acme"),
        "order_date": kw.get("order_date", "2026-08-26"),
        "current_stage": kw.get("current_stage", 8),
        "cancelled": False,
        "line_items": [{"quantity": 10000, "production_process": "print"}],
        "operator_splits": [{"qty_completed": 5000, "line_idx": 0, "quantity": 10000}],
        "qty_delivered": kw.get("qty_delivered", 5000),
        "total_quantity": 10000,
    }
    base.update(kw)
    return base


def test_partial_delivery_stays_50_percent():
    row = status_po_row(_po())
    assert row["percent_delivered"] == 50
    assert status_percent(5000, 10000) == 50
    assert "Status" in ("Status",)
    assert row["status"] in ("Parsial Terkirim", "Cetak berjalan", "Selesai Cetak", "Operator", "Penjadwalan")


def test_status_not_generic_stage_index_done():
    o = _po(current_stage=14, qty_delivered=5000, delivery_status="Sebagian Terkirim", terkirim_confirmed=False)
    assert effective_workflow_status(o) == "Parsial Terkirim"


def test_view_active_excludes_completed():
    orders = [_po(id="a", current_stage=8), _po(id="b", current_stage=14, qty_delivered=10000, delivery_status="Terkirim")]
    got = filter_orders_for_view(orders, "active")
    assert [o["id"] for o in got] == ["a"]


def test_view_work_today():
    orders = [
        _po(id="w", work_date="2026-08-26"),
        _po(id="x", work_date="2026-08-01"),
    ]
    now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
    got = filter_orders_for_view(orders, "work_today", now=now)
    assert [o["id"] for o in got] == ["w"]


def test_build_rows_have_required_columns():
    data = build_status_po([_po()])
    row = data["rows"][0]
    for key in ("po_number", "order_date", "customer", "status", "percent_delivered"):
        assert key in row


def _row(**kw):
    return {
        "po_number": kw.get("po_number", "PO-001"),
        "customer": kw.get("customer", "ABC Pack"),
        "status": kw.get("status", "Operator"),
        "order_date": kw.get("order_date", "2026-08-10"),
    }


def test_filter_po_partial():
    assert matches_status_po_filters(_row(), {"po": "PO-001"}) is True
    assert matches_status_po_filters(_row(po_number="PO-002"), {"po": "PO-001"}) is False
    assert matches_status_po_filters(_row(), {"po": "001"}) is True


def test_filter_customer_partial():
    assert matches_status_po_filters(_row(), {"customer": "ABC"}) is True
    assert matches_status_po_filters(_row(customer="XYZ"), {"customer": "ABC"}) is False


def test_filter_status_exact():
    assert matches_status_po_filters(_row(status="Operator"), {"status": "Operator"}) is True
    assert matches_status_po_filters(_row(status="Packing"), {"status": "Operator"}) is False


def test_filter_date_range():
    f = {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert matches_status_po_filters(_row(order_date="2026-08-10"), f) is True
    assert matches_status_po_filters(_row(order_date="2026-07-15"), f) is False


def test_filter_combined_and():
    f = {"customer": "ABC", "status": "Operator", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert matches_status_po_filters(_row(), f) is True
    assert matches_status_po_filters(_row(customer="XYZ"), f) is False
    assert matches_status_po_filters(_row(status="Packing"), f) is False
    assert matches_status_po_filters(_row(order_date="2026-07-15"), f) is False


def test_filter_empty_matches_all():
    assert matches_status_po_filters(_row(), {}) is True
    assert matches_status_po_filters(_row(), {"po": "", "customer": "", "status": ""}) is True


def test_build_status_po_applies_filters_after_view():
    orders = [
        _po(id="a", po_number="PO-001", customer="ABC", current_stage=8, order_date="2026-08-10"),
        _po(id="b", po_number="PO-002", customer="XYZ", current_stage=8, order_date="2026-08-20"),
        _po(id="c", po_number="PO-003", customer="ABC", current_stage=14, qty_delivered=10000, delivery_status="Terkirim", order_date="2026-08-12"),
    ]
    data = build_status_po(orders, "active", filters={"customer": "ABC"})
    assert [r["id"] for r in data["rows"]] == ["a"]
    assert data["total"] == 1
    reset = build_status_po(orders, "active")
    assert {r["id"] for r in reset["rows"]} == {"a", "b"}


def test_build_status_po_empty_filter_result():
    data = build_status_po([_po(po_number="PO-001")], filters={"po": "NOPE"})
    assert data["rows"] == []
    assert data["total"] == 0
