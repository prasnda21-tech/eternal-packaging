"""Supplier advance foundation. Isolated DB. No invoice allocation and no stock."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import BALANCE_COLLECTION, LEDGER_COLLECTION, ensure_indexes as ensure_stock_indexes
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_orders import cancel_purchase_order, create_purchase_order, get_purchase_order
from services.purchase_supplier_advances import (
    COLLECTION,
    cancel_purchase_supplier_advance,
    confirm_purchase_supplier_advance,
    create_purchase_supplier_advance,
    ensure_indexes,
    submit_purchase_supplier_advance,
    supplier_advance_availability,
)

TEST_DB = "eternal_packaging_test_supplier_advance"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-1", "email": "admin@example.com", "name": "Admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await ensure_stock_indexes(database)
    await database.purchase_orders.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _po(db, *, supplier="PT Maju", qty=10, price=1000, cancelled=False):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({"id": item_id, "item_code": item_id[:8], "item_name": "Item", "base_unit": "PCS"})
    po = await create_purchase_order(db, {
        "supplier": supplier,
        "lines": [{
            "master_item_id": item_id,
            "item_name": "Item",
            "item_code": "IT",
            "unit": "PCS",
            "ordered_qty": qty,
            "unit_price": price,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    if cancelled:
        po = await cancel_purchase_order(db, po["id"], USER, "batal")
    return po, item_id


async def _pay(db, po_id, amount, key=None):
    draft = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po_id,
        "amount": amount,
        "idempotency_key": key or str(uuid.uuid4()),
        "payment_date": "2026-10-05",
    }, USER)
    submitted = await submit_purchase_supplier_advance(db, draft["id"], USER)
    paid = await confirm_purchase_supplier_advance(db, submitted["id"], USER, {
        "confirmation_key": f"confirm:{submitted['id']}",
        "payment_date": "2026-10-05",
    })
    return paid


async def test_create_before_invoice_preserves_supplier_and_rejects_bad_po(db):
    po, _item = await _po(db)
    advance = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"],
        "amount": 30000000,
        "supplier": "pt maju",
        "idempotency_key": "adv-1",
    }, USER)
    assert advance["supplier_key"] == "name:pt maju"
    assert advance["supplier_name"] == "PT Maju"
    assert advance["status_label"] == "Draft"
    assert advance["available_amount"] == 0
    assert await db.purchase_invoices.count_documents({}) == 0
    summary = await supplier_advance_availability(db, po["id"])
    assert summary["total_paid_amount"] == 0
    assert summary["available_amount"] == 0
    with pytest.raises(HTTPException) as bad:
        await create_purchase_supplier_advance(db, {"purchase_order_id": po["id"], "amount": 0}, USER)
    assert bad.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        await create_purchase_supplier_advance(db, {"purchase_order_id": "missing", "amount": 10}, USER)
    assert missing.value.status_code == 404
    cancelled, _ = await _po(db, cancelled=True)
    with pytest.raises(HTTPException) as closed:
        await create_purchase_supplier_advance(db, {"purchase_order_id": cancelled["id"], "amount": 10}, USER)
    assert closed.value.status_code == 400
    with pytest.raises(HTTPException) as other:
        await create_purchase_supplier_advance(db, {
            "purchase_order_id": po["id"], "amount": 10, "supplier": "PT Lain",
        }, USER)
    assert other.value.status_code == 400


async def test_multiple_paid_advances_are_available_and_do_not_touch_stock_or_receipt(db):
    await db.branches.insert_one({"id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True})
    await db.warehouses.insert_one({
        "id": "wh-main", "branch_id": "branch-ygy", "code": "MAIN", "name": "Gudang Yogyakarta", "active": True,
    })
    po, item_id = await _po(db, qty=1000, price=100000)
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 400}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    first = await _pay(db, po["id"], 10000000, "a1")
    second = await _pay(db, po["id"], 20000000, "a2")
    assert first["id"] != second["id"]
    assert first["allocated_amount"] == 0
    summary = await supplier_advance_availability(db, po["id"])
    assert summary["total_paid_amount"] == 30000000
    assert summary["allocated_amount"] == 0
    assert summary["available_amount"] == 30000000
    assert summary["advance_count"] == 2
    again = await get_purchase_order(db, po["id"])
    assert again["lines"][0]["ordered_qty"] == 1000
    stored_receipt = await db.purchase_goods_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert stored_receipt["lines"][0]["received_qty"] == 400
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item_id, "movement_type": {"$ne": "PURCHASE_RECEIPT"}}) == 0
    assert await db.stock_movements.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0
    assert await db.item_stock_manual.count_documents({}) == 0
    assert await db.journal_entries.count_documents({}) == 0
    assert await db.purchase_invoices.count_documents({}) == 0


async def test_confirmation_is_idempotent_and_conflicts(db):
    po, _item = await _po(db)
    draft = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"], "amount": 1500, "idempotency_key": "same", "notes": "a",
    }, USER)
    replay = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"], "amount": 1500, "idempotency_key": "same", "notes": "a",
    }, USER)
    assert replay["id"] == draft["id"]
    with pytest.raises(HTTPException) as changed:
        await create_purchase_supplier_advance(db, {
            "purchase_order_id": po["id"], "amount": 1600, "idempotency_key": "same", "notes": "a",
        }, USER)
    assert changed.value.status_code == 409
    submitted = await submit_purchase_supplier_advance(db, draft["id"], USER)
    assert submitted["status_label"] == "Diajukan"
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 0
    paid = await confirm_purchase_supplier_advance(db, draft["id"], USER, {"confirmation_key": "pay-1", "payment_date": "2026-10-05"})
    retry = await confirm_purchase_supplier_advance(db, draft["id"], USER, {"confirmation_key": "pay-1", "payment_date": "2026-10-05"})
    assert retry["id"] == paid["id"]
    assert await db[COLLECTION].count_documents({"settlement_status": "PAID"}) == 1
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 1500
    with pytest.raises(HTTPException) as conflict:
        await confirm_purchase_supplier_advance(db, draft["id"], USER, {"confirmation_key": "pay-1", "payment_date": "2026-10-06"})
    assert conflict.value.status_code == 409

    other = await create_purchase_supplier_advance(db, {"purchase_order_id": po["id"], "amount": 10}, USER)
    await submit_purchase_supplier_advance(db, other["id"], USER)
    await asyncio.gather(
        confirm_purchase_supplier_advance(db, other["id"], USER, {"confirmation_key": "race", "payment_date": "2026-10-05"}),
        confirm_purchase_supplier_advance(db, other["id"], USER, {"confirmation_key": "race", "payment_date": "2026-10-05"}),
    )
    assert await db[COLLECTION].count_documents({"id": other["id"], "settlement_status": "PAID"}) == 1
    summary = await supplier_advance_availability(db, po["id"])
    assert summary["total_paid_amount"] == 1510


async def test_unpaid_can_cancel_and_paid_history_stays(db):
    po, _item = await _po(db)
    draft = await create_purchase_supplier_advance(db, {"purchase_order_id": po["id"], "amount": 500}, USER)
    cancelled = await cancel_purchase_supplier_advance(db, draft["id"], USER, "salah")
    assert cancelled["cancelled"] is True
    assert cancelled["status_label"] == "Dibatalkan"
    assert await db[COLLECTION].count_documents({"id": draft["id"]}) == 1
    paid = await _pay(db, po["id"], 800)
    with pytest.raises(HTTPException) as blocked:
        await cancel_purchase_supplier_advance(db, paid["id"], USER, "undo")
    assert blocked.value.status_code == 409
    stored = await db[COLLECTION].find_one({"id": paid["id"]}, {"_id": 0})
    assert stored["settlement_status"] == "PAID"
    assert stored.get("cancelled") is not True
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 800
