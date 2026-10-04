"""Delivery Confirmed delta integration with Stock V2 reservations."""
from __future__ import annotations

import pytest

from services.physical_stock_engine import (
    claim_delivery_confirmed_stock,
    finalize_delivery_confirmed_stock,
)
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


def _order(*, colors: str = "Warna 1"):
    return {
        "id": "order-partial",
        "po_number": "PO-PARTIAL",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "delivery_confirmed",
        },
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-1",
            "master_item_id": "item-partial",
            "accurate_item_id": "accurate-partial",
            "quantity": 1000,
            "unit": "PCS",
            "production_process": "print",
            "color": colors,
        }],
        "delivery_confirm_action_ids": [],
        "delivery_confirmations": [],
    }


def _stock_row(order: dict, state: dict):
    outstanding, aliases = aggregate_outstanding_so(
        [order],
        confirmed_delivery_evidence_from_orders([order]),
    )
    return build_stock_rows_v2(
        [{"id": "item-partial", "accurate_item_id": "accurate-partial"}],
        state_by_master={"item-partial": state},
        outstanding_bucket=outstanding,
        outstanding_alias=aliases,
    )[0]


async def _confirm(db, order: dict, *, qty: int, action_id: str):
    items = [{"line_idx": 0, "line_id": "line-1", "qty": qty}]
    claims = await claim_delivery_confirmed_stock(
        db,
        order,
        items,
        action_id,
    )
    order["delivery_confirm_action_ids"].append(action_id)
    order["delivery_confirmations"].append({
        "id": f"confirmation-{action_id}",
        "action_id": action_id,
        "qty": qty,
        "items": items,
    })
    await db.orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "delivery_confirm_action_ids": list(order["delivery_confirm_action_ids"]),
            "delivery_confirmations": list(order["delivery_confirmations"]),
        }},
    )
    movements = await finalize_delivery_confirmed_stock(db, claims)
    return claims, movements


@pytest.mark.anyio
async def test_three_partial_confirmations_apply_only_each_new_delta():
    db = MockDB()
    await setup_initialized_product(
        db,
        "item-partial",
        unprinted=0,
        printed=1000,
    )
    order = _order()
    await db.orders.insert_one(order)

    expected = [
        (600, 400, 400),
        (250, 150, 150),
        (150, 0, 0),
    ]
    for index, (delta, physical, outstanding) in enumerate(expected, start=1):
        claims, movements = await _confirm(
            db,
            order,
            qty=delta,
            action_id=f"delivery-{index}",
        )
        assert claims[0]["claim_data"]["transaction_qty"] == delta
        assert movements[0]["delta"] == -delta
        state = await get_product_stock_state(db, "item-partial")
        row = _stock_row(order, state)
        assert state["total_qty"] == physical
        assert row["outstanding_so_qty"] == outstanding
        assert row["stock_current"] == 0

    delivery_movements = [
        movement for movement in db.stock_movements._docs
        if movement.get("movement_type") == "PENGIRIMAN"
    ]
    assert [movement["transaction_qty"] for movement in delivery_movements] == [600, 250, 150]
    assert sum(movement["transaction_qty"] for movement in delivery_movements) == 1000


@pytest.mark.anyio
async def test_full_delivery_and_retry_create_one_stock_movement():
    db = MockDB()
    await setup_initialized_product(
        db,
        "item-partial",
        unprinted=0,
        printed=1000,
    )
    order = _order()
    await db.orders.insert_one(order)

    await _confirm(db, order, qty=1000, action_id="delivery-full")
    retry = await claim_delivery_confirmed_stock(
        db,
        order,
        [{"line_idx": 0, "line_id": "line-1", "qty": 1000}],
        "delivery-full",
    )
    assert retry[0]["status"] == "ALREADY_FINALIZED"

    state = await get_product_stock_state(db, "item-partial")
    row = _stock_row(order, state)
    assert state["total_qty"] == 0
    assert row["outstanding_so_qty"] == 0
    assert row["stock_current"] == 0
    assert len([
        movement for movement in db.stock_movements._docs
        if movement.get("event_key") == "stock_out:delivery:order-partial:delivery-full:0"
    ]) == 1


def test_unconfirmed_sj_and_four_colors_do_not_change_reservation():
    order = _order(colors="Warna 1, Warna 2, Warna 3, Warna 4")
    state = {
        "master_item_id": "item-partial",
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 1000,
        "unprinted_qty": 0,
        "printed_qty": 1000,
    }
    # Surat Jalan is operational evidence only until Delivery Confirmed.
    order["shipments"] = [{
        "id": "sj-1",
        "lines": [{"line_idx": 0, "qty": 600}],
    }]
    before = _stock_row(order, state)
    assert before["physical_qty"] == 1000
    assert before["outstanding_so_qty"] == 1000
    assert before["stock_current"] == 0

    order["delivery_confirmations"] = [{
        "id": "confirmation-color",
        "action_id": "delivery-color",
        "items": [{"line_idx": 0, "qty": 600}],
    }]
    after = _stock_row(order, {
        **state,
        "total_qty": 400,
        "printed_qty": 400,
    })
    assert after["physical_qty"] == 400
    assert after["outstanding_so_qty"] == 400
    assert after["stock_current"] == 0


def test_negative_sellable_remains_visible_when_physical_is_short():
    order = _order()
    order["line_items"][0]["quantity"] = 150
    row = _stock_row(order, {
        "master_item_id": "item-partial",
        "initialized": True,
        "status": "INITIALIZED",
        "total_qty": 100,
        "unprinted_qty": 0,
        "printed_qty": 100,
    })
    assert row["outstanding_so_qty"] == 150
    assert row["stock_current"] == -50
    assert row["available_qty"] == -50
