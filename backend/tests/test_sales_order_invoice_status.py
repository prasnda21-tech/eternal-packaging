"""Focused tests for Pesanan Penjualan creator + invoice coverage status."""
from __future__ import annotations

import pytest

from services.sales_order_invoice_status import (
    STATUS_BELUM,
    STATUS_PERLU_PEMERIKSAAN,
    STATUS_SEBAGIAN,
    STATUS_SUDAH,
    attach_sales_order_list_invoice_status,
    classify_po_invoice_status,
    creator_display,
    user_can_see_invoice_status,
)


def test_creator_display_name_email_empty():
    assert creator_display("Budi Admin") == "Budi Admin"
    assert creator_display("budi@example.com") == "budi@example.com"
    assert creator_display("") == "Tidak diketahui"
    assert creator_display(None) == "Tidak diketahui"
    assert creator_display("   ") == "Tidak diketahui"


def _lines(a_qty, b_qty):
    return [
        {"line_idx": 0, "quantity": a_qty},
        {"line_idx": 1, "quantity": b_qty},
    ]


def test_belum_when_no_invoices():
    assert classify_po_invoice_status(_lines(1000, 500), []) == STATUS_BELUM


def test_sudah_when_draft_covers_all_lines():
    inv = {
        "financial_status": "draft",
        "lines": [
            {"line_idx": 0, "quantity": 1000},
            {"line_idx": 1, "quantity": 500},
        ],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_SUDAH


def test_sebagian_when_only_one_line_invoiced():
    inv = {
        "financial_status": "draft",
        "lines": [{"line_idx": 0, "quantity": 1000}],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_SEBAGIAN


def test_finalized_counts_like_draft():
    inv = {
        "financial_status": "finalized",
        "lines": [
            {"line_idx": 0, "quantity": 1000},
            {"line_idx": 1, "quantity": 500},
        ],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_SUDAH


def test_draft_plus_finalized_cumulative():
    invs = [
        {"financial_status": "draft", "lines": [{"line_idx": 0, "quantity": 400}]},
        {"financial_status": "finalized", "lines": [
            {"line_idx": 0, "quantity": 600},
            {"line_idx": 1, "quantity": 500},
        ]},
    ]
    assert classify_po_invoice_status(_lines(1000, 500), invs) == STATUS_SUDAH


def test_multi_invoice_partial():
    invs = [
        {"financial_status": "draft", "lines": [{"line_idx": 0, "quantity": 300}]},
        {"financial_status": "finalized", "lines": [{"line_idx": 1, "quantity": 200}]},
    ]
    assert classify_po_invoice_status(_lines(1000, 500), invs) == STATUS_SEBAGIAN


def test_cancelled_ignored_results_belum():
    inv = {
        "financial_status": "cancelled",
        "lines": [
            {"line_idx": 0, "quantity": 1000},
            {"line_idx": 1, "quantity": 500},
        ],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_BELUM


def test_legacy_empty_financial_status_counts_as_draft():
    inv = {
        "financial_status": None,
        "lines": [
            {"line_idx": 0, "quantity": 1000},
            {"line_idx": 1, "quantity": 500},
        ],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_SUDAH


def test_invalid_line_idx_needs_review():
    inv = {
        "financial_status": "draft",
        "lines": [{"quantity": 1000}],  # missing line_idx
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_PERLU_PEMERIKSAAN


def test_orphan_line_needs_review():
    inv = {
        "financial_status": "draft",
        "lines": [{"line_idx": 9, "quantity": 10}],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_PERLU_PEMERIKSAAN


def test_over_qty_needs_review():
    inv = {
        "financial_status": "draft",
        "lines": [{"line_idx": 0, "quantity": 1200}],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_PERLU_PEMERIKSAAN


def test_global_sum_cannot_mask_missing_line():
    """Excess on A must not cover missing B — result is Sebagian, not Sudah."""
    inv = {
        "financial_status": "draft",
        "lines": [{"line_idx": 0, "quantity": 1500}],  # over A but still not covering B
    }
    # Over on A → Perlu Pemeriksaan takes priority
    assert classify_po_invoice_status(_lines(1000, 500), [inv]) == STATUS_PERLU_PEMERIKSAAN
    inv_ok_a_missing_b = {
        "financial_status": "draft",
        "lines": [{"line_idx": 0, "quantity": 1000}],
    }
    assert classify_po_invoice_status(_lines(1000, 500), [inv_ok_a_missing_b]) == STATUS_SEBAGIAN


def test_user_can_see_invoice_status_rbac():
    assert user_can_see_invoice_status({"role": "admin"}) is True
    assert user_can_see_invoice_status({"role": "staff", "permissions": ["sales.invoice.view"]}) is True
    assert user_can_see_invoice_status({"role": "staff", "permissions": ["sales_order.view"]}) is False
    assert user_can_see_invoice_status(None) is False


@pytest.mark.anyio
async def test_attach_batch_page_only_two_queries():
    calls = {"orders": 0, "invoices": 0}

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, n):
            return list(self._rows)

    class _DB:
        class orders:
            @staticmethod
            def find(q, proj):
                calls["orders"] += 1
                ids = q.get("id", {}).get("$in", [])
                assert set(ids) == {"po-1", "po-2"}
                return _Cursor([
                    {"id": "po-1", "line_items": [{"line_idx": 0, "quantity": 10}]},
                    {"id": "po-2", "line_items": [{"line_idx": 0, "quantity": 5}]},
                ])

        class sales_invoices:
            @staticmethod
            def find(q, proj):
                calls["invoices"] += 1
                ids = q.get("po_id", {}).get("$in", [])
                assert set(ids) == {"po-1", "po-2"}
                return _Cursor([
                    {
                        "id": "inv-1",
                        "po_id": "po-1",
                        "financial_status": "draft",
                        "lines": [{"line_idx": 0, "quantity": 10}],
                    },
                ])

    rows = [{"id": "po-1"}, {"id": "po-2"}]
    await attach_sales_order_list_invoice_status(_DB(), rows)
    assert calls == {"orders": 1, "invoices": 1}
    assert rows[0]["invoice_status"] == STATUS_SUDAH
    assert rows[1]["invoice_status"] == STATUS_BELUM
    assert rows[0]["invoice_status_label"] == "Sudah"


def anyio_backend():
    return "asyncio"
