"""Focused unit tests for Pesanan Penjualan header filter helpers."""
from __future__ import annotations

import pytest

from services.sales_order_invoice_status import (
    STATUS_BELUM,
    STATUS_SEBAGIAN,
    STATUS_SUDAH,
    filter_orders_by_invoice_statuses,
    normalize_invoice_status_filters,
)
from services.sales_order_list_filters import (
    CREATOR_UNKNOWN_TOKEN,
    creator_mongo_clause,
    match_calendar_date_range,
    match_process_labels,
    match_qty_range,
    order_date_mongo_clause,
    sales_order_process_label,
    text_search_or_clause,
    validate_date_range,
    validate_qty_range,
)


def test_order_date_clause_and_validation():
    assert validate_date_range("2026-10-12", "2026-10-01")
    assert validate_date_range("2026-10-01", "2026-10-12") is None
    clause = order_date_mongo_clause("2026-10-01", "2026-10-11")
    assert clause["order_date"]["$gte"] == "2026-10-01"
    assert clause["order_date"]["$lte"].startswith("2026-10-11")


def test_customer_text_search_clause():
    clause = text_search_or_clause(" outlet ", ("customer", "po_number", "outlet"))
    assert clause is not None
    assert len(clause["$or"]) == 3


def test_shipping_date_range_null_fail_closed():
    assert match_calendar_date_range(None, "2026-10-01", None) is False
    assert match_calendar_date_range("2026-10-05", "2026-10-01", "2026-10-11") is True
    assert match_calendar_date_range("2026-09-30", "2026-10-01", None) is False
    assert match_calendar_date_range("2026-10-05", None, "2026-10-04") is False


def test_qty_single_unit_and_mixed_unit_safe():
    single = {"quantity_by_unit": [{"unit": "pcs", "quantity": 100}]}
    mixed = {"quantity_by_unit": [
        {"unit": "pcs", "quantity": 100},
        {"unit": "pack", "quantity": 2},
    ]}
    assert match_qty_range(single, qty_min=50, qty_max=150) is True
    assert match_qty_range(single, qty_min=120) is False
    assert match_qty_range(mixed, qty_min=1, qty_max=200) is False
    assert match_qty_range(mixed, qty_min=1, qty_max=3, qty_unit="pack") is True
    assert match_qty_range(mixed, qty_min=50, qty_unit="box") is False
    assert validate_qty_range("20", "5")


def test_process_label_independent_of_tab_semantics():
    assert sales_order_process_label({"cancelled": True, "hold": True}) == "Cancelled"
    assert sales_order_process_label({"hold": True, "current_stage": 1}) == "HOLD"
    assert sales_order_process_label({"current_stage": 5}) == "Penjadwalan"
    assert match_process_labels({"hold": True, "current_stage": 1}, ["HOLD"]) is True
    assert match_process_labels({"current_stage": 5}, ["HOLD"]) is False


def test_creator_unknown_clause():
    clause = creator_mongo_clause(CREATOR_UNKNOWN_TOKEN)
    assert "$or" in clause
    assert creator_mongo_clause("Budi Admin") == {"created_by_user": "Budi Admin"}


def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_invoice_filter_before_page_and_or_semantics():
    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        async def to_list(self, n):
            return list(self._docs)[:n]

    class _DB:
        class orders:
            @staticmethod
            def find(q, proj):
                ids = set(q.get("id", {}).get("$in") or [])
                docs = [
                    {"id": "po-001", "line_items": [{"line_idx": 0, "quantity": 10}]},
                    {"id": "po-002", "line_items": [{"line_idx": 0, "quantity": 10}]},
                    {"id": "po-003", "line_items": [{"line_idx": 0, "quantity": 10}]},
                ]
                return _Cursor([d for d in docs if d["id"] in ids])

        class sales_invoices:
            @staticmethod
            def find(q, proj):
                ids = set(q.get("po_id", {}).get("$in") or [])
                docs = [
                    {
                        "id": "inv-1",
                        "po_id": "po-001",
                        "financial_status": "draft",
                        "lines": [{"line_idx": 0, "quantity": 10}],
                    },
                    {
                        "id": "inv-2",
                        "po_id": "po-002",
                        "financial_status": "draft",
                        "lines": [{"line_idx": 0, "quantity": 4}],
                    },
                ]
                return _Cursor([d for d in docs if d["po_id"] in ids])

    orders = [{"id": "po-001"}, {"id": "po-002"}, {"id": "po-003"}]
    kept = await filter_orders_by_invoice_statuses(
        _DB(), orders, ["sudah", "sebagian"], chunk_size=2
    )
    assert [row["id"] for row in kept] == ["po-001", "po-002"]
    assert kept[0]["invoice_status"] == STATUS_SUDAH
    assert kept[1]["invoice_status"] == STATUS_SEBAGIAN
    assert normalize_invoice_status_filters("sudah,belum") == [STATUS_SUDAH, STATUS_BELUM]
