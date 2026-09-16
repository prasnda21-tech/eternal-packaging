"""OFF-state compatibility: toggle is the V2 authority boundary.

GET current-stock must not write app_settings / product_stock_state / stock_movements.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.current_stock import build_stock_rows, compute_stock_current
from services.stock_engine_v2 import (
    STOCK_INSUFFICIENT_MSG,
    build_stock_rows_v2,
    validate_sales_order_availability,
)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def to_list(self, n=None):
        async def _inner():
            return list(self._docs)
        return _inner() if False else _AsList(self._docs)

    def sort(self, *a, **k):
        return self


class _AsList(list):
    def __await__(self):
        async def _inner():
            return list(self)
        return _inner().__await__()

    async def to_list(self, n=None):
        return list(self)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.writes = 0

    def find(self, *a, **k):
        class _C:
            def __init__(self, docs):
                self._docs = list(docs)
                self._i = 0

            def sort(self, *a, **k):
                return self

            def to_list(self, n=None):
                class _Awaitable(list):
                    def __await__(self):
                        async def _inner():
                            return list(self)
                        return _inner().__await__()
                    async def to_list(self, nn=None):
                        return list(self)
                return _Awaitable(self._docs)

            def __aiter__(self):
                self._i = 0
                return self

            async def __anext__(self):
                if self._i >= len(self._docs):
                    raise StopAsyncIteration
                d = self._docs[self._i]
                self._i += 1
                return d

        return _C(self.docs)

    async def find_one(self, query=None, *a, **k):
        query = query or {}
        for d in self.docs:
            if not query:
                return dict(d)
            if all(d.get(k) == v for k, v in query.items() if not str(k).startswith("$")):
                return dict(d)
            for clause in (query.get("$or") or []):
                if all(d.get(ck) == cv for ck, cv in clause.items()):
                    return dict(d)
        return None

    async def update_one(self, *a, **k):
        self.writes += 1
        raise AssertionError("read path must not write")

    async def insert_one(self, *a, **k):
        self.writes += 1
        raise AssertionError("read path must not write")


class _FakeDB:
    def __init__(self, *, enabled: bool, v2_state: bool, legacy_positive: bool = True):
        self.master_items = _FakeColl([{
            "id": "m1",
            "accurate_item_id": "a1",
            "item_code": "SKU-1",
            "item_name": "Board",
            "unit": "PCS",
        }])
        self.orders = _FakeColl([{
            "id": "o1",
            "cancelled": False,
            "line_items": [{
                "master_item_id": "m1",
                "accurate_item_id": "a1",
                "quantity": 20,
                "production_process": "print",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
        }])
        self.sales_deliveries = _FakeColl([])
        self.item_stock_manual = _FakeColl([{
            "id": "man1",
            "master_item_id": "m1",
            "accurate_item_id": "a1",
            "manual_current_qty": 10000 if legacy_positive else 0,
            "manual_purchase_qty": 0,
        }] if legacy_positive else [])
        self.accurate_purchase_receipts = _FakeColl([])
        self.accurate_receipt_sync_meta = _FakeColl([])
        state = []
        if v2_state:
            state = [{
                "master_item_id": "m1",
                "accurate_item_id": "a1",
                "initialized": True,
                "status": "INITIALIZED",
                "total_qty": 90,
                "unprinted_qty": 90,
                "printed_qty": 0,
            }]
        self.product_stock_state = _FakeColl(state)
        self.stock_movements = _FakeColl([])
        self.app_settings = _FakeColl([{
            "key": "physical_stock",
            "enabled": enabled,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery" if enabled else "loading_confirmation",
        }])

    def __getitem__(self, name):
        return getattr(self, name)


def _legacy_row():
    masters = [{"id": "m1", "accurate_item_id": "a1", "item_code": "SKU-1", "item_name": "Board", "unit": "PCS"}]
    orders = [{
        "cancelled": False,
        "line_items": [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20, "production_process": "print"}],
        "operator_splits": [],
    }]
    manuals = [{"master_item_id": "m1", "accurate_item_id": "a1", "manual_current_qty": 10000, "manual_purchase_qty": 0}]
    rows = build_stock_rows(masters, orders, manuals, receipts=[], purchase_source="MANUAL_FALLBACK")
    return rows[0]


def _v2_row_missing_state():
    rows = build_stock_rows_v2(
        [{"id": "m1", "accurate_item_id": "a1", "item_code": "SKU-1", "item_name": "Board", "unit": "PCS"}],
        state_by_master={},
        outstanding_bucket={"a:a1": 20.0},
        outstanding_alias={"a:a1": "a:a1", "m:m1": "a:a1"},
    )
    return rows[0]


def _v2_row_initialized():
    rows = build_stock_rows_v2(
        [{"id": "m1", "accurate_item_id": "a1", "item_code": "SKU-1", "item_name": "Board", "unit": "PCS"}],
        state_by_master={"m1": {
            "master_item_id": "m1",
            "initialized": True,
            "status": "INITIALIZED",
            "total_qty": 90,
            "unprinted_qty": 90,
            "printed_qty": 0,
        }},
        outstanding_bucket={"a:a1": 20.0},
        outstanding_alias={"a:a1": "a:a1", "m:m1": "a:a1"},
    )
    return rows[0]


def test_legacy_formula_authority_unchanged():
    assert compute_stock_current(20, 10000, 0, 0) == 9980
    row = _legacy_row()
    assert row["stock_current"] == 9980
    assert row["manual_current_qty"] == 10000


def test_stock_saat_ini_off_uses_legacy_not_v2_zero():
    """E. OFF + missing V2 + legacy positive => legacy qty, not V2 zero."""
    legacy = _legacy_row()
    v2_missing = _v2_row_missing_state()
    assert v2_missing["physical_qty"] == 0
    assert v2_missing["stock_current"] == 0
    assert legacy["stock_current"] == 9980
    assert legacy["stock_current"] != v2_missing["stock_current"]


def test_stock_saat_ini_on_uses_v2_formula():
    """F. ON => V2 physical / outstanding."""
    v2 = _v2_row_initialized()
    assert v2["physical_qty"] == 90
    assert v2["outstanding_so_qty"] == 20
    assert v2["stock_current"] == 70
    assert v2["stock_engine"] == "v2"


def test_toggle_transition_same_fixture_off_legacy_on_v2():
    """G. same fixture: OFF legacy result vs ON V2 result."""
    off = _legacy_row()["stock_current"]
    on = _v2_row_missing_state()["stock_current"]
    assert off == 9980
    assert on == 0
    assert off != on
    on_init = _v2_row_initialized()["stock_current"]
    assert on_init == 70


@pytest.mark.anyio
async def test_get_paths_do_not_write_collections():
    """H. settings GET / current-stock helpers remain read-only."""
    from services.physical_stock_settings import get_physical_stock_settings
    from services.stock_engine_v2 import ensure_v2_physical_settings

    db = _FakeDB(enabled=False, v2_state=False)
    settings = await get_physical_stock_settings(db)
    assert settings["enabled"] is False
    probed = await ensure_v2_physical_settings(db)
    assert probed["enabled"] is False
    assert db.app_settings.writes == 0
    assert db.product_stock_state.writes == 0
    assert db.stock_movements.writes == 0


@pytest.mark.anyio
async def test_accurate_off_does_not_reject_missing_v2_state():
    db = _FakeDB(enabled=False, v2_state=False)
    await validate_sales_order_availability(
        db,
        [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 500}],
    )
    assert db.app_settings.writes == 0
    assert db.product_stock_state.writes == 0
    assert db.stock_movements.writes == 0


@pytest.mark.anyio
async def test_accurate_on_missing_state_rejects_via_compat_db():
    db = _FakeDB(enabled=True, v2_state=False)
    with pytest.raises(HTTPException) as ei:
        await validate_sales_order_availability(
            db,
            [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 1}],
        )
    assert ei.value.detail == STOCK_INSUFFICIENT_MSG

