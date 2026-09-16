"""Stock Engine V2 mandatory scenarios."""
from __future__ import annotations

from typing import List

import pytest
from fastapi import HTTPException

from services.stock_engine_v2 import (
    STOCK_INSUFFICIENT_MSG,
    aggregate_outstanding_so,
    apply_stock_opname,
    build_stock_rows_v2,
    compute_available,
    physical_from_state,
    validate_sales_order_availability,
)


def test_compute_available_core_examples():
    """Mandatory Available = MAX(Fisik - Outstanding, 0). Printed does not re-cut."""
    from services.stock_engine_v2 import compute_belum_dicetak, compute_stock_polos

    # Audit example
    assert compute_available(1000, 300, 100) == 700
    assert compute_belum_dicetak(300, 100) == 200
    assert compute_stock_polos(1000, 100) == 900
    assert max(compute_stock_polos(1000, 100) - compute_belum_dicetak(300, 100), 0) == 700

    # Printed ignored for Available (only Fisik - Outstanding)
    assert compute_available(90, 0, 4) == 90
    assert compute_available(50, 0, 2) == 50
    assert compute_available(100, 30, 20) == 70
    assert compute_available(100, 20, 20) == 80
    assert compute_available(100, 30, 0) == 70

    # Never Fisik - Outstanding - Belum - Sudah (triple count)
    assert compute_available(1000, 300, 100) != 1000 - 300 - 200 - 100
    assert compute_available(100, 30, 20) != 100 - 30 - 20


def test_outstanding_so_ordered_minus_shipped():
    orders = [{
        "id": "o1",
        "line_items": [
            {"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20},
        ],
    }]
    deliveries = [{
        "po_id": "o1",
        "line_items": [
            {"master_item_id": "m1", "accurate_item_id": "a1", "qty": 10},
        ],
    }]
    outstanding, _alias = aggregate_outstanding_so(orders, deliveries)
    assert any(abs(v - 10) < 1e-9 for v in outstanding.values())


def test_build_rows_v2_available_formula():
    masters = [{"id": "m1", "accurate_item_id": "a1", "item_code": "X", "item_name": "Item", "unit": "PCS"}]
    state_by_master = {
        "m1": {
            "master_item_id": "m1",
            "initialized": True,
            "status": "INITIALIZED",
            "total_qty": 100,
            "unprinted_qty": 60,
            "printed_qty": 40,
        }
    }
    outstanding = {"a:a1": 20.0}
    alias = {"a:a1": "a:a1", "m:m1": "a:a1"}
    rows = build_stock_rows_v2(
        masters,
        state_by_master=state_by_master,
        outstanding_bucket=outstanding,
        outstanding_alias=alias,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["physical_qty"] == 100
    assert r["unprinted_qty"] == 0  # Belum = max(outstanding 20 - printed 40, 0)
    assert r["physical_unprinted_qty"] == 60
    assert r["printed_qty"] == 40
    assert r["outstanding_so_qty"] == 20
    assert r["stock_current"] == 80  # max(100 - outstanding 20, 0)
    assert r["purchase_qty"] == 0
    assert r["manual_purchase_qty"] == 0
    assert r["operator_print_qty"] == 0


def test_operator_print_does_not_change_available():
    """Cetak only moves Unprinted→Printed; Available follows single formula (no second cut)."""
    before = physical_from_state({
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 100,
        "unprinted_qty": 70,
        "printed_qty": 30,
    })
    after = physical_from_state({
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 100,
        "unprinted_qty": 50,
        "printed_qty": 50,
    })
    assert before["physical_qty"] == after["physical_qty"] == 100
    # With outstanding 50: printing 30→50 keeps Available = max(100-max(50,printed),0) = 50
    assert compute_available(100, 50, before["printed_qty"]) == 50
    assert compute_available(100, 50, after["printed_qty"]) == 50
    # With outstanding 20: printing beyond outstanding reduces Available via formula (not double-cut)
    # Printing beyond outstanding does NOT cut Available (still Fisik - Outstanding)
    assert compute_available(100, 20, 30) == 80
    assert compute_available(100, 20, 50) == 80
    assert compute_available(100, 20, 50) != 100 - 20 - 50  # would be 30 if double-counted


def test_cancel_so_releases_reservation_in_read_model():
    masters = [{"id": "m1", "accurate_item_id": "a1", "item_code": "X", "item_name": "Item", "unit": "PCS"}]
    state = {
        "m1": {
            "master_item_id": "m1",
            "initialized": True,
            "status": "INITIALIZED",
            "total_qty": 100,
            "unprinted_qty": 100,
            "printed_qty": 0,
        }
    }
    out1, alias = aggregate_outstanding_so(
        [{"id": "o1", "line_items": [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20}]}],
        [],
    )
    rows1 = build_stock_rows_v2(
        masters,
        state_by_master=state,
        outstanding_bucket=out1,
        outstanding_alias=alias,
    )
    assert rows1[0]["stock_current"] == 80

    out2, alias2 = aggregate_outstanding_so(
        [{"id": "o1", "cancelled": True, "line_items": [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20}]}],
        [],
    )
    rows2 = build_stock_rows_v2(
        masters,
        state_by_master=state,
        outstanding_bucket=out2,
        outstanding_alias=alias2,
    )
    assert rows2[0]["stock_current"] == 100


def test_delivery_keeps_available_stable_and_gr_increases():
    # Delivery reduces physical + outstanding equally → Available stable (printed fixed)
    assert compute_available(100, 30, 20) == 70
    assert compute_available(90, 20, 20) == 70
    assert compute_available(80, 10, 20) == 70  # delivery drops Fisik+Outstanding equally
    # printed=0 legacy path still: physical - outstanding
    assert compute_available(100, 20, 0) == 80
    assert compute_available(90, 10, 0) == 80
    assert compute_available(80, 0, 0) == 80
    # GR increases physical → Available up
    assert compute_available(150, 20, 0) == 130


class _FakeCursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    async def to_list(self, n=None):
        return list(self._docs)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []
        self.inserts = []

    def find(self, *a, **k):
        return _FakeCursor(self.docs)

    async def find_one(self, query, *a, **k):
        for d in self.docs:
            if not isinstance(query, dict):
                continue
            if query.get("key") and d.get("key") == query.get("key"):
                return dict(d)
            mid = d.get("master_item_id") or d.get("id")
            if query.get("id") == mid or query.get("master_item_id") == mid:
                return dict(d)
            for clause in query.get("$or") or []:
                if clause.get("_id") == mid or clause.get("master_item_id") == mid or clause.get("id") == mid:
                    return dict(d)
            ands = query.get("$and")
            if ands:
                return dict(d)
        return None

    async def find_one_and_update(self, query, update, **k):
        if not self.docs:
            return None
        doc = self.docs[0]
        if "$set" in update:
            doc.update(update["$set"])
        self.updates.append((query, update))
        return dict(doc)

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)


class _FakeDB:
    def __init__(self):
        self.master_items = _FakeColl([{
            "id": "m1",
            "accurate_item_id": "a1",
            "item_code": "SKU-1",
            "item_name": "Board",
            "unit": "PCS",
        }])
        self.orders = _FakeColl([])
        self.sales_deliveries = _FakeColl([])
        self.product_stock_state = _FakeColl([{
            "master_item_id": "m1",
            "accurate_item_id": "a1",
            "initialized": True,
            "status": "INITIALIZED",
            "total_qty": 100,
            "unprinted_qty": 100,
            "printed_qty": 0,
            "base_uom": "PCS",
        }])
        self.stock_movements = _FakeColl([])
        self.app_settings = _FakeColl([{
            "key": "physical_stock",
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery",
        }])

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.mark.anyio
async def test_validate_so_blocks_when_insufficient():
    db = _FakeDB()
    with pytest.raises(HTTPException) as ei:
        await validate_sales_order_availability(
            db,
            [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 120}],
        )
    assert ei.value.status_code == 400
    assert ei.value.detail == STOCK_INSUFFICIENT_MSG


@pytest.mark.anyio
async def test_validate_so_allows_within_available():
    db = _FakeDB()
    await validate_sales_order_availability(
        db,
        [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20}],
    )


@pytest.mark.anyio
async def test_validate_so_off_does_not_block_when_v2_state_missing():
    """A. OFF + missing V2 state + qty>0 must not fail on V2 availability."""
    db = _FakeDB()
    db.app_settings.docs = [{
        "key": "physical_stock",
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
    }]
    db.product_stock_state.docs = []
    await validate_sales_order_availability(
        db,
        [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 9999}],
    )
    assert db.app_settings.updates == []
    assert db.product_stock_state.inserts == []
    assert db.stock_movements.inserts == []


@pytest.mark.anyio
async def test_validate_so_on_missing_state_rejects():
    """B. ON + no initialized V2 state => reject."""
    db = _FakeDB()
    db.product_stock_state.docs = []
    with pytest.raises(HTTPException) as ei:
        await validate_sales_order_availability(
            db,
            [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 1}],
        )
    assert ei.value.status_code == 400
    assert ei.value.detail == STOCK_INSUFFICIENT_MSG


@pytest.mark.anyio
async def test_opname_updates_physical_keeps_outstanding(monkeypatch):
    db = _FakeDB()

    async def fake_record(db, **k):
        doc = {"id": k.get("movement_id"), **k}
        await db.stock_movements.insert_one(doc)
        return doc

    monkeypatch.setattr("services.stock_engine_v2.record_stock_movement", fake_record)

    result = await apply_stock_opname(
        db,
        master_item_id="m1",
        counted_unprinted_qty=40,
        counted_printed_qty=50,
        note="opname test",
        user={"email": "admin@test", "name": "Admin"},
    )
    assert result["unchanged"] is False
    st = result["stock_state"]
    assert st["total_qty"] == 90
    assert st["unprinted_qty"] == 40
    assert st["printed_qty"] == 50
    assert len(db.stock_movements.inserts) == 1

    out, alias = aggregate_outstanding_so(
        [{"id": "o1", "line_items": [{"master_item_id": "m1", "accurate_item_id": "a1", "quantity": 20}]}],
        [],
    )
    rows = build_stock_rows_v2(
        [{"id": "m1", "accurate_item_id": "a1", "item_code": "X", "item_name": "Item", "unit": "PCS"}],
        state_by_master={"m1": st},
        outstanding_bucket=out,
        outstanding_alias=alias,
    )
    assert rows[0]["physical_qty"] == 90
    assert rows[0]["stock_current"] == 70  # max(90 - outstanding 20, 0); printed ignored


def test_gr_cancel_guard_mentions_stock_movements():
    from services import purchase_goods_receipts as pgr
    src = open(pgr.__file__, encoding="utf-8").read()
    assert "stock_movements" in src
    assert "cancel" in src
