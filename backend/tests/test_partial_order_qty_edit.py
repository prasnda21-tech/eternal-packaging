"""Partial quantity correction after some of a sales-order line is already processed.

Local dummy documents only. No Accurate calls and no production orders.
"""
from __future__ import annotations

import copy
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402
from services.line_product_replacement import evaluate_line_product_replacement  # noqa: E402
from services.line_qty_commitment import (  # noqa: E402
    MSG_PENDING,
    MSG_QTY_DELIVERED,
    REASON_QTY_BELOW,
    REASON_QTY_DELIVERED,
    REASON_QTY_PENDING,
    REASON_SCHEDULE,
    committed_qty_from_context,
    operator_progress_line_cap_error,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]


def db():
    return server_mod.db


def _admin():
    return {
        "id": "u-admin",
        "email": "admin@eternalpackaging.com",
        "role": "admin",
        "name": "Admin",
    }


def _viewer():
    return {
        "id": "u-viewer-qty",
        "email": "viewer-qty@eternalpackaging.com",
        "role": "crm",
        "name": "Viewer",
        "permissions_override": {},
    }


async def _seed_master(aid: str, name: str):
    mid = f"m-{aid}"
    doc = {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": aid,
        "item_name": name,
        "unit": "pcs",
        "is_active": True,
        "item_type": "INVENTORY",
    }
    await db().master_items.delete_many({"accurate_item_id": aid})
    await db().master_items.insert_one(doc)
    return doc


async def _seed_order(**extra):
    oid = f"ord-qty-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": oid,
        "po_number": f"PO-QTY-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Dummy Customer",
        "current_stage": 0,
        "cancelled": False,
        "sample_files": [],
        "shipments": [],
        "loading_confirmations": [],
        "delivery_confirmations": [],
        "packing_info": {},
        "operator_splits": [],
        "updated_at": "2026-10-05T00:00:00+00:00",
        "line_items": extra.pop("line_items"),
        "total_quantity": extra.pop("total_quantity", None),
        **extra,
    }
    if doc["total_quantity"] is None:
        doc["total_quantity"] = sum(int(li.get("quantity") or 0) for li in doc["line_items"])
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


def _line(master, qty=10000, **extra):
    row = {
        "name": master["item_name"],
        "item_code": master["item_code"],
        "quantity": qty,
        "unit": "pcs",
        "unit_price": 500,
        "line_subtotal": qty * 500,
        "production_process": "print",
        "accurate_item_id": master["accurate_item_id"],
        "master_item_id": master["id"],
        "line_id": f"line-{master['id']}",
    }
    row.update(extra)
    return row


def _split(line_idx, scheduled, completed_parts, *, split_id="split-1"):
    entries = [
        {"id": f"e-{i}", "qty": part, "stage": "operator", "action_id": f"act-{split_id}-{i}"}
        for i, part in enumerate(completed_parts)
    ]
    return {
        "id": split_id,
        "operator_email": "admin@eternalpackaging.com",
        "operator_name": "Operator Dummy",
        "quantity": scheduled,
        "qty_completed": sum(completed_parts),
        "line_idx": line_idx,
        "progress_entries": entries,
    }


def _as_user(user):
    def _dep():
        return user
    return _dep


async def _patch(order_id, body, user=None):
    import httpx

    app.dependency_overrides[current_user] = _as_user(user or _admin())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.patch(f"/api/sales-orders/{order_id}/lines/0/product", json=body)
    finally:
        app.dependency_overrides.pop(current_user, None)


async def _progress(order_id, split_id, qty, action_id):
    import httpx

    app.dependency_overrides[current_user] = _as_user(_admin())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                f"/api/orders/{order_id}/operator-progress",
                json={"operator_id": split_id, "qty": qty, "action_id": action_id},
            )
    finally:
        app.dependency_overrides.pop(current_user, None)


def _print_ready(**extra):
    base = {
        "design_bypassed": True,
        "payment_bypassed": True,
        "helper_prep": {"qty_prepared": 10000},
        "sample_approved": True,
        "selling_total": 5000000,
    }
    base.update(extra)
    return base


def test_committed_qty_is_max_not_sum():
    order = {
        "line_items": [{"quantity": 10000, "production_process": "print"}],
        "operator_splits": [_split(0, 10000, [1000, 1000, 1000])],
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": 3000}]},
        "shipments": [{"id": "sj", "lines": [{"line_idx": 0, "qty": 3000}]}],
        "loading_confirmations": [{"id": "ld", "lines": [{"line_idx": 0, "qty": 3000}]}],
    }
    ctx = {"invoiced": {0: 3000}, "stock_cetak": {0: 3000}, "stock_pengiriman": {0: 3000}, "pending_lines": set()}
    breakdown = committed_qty_from_context(order, 0, ctx)
    assert breakdown["operator_qty"] == 3000
    assert breakdown["packed_qty"] == 3000
    assert breakdown["shipped_qty"] == 3000
    assert breakdown["invoiced_qty"] == 3000
    assert breakdown["stock_qty"] == 3000
    assert breakdown["committed_qty"] == 3000

    mixed = committed_qty_from_context(
        order,
        0,
        {"invoiced": {0: 5000}, "stock_cetak": {0: 3000}, "stock_pengiriman": {0: 4000}, "pending_lines": set()},
    )
    order["shipments"] = [{"id": "sj", "lines": [{"line_idx": 0, "qty": 4000}]}]
    mixed = committed_qty_from_context(
        order,
        0,
        {"invoiced": {0: 5000}, "stock_cetak": {0: 3000}, "stock_pengiriman": {0: 4000}, "pending_lines": set()},
    )
    assert mixed["operator_qty"] == 3000
    assert mixed["shipped_qty"] == 4000
    assert mixed["invoiced_qty"] == 5000
    assert mixed["stock_qty"] == 4000
    assert mixed["committed_qty"] == 5000


def test_operator_entries_are_not_collapsed_to_one_row():
    order = {
        "line_items": [{"quantity": 10000, "production_process": "print"}],
        "operator_splits": [{
            "id": "s",
            "line_idx": 0,
            "quantity": 10000,
            "qty_completed": 1000,
            "progress_entries": [
                {"qty": 1000, "action_id": "a"},
                {"qty": 1000, "action_id": "b"},
                {"qty": 1000, "action_id": "c", "cancelled": True},
                {"qty": 1000, "action_id": "d"},
            ],
        }],
    }
    breakdown = committed_qty_from_context(order, 0, {"pending_lines": set()})
    assert breakdown["operator_qty"] == 3000
    assert breakdown["committed_qty"] == 3000


def test_line_cap_rejects_stale_split_above_latest_line_qty():
    order = {
        "line_items": [
            {"quantity": 5000, "production_process": "print"},
            {"quantity": 10000, "production_process": "print"},
        ],
    }
    split = {"id": "s0", "line_idx": 0, "quantity": 10000, "qty_completed": 3000}
    err = operator_progress_line_cap_error(order, [split], split, 6000)
    assert err
    assert "5.000" in err
    assert operator_progress_line_cap_error(order, [split], split, 5000) is None


@pytest.mark.anyio
async def test_scenario_a_product_stays_locked_and_qty_respects_floor():
    master = await _seed_master("AID-QTY-A", "Cup A")
    other = await _seed_master("AID-QTY-A2", "Cup B")
    order = await _seed_order(
        selling_total=5000000,
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [1000, 1000, 1000])],
    )
    await db().stock_movements.insert_one({
        "id": f"mov-{order['id']}",
        "order_id": order["id"],
        "line_idx": 0,
        "movement_type": "STATUS_CETAK",
        "transaction_qty": 3000,
    })
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["allowed"] is False
        assert guard["product_replace_allowed"] is False
        assert guard["qty_edit_allowed"] is True
        assert guard["current_qty"] == 10000
        assert guard["committed_qty"] == 3000
        assert guard["editable_remaining"] == 7000
        assert guard["min_qty"] == 3000
        assert guard["max_qty"] is None
        assert guard["qty_increase_allowed"] is True

        replaced = await _patch(order["id"], {"master_item_id": other["id"]})
        assert replaced.status_code == 409
        detail = replaced.json()["detail"]
        assert detail["code"] != REASON_QTY_BELOW

        same = await _patch(order["id"], {"quantity": 10000})
        assert same.status_code == 200, same.text
        assert same.json()["message"] == "Qty tidak berubah."
        stored = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert stored["updated_at"] == order["updated_at"]
        assert stored["line_items"][0]["quantity"] == 10000

        too_low = await _patch(order["id"], {"quantity": 2999})
        low = too_low.json()["detail"]
        assert too_low.status_code == 409
        assert low["code"] == REASON_QTY_BELOW
        assert low["current_qty"] == 10000
        assert low["committed_qty"] == 3000
        assert low["minimum_qty"] == 3000
        assert low["requested_qty"] == 2999
        assert "10.000" in low["message"]
        assert "2.999" in low["message"]

        for target in (9000, 8000, 7000, 5000, 3000):
            fresh_order = await _seed_order(
                line_items=[_line(master)],
                operator_splits=[_split(0, 10000, [1000, 1000, 1000])],
            )
            await db().stock_movements.insert_one({
                "id": f"mov-{fresh_order['id']}",
                "order_id": fresh_order["id"],
                "line_idx": 0,
                "movement_type": "STATUS_CETAK",
                "transaction_qty": 3000,
            })
            res = await _patch(fresh_order["id"], {"quantity": target})
            assert res.status_code == 200, res.text
            saved = await db().orders.find_one({"id": fresh_order["id"]}, {"_id": 0})
            assert saved["line_items"][0]["quantity"] == target
            assert saved["line_items"][0]["accurate_item_id"] == master["accurate_item_id"]
            await db().stock_movements.delete_many({"order_id": fresh_order["id"]})
            await db().orders.delete_many({"id": fresh_order["id"]})
    finally:
        await db().stock_movements.delete_many({"order_id": order["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_invoice_floor_does_not_rewrite_invoice():
    master = await _seed_master("AID-QTY-B", "Cup Invoice")
    order = await _seed_order(
        selling_total=5000000,
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    invoice = {
        "id": f"inv-{order['id']}",
        "po_id": order["id"],
        "financial_status": "finalized",
        "accounting_status": "posted",
        "grand_total": 2500000,
        "lines": [{"line_idx": 0, "quantity": 5000, "unit_price": 500}],
    }
    draft = {
        "id": f"inv-draft-{order['id']}",
        "po_id": order["id"],
        "financial_status": "draft",
        "lines": [{"line_idx": 0, "quantity": 0}],
    }
    cancelled = {
        "id": f"inv-cancel-{order['id']}",
        "po_id": order["id"],
        "financial_status": "cancelled",
        "lines": [{"line_idx": 0, "quantity": 9000}],
    }
    await db().sales_invoices.insert_many([invoice, draft, cancelled])
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["committed_qty"] == 5000
        ok = await _patch(order["id"], {"quantity": 5000})
        assert ok.status_code == 200, ok.text
        low = await _patch(order["id"], {"quantity": 4999})
        assert low.status_code == 409
        assert low.json()["detail"]["code"] == REASON_QTY_BELOW
        assert low.json()["detail"]["committed_qty"] == 5000
        higher = await _seed_order(
            line_items=[_line(master)],
            operator_splits=[_split(0, 10000, [3000])],
        )
        await db().sales_invoices.update_one({"id": invoice["id"]}, {"$set": {"po_id": higher["id"]}})
        allowed = await _patch(higher["id"], {"quantity": 8000})
        assert allowed.status_code == 200, allowed.text
        stored_invoice = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert stored_invoice["lines"] == invoice["lines"]
        assert stored_invoice["financial_status"] == "finalized"
        assert stored_invoice["grand_total"] == 2500000
        assert stored_invoice["accounting_status"] == "posted"
        await db().orders.delete_many({"id": higher["id"]})
    finally:
        await db().sales_invoices.delete_many({"po_id": {"$in": [order["id"]]}})
        await db().sales_invoices.delete_many({"id": invoice["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_active_shipment_floor_ignores_inactive_sj():
    master = await _seed_master("AID-QTY-C", "Cup SJ")
    order = await _seed_order(
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
        shipments=[
            {"id": "sj-1", "status": "confirmed", "lines": [{"line_idx": 0, "qty": 2000}]},
            {"id": "sj-2", "lines": [{"line_idx": 0, "qty": 2000}]},
            {"id": "sj-cancel", "status": "cancelled", "lines": [{"line_idx": 0, "qty": 9000}]},
            {"id": "sj-super", "superseded": True, "lines": [{"line_idx": 0, "qty": 8000}]},
            {"id": "sj-del", "is_deleted": True, "lines": [{"line_idx": 0, "qty": 7000}]},
        ],
    )
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["committed_qty"] == 4000
        ok = await _patch(order["id"], {"quantity": 4000})
        assert ok.status_code == 200, ok.text
        low = await _patch(order["id"], {"quantity": 3999})
        assert low.status_code == 409
        assert low.json()["detail"]["committed_qty"] == 4000
        assert low.json()["detail"]["code"] == REASON_QTY_BELOW
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_stock_types_are_not_summed_and_unrelated_movements_are_ignored():
    master = await _seed_master("AID-QTY-ST", "Cup Stock")
    order = await _seed_order(
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [1000, 1000, 1000])],
    )
    await db().stock_movements.insert_many([
        {"id": "c1", "order_id": order["id"], "line_idx": 0, "movement_type": "STATUS_CETAK", "transaction_qty": 1000},
        {"id": "c2", "order_id": order["id"], "line_idx": 0, "movement_type": "STATUS_CETAK", "transaction_qty": 1000},
        {"id": "c3", "order_id": order["id"], "line_idx": 0, "movement_type": "STATUS_CETAK", "transaction_qty": 1000},
        {"id": "p1", "order_id": order["id"], "line_idx": 0, "movement_type": "PENGIRIMAN", "transaction_qty": 3000},
        {"id": "open", "order_id": order["id"], "line_idx": 0, "movement_type": "SALDO_AWAL", "transaction_qty": 50000},
        {"id": "kor", "order_id": order["id"], "line_idx": 0, "movement_type": "KOREKSI", "transaction_qty": 40000},
        {"id": "void", "order_id": order["id"], "line_idx": 0, "movement_type": "STATUS_CETAK", "transaction_qty": 9000, "voided_at": "2026-01-01"},
    ])
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["committed_qty"] == 3000
        assert guard["allowed"] is False
        res = await _patch(order["id"], {"quantity": 8000})
        assert res.status_code == 200, res.text
    finally:
        await db().stock_movements.delete_many({"order_id": order["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_pending_stock_claim_blocks_qty_correction():
    master = await _seed_master("AID-QTY-PEND", "Cup Pending")
    order = await _seed_order(
        line_items=[_line(master, production_process="non_print")],
        operator_splits=[],
    )
    await db().product_stock_state.insert_one({
        "master_item_id": master["id"],
        "pending_event": {
            "order_id": order["id"],
            "line_idx": 0,
            "movement_type": "STATUS_CETAK",
            "transaction_qty": 1000,
            "stage": "CLAIMED",
        },
    })
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["qty_edit_allowed"] is False
        assert guard["allowed"] is False
        blocked = await _patch(order["id"], {"quantity": 8000})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == REASON_QTY_PENDING
        assert blocked.json()["detail"]["message"] == MSG_PENDING
        blocked_up = await _patch(order["id"], {"quantity": 12000})
        assert blocked_up.status_code == 409
        assert blocked_up.json()["detail"]["message"] == MSG_PENDING
        await db().product_stock_state.delete_many({"master_item_id": master["id"]})
        allowed = await _patch(order["id"], {"quantity": 8000})
        assert allowed.status_code == 200, allowed.text
    finally:
        await db().product_stock_state.delete_many({"master_item_id": master["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_qty_recalculates_stored_price_without_master_quote():
    import services.sales_order_prices as prices

    master = await _seed_master("AID-QTY-PRICE", "Cup Price")
    order = await _seed_order(
        selling_total=5000000,
        line_items=[_line(master, 10000)],
    )
    called = {"hit": False}

    async def _boom(*_args, **_kwargs):
        called["hit"] = True
        raise AssertionError("price master was consulted")

    original = prices.quote_selling_price
    prices.quote_selling_price = _boom
    try:
        res = await _patch(order["id"], {"quantity": 8000})
        assert res.status_code == 200, res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        line = saved["line_items"][0]
        assert line["unit_price"] == 500
        assert line["line_subtotal"] == 4000000
        assert saved["selling_total"] == 4000000
        assert called["hit"] is False
    finally:
        prices.quote_selling_price = original
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_single_split_remainder_is_trimmed_and_completed_stays():
    master = await _seed_master("AID-QTY-SCH", "Cup Schedule")
    order = await _seed_order(
        **_print_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [1000, 1000, 1000])],
    )
    try:
        res = await _patch(order["id"], {"quantity": 8000})
        assert res.status_code == 200, res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        split = saved["operator_splits"][0]
        assert split["quantity"] == 8000
        assert split["qty_completed"] == 3000
        assert [entry["qty"] for entry in split["progress_entries"]] == [1000, 1000, 1000]
        assert saved["line_items"][0]["quantity"] == 8000

        filled = await _progress(order["id"], "split-1", 5000, "fill-to-cap")
        assert filled.status_code == 200, filled.text
        over = await _progress(order["id"], "split-1", 1, "over-cap")
        assert over.status_code == 400, over.text
        after = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert after["operator_splits"][0]["qty_completed"] == 8000
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_ambiguous_operator_schedule_rejects_without_redistributing():
    master = await _seed_master("AID-QTY-AMB", "Cup Ambiguous")
    order = await _seed_order(
        line_items=[_line(master)],
        operator_splits=[
            _split(0, 6000, [3000], split_id="op-a"),
            _split(0, 4000, [], split_id="op-b"),
        ],
    )
    try:
        res = await _patch(order["id"], {"quantity": 8000})
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == REASON_SCHEDULE
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 10000
        assert [s["quantity"] for s in saved["operator_splits"]] == [6000, 4000]
        assert saved["operator_splits"][0]["qty_completed"] == 3000
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_operator_progress_cannot_pass_latest_line_qty():
    master = await _seed_master("AID-QTY-CAP", "Cup Cap")
    other = await _seed_master("AID-QTY-CAP2", "Lid Cap")
    order = await _seed_order(
        **_print_ready(helper_prep={"qty_prepared": 20000}, selling_total=7500000),
        total_quantity=15000,
        line_items=[
            _line(master, 5000),
            _line(other, 10000, production_process="print"),
        ],
        operator_splits=[_split(0, 10000, [3000], split_id="split-cap")],
    )
    try:
        res = await _progress(order["id"], "split-cap", 3000, "too-far")
        assert res.status_code == 400, res.text
        assert "pesanan terkini" in res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["operator_splits"][0]["qty_completed"] == 3000
        assert saved["line_items"][0]["quantity"] == 5000
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_lowered_order_blocks_later_operator_progress():
    master = await _seed_master("AID-QTY-RACE", "Cup Race")
    order = await _seed_order(
        **_print_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    try:
        lowered = await _patch(order["id"], {"quantity": 5000})
        assert lowered.status_code == 200, lowered.text
        attempt = await _progress(order["id"], "split-1", 3000, "after-admin")
        assert attempt.status_code == 400, attempt.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 5000
        assert saved["operator_splits"][0]["qty_completed"] == 3000
        assert saved["operator_splits"][0]["quantity"] == 5000
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_sample_locks_product_but_not_qty_and_cancel_blocks_both():
    master = await _seed_master("AID-QTY-SAM", "Cup Sample")
    other = await _seed_master("AID-QTY-SAM2", "Cup Sample 2")
    sampled = await _seed_order(
        sample_files=[{"id": "sf", "name": "sample.png"}],
        line_items=[_line(master)],
    )
    cancelled = await _seed_order(
        cancelled=True,
        line_items=[_line(master, 10000)],
    )
    try:
        guard = await evaluate_line_product_replacement(db(), sampled, 0)
        assert guard["allowed"] is False
        assert guard["qty_edit_allowed"] is True
        product = await _patch(sampled["id"], {"master_item_id": other["id"]})
        assert product.status_code == 409
        qty = await _patch(sampled["id"], {"quantity": 8000})
        assert qty.status_code == 200, qty.text

        cancel_guard = await evaluate_line_product_replacement(db(), cancelled, 0)
        assert cancel_guard["qty_edit_allowed"] is False
        assert cancel_guard["allowed"] is False
        denied = await _patch(cancelled["id"], {"quantity": 8000})
        assert denied.status_code == 409
        untouched = await db().orders.find_one({"id": cancelled["id"]}, {"_id": 0})
        assert untouched["line_items"][0]["quantity"] == 10000
    finally:
        await db().orders.delete_many({"id": {"$in": [sampled["id"], cancelled["id"]]}})


@pytest.mark.anyio
async def test_qty_edit_requires_po_edit_and_admin_still_hits_the_floor():
    master = await _seed_master("AID-QTY-PERM", "Cup Perm")
    order = await _seed_order(
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    try:
        forbidden = await _patch(order["id"], {"quantity": 8000}, user=_viewer())
        assert forbidden.status_code == 403
        rejected = await _patch(order["id"], {"quantity": 2999}, user=_admin())
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == REASON_QTY_BELOW
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 10000
    finally:
        await db().orders.delete_many({"id": order["id"]})


def _schedule_ready(**extra):
    base = _print_ready(helper_prep={"qty_prepared": 20000}, selling_total=5000000)
    base.update(extra)
    return base


async def _post(path, body):
    import httpx

    app.dependency_overrides[current_user] = _as_user(_admin())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=body)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_qty_can_increase_above_current_without_an_artificial_maximum():
    master = await _seed_master("AID-QTY-UP", "Cup Increase")
    other = await _seed_master("AID-QTY-UP2", "Cup Other")
    created = []
    try:
        for target in (3000, 8000, 10000, 11000, 12000, 15000):
            order = await _seed_order(
                selling_total=5000000,
                line_items=[_line(master, price_source="pengaturan_harga")],
                operator_splits=[_split(0, 10000, [3000])],
            )
            created.append(order["id"])
            await db().stock_movements.insert_one({
                "id": f"mov-{order['id']}",
                "order_id": order["id"],
                "line_idx": 0,
                "movement_type": "STATUS_CETAK",
                "transaction_qty": 3000,
            })
            res = await _patch(order["id"], {"quantity": target})
            assert res.status_code == 200, res.text
            saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
            assert saved["line_items"][0]["quantity"] == target
            assert saved["line_items"][0]["accurate_item_id"] == master["accurate_item_id"]
            assert saved["line_items"][0]["unit_price"] == 500
            assert saved["line_items"][0]["price_source"] == "pengaturan_harga"
            if target == 10000:
                assert res.json()["message"] == "Qty tidak berubah."
                assert saved["updated_at"] == order["updated_at"]
            else:
                assert saved["line_items"][0]["line_subtotal"] == target * 500
                assert saved["selling_total"] == target * 500
                assert saved["total_quantity"] == target
                assert res.json()["before_quantity"] == 10000
                assert res.json()["after_quantity"] == target
        blocked = await _patch(created[0], {"master_item_id": other["id"], "quantity": 15000})
        assert blocked.status_code == 409
        stored = await db().orders.find_one({"id": created[0]}, {"_id": 0})
        assert stored["line_items"][0]["accurate_item_id"] == master["accurate_item_id"]
    finally:
        await db().stock_movements.delete_many({"order_id": {"$in": created}})
        await db().orders.delete_many({"id": {"$in": created}})


@pytest.mark.anyio
async def test_increase_keeps_schedule_and_operator_cap_until_reschedule():
    from quantity_rules import operator_assigned_qty, print_qty_total

    master = await _seed_master("AID-QTY-SCHUP", "Cup Schedule Up")
    order = await _seed_order(
        **_schedule_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    try:
        res = await _patch(order["id"], {"quantity": 12000})
        assert res.status_code == 200, res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        split = saved["operator_splits"][0]
        assert saved["line_items"][0]["quantity"] == 12000
        assert split["quantity"] == 10000
        assert split["qty_completed"] == 3000
        assert [entry["qty"] for entry in split["progress_entries"]] == [3000]
        assert print_qty_total(saved) - operator_assigned_qty(saved) == 2000
        over = await _progress(order["id"], "split-1", 7001, "over-old-split")
        assert over.status_code == 400, over.text
        await db().orders.update_one(
            {"id": order["id"]},
            {"$set": {"operator_splits.0.quantity": 12000}},
        )
        reached = await _progress(order["id"], "split-1", 9000, "reach-new-order")
        assert reached.status_code == 200, reached.text
        past = await _progress(order["id"], "split-1", 1, "past-order")
        assert past.status_code == 400, past.text
        final = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert final["line_items"][0]["quantity"] == 12000
        assert final["operator_splits"][0]["qty_completed"] == 12000
        assert final["operator_splits"][0]["quantity"] == 12000
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_increase_leaves_invoice_and_shipment_documents_unchanged():
    from services.sales_invoices import get_po_invoice_preview

    master = await _seed_master("AID-QTY-INVUP", "Cup Invoice Up")
    order = await _seed_order(
        selling_total=5000000,
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
        shipments=[{"id": "sj-keep", "status": "confirmed", "lines": [{"line_idx": 0, "qty": 4000}]}],
    )
    invoice = {
        "id": f"inv-up-{order['id']}",
        "po_id": order["id"],
        "financial_status": "finalized",
        "accounting_status": "posted",
        "payment_status": "LUNAS",
        "grand_total": 2500000,
        "lines": [{"line_idx": 0, "quantity": 5000, "unit_price": 500}],
    }
    await db().sales_invoices.insert_one(invoice)
    try:
        res = await _patch(order["id"], {"quantity": 12000})
        assert res.status_code == 200, res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 12000
        assert saved["shipments"] == order["shipments"]
        stored_invoice = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert stored_invoice["lines"] == invoice["lines"]
        assert stored_invoice["financial_status"] == "finalized"
        assert stored_invoice["accounting_status"] == "posted"
        assert stored_invoice["payment_status"] == "LUNAS"
        assert stored_invoice["grand_total"] == 2500000
        preview = await get_po_invoice_preview(db(), saved)
        assert preview["lines"][0]["invoiced_cumulative"] == 5000
        assert preview["lines"][0]["remaining_invoice_qty"] == 7000
        assert preview["lines"][0]["unit_price"] == 500
    finally:
        await db().sales_invoices.delete_many({"id": invoice["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_increase_allowed_when_invoice_is_finalized_posted_and_paid():
    from services.sales_invoices import get_po_invoice_preview

    master = await _seed_master("AID-QTY-PAID", "Cup Paid")
    order = await _seed_order(
        selling_total=5000000,
        current_stage=11,
        line_items=[_line(master)],
    )
    invoice = {
        "id": f"inv-paid-{order['id']}",
        "po_id": order["id"],
        "financial_status": "finalized",
        "accounting_status": "posted",
        "payment_status": "LUNAS",
        "grand_total": 5000000,
        "lines": [{"line_idx": 0, "quantity": 10000, "unit_price": 500}],
    }
    await db().sales_invoices.insert_one(invoice)
    try:
        res = await _patch(order["id"], {"quantity": 12000})
        assert res.status_code == 200, res.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 12000
        assert saved["current_stage"] == 11
        stored_invoice = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert stored_invoice["lines"][0]["quantity"] == 10000
        assert stored_invoice["accounting_status"] == "posted"
        assert stored_invoice["payment_status"] == "LUNAS"
        preview = await get_po_invoice_preview(db(), saved)
        assert preview["lines"][0]["remaining_invoice_qty"] == 2000
    finally:
        await db().sales_invoices.delete_many({"id": invoice["id"]})
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_fully_delivered_order_rejects_qty_increase():
    from services.line_product_replacement import evaluate_line_product_replacement
    from services.status_po import po_age_bounds

    master = await _seed_master("AID-QTY-DONE", "Cup Delivered")
    order = await _seed_order(
        selling_total=5000000,
        current_stage=14,
        qty_delivered=10000,
        delivery_status="Terkirim",
        line_items=[_line(master)],
    )
    before_age = po_age_bounds(order)
    try:
        guard = await evaluate_line_product_replacement(db(), order, 0)
        assert guard["qty_increase_allowed"] is False
        assert guard["qty_increase_reason"] == MSG_QTY_DELIVERED
        res = await _patch(order["id"], {"quantity": 12000})
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["code"] == REASON_QTY_DELIVERED
        assert detail["message"] == MSG_QTY_DELIVERED
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 10000
        assert saved["current_stage"] == 14
        assert saved["qty_delivered"] == 10000
        assert saved["delivery_status"] == "Terkirim"
        assert saved["updated_at"] == order["updated_at"]
        assert po_age_bounds(saved) == before_age
        assert before_age["age_closed"] is True
    finally:
        await db().orders.delete_many({"id": order["id"]})


async def _save_progress_from_stale_read(order_id, stale, post):
    """First full order read returns the pre-increase snapshot. Later reads are live."""
    from motor.motor_asyncio import AsyncIOMotorCollection

    real_find = AsyncIOMotorCollection.find_one
    seen = {"full": 0}

    async def fake_find(self, filt=None, *args, **kwargs):
        projection = args[0] if args else kwargs.get("projection")
        if getattr(self, "name", None) == "orders" and isinstance(filt, dict) and filt.get("id") == order_id:
            if projection == {"_id": 0}:
                seen["full"] += 1
                if seen["full"] == 1:
                    return copy.deepcopy(stale)
        return await real_find(self, filt, *args, **kwargs)

    AsyncIOMotorCollection.find_one = fake_find
    try:
        response = await post()
    finally:
        AsyncIOMotorCollection.find_one = real_find
    return response, seen["full"]


@pytest.mark.anyio
async def test_screener_stale_snapshot_does_not_overwrite_increased_qty():
    master = await _seed_master("AID-QTY-SCR", "Cup Screener")
    order = await _seed_order(
        **_schedule_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    stale = copy.deepcopy(order)
    try:
        increased = await _patch(order["id"], {"quantity": 12000})
        assert increased.status_code == 200, increased.text
        response, reads = await _save_progress_from_stale_read(
            order["id"],
            stale,
            lambda: _post(f"/api/orders/{order['id']}/screener-info", {"qty": 100, "action_id": "scr-stale"}),
        )
        assert reads >= 1
        assert response.status_code == 200, response.text
        saved = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        assert saved["line_items"][0]["quantity"] == 12000
        assert saved["total_quantity"] == 12000
        assert saved["screener_info"]["screen_qty"] == 100
        assert saved["line_items"][0]["unit_price"] == 500
    finally:
        await db().orders.delete_many({"id": order["id"]})


@pytest.mark.anyio
async def test_helper_stale_snapshot_does_not_overwrite_increased_qty():
    master = await _seed_master("AID-QTY-HLP", "Cup Helper")
    order = await _seed_order(
        **_schedule_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000])],
    )
    legacy = await _seed_order(
        **_schedule_ready(),
        line_items=[_line(master)],
        operator_splits=[_split(0, 10000, [3000], split_id="split-legacy")],
    )
    stale_items = copy.deepcopy(order)
    stale_legacy = copy.deepcopy(legacy)
    try:
        assert (await _patch(order["id"], {"quantity": 12000})).status_code == 200
        assert (await _patch(legacy["id"], {"quantity": 12000})).status_code == 200
        items_res, items_reads = await _save_progress_from_stale_read(
            order["id"],
            stale_items,
            lambda: _post(
                f"/api/orders/{order['id']}/helper-prep/items",
                {"items": [{"line_idx": 0, "qty": 3000, "action_id": "hlp-stale"}]},
            ),
        )
        legacy_res, legacy_reads = await _save_progress_from_stale_read(
            legacy["id"],
            stale_legacy,
            lambda: _post(
                f"/api/orders/{legacy['id']}/helper-prep",
                {"qty_required": 4000, "qty_prepared": 4000},
            ),
        )
        assert items_reads >= 1 and legacy_reads >= 1
        assert items_res.status_code == 200, items_res.text
        assert legacy_res.status_code == 200, legacy_res.text
        saved_items = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
        saved_legacy = await db().orders.find_one({"id": legacy["id"]}, {"_id": 0})
        assert saved_items["line_items"][0]["quantity"] == 12000
        assert saved_items["helper_prep"]["qty_prepared"] == 3000
        assert saved_legacy["line_items"][0]["quantity"] == 12000
        assert saved_legacy["helper_prep"]["qty_prepared"] == 4000
    finally:
        await db().orders.delete_many({"id": {"$in": [order["id"], legacy["id"]]}})
