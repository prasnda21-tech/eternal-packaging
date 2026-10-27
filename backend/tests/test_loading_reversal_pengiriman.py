"""BUG-03: REVERSAL_PENGIRIMAN for Loading cancel — bucket-correct, idempotent."""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.stock_ledger import (
    MOVEMENT_PENGIRIMAN,
    MOVEMENT_REVERSAL_PENGIRIMAN,
    VALID_MOVEMENT_TYPES,
)
from services.physical_stock_engine import (
    execute_durable_stock_event,
    find_loading_confirmation_stock_movements,
    loading_reversal_event_id,
    reverse_loading_confirmation_stock,
)
from services.product_stock_state import get_product_stock_state


# Reuse MockDB from physical stock engine tests.
from tests.test_physical_stock_engine import (  # noqa: E402
    MockDB,
    setup_initialized_product,
)


def _conf(cid="lc-1", action_id="act-1", qty=1000, line_idx=0):
    return {
        "id": cid,
        "action_id": action_id,
        "surat_jalan_id": "sj-1",
        "shipment_id": "sj-1",
        "sj_number": "SJ-1",
        "surat_jalan_name": "SJ-1",
        "lines": [{"line_idx": line_idx, "qty": qty, "name": "Cup"}],
    }


def _order(confs=None, item="item-load-1"):
    return {
        "id": "po-load-1",
        "po_number": "PO-LOAD-1",
        "line_items": [{
            "line_idx": 0, "line_id": "L0", "name": "Cup",
            "quantity": 2000, "master_item_id": item,
            "production_process": "print", "unit": "PCS",
        }],
        "loading_confirmations": confs or [_conf()],
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "loading_confirmation",
        },
    }


@pytest.mark.anyio
async def test_a_j_k_reverse_restores_printed_exactly_1000():
    """A/J/K: cancel reverse restores printed 1000; unprinted unchanged."""
    db = MockDB()
    await setup_initialized_product(db, "item-load-1", unprinted=500.0, printed=2000.0)
    # Simulate committed Loading PENGIRIMAN from printed
    event_id = "loading:po-load-1:act-1:0"
    await execute_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id="item-load-1",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=1000,
        transaction_uom="PCS",
        source_entity="SUDAH_DICETAK",
        destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO-LOAD-1",
        reference_type="loading_confirmation",
        reference_id="po-load-1",
        order_id="po-load-1",
        line_idx=0,
        line_id="L0",
        action_id="act-1",
        is_print=True,
        user={"email": "t@x"},
    )
    before = await get_product_stock_state(db, "item-load-1")
    assert before["printed_qty"] == 1000.0
    assert before["unprinted_qty"] == 500.0
    assert before["total_qty"] == 1500.0

    order = _order()
    out = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t@x"},
    )
    assert out["reversed"] is True
    assert out["replayed"] is False
    assert len(out["reversals"]) == 1
    rev = out["reversals"][0]["movement"]
    assert rev["movement_type"] == MOVEMENT_REVERSAL_PENGIRIMAN
    assert rev["delta_printed"] == 1000.0
    assert rev["delta_unprinted"] == 0.0

    after = await get_product_stock_state(db, "item-load-1")
    assert after["printed_qty"] == 2000.0
    assert after["unprinted_qty"] == 500.0  # K: unprinted untouched
    assert after["total_qty"] == 2500.0
    assert after["total_qty"] == after["printed_qty"] + after["unprinted_qty"]


@pytest.mark.anyio
async def test_non_print_restores_unprinted_only():
    db = MockDB()
    await setup_initialized_product(db, "item-plain", unprinted=2000.0, printed=100.0)
    event_id = "stock_out:loading:po-load-1:lc-1:0"
    await execute_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id="item-plain",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=400,
        transaction_uom="PCS",
        source_entity="BELUM_DICETAK",
        destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO-LOAD-1",
        reference_type="loading_confirmation",
        reference_id="po-load-1",
        order_id="po-load-1",
        line_idx=0,
        is_print=False,
        production_process="non_print",
        user={"email": "t@x"},
    )
    order = {
        "id": "po-load-1",
        "po_number": "PO-LOAD-1",
        "line_items": [{
            "line_idx": 0, "master_item_id": "item-plain",
            "production_process": "non_print", "unit": "PCS",
        }],
        "loading_confirmations": [_conf(qty=400)],
    }
    await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t@x"},
    )
    st = await get_product_stock_state(db, "item-plain")
    assert st["unprinted_qty"] == 2000.0
    assert st["printed_qty"] == 100.0


@pytest.mark.anyio
async def test_b_sibling_loading_movements_isolated():
    db = MockDB()
    await setup_initialized_product(db, "item-load-1", unprinted=0.0, printed=3000.0)
    await execute_durable_stock_event(
        db, event_id="loading:po-load-1:act-1:0", master_item_id="item-load-1",
        movement_type=MOVEMENT_PENGIRIMAN, transaction_qty=1000, transaction_uom="PCS",
        source_entity="SUDAH_DICETAK", destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO", reference_type="loading_confirmation",
        reference_id="po-load-1", order_id="po-load-1", line_idx=0,
        action_id="act-1", is_print=True, user={"email": "t"},
    )
    await execute_durable_stock_event(
        db, event_id="loading:po-load-1:act-2:0", master_item_id="item-load-1",
        movement_type=MOVEMENT_PENGIRIMAN, transaction_qty=500, transaction_uom="PCS",
        source_entity="SUDAH_DICETAK", destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO", reference_type="loading_confirmation",
        reference_id="po-load-1", order_id="po-load-1", line_idx=0,
        action_id="act-2", is_print=True, user={"email": "t"},
    )
    order = _order(confs=[
        _conf("lc-1", "act-1", 1000),
        _conf("lc-2", "act-2", 500),
    ])
    out = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t"},
    )
    assert len(out["reversals"]) == 1
    assert out["reversals"][0]["original_id"] == "loading:po-load-1:act-1:0"
    # Sibling movement still present, not reversed
    sib = await db.stock_movements.find_one({"id": "loading:po-load-1:act-2:0"}, {"_id": 0})
    assert sib is not None
    rev_sib = await db.stock_movements.find_one(
        {"id": loading_reversal_event_id("loading:po-load-1:act-2:0")}, {"_id": 0},
    )
    assert rev_sib is None
    st = await get_product_stock_state(db, "item-load-1")
    # 3000 -1000 -500 +1000(rev) = 2500 printed
    assert st["printed_qty"] == 2500.0


@pytest.mark.anyio
async def test_c_repeat_reverse_idempotent_no_double():
    db = MockDB()
    await setup_initialized_product(db, "item-load-1", unprinted=0.0, printed=2000.0)
    await execute_durable_stock_event(
        db, event_id="loading:po-load-1:act-1:0", master_item_id="item-load-1",
        movement_type=MOVEMENT_PENGIRIMAN, transaction_qty=1000, transaction_uom="PCS",
        source_entity="SUDAH_DICETAK", destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO", reference_type="loading_confirmation",
        reference_id="po-load-1", order_id="po-load-1", line_idx=0,
        action_id="act-1", is_print=True, user={"email": "t"},
    )
    order = _order()
    conf = order["loading_confirmations"][0]
    first = await reverse_loading_confirmation_stock(db, order, conf, user={"email": "t"})
    second = await reverse_loading_confirmation_stock(db, order, conf, user={"email": "t"})
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["reversals"][0]["replayed"] is True
    st = await get_product_stock_state(db, "item-load-1")
    assert st["printed_qty"] == 2000.0
    revs = [
        m for m in db.stock_movements._docs
        if m.get("movement_type") == MOVEMENT_REVERSAL_PENGIRIMAN
    ]
    assert len(revs) == 1


@pytest.mark.anyio
async def test_d_concurrent_reverse_single_movement():
    """D: overlapping reverse calls share stable event id → one ledger row."""
    db = MockDB()
    await setup_initialized_product(db, "item-load-1", unprinted=0.0, printed=2000.0)
    await execute_durable_stock_event(
        db, event_id="loading:po-load-1:act-1:0", master_item_id="item-load-1",
        movement_type=MOVEMENT_PENGIRIMAN, transaction_qty=1000, transaction_uom="PCS",
        source_entity="SUDAH_DICETAK", destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO", reference_type="loading_confirmation",
        reference_id="po-load-1", order_id="po-load-1", line_idx=0,
        action_id="act-1", is_print=True, user={"email": "t"},
    )
    order = _order()
    conf = order["loading_confirmations"][0]
    r1, r2 = await asyncio.gather(
        reverse_loading_confirmation_stock(db, order, conf, user={"email": "a"}),
        reverse_loading_confirmation_stock(db, order, conf, user={"email": "b"}),
    )
    assert r1["reversed"] and r2["reversed"]
    revs = [
        m for m in db.stock_movements._docs
        if m.get("movement_type") == MOVEMENT_REVERSAL_PENGIRIMAN
    ]
    assert len(revs) == 1
    st = await get_product_stock_state(db, "item-load-1")
    assert st["printed_qty"] == 2000.0


def test_e_reverse_failure_keeps_loading_active():
    import server

    order = {
        "id": "po-1",
        "shipments": [{
            "id": "sj-1", "number": "SJ-1", "sales_delivery_id": "sj-1",
            "is_cancelled": True, "cancelled": True, "status": "cancelled",
            "lines": [{"line_idx": 0, "qty": 100}],
        }],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "loading_confirmations": [_conf()],
        "line_items": [{"quantity": 100, "line_idx": 0}],
        "total_quantity": 100,
    }
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order))
    mutate_n = {"n": 0}

    async def fake_lock(oid, mutate):
        mutate_n["n"] += 1
        o = copy.deepcopy(order)
        mutate(o)
        return o

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server, "_require_perm", AsyncMock(),
        ), patch.object(server, "_with_quantity_lock", side_effect=fake_lock), patch.object(
            server, "_audit_po", AsyncMock(),
        ), patch(
            "services.physical_stock_engine.reverse_loading_confirmation_stock",
            new=AsyncMock(side_effect=HTTPException(503, {
                "message": "fail", "code": "LOADING_DELETE_STOCK_REVERSE_FAILED",
            })),
        ):
            with pytest.raises(HTTPException) as exc:
                await server.delete_loading_confirmation(
                    "po-1", "lc-1", user={"email": "a@x", "id": "u1"},
                )
            assert exc.value.status_code == 503
            assert mutate_n["n"] == 0

    asyncio.run(_run())


def test_f_durable_engine_error_fail_closed_no_status_change():
    """F: execute_durable failure → no soft-cancel (existing durable protocol)."""
    import server

    order = {
        "id": "po-1",
        "shipments": [{
            "id": "sj-1", "is_cancelled": True, "cancelled": True, "status": "cancelled",
            "lines": [{"line_idx": 0, "qty": 100}],
        }],
        "qty_delivered": 0,
        "loading_confirmations": [_conf()],
        "line_items": [{"quantity": 100}],
        "total_quantity": 100,
    }

    async def boom(*a, **k):
        raise RuntimeError("durable claim unavailable")

    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order))
    mock_db.stock_movements.find_one = AsyncMock(return_value=None)

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server, "_require_perm", AsyncMock(),
        ), patch.object(
            server, "_with_quantity_lock", new=AsyncMock(),
        ) as lock, patch(
            "services.physical_stock_engine.find_loading_confirmation_stock_movements",
            new=AsyncMock(return_value=[{
                "id": "loading:po-1:act-1:0",
                "event_key": "loading:po-1:act-1:0",
                "movement_type": "PENGIRIMAN",
                "master_item_id": "item-1",
                "base_qty": 100,
                "delta_printed": -100,
                "delta_unprinted": 0,
                "is_print": True,
                "transaction_uom": "PCS",
            }]),
        ), patch(
            "services.inventory_issue_posting.fence_loading_cancel_against_inventory_issues",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.inventory_issue_posting.release_loading_cancel_inventory_issue_fence",
            new=AsyncMock(),
        ), patch(
            "services.physical_stock_engine.execute_durable_stock_event",
            new=boom,
        ):
            with pytest.raises(HTTPException) as exc:
                await server.delete_loading_confirmation(
                    "po-1", "lc-1", user={"email": "a@x"},
                )
            assert exc.value.status_code == 503
            assert exc.value.detail["code"] == "LOADING_DELETE_STOCK_REVERSE_FAILED"
            lock.assert_not_awaited()

    asyncio.run(_run())


def test_g_delivered_guard_still_blocks():
    from quantity_rules import validate_cancel_loading_confirmation

    order = {
        "id": "po-1",
        "total_quantity": 1000,
        "qty_delivered": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "line_items": [{"quantity": 1000}],
        "shipments": [{"id": "sj-1", "is_cancelled": True, "cancelled": True, "status": "cancelled"}],
        "loading_confirmations": [_conf(qty=1000)],
    }
    err = validate_cancel_loading_confirmation(order, "lc-1")
    assert err and "seluruhnya" in err.lower()


@pytest.mark.anyio
async def test_h_legacy_no_movement_noop():
    db = MockDB()
    order = _order()
    out = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t"},
    )
    assert out["reason"] == "no_pss_loading_stock_movements"
    assert out["replayed"] is True
    assert out["reversals"] == []


def test_i_r2_standalone_no_extra_pss_when_no_movements():
    """I: without PSS loading movements, reverse is no-op (no REVERSAL_PENGIRIMAN)."""
    import server

    order = {
        "id": "po-1",
        "shipments": [{
            "id": "sj-1", "is_cancelled": True, "cancelled": True, "status": "cancelled",
            "lines": [{"line_idx": 0, "qty": 10}],
        }],
        "qty_delivered": 0,
        "loading_confirmations": [_conf(qty=10)],
        "line_items": [{"quantity": 10}],
        "total_quantity": 10,
    }

    async def fake_lock(oid, mutate):
        o = copy.deepcopy(order)
        mutate(o)
        return o

    async def _run():
        with patch.object(server, "db", MagicMock(orders=MagicMock(
            find_one=AsyncMock(return_value=copy.deepcopy(order)),
        ))), patch.object(server, "_require_perm", AsyncMock()), patch.object(
            server, "_with_quantity_lock", side_effect=fake_lock,
        ), patch.object(server, "_audit_po", AsyncMock()), patch.object(
            server, "_strip_sj_bytes", side_effect=lambda o: o,
        ), patch.object(server, "_sync_workflow_stage", lambda o: None), patch.object(
            server, "add_timeline", lambda *a, **k: None,
        ), patch(
            "services.physical_stock_engine.reverse_loading_confirmation_stock",
            new=AsyncMock(return_value={
                "reversed": False, "replayed": True, "reversals": [],
                "reason": "no_pss_loading_stock_movements",
            }),
        ) as rev:
            out = await server.delete_loading_confirmation(
                "po-1", "lc-1", user={"email": "a@x", "id": "u1", "name": "A"},
            )
            rev.assert_awaited_once()
            assert out["order"]["loading_confirmations"][0]["cancelled"] is True

    asyncio.run(_run())


def test_l_bug01_02_markers_and_reversal_type_registered():
    assert MOVEMENT_REVERSAL_PENGIRIMAN in VALID_MOVEMENT_TYPES
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_reject_legacy_terkirim_completion" in src
    assert "_bulk_hard_delete_sales_deliveries_atomic" in src
    from permissions import STAGE_PERMISSION
    assert STAGE_PERMISSION.get(13) == "delivery.update"


def test_original_pengiriman_not_deleted():
    async def _run():
        db = MockDB()
        await setup_initialized_product(db, "item-load-1", unprinted=0.0, printed=1500.0)
        oid = "loading:po-load-1:act-1:0"
        await execute_durable_stock_event(
            db, event_id=oid, master_item_id="item-load-1",
            movement_type=MOVEMENT_PENGIRIMAN, transaction_qty=500, transaction_uom="PCS",
            source_entity="SUDAH_DICETAK", destination_entity="LOADING_PENGIRIMAN",
            reference_number="PO", reference_type="loading_confirmation",
            reference_id="po-load-1", order_id="po-load-1", line_idx=0,
            action_id="act-1", is_print=True, user={"email": "t"},
        )
        order = _order()
        await reverse_loading_confirmation_stock(
            db, order, order["loading_confirmations"][0], user={"email": "t"},
        )
        original = await db.stock_movements.find_one({"id": oid}, {"_id": 0})
        assert original is not None
        assert original["movement_type"] == MOVEMENT_PENGIRIMAN
        assert original.get("voided_at") is None

    asyncio.run(_run())
