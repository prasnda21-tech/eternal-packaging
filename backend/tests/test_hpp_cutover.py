"""Local inventory HPP cutover foundation. Private database. No customer pricing writes."""
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
if "replicaSet=" not in os.environ["MONGO_URL"]:
    os.environ["MONGO_URL"] = os.environ["MONGO_URL"].rstrip("/") + (
        "&" if "?" in os.environ["MONGO_URL"] else "?"
    ) + "replicaSet=eternal_local_rs"

from services.hpp_cutover import (
    confirm_cutover,
    ensure_indexes,
    list_reconciliation,
    recognize_posted_goods_receipt,
    recover_stale_cutover_lease,
)
from services.hpp_valuation import recalculate_hpp
from services.physical_stock import (
    LEDGER_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    ensure_indexes as ensure_stock_indexes,
    get_balance,
)
from services.purchase_goods_receipts import (
    cancel_purchase_goods_receipt,
    create_purchase_goods_receipt,
)
from services.purchase_invoices import create_purchase_invoice

DB_NAME = "eternal_hpp_cutover_foundation"
USER = {"id": "cutover-user", "name": "Cutover", "role": "admin", "email": "c@x"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    await ensure_stock_indexes(database)
    await ensure_indexes(database)
    await database.hpp_cost_versions.create_index("id", unique=True)
    await database.hpp_cost_layers.create_index(
        [("calculation_version", 1), ("layer_id", 1)],
        unique=True,
    )
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.app_settings.create_index("key", unique=True)
    yield database
    await client.drop_database(DB_NAME)
    client.close()


async def _settings(db, method="RATA-RATA", recognition="goods_receipt", active=None):
    await db.app_settings.insert_one({
        "key": "hpp_valuation",
        "method": method,
        "recognition_point": recognition,
        "recognition_label": "Saat Penerimaan Barang",
        "active_version": active,
        "last_recalculation_status": "SUCCESS",
    })


def _payload(request_id, mid, cost, **extra):
    body = {
        "client_request_id": request_id,
        "expected_method": extra.pop("expected_method", "RATA-RATA"),
        "expected_recognition_point": extra.pop("expected_recognition_point", "goods_receipt"),
        "expected_active_version": extra.pop("expected_active_version", None),
        "acknowledge_settings_change": extra.pop("acknowledge_settings_change", False),
        "lines": [{
            "master_item_id": mid,
            "accurate_item_id": extra.pop("accurate_item_id", "acc-a"),
            "item_code": "A",
            "item_name": "Produk A",
            "opening_unit_cost": cost,
            "base_uom": "PCS",
        }],
    }
    body.update(extra)
    return body


async def _ensure_org(db):
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await db.branches.insert_one({
            "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": "wh-ygy", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        })


async def _stock(db, mid, qty, aid="acc-a"):
    await _ensure_org(db)
    await db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "base_uom": "PCS",
        "total_qty": qty,
        "unprinted_qty": qty if qty > 0 else 0,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    await db.physical_stock_balances.update_one(
        {"master_item_id": mid, "warehouse_id": "wh-ygy"},
        {"$set": {
            "id": f"bal-{mid}",
            "master_item_id": mid,
            "branch_id": "branch-ygy",
            "warehouse_id": "wh-ygy",
            "physical_qty": qty,
        }},
        upsert=True,
    )


async def _row(db, cutover_id, mid):
    rows = await list_reconciliation(db, cutover_id)
    return next(row for row in rows if row["master_item_id"] == mid)


@pytest.mark.anyio
async def test_a_historical_gr_not_replayed(db, monkeypatch):
    mid = "sku-a"
    await _stock(db, mid, 100)
    await _settings(db)
    await db.purchase_orders.insert_one({
        "id": "po-old",
        "po_number": "PB-OLD",
        "cancelled": False,
        "lines": [{
            "line_id": "pol-old",
            "master_item_id": mid,
            "accurate_item_id": "acc-a",
            "ordered_qty": 50,
            "unit_price": 800,
        }],
    })
    await db.purchase_goods_receipts.insert_one({
        "id": "gr-old",
        "purchase_order_id": "po-old",
        "receipt_number": "GR-OLD",
        "receipt_date": "2020-01-02",
        "cancelled": False,
        "lines": [{
            "line_id": "grl-old",
            "purchase_order_line_id": "pol-old",
            "master_item_id": mid,
            "accurate_item_id": "acc-a",
            "received_qty": 50,
            "unit_price": 800,
        }],
    })

    async def replay(*_a, **_k):
        raise AssertionError("historical acquisition replay")

    monkeypatch.setattr("services.hpp_valuation._load_source_docs", replay)
    monkeypatch.setattr("services.hpp_valuation.build_acquisition_events", replay)
    result = await confirm_cutover(db, _payload("req-a", mid, 1000), USER)
    assert result["cutover"]["status"] == "ACTIVE"
    row = await _row(db, result["cutover"]["cutover_id"], mid)
    assert row["valuation_qty"] == 100
    assert row["valuation_value"] == 100000
    assert row["current_hpp"] == 1000
    assert row["hpp_status"] == "OK"
    layers = await db.hpp_cost_layers.find(
        {"calculation_version": result["settings"]["active_version"]},
        {"_id": 0},
    ).to_list(10)
    assert [layer["source_type"] for layer in layers] == ["OPENING_HPP"]
    assert layers[0]["remaining_qty"] == 100
    await recalculate_hpp(db, method="RATA-RATA", recognition_point="goods_receipt", user=USER)
    assert await db.purchase_goods_receipts.count_documents({"id": "gr-old"}) == 1
    stock = await db.product_stock_state.find_one({"master_item_id": mid})
    assert stock["total_qty"] == 100


@pytest.mark.anyio
async def test_b_future_receipt_updates_average(db):
    mid = "sku-b"
    aid = "acc-b"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS", "item_name": "B"})
    await _settings(db)
    opened = await confirm_cutover(db, _payload("req-b", mid, 1000, accurate_item_id=aid), USER)
    movements_before = await db.stock_movements.count_documents({})
    po_line = "pol-b"
    await db.purchase_orders.insert_one({
        "id": "po-b",
        "po_number": "PB-B",
        "supplier": "S",
        "cancelled": False,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery",
        },
        "lines": [{
            "line_id": po_line,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": "B",
            "item_name": "Produk B",
            "unit": "PCS",
            "ordered_qty": 50,
            "unit_price": 1200,
        }],
    })
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-b",
        "receipt_date": "2026-09-22",
        "idempotency_key": "gr-b-1",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }, USER)
    stock = await db.product_stock_state.find_one({"master_item_id": mid})
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert stock["total_qty"] == 100
    assert (await get_balance(db, mid, "branch-ygy", "wh-ygy"))["physical_qty"] == 150
    assert row["valuation_qty"] == 150
    assert row["valuation_value"] == 160000
    assert row["exact_average"].startswith("1066.666")
    assert await db.hpp_recognition_events.count_documents({"source_key": {"$regex": "^gr:"}}) == 1
    assert await db.stock_movements.count_documents({}) == movements_before
    qty_after_gr = stock["total_qty"]
    moves_after_gr = await db.stock_movements.count_documents({})
    await recalculate_hpp(db, method="RATA-RATA", recognition_point="goods_receipt", user=USER)
    stock2 = await db.product_stock_state.find_one({"master_item_id": mid})
    assert stock2["total_qty"] == qty_after_gr
    assert await db.stock_movements.count_documents({}) == moves_after_gr
    assert receipt["id"]


@pytest.mark.anyio
async def test_c_retry_same_receipt_does_not_duplicate(db):
    mid = "sku-c"
    aid = "acc-c"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(db, _payload("req-c", mid, 1000, accurate_item_id=aid), USER)
    po_line = "pol-c"
    await db.purchase_orders.insert_one({
        "id": "po-c",
        "po_number": "PB-C",
        "supplier": "S",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": po_line,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_name": "C",
            "unit": "PCS",
            "ordered_qty": 50,
            "unit_price": 1200,
        }],
    })
    body = {
        "purchase_order_id": "po-c",
        "idempotency_key": "gr-c-retry",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }
    await create_purchase_goods_receipt(db, body, USER)
    before = await _row(db, opened["cutover"]["cutover_id"], mid)
    events_before = await db.hpp_recognition_events.count_documents({})
    moves_before = await db.stock_movements.count_documents({})
    await create_purchase_goods_receipt(db, body, USER)
    after = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert await db.hpp_recognition_events.count_documents({}) == events_before
    assert after["valuation_qty"] == before["valuation_qty"]
    assert after["valuation_value"] == before["valuation_value"]
    assert await db.stock_movements.count_documents({}) == moves_before


@pytest.mark.anyio
async def test_d_backdated_receipt_included_once(db):
    mid = "sku-d"
    aid = "acc-d"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(db, _payload("req-d", mid, 1000, accurate_item_id=aid), USER)
    po_line = "pol-d"
    await db.purchase_orders.insert_one({
        "id": "po-d",
        "po_number": "PB-D",
        "supplier": "S",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": po_line,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_name": "D",
            "unit": "PCS",
            "ordered_qty": 10,
            "unit_price": 900,
        }],
    })
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-d",
        "receipt_date": "2019-01-01",
        "idempotency_key": "gr-d-back",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 10}],
    }, USER)
    event = await db.hpp_recognition_events.find_one({"cutover_id": opened["cutover"]["cutover_id"]}, {"_id": 0})
    assert event["receipt_date"] == "2019-01-01"
    assert event["recognized_at"] > opened["cutover"]["created_at"]
    assert event["status"] == "RECOGNIZED"
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["valuation_qty"] == 110
    again = await recognize_posted_goods_receipt(db, receipt, USER)
    assert len(again) == 1
    assert await db.hpp_recognition_events.count_documents({"source_key": event["source_key"]}) == 1
    row2 = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row2["valuation_qty"] == 110


@pytest.mark.anyio
async def test_e_recalc_failure_is_not_success(db, monkeypatch):
    mid = "sku-e"
    await _stock(db, mid, 100)
    await db.hpp_cost_versions.insert_one({"id": "prev-e", "version": 1, "status": "ACTIVE", "method": "RATA-RATA"})
    await _settings(db, active="prev-e")

    def boom(*_a, **_k):
        raise RuntimeError("forced recalc failure")

    monkeypatch.setattr("services.hpp_cutover.moving_average_from_events", boom)
    with pytest.raises(HTTPException) as caught:
        await confirm_cutover(db, _payload("req-e", mid, 1000, expected_active_version="prev-e"), USER)
    assert caught.value.status_code == 500
    assert caught.value.detail["code"] == "FAILED"
    settings = await db.app_settings.find_one({"key": "hpp_valuation"})
    assert settings["active_version"] == "prev-e"
    assert settings["last_recalculation_status"] != "SUCCESS"
    cutover = await db.hpp_valuation_cutovers.find_one({"client_request_id": "req-e"})
    assert cutover["status"] == "FAILED"
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 0
    assert (await db.hpp_cost_versions.find_one({"id": "prev-e"}))["status"] == "ACTIVE"


@pytest.mark.anyio
async def test_f_concurrent_confirm_one_winner(db, monkeypatch):
    import asyncio
    from services import hpp_cutover

    mid = "sku-f"
    await _stock(db, mid, 40)
    await _settings(db)
    first_done = asyncio.Event()
    real_acquire = hpp_cutover._acquire_lease

    async def ordered_acquire(database, claim_id, owner, request_id):
        if claim_id == hpp_cutover.CLAIM_CONFIRM and request_id == "req-f-2":
            await first_done.wait()
        return await real_acquire(database, claim_id, owner, request_id)

    monkeypatch.setattr(hpp_cutover, "_acquire_lease", ordered_acquire)

    async def attempt(request_id, user_id):
        user = {**USER, "id": user_id}
        try:
            return await confirm_cutover(db, _payload(request_id, mid, 500), user)
        finally:
            if request_id == "req-f-1":
                first_done.set()

    results = await asyncio.gather(
        attempt("req-f-1", "user-1"),
        attempt("req-f-2", "user-2"),
        return_exceptions=True,
    )
    successes = [row for row in results if not isinstance(row, Exception)]
    conflicts = [
        row for row in results
        if isinstance(row, HTTPException) and row.detail.get("code") == "CONFLICT"
    ]
    assert len(successes) == 1, results
    assert len(conflicts) == 1, results
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 1
    assert await db.hpp_cost_versions.count_documents({"status": "ACTIVE"}) == 1


@pytest.mark.anyio
async def test_g_stale_lease_recovers_without_activating(db):
    await _settings(db, active="prev-g")
    await db.hpp_cost_versions.insert_one({"id": "prev-g", "version": 1, "status": "ACTIVE"})
    await db.hpp_cutover_claims.update_one(
        {"_id": "hpp_cutover"},
        {"$set": {
            "state": "BUILDING",
            "lease_owner": "crashed",
            "client_request_id": "req-g",
            "lease_expires_at": "2000-01-01T00:00:00+00:00",
        }},
    )
    await db.hpp_valuation_cutovers.insert_one({
        "cutover_id": "cut-g",
        "status": "BUILDING",
        "method": "RATA-RATA",
        "recognition_point": "goods_receipt",
        "baseline_version_id": None,
        "created_by": {},
        "created_at": "2026-09-22T00:00:00+00:00",
        "activated_at": None,
        "client_request_id": "req-g",
        "lease_owner": "crashed",
        "lease_expires_at": "2000-01-01T00:00:00+00:00",
    })
    await db.hpp_cost_versions.insert_one({
        "id": "building-g",
        "version": 2,
        "status": "BUILDING",
        "cutover_client_request_id": "req-g",
    })
    first = await recover_stale_cutover_lease(db)
    assert first["changed"] is True
    assert first["status"] == "NEEDS_RECOVERY"
    assert first["activated"] == 0
    cutover = await db.hpp_valuation_cutovers.find_one({"cutover_id": "cut-g"})
    version = await db.hpp_cost_versions.find_one({"id": "building-g"})
    settings = await db.app_settings.find_one({"key": "hpp_valuation"})
    assert cutover["status"] == "NEEDS_RECOVERY"
    assert version["status"] == "NEEDS_RECOVERY"
    assert settings["active_version"] == "prev-g"
    assert settings["method"] == "RATA-RATA"
    second = await recover_stale_cutover_lease(db)
    assert second["changed"] is False
    assert (await db.hpp_valuation_cutovers.find_one({"cutover_id": "cut-g"}))["status"] == "NEEDS_RECOVERY"
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE"}) == 0


@pytest.mark.anyio
async def test_h_settings_conflict_preserves_fifo(db):
    await db.hpp_cost_versions.insert_one({"id": "fifo-v", "version": 1, "status": "ACTIVE", "method": "FIFO"})
    await _settings(db, method="FIFO", recognition="goods_receipt", active="fifo-v")
    with pytest.raises(HTTPException) as caught:
        await confirm_cutover(db, _payload(
            "req-h",
            "sku-h",
            100,
            expected_method="FIFO",
            expected_recognition_point="goods_receipt",
            expected_active_version="fifo-v",
            acknowledge_settings_change=False,
        ), USER)
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "SETTINGS_CONFLICT"
    settings = await db.app_settings.find_one({"key": "hpp_valuation"})
    assert settings["method"] == "FIFO"
    assert settings["active_version"] == "fifo-v"
    assert await db.hpp_valuation_cutovers.count_documents({}) == 0


@pytest.mark.anyio
async def test_i_customer_pricing_unchanged(db):
    await _stock(db, "sku-i", 10)
    await _settings(db)
    fixture = {
        "id": "cp-1",
        "manual_hpp": 335,
        "hpp_snapshot": 335,
        "tier_prices": [400],
        "pricing_mode": "HPP_FORMULA",
        "profit_mode": "HPP_PLUS_FIXED",
        "hpp_source": "MANUAL_HPP",
    }
    await db.customer_pricing_versions.insert_one(dict(fixture))
    await db.customer_pricing_groups.insert_one({"id": "g-1", "group_name": "Tetap"})
    await db.customer_pricing_products.insert_one({"id": "p-1", "manual_hpp": 560})
    before_versions = await db.customer_pricing_versions.find({}, {"_id": 0}).to_list(10)
    before_groups = await db.customer_pricing_groups.count_documents({})
    before_products = await db.customer_pricing_products.count_documents({})
    await confirm_cutover(db, _payload("req-i", "sku-i", 900), USER)
    after_versions = await db.customer_pricing_versions.find({}, {"_id": 0}).to_list(10)
    assert after_versions == before_versions
    assert await db.customer_pricing_groups.count_documents({}) == before_groups
    assert await db.customer_pricing_products.count_documents({}) == before_products


@pytest.mark.anyio
async def test_j_hpp_confirm_does_not_mutate_stock_qty(db):
    mid = "sku-j"
    await _stock(db, mid, 80)
    await db.stock_movements.insert_one({
        "id": "mov-existing",
        "event_key": "mov-existing",
        "master_item_id": mid,
        "transaction_qty": 80,
        "movement_type": "SALDO_AWAL",
    })
    await _settings(db)
    before_state = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    before_moves = await db.stock_movements.find({}, {"_id": 0}).to_list(10)
    await confirm_cutover(db, _payload("req-j", mid, 700), USER)
    await recalculate_hpp(db, method="RATA-RATA", recognition_point="goods_receipt", user=USER)
    after_state = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    after_moves = await db.stock_movements.find({}, {"_id": 0}).to_list(10)
    assert after_state["total_qty"] == before_state["total_qty"] == 80
    assert after_state["unprinted_qty"] == before_state["unprinted_qty"]
    assert after_state["printed_qty"] == before_state["printed_qty"]
    assert after_moves == before_moves


@pytest.mark.anyio
async def test_zero_and_negative_stock_do_not_fake_qty(db):
    await _settings(db)
    zero = await confirm_cutover(db, _payload("req-zero", "sku-zero", 1000, accurate_item_id="acc-z"), USER)
    row = await _row(db, zero["cutover"]["cutover_id"], "sku-zero")
    assert row["stock_fisik"] == 0
    assert row["valuation_qty"] == 0
    assert row["valuation_value"] == 0
    assert row["hpp_status"] == "OK"
    assert await db.hpp_cost_layers.count_documents({"source_type": "OPENING_HPP", "master_item_id": "sku-zero"}) == 0
    assert await db.product_stock_state.count_documents({"master_item_id": "sku-zero"}) == 0

    await db.hpp_valuation_cutovers.delete_many({})
    await db.hpp_cost_versions.update_many({"status": "ACTIVE"}, {"$set": {"status": "SUPERSEDED"}})
    await db.app_settings.update_one({"key": "hpp_valuation"}, {"$set": {"active_version": None, "method": "RATA-RATA"}})
    await _stock(db, "sku-neg", -5, "acc-n")
    with pytest.raises(HTTPException) as caught:
        await confirm_cutover(db, _payload(
            "req-neg",
            "sku-neg",
            1000,
            accurate_item_id="acc-n",
            expected_active_version=None,
        ), USER)
    assert caught.value.detail["code"] == "QTY_MISMATCH"
    stock = await db.product_stock_state.find_one({"master_item_id": "sku-neg"})
    assert stock["total_qty"] == -5
    assert await db.hpp_cost_layers.count_documents({"master_item_id": "sku-neg"}) == 0
    assert await db.hpp_valuation_cutovers.count_documents({"status": "ACTIVE", "client_request_id": "req-neg"}) == 0


async def _po(
    db,
    *,
    po_id,
    mid,
    aid,
    qty,
    unit,
    unit_price,
    line_id=None,
):
    line_id = line_id or f"line-{po_id}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-{po_id}",
        "supplier": "Supplier",
        "cancelled": False,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "sales_delivery",
        },
        "lines": [{
            "line_id": line_id,
            "line_idx": 0,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": aid,
            "item_name": aid,
            "unit": unit,
            "ordered_qty": qty,
            "unit_price": unit_price,
        }],
    })
    return line_id


@pytest.mark.anyio
async def test_1_base_uom_gr_uses_base_qty_and_base_unit_cost(db):
    mid = "sku-base-uom"
    aid = "acc-base-uom"
    await _stock(db, mid, 0, aid)
    await db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": aid,
        "unit": "PCS",
        "item_name": "Base UOM",
    })
    await db.product_unit_settings.insert_one({
        "master_item_id": mid,
        "base_uom": "PCS",
        "transaction_uom": "DUS",
        "conversion_factor": 1000,
    })
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-1", mid, 500, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-base-uom",
        mid=mid,
        aid=aid,
        qty=1,
        unit="DUS",
        unit_price=500000,
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-base-uom",
        "idempotency_key": "gr-base-uom",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 1}],
    }, USER)

    event = await db.hpp_recognition_events.find_one(
        {"source_type": "GOODS_RECEIPT"},
        {"_id": 0},
    )
    assert event["transaction_qty"] == 1
    assert event["transaction_uom"] == "DUS"
    assert event["conversion_factor"] == 1000
    assert event["base_qty"] == 1000
    assert event["base_uom"] == "PCS"
    assert event["qty"] == 1000
    assert event["unit_cost"] == 500
    assert event["total_cost"] == 500000
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["stock_fisik"] == 1000
    assert row["valuation_qty"] == 1000
    assert row["difference"] == 0
    assert row["current_hpp"] == 500
    assert row["valuation_value"] == 500000
    assert row["hpp_status"] == "OK"


@pytest.mark.anyio
async def test_2_normal_gr_moving_average(db):
    mid = "sku-normal-gr"
    aid = "acc-normal-gr"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-2", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-normal-gr",
        mid=mid,
        aid=aid,
        qty=50,
        unit="PCS",
        unit_price=1200,
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-normal-gr",
        "idempotency_key": "gr-normal",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }, USER)
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["valuation_qty"] == 150
    assert row["valuation_value"] == 160000
    assert row["exact_average"].startswith("1066.666")
    assert row["hpp_status"] == "OK"


@pytest.mark.anyio
async def test_3_and_4_gr_cancel_reverses_hpp_once(db):
    mid = "sku-cancel-gr"
    aid = "acc-cancel-gr"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-3", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-cancel-gr",
        mid=mid,
        aid=aid,
        qty=50,
        unit="PCS",
        unit_price=1200,
    )
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-cancel-gr",
        "idempotency_key": "gr-cancel",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }, USER)
    before = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert before["valuation_qty"] == 150
    assert before["valuation_value"] == 160000

    first = await cancel_purchase_goods_receipt(db, receipt["id"], USER, "local test")
    assert first["cancelled"] is True
    after = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert after["stock_fisik"] == 100
    assert after["valuation_qty"] == 100
    assert after["valuation_value"] == 100000
    assert after["current_hpp"] == 1000
    assert after["hpp_status"] == "OK"
    original = await db.hpp_recognition_events.find_one(
        {"source_key": {"$regex": f"^gr:{receipt['id']}:"}},
        {"_id": 0},
    )
    reversal = await db.hpp_recognition_events.find_one(
        {"source_key": {"$regex": f"^gr-reversal:{receipt['id']}:"}},
        {"_id": 0},
    )
    assert original["status"] == "REVERSED"
    assert reversal["status"] == "REVERSED"
    assert reversal["original_event_id"] == original["event_id"]
    assert reversal["qty"] == 50
    assert reversal["unit_cost"] == 1200
    assert reversal["created_by"]["id"] == USER["id"]

    reversal_count = await db.hpp_recognition_events.count_documents({
        "source_key": {"$regex": f"^gr-reversal:{receipt['id']}:"},
    })
    movement_count = await db.stock_movements.count_documents({})
    second = await cancel_purchase_goods_receipt(db, receipt["id"], USER, "retry")
    assert second["cancelled"] is True
    assert await db.hpp_recognition_events.count_documents({
        "source_key": {"$regex": f"^gr-reversal:{receipt['id']}:"},
    }) == reversal_count
    assert await db.stock_movements.count_documents({}) == movement_count
    retry_row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert retry_row["valuation_qty"] == 100
    assert retry_row["valuation_value"] == 100000


@pytest.mark.anyio
async def test_5_hpp_failure_after_stock_success_retries_only_hpp(db, monkeypatch):
    from services import hpp_cutover

    mid = "sku-hpp-retry"
    aid = "acc-hpp-retry"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-5", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-hpp-retry",
        mid=mid,
        aid=aid,
        qty=50,
        unit="PCS",
        unit_price=1200,
    )
    body = {
        "purchase_order_id": "po-hpp-retry",
        "idempotency_key": "gr-hpp-retry",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }
    real_rebuild = hpp_cutover.rebuild_cutover_valuation

    async def fail_rebuild(*_args, **_kwargs):
        raise RuntimeError("forced HPP rebuild failure")

    monkeypatch.setattr(hpp_cutover, "rebuild_cutover_valuation", fail_rebuild)
    with pytest.raises(RuntimeError, match="forced HPP"):
        await create_purchase_goods_receipt(db, body, USER)
    assert await db.purchase_goods_receipts.count_documents({"idempotency_key": "gr-hpp-retry"}) == 1
    assert (await db.product_stock_state.find_one({"master_item_id": mid}))["total_qty"] == 100
    assert await db.stock_movements.count_documents({"reference_type": "goods_receipt"}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RECEIPT}) == 1
    failed = await db.hpp_recognition_events.find_one({"source_type": "GOODS_RECEIPT"}, {"_id": 0})
    assert failed["status"] == "FAILED"
    failed_row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert failed_row["hpp_status"] == "FAILED"
    assert failed_row["difference"] == 50

    monkeypatch.setattr(hpp_cutover, "rebuild_cutover_valuation", real_rebuild)
    receipt = await create_purchase_goods_receipt(db, body, USER)
    assert receipt["id"]
    assert await db.purchase_goods_receipts.count_documents({"idempotency_key": "gr-hpp-retry"}) == 1
    assert (await db.product_stock_state.find_one({"master_item_id": mid}))["total_qty"] == 100
    assert await db.stock_movements.count_documents({"reference_type": "goods_receipt"}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RECEIPT}) == 1
    repaired = await db.hpp_recognition_events.find_one({"source_type": "GOODS_RECEIPT"}, {"_id": 0})
    assert repaired["status"] == "RECOGNIZED"
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["hpp_status"] == "OK"
    assert row["valuation_qty"] == 150
    assert row["valuation_value"] == 160000


@pytest.mark.anyio
async def test_6_missing_cost_is_needs_cost_not_zero_hpp(db):
    mid = "sku-missing-cost"
    aid = "acc-missing-cost"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-6", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-missing-cost",
        mid=mid,
        aid=aid,
        qty=50,
        unit="PCS",
        unit_price=0,
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-missing-cost",
        "idempotency_key": "gr-missing-cost",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }, USER)
    event = await db.hpp_recognition_events.find_one(
        {"source_type": "GOODS_RECEIPT"},
        {"_id": 0},
    )
    assert event["status"] == "NEEDS_COST"
    assert event["unit_cost"] is None
    assert event["total_cost"] is None
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["stock_fisik"] == 150
    assert row["valuation_qty"] == 100
    assert row["difference"] == 50
    assert row["hpp_status"] == "NEEDS_COST"
    assert row["reconciliation_reason"] == "NEEDS_COST"


@pytest.mark.anyio
async def test_7_missing_uom_is_needs_uom_not_wrong_valuation(db):
    mid = "sku-missing-uom"
    aid = "acc-missing-uom"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    opened = await confirm_cutover(
        db,
        _payload("hardening-7", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-missing-uom",
        mid=mid,
        aid=aid,
        qty=1,
        unit="DUS",
        unit_price=500000,
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-missing-uom",
        "idempotency_key": "gr-missing-uom",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 1}],
    }, USER)
    event = await db.hpp_recognition_events.find_one(
        {"source_type": "GOODS_RECEIPT"},
        {"_id": 0},
    )
    assert event["status"] == "NEEDS_UOM"
    assert event["qty"] is None
    assert event["unit_cost"] is None
    assert event["total_cost"] is None
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["valuation_qty"] == 100
    assert row["valuation_value"] == 100000
    assert row["hpp_status"] == "NEEDS_UOM"
    assert row["reconciliation_reason"] == "NEEDS_UOM"


@pytest.mark.anyio
async def test_8_customer_pricing_remains_byte_for_byte_unchanged(db):
    mid = "sku-pricing-guard"
    aid = "acc-pricing-guard"
    await _stock(db, mid, 10, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    fixture = {
        "id": "pricing-guard",
        "products": [{
            "accurate_item_id": aid,
            "master_item_id": mid,
            "manual_hpp": 335,
            "hpp_snapshot": 335,
            "tier_prices": [400, 390],
        }],
        "pricing_mode": "HPP_FORMULA",
        "profit_mode": "HPP_PLUS_FIXED",
        "hpp_source": "MANUAL_HPP",
    }
    await db.customer_pricing_versions.insert_one(fixture)
    before = await db.customer_pricing_versions.find_one({"id": "pricing-guard"})
    await confirm_cutover(
        db,
        _payload("hardening-8", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-pricing-guard",
        mid=mid,
        aid=aid,
        qty=1,
        unit="PCS",
        unit_price=1000,
    )
    await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-pricing-guard",
        "idempotency_key": "gr-pricing-guard",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 1}],
    }, USER)
    after = await db.customer_pricing_versions.find_one({"id": "pricing-guard"})
    assert after == before


@pytest.mark.anyio
async def test_9_purchase_invoice_creates_no_stock_or_hpp_event(db):
    mid = "sku-invoice-guard"
    aid = "acc-invoice-guard"
    await _stock(db, mid, 100, aid)
    await db.master_items.insert_one({"id": mid, "accurate_item_id": aid, "unit": "PCS"})
    await _settings(db)
    await confirm_cutover(
        db,
        _payload("hardening-9", mid, 1000, accurate_item_id=aid),
        USER,
    )
    po_line = await _po(
        db,
        po_id="po-invoice-guard",
        mid=mid,
        aid=aid,
        qty=50,
        unit="PCS",
        unit_price=1200,
    )
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": "po-invoice-guard",
        "idempotency_key": "gr-invoice-guard",
        "lines": [{"purchase_order_line_id": po_line, "received_qty": 50}],
    }, USER)
    stock_before = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    movement_count = await db.stock_movements.count_documents({})
    hpp_count = await db.hpp_recognition_events.count_documents({})
    invoice = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": receipt["id"],
        "purchase_order_id": "po-invoice-guard",
        "idempotency_key": "invoice-guard",
        "lines": [{"purchase_order_line_id": po_line, "invoice_qty": 50}],
    }, USER)
    assert invoice["id"]
    stock_after = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    assert stock_after == stock_before
    assert await db.stock_movements.count_documents({}) == movement_count
    assert await db.hpp_recognition_events.count_documents({}) == hpp_count
