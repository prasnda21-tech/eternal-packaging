"""SJ cancel soft-cancels exact-SJ Loading; multi-SJ isolation; fail-closed stock."""
from __future__ import annotations

import pytest

from services.physical_stock import (
    MSG_LOADING_STOCK_UNSUPPORTED,
    PhysicalStockValidationError,
    _assert_no_unsupported_loading_stock,
    _expand_sj_cancel_identity,
    _loading_confirmation_matches_sj,
    _soft_cancel_loading_for_sj_identity,
)
from quantity_rules import (
    available_for_loading_confirm_line,
    loading_confirmations_stored,
    qty_loaded_for_line,
)
from services.line_product_replacement import collect_order_embedded_downstream_hits


def _order_two_sj():
    return {
        "id": "po-1",
        "line_items": [
            {"name": "Cup", "quantity": 1000, "production_process": "print", "line_id": "L0"},
            {"name": "Lid", "quantity": 1000, "production_process": "non_print", "line_id": "L1"},
        ],
        "packing_info": {
            "qty_packed": 1000,
            "lines": [{"line_idx": 0, "packed_qty": 1000}],
        },
        "shipments": [
            {
                "id": "sj-a",
                "number": "SJ-A",
                "sales_delivery_id": "sj-a",
                "lines": [{"line_idx": 0, "qty": 400}, {"line_idx": 1, "qty": 400}],
            },
            {
                "id": "sj-b",
                "number": "SJ-B",
                "sales_delivery_id": "sj-b",
                "lines": [{"line_idx": 0, "qty": 600}, {"line_idx": 1, "qty": 600}],
            },
        ],
        "surat_jalan_files": [
            {"id": "sj-a", "shipment_id": "sj-a", "sj_number": "SJ-A", "name": "a.pdf"},
            {"id": "sj-b", "shipment_id": "sj-b", "sj_number": "SJ-B", "name": "b.pdf"},
        ],
        "loading_confirmations": [
            {
                "id": "lc-a1",
                "surat_jalan_id": "sj-a",
                "shipment_id": "sj-a",
                "sj_number": "SJ-A",
                "surat_jalan_name": "SJ-A",
                "confirmed_by": "a@x",
                "confirmed_at": "2026-01-01T00:00:00+00:00",
                "lines": [{"line_idx": 0, "qty": 400, "name": "Cup"}, {"line_idx": 1, "qty": 400, "name": "Lid"}],
            },
            {
                "id": "lc-a2",
                "surat_jalan_id": "sj-a",
                "shipment_id": "sj-a",
                "sj_number": "SJ-A",
                "lines": [{"line_idx": 0, "qty": 0}],  # edge — still matched by SJ
            },
            {
                "id": "lc-b",
                "surat_jalan_id": "sj-b",
                "shipment_id": "sj-b",
                "sj_number": "SJ-B",
                "lines": [{"line_idx": 0, "qty": 600, "name": "Cup"}, {"line_idx": 1, "qty": 600, "name": "Lid"}],
            },
        ],
        "loading_snapshots": [
            {"id": "snap-a", "surat_jalan_id": "sj-a", "shipment_id": "sj-a", "status": "active"},
            {"id": "snap-b", "surat_jalan_id": "sj-b", "shipment_id": "sj-b", "status": "active"},
        ],
    }


def test_identity_match_and_expand():
    order = _order_two_sj()
    identity = {"sj-a", "SJ-A"}
    expanded = _expand_sj_cancel_identity(order, identity)
    assert "sj-a" in expanded
    assert "SJ-A" in expanded
    assert _loading_confirmation_matches_sj(order["loading_confirmations"][0], expanded)
    assert not _loading_confirmation_matches_sj(order["loading_confirmations"][2], expanded)


def test_soft_cancel_only_matching_sj_and_releases_qty():
    order = _order_two_sj()
    before_cup = qty_loaded_for_line(order, 0)
    assert before_cup == 1000
    expanded = _expand_sj_cancel_identity(order, {"sj-a"})
    cancelled = _soft_cancel_loading_for_sj_identity(
        order, expanded, actor_id="admin@x", cancelled_at="t1", reason="Pembatalan Surat Jalan",
    )
    assert set(cancelled) == {"lc-a1", "lc-a2"}
    active = loading_confirmations_stored(order)
    assert [c["id"] for c in active] == ["lc-b"]
    assert order["loading_confirmations"][0]["cancelled"] is True
    assert order["loading_confirmations"][0]["confirmed_by"] == "a@x"
    assert order["loading_confirmations"][0]["cancelled_reason"] == "Pembatalan Surat Jalan"
    assert order["loading_snapshots"][0]["status"] == "cancelled"
    assert order["loading_snapshots"][1]["status"] == "active"
    assert qty_loaded_for_line(order, 0) == 600
    assert available_for_loading_confirm_line(order, 0) == 400  # packed 1000 - loaded 600


def test_soft_cancel_idempotent():
    order = _order_two_sj()
    expanded = _expand_sj_cancel_identity(order, {"sj-a"})
    first = _soft_cancel_loading_for_sj_identity(
        order, expanded, actor_id="a", cancelled_at="t1", reason="x",
    )
    second = _soft_cancel_loading_for_sj_identity(
        order, expanded, actor_id="a", cancelled_at="t2", reason="x",
    )
    assert first
    assert second == []


def test_cancelled_loading_does_not_block_product_replace_hits():
    order = _order_two_sj()
    assert "loading" in collect_order_embedded_downstream_hits(order, 1)
    expanded = _expand_sj_cancel_identity(order, {"sj-a", "sj-b"})
    _soft_cancel_loading_for_sj_identity(
        order, expanded, actor_id="a", cancelled_at="t", reason="x",
    )
    assert "loading" not in collect_order_embedded_downstream_hits(order, 1)


def test_active_loading_still_blocks():
    order = _order_two_sj()
    hits = collect_order_embedded_downstream_hits(order, 0)
    assert "loading" in hits


def test_fail_closed_when_loading_stock_movement_exists():
    import asyncio

    class _FakeCursor:
        def __init__(self, doc):
            self._doc = doc

        async def find_one(self, *a, **k):
            return self._doc

    class _FakeDb:
        def __init__(self, mov):
            self.stock_movements = _FakeCursor(mov)
            self.product_stock_state = _FakeCursor(None)

    db = _FakeDb({"id": "m1"})

    async def _run():
        with pytest.raises(PhysicalStockValidationError) as exc:
            await _assert_no_unsupported_loading_stock(
                db, order_id="po-1", confirmation_ids=["lc-a1"],
            )
        assert MSG_LOADING_STOCK_UNSUPPORTED in str(exc.value)

    asyncio.run(_run())


def test_fail_closed_passes_when_no_loading_movements():
    import asyncio

    class _FakeCursor:
        async def find_one(self, *a, **k):
            return None

    class _FakeDb:
        stock_movements = _FakeCursor()
        product_stock_state = _FakeCursor()

    asyncio.run(_assert_no_unsupported_loading_stock(
        _FakeDb(), order_id="po-1", confirmation_ids=["lc-a1"],
    ))
