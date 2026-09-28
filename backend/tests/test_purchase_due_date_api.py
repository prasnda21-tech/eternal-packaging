"""Service-level persistence for invoice due_date and payment requested_payment_date."""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = f"eternal_purchase_due_svc_{uuid.uuid4().hex[:8]}"

from motor.motor_asyncio import AsyncIOMotorClient

from services.purchase_invoices import attach_payment_fields, create_purchase_invoice
from services.purchase_payments import create_purchase_payment


USER = {"id": "u1", "user_id": "u1", "name": "Tester", "email": "t@test.local", "role": "admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    yield database
    await client.drop_database(os.environ["DB_NAME"])
    client.close()


async def _seed_po_receipt(db, *, qty=10, price=35.0):
    mid = str(uuid.uuid4())
    po_id = str(uuid.uuid4())
    line_id = str(uuid.uuid4())
    gr_id = str(uuid.uuid4())
    gr_line_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": mid,
        "item_code": "DUE-100",
        "item_name": "Due Test Item",
        "base_uom": "PCS",
    })
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-DUE-{uuid.uuid4().hex[:6]}",
        "supplier": "JAWARA",
        "order_date": "2026-09-28",
        "lines": [{
            "line_idx": 0,
            "line_id": line_id,
            "master_item_id": mid,
            "accurate_item_id": "",
            "item_code": "DUE-100",
            "item_name": "Due Test Item",
            "unit": "PCS",
            "ordered_qty": qty,
            "quantity": qty,
            "unit_price": price,
            "line_subtotal": qty * price,
        }],
        "subtotal": qty * price,
        "grand_total": qty * price,
        "cancelled": False,
    })
    await db.purchase_goods_receipts.insert_one({
        "id": gr_id,
        "receipt_number": f"GR-DUE-{uuid.uuid4().hex[:6]}",
        "purchase_order_id": po_id,
        "po_number": f"PB-DUE",
        "supplier": "JAWARA",
        "receipt_date": "2026-09-28",
        "lines": [{
            "line_idx": 0,
            "line_id": gr_line_id,
            "purchase_order_line_id": line_id,
            "master_item_id": mid,
            "item_code": "DUE-100",
            "item_name": "Due Test Item",
            "unit": "PCS",
            "received_qty": qty,
        }],
        "cancelled": False,
    })
    return po_id, gr_id, line_id


@pytest.mark.anyio
async def test_due_date_persists_and_missing_due_is_valid(db):
    po_id, gr_id, _ = await _seed_po_receipt(db)
    inv = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": gr_id,
        "purchase_order_id": po_id,
        "invoice_date": "2026-09-28",
        "due_date": "2026-10-05",
        "idempotency_key": f"inv-{uuid.uuid4()}",
    }, USER)
    assert inv["due_date"] == "2026-10-05"
    assert inv["due_status"] == "BELUM JATUH TEMPO" or inv["due_status"] in {
        "BELUM JATUH TEMPO", "JATUH TEMPO HARI INI", "TERLAMBAT", "LUNAS"
    }

    po2, gr2, _ = await _seed_po_receipt(db)
    inv2 = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": gr2,
        "purchase_order_id": po2,
        "invoice_date": "2026-09-28",
        "idempotency_key": f"inv2-{uuid.uuid4()}",
    }, USER)
    assert inv2.get("due_date") is None
    assert inv2["due_status"] == "BELUM ADA JATUH TEMPO"

    # Old document without due_date key still enriches cleanly.
    legacy = await attach_payment_fields(db, {
        "id": str(uuid.uuid4()),
        "grand_total": 100,
        "cancelled": False,
    })
    assert legacy.get("due_date") is None
    assert legacy["due_status"] == "BELUM ADA JATUH TEMPO"


@pytest.mark.anyio
async def test_requested_payment_date_persists_on_payment_draft(db):
    po_id, gr_id, _ = await _seed_po_receipt(db)
    inv = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": gr_id,
        "purchase_order_id": po_id,
        "invoice_date": "2026-09-28",
        "due_date": "2026-10-01",
        "idempotency_key": f"inv-pay-{uuid.uuid4()}",
    }, USER)
    payment = await create_purchase_payment(db, {
        "purchase_invoice_id": inv["id"],
        "amount": inv["grand_total"],
        "requested_payment_date": "2026-10-02",
        "idempotency_key": f"pay-{uuid.uuid4()}",
    }, USER)
    assert payment["requested_payment_date"] == "2026-10-02"
    assert payment.get("paid_at") in (None, "", False) or payment.get("settlement_status") == "UNPAID"
