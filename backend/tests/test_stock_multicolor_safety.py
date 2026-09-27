"""Read-only integration checks for multi-color production versus Stock V2."""
from __future__ import annotations

import pytest

from services.physical_stock_engine import handle_operator_progress
from services.product_stock_state import get_product_stock_state
from services.stock_engine_v2 import (
    aggregate_outstanding_so,
    build_stock_rows_v2,
    confirmed_delivery_evidence_from_orders,
)
from tests.test_physical_stock_engine import MockDB, setup_initialized_product


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _order(colors):
    return {
        "id": "order-multicolor",
        "po_number": "PO-MULTICOLOR",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "delivery_confirmed",
        },
        "line_items": [{
            "line_id": "line-1",
            "line_idx": 0,
            "master_item_id": "multi-item",
            "accurate_item_id": "acc-multi-item",
            "quantity": 1000,
            "unit": "PCS",
            "production_process": "print",
            # Current production storage keeps the ordered colors on one line.
            "color": ", ".join(colors),
        }],
        "operator_splits": [{
            "id": "split-1",
            "line_idx": 0,
            "quantity": 1000,
            "qty_completed": 1000,
            "operator_name": "Operator",
        }],
        "delivery_confirmations": [],
    }


def _stock_row(order, physical, *, unprinted=None, printed=0):
    outstanding, aliases = aggregate_outstanding_so(
        [order],
        confirmed_delivery_evidence_from_orders([order]),
    )
    return build_stock_rows_v2(
        [{"id": "multi-item", "accurate_item_id": "acc-multi-item"}],
        state_by_master={"multi-item": {
            "master_item_id": "multi-item",
            "initialized": True,
            "status": "INITIALIZED",
            "total_qty": physical,
            "unprinted_qty": physical if unprinted is None else unprinted,
            "printed_qty": printed,
        }},
        outstanding_bucket=outstanding,
        outstanding_alias=aliases,
    )[0]


@pytest.mark.parametrize(
    "colors",
    [
        ["Warna 1"],
        ["Warna 1", "Warna 2"],
        ["Warna 1", "Warna 2", "Warna 3", "Warna 4"],
    ],
)
def test_color_count_does_not_multiply_reservation(colors):
    row = _stock_row(_order(colors), physical=1000)
    assert row["outstanding_so_qty"] == 1000
    assert row["stock_current"] == 0


@pytest.mark.anyio
async def test_repeated_color_passes_cannot_consume_same_physical_qty_four_times():
    db = MockDB()
    await setup_initialized_product(
        db,
        "multi-item",
        unprinted=1000,
        printed=0,
    )
    order = _order(["Warna 1", "Warna 2", "Warna 3", "Warna 4"])
    split = order["operator_splits"][0]

    movements = []
    for color_number in range(1, 5):
        movement = await handle_operator_progress(
            db,
            order,
            split,
            increment=1000,
            action_id=f"color-{color_number}",
            live_enabled=True,
        )
        if movement:
            movements.append(movement)

    state = await get_product_stock_state(db, "multi-item")
    assert state["total_qty"] == 1000
    assert state["unprinted_qty"] == 0
    assert state["printed_qty"] == 1000
    assert len(movements) == 1
    assert movements[0]["delta"] == 0

    row = _stock_row(
        order,
        physical=state["total_qty"],
        unprinted=state["unprinted_qty"],
        printed=state["printed_qty"],
    )
    assert row["outstanding_so_qty"] == 1000
    assert row["stock_current"] == 0


def test_multicolor_partial_and_full_delivery_follow_confirmation_only():
    order = _order(["Warna 1", "Warna 2", "Warna 3", "Warna 4"])
    order["delivery_confirmations"] = [{
        "id": "confirmation-1",
        "items": [{"line_idx": 0, "qty": 600}],
    }]
    partial = _stock_row(order, physical=400)
    assert partial["outstanding_so_qty"] == 400
    assert partial["stock_current"] == 0

    order["delivery_confirmations"].append({
        "id": "confirmation-2",
        "items": [{"line_idx": 0, "qty": 400}],
    })
    complete = _stock_row(order, physical=0)
    assert complete["outstanding_so_qty"] == 0
    assert complete["stock_current"] == 0
