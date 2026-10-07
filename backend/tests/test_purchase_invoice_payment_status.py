"""Invoice settlement status. Advance is optional. Isolated DB."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import BALANCE_COLLECTION, LEDGER_COLLECTION
from services.purchase_invoice_finance import invoice_financial_summary, normalize_legacy_payment_status
from services.purchase_invoices import create_purchase_invoice, get_purchase_invoice
from services.purchase_orders import create_purchase_order
from services.purchase_payments import confirm_purchase_payment, create_purchase_payment
from services.purchase_supplier_advance_allocations import create_allocation, ensure_indexes, reverse_allocation
from services.purchase_supplier_advances import (
    confirm_purchase_supplier_advance,
    create_purchase_supplier_advance,
    ensure_indexes as ensure_advance_indexes,
    submit_purchase_supplier_advance,
    supplier_advance_availability,
)

TEST_DB = "eternal_packaging_test_invoice_payment_status"
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
    await database.purchase_orders.create_index("id", unique=True)
    await database.purchase_invoices.create_index("id", unique=True)
    await database.purchase_payments.create_index("id", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _po(db, qty=100, price=1):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id, "item_code": item_id[:8], "item_name": "Item", "base_unit": "PCS", "accurate_item_id": item_id,
    })
    return await create_purchase_order(db, {
        "supplier": "PT Tanpa UM",
        "lines": [{
            "master_item_id": item_id, "item_name": "Item", "item_code": "IT", "unit": "PCS",
            "ordered_qty": qty, "unit_price": price,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


async def _invoice(db, po, qty=None):
    return await create_purchase_invoice(db, {
        "purchase_order_id": po["id"],
        "invoice_source": "purchase_order",
        "precedes_goods_receipt": True,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "invoice_qty": qty or po["lines"][0]["ordered_qty"]}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


async def _pay(db, invoice_id, amount):
    payment = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice_id, "amount": amount, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await db.purchase_payments.update_one({"id": payment["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    return await confirm_purchase_payment(db, payment["id"], USER)


async def _paid_advance(db, po, amount):
    draft = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"], "amount": amount, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await submit_purchase_supplier_advance(db, draft["id"], USER)
    return await confirm_purchase_supplier_advance(db, draft["id"], USER, {"confirmation_key": f"c:{draft['id']}"})


async def test_payment_only_flow_reaches_lunas_without_advance(db):
    po = await _po(db)
    invoice = await _invoice(db, po)
    assert invoice["payment_status"] == "BELUM_DIBAYAR"
    assert invoice["advance_applied_amount"] == 0
    assert invoice["outstanding_amount"] == 100
    assert await db.purchase_supplier_advances.count_documents({}) == 0
    partial = (await _pay(db, invoice["id"], 40))["invoice"]
    assert partial["payment_status"] == "DIBAYAR_SEBAGIAN"
    assert partial["paid_total"] == 40
    assert partial["outstanding_amount"] == 60
    second = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice["id"], "amount": 30, "idempotency_key": "pay-30",
    }, USER)
    replay = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice["id"], "amount": 30, "idempotency_key": "pay-30",
    }, USER)
    assert replay["id"] == second["id"]
    await db.purchase_payments.update_one({"id": second["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    await confirm_purchase_payment(db, second["id"], USER)
    again = await confirm_purchase_payment(db, second["id"], USER)
    assert again["reused"] is True
    mid = await get_purchase_invoice(db, invoice["id"])
    assert mid["outstanding_amount"] == 30
    await _pay(db, invoice["id"], 30)
    done = await get_purchase_invoice(db, invoice["id"])
    assert done["paid_total"] == 100
    assert done["outstanding_amount"] == 0
    assert done["payment_status"] == "LUNAS"
    with pytest.raises(HTTPException):
        await create_purchase_payment(db, {"purchase_invoice_id": invoice["id"], "amount": 1}, USER)
    draft = await _invoice(db, await _po(db))
    pending = await create_purchase_payment(db, {"purchase_invoice_id": draft["id"], "amount": 10}, USER)
    untouched = await get_purchase_invoice(db, draft["id"])
    assert untouched["payment_status"] == "BELUM_DIBAYAR"
    assert untouched["outstanding_amount"] == 100
    assert pending["settlement_status"] == "UNPAID"
    assert normalize_legacy_payment_status("DP") == "DIBAYAR_SEBAGIAN"
    assert invoice_financial_summary(invoice_amount=100, payments_applied=0)["payment_status"] == "BELUM_DIBAYAR"


async def test_advance_is_optional_and_only_allocation_reduces_invoice(db):
    po = await _po(db)
    invoice = await _invoice(db, po)
    advance = await _paid_advance(db, po, 30)
    still = await get_purchase_invoice(db, invoice["id"])
    assert still["outstanding_amount"] == 100
    assert still["payment_status"] == "BELUM_DIBAYAR"
    partial = await create_allocation(db, {
        "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice["id"], "amount": 10,
        "idempotency_key": "alloc-10",
    }, USER)
    after = await get_purchase_invoice(db, invoice["id"])
    assert after["advance_applied_amount"] == 10
    assert after["outstanding_amount"] == 90
    assert after["payment_status"] == "DIBAYAR_SEBAGIAN"
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 20
    paid = (await _pay(db, invoice["id"], 40))["invoice"]
    assert paid["paid_total"] == 40
    assert paid["outstanding_amount"] == 50
    with pytest.raises(HTTPException):
        await _pay(db, invoice["id"], 60)
    with pytest.raises(HTTPException):
        await create_allocation(db, {
            "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice["id"], "amount": 21,
        }, USER)

    full_po = await _po(db)
    full_invoice = await _invoice(db, full_po)
    full_advance = await _paid_advance(db, full_po, 100)
    await create_allocation(db, {
        "supplier_advance_id": full_advance["id"], "purchase_invoice_id": full_invoice["id"], "amount": 100,
    }, USER)
    settled = await get_purchase_invoice(db, full_invoice["id"])
    assert settled["paid_total"] == 0
    assert settled["outstanding_amount"] == 0
    assert settled["payment_status"] == "LUNAS"

    await reverse_allocation(db, partial["id"], USER)
    restored = await get_purchase_invoice(db, invoice["id"])
    assert restored["advance_applied_amount"] == 0
    assert restored["paid_total"] == 40
    assert restored["outstanding_amount"] == 60
    assert restored["payment_status"] == "DIBAYAR_SEBAGIAN"
    assert (await supplier_advance_availability(db, po["id"]))["available_amount"] == 30


async def test_concurrency_cannot_over_settle_and_stock_stays_untouched(db):
    po = await _po(db, qty=30, price=1)
    invoice = await _invoice(db, po, 30)
    first = await create_purchase_payment(db, {"purchase_invoice_id": invoice["id"], "amount": 20}, USER)
    second = await create_purchase_payment(db, {"purchase_invoice_id": invoice["id"], "amount": 20}, USER)
    await db.purchase_payments.update_many({}, {"$set": {"submission_status": "SUBMITTED"}})
    results = await asyncio.gather(
        confirm_purchase_payment(db, first["id"], USER),
        confirm_purchase_payment(db, second["id"], USER),
        return_exceptions=True,
    )
    assert sum(1 for row in results if not isinstance(row, Exception)) == 1
    assert (await get_purchase_invoice(db, invoice["id"]))["outstanding_amount"] == 10

    po2 = await _po(db, qty=30, price=1)
    invoice2 = await _invoice(db, po2, 30)
    advance = await _paid_advance(db, po2, 20)
    payment = await create_purchase_payment(db, {"purchase_invoice_id": invoice2["id"], "amount": 20}, USER)
    await db.purchase_payments.update_one({"id": payment["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    mixed = await asyncio.gather(
        create_allocation(db, {
            "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice2["id"], "amount": 20,
            "idempotency_key": "mix-alloc",
        }, USER),
        confirm_purchase_payment(db, payment["id"], USER),
        return_exceptions=True,
    )
    assert sum(1 for row in mixed if not isinstance(row, Exception)) == 1
    final = await get_purchase_invoice(db, invoice2["id"])
    assert final["settled_amount"] == 20
    assert final["outstanding_amount"] == 10
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert await db.stock_movements.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0
    assert await db.item_stock_manual.count_documents({}) == 0
