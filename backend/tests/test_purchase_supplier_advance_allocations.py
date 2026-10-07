"""Supplier advance allocation to purchase invoices. Isolated DB."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import BALANCE_COLLECTION, LEDGER_COLLECTION, ensure_indexes as ensure_stock_indexes
from services.purchase_invoices import create_purchase_invoice, get_purchase_invoice
from services.purchase_orders import create_purchase_order
from services.purchase_payments import confirm_purchase_payment, create_purchase_payment
from services.purchase_supplier_advance_allocations import (
    COLLECTION,
    create_allocation,
    ensure_indexes,
    reverse_allocation,
)
from services.purchase_supplier_advances import (
    cancel_purchase_supplier_advance,
    confirm_purchase_supplier_advance,
    create_purchase_supplier_advance,
    ensure_indexes as ensure_advance_indexes,
    submit_purchase_supplier_advance,
    supplier_advance_availability,
)

TEST_DB = "eternal_packaging_test_advance_allocation"
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
    await ensure_advance_indexes(database)
    await ensure_stock_indexes(database)
    await database.purchase_orders.create_index("id", unique=True)
    await database.purchase_invoices.create_index("id", unique=True)
    await database.purchase_payments.create_index("id", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _po(db, *, supplier="PT Maju", qty=100, price=1000000):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id, "item_code": item_id[:8], "item_name": "Item", "base_unit": "PCS", "accurate_item_id": item_id,
    })
    po = await create_purchase_order(db, {
        "supplier": supplier,
        "lines": [{
            "master_item_id": item_id, "item_name": "Item", "item_code": "IT", "unit": "PCS",
            "ordered_qty": qty, "unit_price": price,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    return po


async def _advance(db, po, amount, *, pay=True, cancel=False):
    draft = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"], "amount": amount, "idempotency_key": str(uuid.uuid4()), "payment_date": "2026-10-05",
    }, USER)
    if cancel:
        return await cancel_purchase_supplier_advance(db, draft["id"], USER, "batal")
    if not pay:
        return draft
    await submit_purchase_supplier_advance(db, draft["id"], USER)
    return await confirm_purchase_supplier_advance(db, draft["id"], USER, {
        "confirmation_key": f"confirm:{draft['id']}", "payment_date": "2026-10-05",
    })


async def _invoice(db, po, qty):
    return await create_purchase_invoice(db, {
        "purchase_order_id": po["id"],
        "invoice_source": "purchase_order",
        "precedes_goods_receipt": True,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "invoice_qty": qty}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


def _alloc(advance_id, invoice_id, amount, key=None, notes=""):
    payload = {
        "supplier_advance_id": advance_id,
        "purchase_invoice_id": invoice_id,
        "amount": amount,
        "notes": notes,
    }
    if key:
        payload["idempotency_key"] = key
    return payload


async def test_allocation_rules_partial_multiple_and_remainder(db):
    po = await _po(db)
    invoice = await _invoice(db, po, 100)
    assert invoice["outstanding_amount"] == 100000000
    assert invoice["advance_applied_amount"] == 0
    draft = await _advance(db, po, 5000, pay=False)
    with pytest.raises(HTTPException) as unpaid:
        await create_allocation(db, _alloc(draft["id"], invoice["id"], 1), USER)
    assert unpaid.value.status_code == 400
    submitted = await submit_purchase_supplier_advance(db, draft["id"], USER)
    with pytest.raises(HTTPException):
        await create_allocation(db, _alloc(submitted["id"], invoice["id"], 1), USER)
    cancelled = await _advance(db, po, 5000, cancel=True)
    with pytest.raises(HTTPException):
        await create_allocation(db, _alloc(cancelled["id"], invoice["id"], 1), USER)

    other = await _po(db, supplier="PT Lain", qty=1, price=10)
    other_invoice = await _invoice(db, other, 1)
    first = await _advance(db, po, 10000000)
    second = await _advance(db, po, 20000000)
    with pytest.raises(HTTPException) as wrong_supplier:
        await create_allocation(db, _alloc(first["id"], other_invoice["id"], 1), USER)
    assert wrong_supplier.value.status_code == 400
    same_supplier = await _po(db, supplier="PT Maju", qty=1, price=10)
    same_invoice = await _invoice(db, same_supplier, 1)
    with pytest.raises(HTTPException) as wrong_po:
        await create_allocation(db, _alloc(first["id"], same_invoice["id"], 1), USER)
    assert wrong_po.value.status_code == 400
    with pytest.raises(HTTPException):
        await create_allocation(db, _alloc(first["id"], invoice["id"], 0), USER)

    summary_before = await supplier_advance_availability(db, po["id"])
    assert summary_before["available_amount"] == 30000000
    assert summary_before["allocated_amount"] == 0
    part = await create_allocation(db, _alloc(first["id"], invoice["id"], 10000000, "part-1"), USER)
    again = await create_allocation(db, _alloc(second["id"], invoice["id"], 10000000, "part-2"), USER)
    rest = await create_allocation(db, _alloc(second["id"], invoice["id"], 10000000, "part-3"), USER)
    assert len({part["id"], again["id"], rest["id"]}) == 3
    summary = await supplier_advance_availability(db, po["id"])
    assert summary["total_paid_amount"] == 30000000
    assert summary["allocated_amount"] == 30000000
    assert summary["available_amount"] == 0
    refreshed = await get_purchase_invoice(db, invoice["id"])
    assert refreshed["grand_total"] == 100000000
    assert refreshed["advance_applied_amount"] == 30000000
    assert refreshed["paid_total"] == 0
    assert refreshed["outstanding_amount"] == 70000000
    assert refreshed["payment_status"] == "DIBAYAR_SEBAGIAN"
    assert refreshed["outstanding_amount"] >= 0


async def test_over_allocation_idempotency_concurrency_payment_and_isolation(db):
    po = await _po(db, qty=100, price=1)
    invoice = await _invoice(db, po, 100)
    advance = await _advance(db, po, 120)
    with pytest.raises(HTTPException) as too_much:
        await create_allocation(db, _alloc(advance["id"], invoice["id"], 120), USER)
    assert too_much.value.status_code == 409
    full = await create_allocation(db, _alloc(advance["id"], invoice["id"], 100, "full"), USER)
    settled = await get_purchase_invoice(db, invoice["id"])
    assert settled["outstanding_amount"] == 0
    assert settled["paid_total"] == 0
    assert settled["payment_status"] == "LUNAS"
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 20
    replay = await create_allocation(db, _alloc(advance["id"], invoice["id"], 100, "full"), USER)
    assert replay["id"] == full["id"]
    assert await db[COLLECTION].count_documents({"idempotency_key": "full"}) == 1
    with pytest.raises(HTTPException) as changed:
        await create_allocation(db, _alloc(advance["id"], invoice["id"], 10, "full"), USER)
    assert changed.value.status_code == 409

    po2 = await _po(db, qty=30, price=1)
    invoice2 = await _invoice(db, po2, 30)
    pool = await _advance(db, po2, 30)
    results = await asyncio.gather(
        create_allocation(db, _alloc(pool["id"], invoice2["id"], 20, "race-a"), USER),
        create_allocation(db, _alloc(pool["id"], invoice2["id"], 20, "race-b"), USER),
        return_exceptions=True,
    )
    assert sum(1 for row in results if not isinstance(row, Exception)) == 1
    assert (await supplier_advance_availability(db, po2["id"]))["allocated_amount"] == 20

    po3 = await _po(db, qty=20, price=1)
    invoice3 = await _invoice(db, po3, 20)
    left = await _advance(db, po3, 15)
    right = await _advance(db, po3, 15)
    both = await asyncio.gather(
        create_allocation(db, _alloc(left["id"], invoice3["id"], 15, "inv-a"), USER),
        create_allocation(db, _alloc(right["id"], invoice3["id"], 15, "inv-b"), USER),
        return_exceptions=True,
    )
    assert sum(1 for row in both if not isinstance(row, Exception)) == 1
    assert (await get_purchase_invoice(db, invoice3["id"]))["outstanding_amount"] == 5

    po4 = await _po(db, qty=100, price=1)
    invoice4 = await _invoice(db, po4, 100)
    mixed = await _advance(db, po4, 30)
    await create_allocation(db, _alloc(mixed["id"], invoice4["id"], 30, "mix"), USER)
    payment = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice4["id"], "amount": 40, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await db.purchase_payments.update_one({"id": payment["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    await confirm_purchase_payment(db, payment["id"], USER)
    mid = await get_purchase_invoice(db, invoice4["id"])
    assert mid["advance_applied_amount"] == 30
    assert mid["paid_total"] == 40
    assert mid["outstanding_amount"] == 30
    payment2 = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice4["id"], "amount": 30, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await db.purchase_payments.update_one({"id": payment2["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    await confirm_purchase_payment(db, payment2["id"], USER)
    done = await get_purchase_invoice(db, invoice4["id"])
    assert done["outstanding_amount"] == 0
    assert done["paid_total"] == 70
    assert await db.purchase_payments.count_documents({"purchase_invoice_id": invoice["id"]}) == 0
    assert await db.journal_entries.count_documents({}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert await db.stock_movements.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0
    assert await db.item_stock_manual.count_documents({}) == 0


async def test_one_advance_keeps_remainder_across_invoices_and_reversal_restores(db):
    po = await _po(db, qty=100, price=1)
    advance = await _advance(db, po, 100)
    first = await _invoice(db, po, 60)
    second = await _invoice(db, po, 30)
    created = await create_allocation(db, _alloc(advance["id"], first["id"], 60, "a"), USER)
    await create_allocation(db, _alloc(advance["id"], second["id"], 30, "b"), USER)
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 10
    assert (await get_purchase_invoice(db, first["id"]))["outstanding_amount"] == 0
    assert (await get_purchase_invoice(db, second["id"]))["outstanding_amount"] == 0
    reversed_doc = await reverse_allocation(db, created["id"], USER)
    assert reversed_doc["status"] == "REVERSED"
    assert reversed_doc["amount"] == 60
    assert await db[COLLECTION].count_documents({"id": created["id"]}) == 1
    retry = await reverse_allocation(db, created["id"], USER)
    assert retry["status"] == "REVERSED"
    assert await db[COLLECTION].count_documents({"status": "REVERSED", "supplier_advance_id": advance["id"]}) == 1
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 70
    restored = await get_purchase_invoice(db, first["id"])
    assert restored["advance_applied_amount"] == 0
    assert restored["outstanding_amount"] == 60
