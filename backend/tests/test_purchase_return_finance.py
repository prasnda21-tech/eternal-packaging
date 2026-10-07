"""Purchase return credit and supplier-credit foundation. Isolated DB only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import LEDGER_COLLECTION, MOVEMENT_PURCHASE_RETURN, MOVEMENT_REVERSAL, ensure_indexes
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_invoices import create_purchase_invoice, get_purchase_invoice
from services.purchase_orders import create_purchase_order
from services.purchase_payments import confirm_purchase_payment, create_purchase_payment
from services.purchase_return_finance import (
    CREDIT_COLLECTION,
    SUPPLIER_CREDIT_COLLECTION,
    allocate_ambiguous_return,
    return_credit_amount,
)
from services.purchase_returns import cancel_purchase_return, confirm_purchase_return, create_purchase_return
from services.purchase_supplier_advance_allocations import create_allocation
from services.purchase_supplier_advances import (
    confirm_purchase_supplier_advance,
    create_purchase_supplier_advance,
    ensure_indexes as ensure_advance_indexes,
    submit_purchase_supplier_advance,
)

TEST_DB = "eternal_packaging_test_purchase_return_finance"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-1", "email": "admin@example.com", "name": "Admin"}
PRICE = 1_000_000


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
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_returns.create_index("id", unique=True)
    await database.purchase_returns.create_index("return_number", unique=True)
    await database.purchase_invoices.create_index("id", unique=True)
    await database.purchase_payments.create_index("id", unique=True)
    await database[CREDIT_COLLECTION].create_index("id", unique=True)
    await database[CREDIT_COLLECTION].create_index(
        "idempotency_key", unique=True, partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database[SUPPLIER_CREDIT_COLLECTION].create_index("id", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _ready(db, qty=100, price=PRICE):
    branch_id = "branch-ygy"
    if not await db.branches.find_one({"id": branch_id}):
        await db.branches.insert_one({"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True})
        await db.warehouses.insert_one({
            "id": "wh-main", "branch_id": branch_id, "code": "MAIN", "name": "Gudang", "active": True,
        })
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id, "item_code": "IT", "item_name": "Item", "base_unit": "PCS", "accurate_item_id": item_id,
    })
    po = await create_purchase_order(db, {
        "supplier": "Supplier Kredit",
        "lines": [{
            "master_item_id": item_id, "item_name": "Item", "item_code": "IT", "unit": "PCS",
            "ordered_qty": qty, "unit_price": price,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    receipt = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": qty}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    return po, receipt


async def _invoice(db, po, qty, receipt=None, price_ok=True):
    payload = {
        "purchase_order_id": po["id"],
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "invoice_qty": qty}],
        "idempotency_key": str(uuid.uuid4()),
    }
    if receipt is not None:
        payload.update({"invoice_source": "receipt", "receipt_id": receipt["id"]})
    else:
        payload["precedes_goods_receipt"] = True
    return await create_purchase_invoice(db, payload, USER)


async def _pay(db, invoice_id, amount, confirm=True):
    payment = await create_purchase_payment(db, {
        "purchase_invoice_id": invoice_id, "amount": amount, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await db.purchase_payments.update_one({"id": payment["id"]}, {"$set": {"submission_status": "SUBMITTED"}})
    if not confirm:
        return payment
    return await confirm_purchase_payment(db, payment["id"], USER)


async def _advance(db, po, amount):
    draft = await create_purchase_supplier_advance(db, {
        "purchase_order_id": po["id"], "amount": amount, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await submit_purchase_supplier_advance(db, draft["id"], USER)
    return await confirm_purchase_supplier_advance(db, draft["id"], USER, {"confirmation_key": f"c:{draft['id']}"})


async def _return(db, receipt, qty):
    draft = await create_purchase_return(db, {
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": qty,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    return await confirm_purchase_return(db, draft["id"], {"idempotency_key": str(uuid.uuid4())}, USER)


def test_rounding_last_qty_reconciles_the_line_value():
    assert return_credit_amount(line_value=30.02, invoice_qty=3, qty=1) == 10.01
    second = return_credit_amount(line_value=30.02, invoice_qty=3, qty=1, already_qty=1, already_amount=10.01)
    assert second == 10.01
    last = return_credit_amount(line_value=30.02, invoice_qty=3, qty=1, already_qty=2, already_amount=20.02)
    assert last == 10.0
    assert round(10.01 + 10.01 + last, 2) == 30.02


async def test_pre_invoice_return_has_no_credit(db):
    po, receipt = await _ready(db)
    confirmed = await _return(db, receipt, 20)
    assert confirmed["financial_status"] == "NO_FINANCIAL_EFFECT"
    assert await db[CREDIT_COLLECTION].count_documents({}) == 0
    assert await db[SUPPLIER_CREDIT_COLLECTION].count_documents({}) == 0
    enriched_po = await create_purchase_invoice(db, {
        "purchase_order_id": po["id"],
        "invoice_source": "receipt",
        "receipt_id": receipt["id"],
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "invoice_qty": 80}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    assert enriched_po["lines"][0]["invoice_qty"] == 80
    with pytest.raises(HTTPException):
        await _invoice(db, po, 100, receipt=receipt)
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 1


async def test_unambiguous_credit_payment_advance_and_excess(db):
    po, receipt = await _ready(db)
    invoice = await _invoice(db, po, 100, receipt=receipt)
    assert invoice["grand_total"] == 100_000_000
    assert invoice["payment_status"] == "BELUM_DIBAYAR"
    credited = await _return(db, receipt, 20)
    assert credited["financial_status"] == "FINANCIALIZED"
    assert credited["lines"][0]["invoice_trace"]["invoice_ownership_ambiguous"] is False
    unpaid = await get_purchase_invoice(db, invoice["id"])
    assert unpaid["purchase_return_credit"] == 20_000_000
    assert unpaid["advance_applied"] == 0
    assert unpaid["payments_applied"] == 0
    assert unpaid["outstanding_amount"] == 80_000_000
    assert unpaid["supplier_credit_generated"] == 0
    assert unpaid["payment_status"] == "BELUM_DIBAYAR"
    assert await db[SUPPLIER_CREDIT_COLLECTION].count_documents({}) == 0
    await cancel_purchase_return(db, credited["id"], USER, reason="undo")
    restored = await get_purchase_invoice(db, invoice["id"])
    assert restored["purchase_return_credit"] == 0
    assert restored["outstanding_amount"] == 100_000_000
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 1
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 1

    paid = await _pay(db, invoice["id"], 40_000_000)
    assert paid["invoice"]["outstanding_amount"] == 60_000_000
    again = await _return(db, receipt, 20)
    mid = await get_purchase_invoice(db, invoice["id"])
    assert mid["purchase_return_credit"] == 20_000_000
    assert mid["payments_applied"] == 40_000_000
    assert mid["outstanding_amount"] == 40_000_000
    assert mid["payment_status"] == "DIBAYAR_SEBAGIAN"
    with pytest.raises(HTTPException):
        await _pay(db, invoice["id"], 50_000_000)
    await _pay(db, invoice["id"], 40_000_000)
    lunas = await get_purchase_invoice(db, invoice["id"])
    assert lunas["outstanding_amount"] == 0
    assert lunas["payment_status"] == "LUNAS"
    assert lunas["supplier_credit_generated"] == 0

    po2, receipt2 = await _ready(db)
    invoice2 = await _invoice(db, po2, 100, receipt=receipt2)
    advance = await _advance(db, po2, 100_000_000)
    await create_allocation(db, {
        "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice2["id"],
        "amount": 30_000_000, "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await _pay(db, invoice2["id"], 20_000_000)
    await _return(db, receipt2, 10)
    mixed = await get_purchase_invoice(db, invoice2["id"])
    assert (mixed["invoice_amount"], mixed["purchase_return_credit"], mixed["advance_applied"], mixed["payments_applied"], mixed["outstanding_amount"]) == (
        100_000_000, 10_000_000, 30_000_000, 20_000_000, 40_000_000,
    )
    assert await db.purchase_supplier_advances.find_one({"id": advance["id"]})
    before_advance = (await db.purchase_supplier_advances.find_one({"id": advance["id"]}))["original_amount"]
    with pytest.raises(HTTPException):
        await create_allocation(db, {
            "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice2["id"],
            "amount": 50_000_000, "idempotency_key": str(uuid.uuid4()),
        }, USER)
    assert (await db.purchase_supplier_advances.find_one({"id": advance["id"]}))["original_amount"] == before_advance

    po3, receipt3 = await _ready(db)
    invoice3 = await _invoice(db, po3, 100, receipt=receipt3)
    await _pay(db, invoice3["id"], 100_000_000)
    excess = await _return(db, receipt3, 20)
    settled = await get_purchase_invoice(db, invoice3["id"])
    assert settled["outstanding_amount"] == 0
    assert settled["payment_status"] == "LUNAS"
    assert settled["supplier_credit_generated"] == 20_000_000
    credit = await db[SUPPLIER_CREDIT_COLLECTION].find_one({"source_purchase_return_id": excess["id"]})
    assert credit["original_amount"] == 20_000_000
    assert credit["available_amount"] == 20_000_000
    assert credit["supplier_key"] == "name:supplier kredit"
    assert credit["status"] == "ACTIVE"
    await cancel_purchase_return(db, excess["id"], USER, reason="undo excess")
    after = await get_purchase_invoice(db, invoice3["id"])
    assert after["purchase_return_credit"] == 0
    assert after["outstanding_amount"] == 0
    assert after["payments_applied"] == 100_000_000
    assert (await db[SUPPLIER_CREDIT_COLLECTION].find_one({"id": credit["id"]}))["status"] == "REVERSED"
    replay = await cancel_purchase_return(db, excess["id"], USER, reason="undo excess")
    assert replay["status"] == "REVERSED"
    original = await db[LEDGER_COLLECTION].find_one({
        "metadata.purchase_return_id": excess["id"], "movement_type": MOVEMENT_PURCHASE_RETURN,
    })
    assert original["qty_delta"] == -20
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL, "reference_id": original["id"],
    }) == 1

    po4, receipt4 = await _ready(db)
    invoice4 = await _invoice(db, po4, 100, receipt=receipt4)
    await _pay(db, invoice4["id"], 90_000_000)
    partial = await _return(db, receipt4, 20)
    row = await get_purchase_invoice(db, invoice4["id"])
    assert row["outstanding_amount"] == 0
    assert row["purchase_return_credit_applied_to_outstanding"] == 10_000_000
    assert row["supplier_credit_generated"] == 10_000_000
    source = await db[SUPPLIER_CREDIT_COLLECTION].find_one({"source_purchase_return_id": partial["id"]})
    assert source["source_purchase_return_credit_id"]
    assert await db.purchase_payments.count_documents({"purchase_invoice_id": invoice4["id"], "amount": 20_000_000}) == 0


async def test_ambiguous_allocation_is_explicit_and_idempotent(db):
    po, receipt = await _ready(db)
    first = await _invoice(db, po, 60, receipt=receipt)
    second = await _invoice(db, po, 40, receipt=receipt)
    confirmed = await _return(db, receipt, 20)
    assert confirmed["financial_status"] == "PENDING_FINANCIAL_ALLOCATION"
    assert confirmed["lines"][0]["invoice_trace"]["invoice_ownership_ambiguous"] is True
    assert await db[CREDIT_COLLECTION].count_documents({}) == 0
    assert await db[SUPPLIER_CREDIT_COLLECTION].count_documents({}) == 0
    key = "alloc-once"
    body = {
        "idempotency_key": key,
        "allocations": [
            {"purchase_return_line_id": confirmed["lines"][0]["line_id"], "purchase_invoice_line_id": first["lines"][0]["line_id"], "qty": 10},
            {"purchase_return_line_id": confirmed["lines"][0]["line_id"], "purchase_invoice_line_id": second["lines"][0]["line_id"], "qty": 10},
        ],
    }
    done = await allocate_ambiguous_return(db, confirmed["id"], body, USER)
    assert done["financial_status"] == "FINANCIALIZED"
    assert len(done["credits"]) == 2
    assert {row["credit_amount"] for row in done["credits"]} == {10_000_000}
    replay = await allocate_ambiguous_return(db, confirmed["id"], body, USER)
    assert len(replay["credits"]) == 2
    with pytest.raises(HTTPException) as changed:
        await allocate_ambiguous_return(db, confirmed["id"], {
            "idempotency_key": key,
            "allocations": [{
                "purchase_return_line_id": confirmed["lines"][0]["line_id"],
                "purchase_invoice_line_id": first["lines"][0]["line_id"],
                "qty": 20,
            }],
        }, USER)
    assert changed.value.status_code == 409
    foreign = await db.purchase_invoices.insert_one({
        "id": "inv-foreign", "purchase_order_id": "other-po", "supplier": "Supplier Lain",
        "cancelled": False, "grand_total": 1000,
        "lines": [{"line_id": "foreign-line", "purchase_order_line_id": "other", "invoice_qty": 10, "unit_price": 100, "line_subtotal": 1000}],
    })
    del foreign
    with pytest.raises(HTTPException):
        await allocate_ambiguous_return(db, confirmed["id"], {
            "allocations": [{
                "purchase_return_line_id": confirmed["lines"][0]["line_id"],
                "purchase_invoice_line_id": "foreign-line",
                "qty": 1,
            }],
        }, USER)
    await db.purchase_returns.update_one(
        {"id": confirmed["id"]},
        {"$push": {"lines.0.invoice_trace.related_invoice_lines": {
            "purchase_invoice_id": "inv-foreign",
            "purchase_invoice_line_id": "foreign-line",
            "invoice_qty": 10,
        }}},
    )
    with pytest.raises(HTTPException) as wrong_supplier:
        await allocate_ambiguous_return(db, confirmed["id"], {
            "allocations": [{
                "purchase_return_line_id": confirmed["lines"][0]["line_id"],
                "purchase_invoice_line_id": "foreign-line",
                "qty": 1,
            }],
        }, USER)
    assert wrong_supplier.value.status_code == 400


async def test_financial_qty_cannot_exceed_physical_or_invoiced_qty(db):
    po, receipt = await _ready(db)
    invoice = await _invoice(db, po, 60, receipt=receipt)
    await _invoice(db, po, 40, receipt=receipt)
    confirmed = await _return(db, receipt, 20)
    with pytest.raises(HTTPException):
        await allocate_ambiguous_return(db, confirmed["id"], {
            "allocations": [{
                "purchase_return_line_id": confirmed["lines"][0]["line_id"],
                "purchase_invoice_line_id": invoice["lines"][0]["line_id"],
                "qty": 30,
            }],
        }, USER)
    await allocate_ambiguous_return(db, confirmed["id"], {
        "allocations": [{
            "purchase_return_line_id": confirmed["lines"][0]["line_id"],
            "purchase_invoice_line_id": invoice["lines"][0]["line_id"],
            "qty": 20,
        }],
    }, USER)
    with pytest.raises(HTTPException):
        await allocate_ambiguous_return(db, confirmed["id"], {
            "idempotency_key": "second",
            "allocations": [{
                "purchase_return_line_id": confirmed["lines"][0]["line_id"],
                "purchase_invoice_line_id": invoice["lines"][0]["line_id"],
                "qty": 20,
            }],
        }, USER)
    assert await db[CREDIT_COLLECTION].count_documents({"status": "ACTIVE"}) == 1


async def test_concurrent_credit_and_settlement_do_not_oversettle(db):
    po, receipt = await _ready(db, qty=100, price=1)
    invoice = await _invoice(db, po, 100, receipt=receipt)
    await _pay(db, invoice["id"], 80)
    pending = await _pay(db, invoice["id"], 15, confirm=False)
    draft = await create_purchase_return(db, {
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 15,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await asyncio.gather(
        confirm_purchase_payment(db, pending["id"], USER),
        confirm_purchase_return(db, draft["id"], {"idempotency_key": "race-credit"}, USER),
        return_exceptions=True,
    )
    final = await get_purchase_invoice(db, invoice["id"])
    assert final["outstanding_amount"] >= 0
    covered = final["payments_applied"] + final["purchase_return_credit_applied_to_outstanding"]
    assert covered <= final["invoice_amount"]
    credits = await db[CREDIT_COLLECTION].find({"purchase_invoice_id": invoice["id"], "status": "ACTIVE"}).to_list(10)
    if credits:
        assert round(sum(row["applied_to_invoice_amount"] + row["supplier_credit_amount"] for row in credits), 2) == round(sum(row["credit_amount"] for row in credits), 2)

    po2, receipt2 = await _ready(db, qty=100, price=1)
    invoice2 = await _invoice(db, po2, 100, receipt=receipt2)
    await _pay(db, invoice2["id"], 80)
    advance = await _advance(db, po2, 100)
    draft2 = await create_purchase_return(db, {
        "lines": [{
            "purchase_goods_receipt_id": receipt2["id"],
            "purchase_goods_receipt_line_id": receipt2["lines"][0]["line_id"],
            "return_qty": 15,
        }],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    await asyncio.gather(
        create_allocation(db, {
            "supplier_advance_id": advance["id"], "purchase_invoice_id": invoice2["id"],
            "amount": 15, "idempotency_key": "race-alloc",
        }, USER),
        confirm_purchase_return(db, draft2["id"], {"idempotency_key": "race-credit-2"}, USER),
        return_exceptions=True,
    )
    final2 = await get_purchase_invoice(db, invoice2["id"])
    assert final2["outstanding_amount"] >= 0
    assert final2["advance_applied"] + final2["payments_applied"] + final2["purchase_return_credit_applied_to_outstanding"] <= final2["invoice_amount"]
    assert await db.stock_movements.count_documents({}) == 0
