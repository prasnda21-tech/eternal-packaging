"""Parity: list invoiceable_summary shares rules with get_po_invoice_preview."""
from __future__ import annotations

import pytest

from services.sales_invoices import (
    accumulate_invoiced_qty_by_line_idx,
    batch_po_invoiceable_summaries,
    summarize_po_invoiceable_quantities,
)


def test_accumulate_skips_cancelled_only():
    invoices = [
        {"financial_status": "draft", "lines": [{"line_idx": 0, "quantity": 100}]},
        {"financial_status": "finalized", "lines": [{"line_idx": 0, "quantity": 50}]},
        {"financial_status": "cancelled", "lines": [{"line_idx": 0, "quantity": 999}]},
    ]
    assert accumulate_invoiced_qty_by_line_idx(invoices) == {0: 150}


def test_summarize_remaining_and_multi_unit():
    lines = [
        {"line_idx": 0, "quantity": 1000, "unit": "pcs"},
        {"line_idx": 1, "quantity": 10, "unit": "dus"},
    ]
    summary = summarize_po_invoiceable_quantities(lines, {0: 400, 1: 2})
    assert summary["available"] is True
    assert summary["total_remaining_invoice_qty"] == 608
    assert summary["remaining_by_unit"] == [
        {"unit": "pcs", "qty": 600},
        {"unit": "dus", "qty": 8},
    ]
    assert summary["lines"][0]["remaining_invoice_qty"] == 600
    assert summary["lines"][1]["remaining_invoice_qty"] == 8


def test_summarize_never_negative():
    summary = summarize_po_invoiceable_quantities(
        [{"line_idx": 0, "quantity": 10, "unit": "pcs"}],
        {0: 99},
    )
    assert summary["total_remaining_invoice_qty"] == 0
    assert summary["lines"][0]["remaining_invoice_qty"] == 0


@pytest.mark.anyio
async def test_batch_matches_preview_math(monkeypatch):
    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def __aiter__(self):
            self._i = 0
            return self

        async def __anext__(self):
            if self._i >= len(self._rows):
                raise StopAsyncIteration
            row = self._rows[self._i]
            self._i += 1
            return row

    class _FakeInvoices:
        def find(self, *_a, **_k):
            return _FakeCursor([
                {
                    "po_id": "po-a",
                    "financial_status": "draft",
                    "lines": [{"line_idx": 0, "quantity": 200}],
                },
                {
                    "po_id": "po-a",
                    "financial_status": "cancelled",
                    "lines": [{"line_idx": 0, "quantity": 500}],
                },
                {
                    "po_id": "po-b",
                    "financial_status": "finalized",
                    "lines": [{"line_idx": 0, "quantity": 50}],
                },
            ])

    class _FakeDb:
        sales_invoices = _FakeInvoices()

    orders = [
        {"id": "po-a", "line_items": [{"quantity": 1000, "unit": "pcs", "line_idx": 0}]},
        {"id": "po-b", "line_items": [{"quantity": 80, "unit": "pcs", "line_idx": 0}]},
    ]
    out = await batch_po_invoiceable_summaries(_FakeDb(), orders)
    assert out["po-a"]["total_remaining_invoice_qty"] == 800
    assert out["po-b"]["total_remaining_invoice_qty"] == 30
    assert out["po-a"]["available"] is True
    assert out["po-a"]["remaining_by_unit"] == [{"unit": "pcs", "qty": 800}]
