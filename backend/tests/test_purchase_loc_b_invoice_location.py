"""PUR-LOC-01 LOC-B: purchase invoice location snapshot from PO/GR."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_pur_loc_b")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")

from services.purchase_invoices import (  # noqa: E402
    COLLECTION,
    attach_payment_fields,
    create_purchase_invoice,
    get_po_invoice_preview,
    get_purchase_invoice,
    get_receipt_invoice_preview,
)

TEST_DB = os.environ["DB_NAME"]
pytestmark = pytest.mark.anyio

USER = {"id": "u-loc-b", "email": "buyer@example.com", "name": "Buyer", "role": "admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    for name in (
        COLLECTION,
        "purchase_orders",
        "purchase_goods_receipts",
        "master_items",
        "branches",
        "warehouses",
        "physical_stock_balances",
        "physical_stock_ledger",
        "stock_movements",
    ):
        await database[name].create_index("id", unique=True)
    await database[COLLECTION].create_index("invoice_number", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _seed_masters(db):
    await db.branches.insert_one({
        "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
    })
    await db.warehouses.insert_one({
        "id": "wh-ygy-a",
        "branch_id": "branch-ygy",
        "code": "YGY-A",
        "name": "Gudang YGY A",
        "active": True,
    })
    item_id = f"item-{uuid.uuid4().hex[:8]}"
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": "BR-LOCB",
        "item_name": "Bahan LOC-B",
        "name": "Bahan LOC-B",
        "is_active": True,
        "base_unit": "PCS",
    })
    return item_id


async def _seed_po(db, item_id, *, with_branch=True, received=0, qty=10):
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"pol-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": po_id,
        "po_number": f"PB-LOCB-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Supplier LOC-B",
        "cancelled": False,
        "lines": [{
            "line_id": line_id,
            "line_idx": 0,
            "master_item_id": item_id,
            "accurate_item_id": "",
            "item_code": "BR-LOCB",
            "item_name": "Bahan LOC-B",
            "unit": "PCS",
            "ordered_qty": qty,
            "unit_price": 1000,
            "line_subtotal": qty * 1000,
            "cumulative_received_qty": received,
            "cumulative_invoiced_qty": 0,
            "remaining_invoice_qty": qty,
            "receipt_invoice_eligible_qty": received,
            "net_received_qty": received,
            "confirmed_return_qty": 0,
        }],
    }
    if with_branch:
        doc.update({
            "branch_id": "branch-ygy",
            "branch_code": "YGY",
            "branch_name": "Yogyakarta",
        })
    await db.purchase_orders.insert_one(doc)
    return po_id, line_id


async def _seed_gr(db, po_id, line_id, item_id, *, qty=4, branch_id="branch-ygy", warehouse_id="wh-ygy-a"):
    gr_id = f"gr-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": gr_id,
        "receipt_number": f"GR-LOCB-{uuid.uuid4().hex[:4].upper()}",
        "purchase_order_id": po_id,
        "cancelled": False,
        "supplier": "Supplier LOC-B",
        "receipt_date": "2026-10-20",
        "lines": [{
            "line_id": f"gl-{uuid.uuid4().hex[:6]}",
            "purchase_order_line_id": line_id,
            "master_item_id": item_id,
            "received_qty": qty,
            "unit": "PCS",
            "unit_price": 1000,
        }],
    }
    if branch_id is not None:
        doc["branch_id"] = branch_id
    if warehouse_id is not None:
        doc["warehouse_id"] = warehouse_id
    await db.purchase_goods_receipts.insert_one(doc)
    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {
            "lines.0.cumulative_received_qty": qty,
            "lines.0.net_received_qty": qty,
            "lines.0.receipt_invoice_eligible_qty": qty,
            "lines.0.remaining_invoice_qty": 10,
        }},
    )
    return gr_id


async def test_invoice_from_po_ygy_warehouse_null(db):
    item_id = await _seed_masters(db)
    po_id, line_id = await _seed_po(db, item_id, with_branch=True)
    preview = await get_po_invoice_preview(db, po_id)
    assert preview["branch_id"] == "branch-ygy"
    assert preview["branch_code"] == "YGY"
    assert preview.get("warehouse_id") in (None, "")

    before_bal = await db.physical_stock_balances.count_documents({})
    before_mov = await db.physical_stock_ledger.count_documents({})
    inv = await create_purchase_invoice(db, {
        "invoice_source": "purchase_order",
        "purchase_order_id": po_id,
        "precedes_goods_receipt": True,
        "invoice_date": "2026-10-21",
        "payment_term_days": 30,
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 2}],
        "idempotency_key": f"inv-{uuid.uuid4()}",
        # spoof — must be ignored
        "branch_id": "branch-smg",
        "warehouse_id": "wh-fake",
    }, USER)
    assert inv["branch_id"] == "branch-ygy"
    assert inv["branch_code"] == "YGY"
    assert inv["warehouse_id"] is None
    assert inv["payment_term_days"] == 30
    assert await db.physical_stock_balances.count_documents({}) == before_bal
    assert await db.physical_stock_ledger.count_documents({}) == before_mov
    # preview/create consistency
    assert inv["branch_id"] == preview["branch_id"]
    assert inv.get("warehouse_id") == preview.get("warehouse_id")


async def test_invoice_from_gr_ygy_warehouse(db):
    item_id = await _seed_masters(db)
    po_id, line_id = await _seed_po(db, item_id, with_branch=True, received=4)
    gr_id = await _seed_gr(db, po_id, line_id, item_id, qty=4)
    preview = await get_receipt_invoice_preview(db, gr_id)
    assert preview["branch_id"] == "branch-ygy"
    assert preview["warehouse_id"] == "wh-ygy-a"
    assert preview["warehouse_code"] == "YGY-A"

    inv = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": gr_id,
        "purchase_order_id": po_id,
        "invoice_date": "2026-10-21",
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 2}],
        "idempotency_key": f"inv-{uuid.uuid4()}",
        "branch_id": "spoof-branch",
        "warehouse_id": "spoof-wh",
    }, USER)
    assert inv["branch_id"] == "branch-ygy"
    assert inv["warehouse_id"] == "wh-ygy-a"
    assert inv["warehouse_code"] == "YGY-A"
    assert inv["branch_id"] == preview["branch_id"]
    assert inv["warehouse_id"] == preview["warehouse_id"]


async def test_legacy_po_early_invoice_null_location(db):
    item_id = await _seed_masters(db)
    po_id, line_id = await _seed_po(db, item_id, with_branch=False)
    preview = await get_po_invoice_preview(db, po_id)
    assert preview.get("branch_id") is None
    assert preview.get("warehouse_id") is None

    inv = await create_purchase_invoice(db, {
        "invoice_source": "purchase_order",
        "purchase_order_id": po_id,
        "precedes_goods_receipt": True,
        "invoice_date": "2026-10-21",
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 1}],
        "idempotency_key": f"inv-{uuid.uuid4()}",
    }, USER)
    assert inv.get("branch_id") is None
    assert inv.get("warehouse_id") is None


async def test_historical_gr_location_preserved(db):
    item_id = await _seed_masters(db)
    po_id, line_id = await _seed_po(db, item_id, with_branch=True, received=3)
    # Historical GR: branch only, no warehouse — do not invent warehouse
    gr_id = await _seed_gr(
        db, po_id, line_id, item_id, qty=3, branch_id="branch-ygy", warehouse_id=None,
    )
    inv = await create_purchase_invoice(db, {
        "invoice_source": "receipt",
        "receipt_id": gr_id,
        "invoice_date": "2026-10-21",
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 1}],
        "idempotency_key": f"inv-{uuid.uuid4()}",
    }, USER)
    assert inv["branch_id"] == "branch-ygy"
    assert inv.get("warehouse_id") is None


async def test_legacy_invoice_get_without_snapshot(db):
    item_id = await _seed_masters(db)
    po_id, _line_id = await _seed_po(db, item_id, with_branch=True)
    inv_id = f"inv-legacy-{uuid.uuid4().hex[:8]}"
    await db[COLLECTION].insert_one({
        "id": inv_id,
        "invoice_number": f"FPB-LEGACY-{uuid.uuid4().hex[:4].upper()}",
        "purchase_order_id": po_id,
        "supplier": "Legacy",
        "cancelled": False,
        "grand_total": 1000,
        "subtotal": 1000,
        "lines": [],
        "invoice_date": "2026-09-01",
    })
    got = await get_purchase_invoice(db, inv_id)
    assert got["id"] == inv_id
    assert got.get("branch_id") is None
    assert got.get("warehouse_id") is None
    # attach_payment_fields also safe
    attached = await attach_payment_fields(db, got)
    assert attached.get("branch_id") is None


async def test_client_spoof_cannot_override(db):
    item_id = await _seed_masters(db)
    po_id, line_id = await _seed_po(db, item_id, with_branch=True)
    inv = await create_purchase_invoice(db, {
        "invoice_source": "purchase_order",
        "purchase_order_id": po_id,
        "precedes_goods_receipt": True,
        "invoice_date": "2026-10-21",
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 1}],
        "branch_id": "branch-hacker",
        "branch_code": "HCK",
        "branch_name": "Hacker",
        "warehouse_id": "wh-hacker",
        "warehouse_code": "HCK-1",
        "warehouse_name": "Hack WH",
        "idempotency_key": f"inv-{uuid.uuid4()}",
    }, USER)
    assert inv["branch_id"] == "branch-ygy"
    assert inv["branch_code"] == "YGY"
    assert inv["warehouse_id"] is None
    stored = await db[COLLECTION].find_one({"id": inv["id"]}, {"_id": 0})
    assert stored["branch_id"] == "branch-ygy"
    assert stored.get("warehouse_id") in (None, "")
