"""Durable Stock Opname physical-count and concurrency scenarios."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from services.product_stock_state import get_product_stock_state
from services.stock_engine_v2 import apply_stock_opname
from tests.test_physical_stock_engine import MockDB, setup_initialized_product


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db():
    return MockDB()


async def _seed(db, *, unprinted=80, printed=20):
    await setup_initialized_product(
        db,
        "opname-item",
        unprinted=float(unprinted),
        printed=float(printed),
    )
    await db.orders.insert_one({
        "id": "so-open",
        "line_items": [{"master_item_id": "opname-item", "quantity": 30}],
        "delivery_confirmations": [],
    })


def _valuation(hpp=1000):
    return {
        "master_item_id": "opname-item",
        "hpp_status": "OK",
        "current_hpp": Decimal(str(hpp)),
        "hpp_source": "PURCHASING_MOVING_AVERAGE",
    }


@pytest.mark.anyio
async def test_positive_and_negative_variance_use_physical_count_and_preserve_orders(db):
    await _seed(db)
    order_before = list(db.orders._docs)

    positive = await apply_stock_opname(
        db,
        master_item_id="opname-item",
        stock_physical_counted=120,
        outstanding_so_qty=30,
        client_request_id="positive",
        valuation_context=_valuation(),
    )
    assert positive["stock_physical_before"] == 100
    assert positive["stock_physical_counted"] == 120
    assert positive["difference_qty"] == 20
    assert positive["estimated_difference_value"] == Decimal("20000.00")
    assert positive["valuation_adjustment_required"] is True
    assert positive["movement"]["outstanding_so_snapshot"] == 30
    assert (await get_product_stock_state(db, "opname-item"))["total_qty"] == 120

    negative = await apply_stock_opname(
        db,
        master_item_id="opname-item",
        stock_physical_counted=90,
        outstanding_so_qty=30,
        client_request_id="negative",
        valuation_context=_valuation(),
    )
    assert negative["stock_physical_before"] == 120
    assert negative["stock_physical_counted"] == 90
    assert negative["difference_qty"] == -30
    assert negative["estimated_difference_value"] == Decimal("-30000.00")
    assert negative["valuation_adjustment_required"] is True
    state = await get_product_stock_state(db, "opname-item")
    assert (state["total_qty"], state["unprinted_qty"], state["printed_qty"]) == (90, 70, 20)
    assert db.orders._docs == order_before


@pytest.mark.anyio
async def test_retry_same_opname_request_is_idempotent_without_second_movement(db):
    await _seed(db)
    kwargs = {
        "master_item_id": "opname-item",
        "stock_physical_counted": 110,
        "outstanding_so_qty": 30,
        "client_request_id": "same-request",
        "valuation_context": _valuation(),
    }
    first = await apply_stock_opname(db, **kwargs)
    second = await apply_stock_opname(db, **kwargs)

    assert first["movement"]["id"] == second["movement"]["id"]
    assert second["idempotent"] is True
    corrections = [
        row for row in db.stock_movements._docs
        if row.get("reference_type") == "stock_opname"
    ]
    assert len(corrections) == 1
    assert (await get_product_stock_state(db, "opname-item"))["total_qty"] == 110


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("hpp_source", "expected_adjustment_required"),
    [
        ("PURCHASING_MOVING_AVERAGE", True),
        ("PUBLISHED_FALLBACK", False),
    ],
)
async def test_hpp_snapshot_is_informational_and_source_explicit(
    db,
    hpp_source,
    expected_adjustment_required,
):
    await _seed(db)
    result = await apply_stock_opname(
        db,
        master_item_id="opname-item",
        stock_physical_counted=120,
        outstanding_so_qty=30,
        client_request_id=f"hpp-{hpp_source}",
        valuation_context={
            "hpp_status": "QTY_MISMATCH",
            "current_hpp": Decimal("205"),
            "hpp_source": hpp_source,
        },
    )

    assert result["effective_hpp_snapshot"] == Decimal("205.00")
    assert result["hpp_source"] == hpp_source
    assert result["hpp_status_snapshot"] == "QTY_MISMATCH"
    assert result["estimated_difference_value"] == Decimal("4100.00")
    assert result["estimated_value_is_informational"] is True
    assert result["valuation_adjustment_required"] is expected_adjustment_required
    assert result["movement"]["estimated_value_is_informational"] is True
    assert result["movement"]["valuation_adjustment_required"] is expected_adjustment_required
    assert not hasattr(db, "journal_entries")


@pytest.mark.anyio
async def test_concurrent_distinct_opname_requests_have_one_clean_winner(db):
    await _seed(db)
    barrier = asyncio.Barrier(2)
    original_claim = db.product_stock_state.find_one_and_update

    async def gated_claim(query, update, return_document=None):
        if (
            query.get("pending_event") is None
            and query.get("total_qty") == 100
            and query.get("unprinted_qty") == 80
            and query.get("printed_qty") == 20
        ):
            await barrier.wait()
        return await original_claim(
            query,
            update,
            return_document=return_document,
        )

    db.product_stock_state.find_one_and_update = gated_claim

    async def submit(request_id, counted):
        return await apply_stock_opname(
            db,
            master_item_id="opname-item",
            stock_physical_counted=counted,
            outstanding_so_qty=30,
            client_request_id=request_id,
            valuation_context={"hpp_status": "MISSING_COST"},
        )

    results = await asyncio.gather(
        submit("race-a", 110),
        submit("race-b", 120),
        return_exceptions=True,
    )
    successes = [row for row in results if not isinstance(row, BaseException)]
    conflicts = [
        row for row in results
        if isinstance(row, HTTPException) and row.status_code in (400, 409)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1

    state = await get_product_stock_state(db, "opname-item")
    assert state["total_qty"] == successes[0]["stock_physical_counted"]
    assert state["total_qty"] == state["unprinted_qty"] + state["printed_qty"]
    assert not state.get("pending_event")
    corrections = [
        row for row in db.stock_movements._docs
        if row.get("reference_type") == "stock_opname"
    ]
    assert len(corrections) == 1
    assert corrections[0]["balance_after"] == state["total_qty"]
