"""Authority transition safety — document stock_authority must freeze engine choice.

Proves Mode A↔B, document-only→enabled, PARTIAL resume, cancel, Accurate history,
and FIRST_RECEIPT_AUTO races do not dual-write or retroactively stock-in.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

STANDALONE_URL = os.environ.get("STANDALONE_MONGO_URL", "mongodb://127.0.0.1:27018")
TEST_DB = "eternal_packaging_test_authority_transition"

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
)
from services.physical_stock_settings import update_physical_stock_settings  # noqa: E402
from services.product_stock_state import (  # noqa: E402
    assess_first_receipt_eligibility,
    ensure_first_receipt_pss_bootstrap,
    get_product_stock_state,
    initialize_opening_balance,
)
from services.purchase_goods_receipts import create_purchase_goods_receipt  # noqa: E402
from services.purchase_orders import create_purchase_order  # noqa: E402
from services.purchase_receipts import (  # noqa: E402
    STOCK_AUTHORITY_LEGACY_PSS,
    STOCK_AUTHORITY_NONE,
    STOCK_AUTHORITY_STANDALONE,
    cancel_manual_purchase_receipt,
    create_manual_purchase_receipt,
    resolve_document_stock_authority,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(STANDALONE_URL)
    hello = await client.admin.command("hello")
    assert not hello.get("setName")
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=False)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db, *, code="T"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": f"SKU-{code}",
        "item_name": f"Barang {code}",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "name": "Main",
        "active": True,
        "is_default": True,
        "code": "MAIN",
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(item)
    await db[BRANCHES_COLLECTION].insert_one(branch)
    await db[WAREHOUSES_COLLECTION].insert_one(wh)
    return item, branch, wh


USER = {"id": "admin-1", "email": "admin@test.local", "name": "Admin", "role": "admin"}


async def _qty(db, mid, wh_id):
    bal = await db[BALANCE_COLLECTION].find_one({"master_item_id": mid, "warehouse_id": wh_id})
    return int((bal or {}).get("physical_qty") or 0)


async def test_mode_a_to_b_langsung_retry_no_warehouse_dual_write(db):
    """Mode A Langsung posted PSS; after Mode B ON, same idempotency key must not ledger."""
    item, _, wh = await _seed(db, code="A2B")
    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    key = f"idem-a2b-{uuid.uuid4().hex[:8]}"
    doc = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "idempotency_key": key,
        "lines": [{
            "master_item_id": item["id"],
            "item_code": "A2B",
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 5,
        }],
    }, USER)
    assert resolve_document_stock_authority(doc) == STOCK_AUTHORITY_LEGACY_PSS
    pss = await get_product_stock_state(db, item["id"])
    assert float(pss["total_qty"]) == 5.0
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == 0

    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    again = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "idempotency_key": key,
        "warehouse_id": wh["id"],
        "lines": [{
            "master_item_id": item["id"],
            "item_code": "A2B",
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 5,
        }],
    }, USER)
    assert again["accurate_receipt_id"] == doc["accurate_receipt_id"]
    assert resolve_document_stock_authority(again) == STOCK_AUTHORITY_LEGACY_PSS
    assert await _qty(db, item["id"], wh["id"]) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == 0
    pss2 = await get_product_stock_state(db, item["id"])
    assert float(pss2["total_qty"]) == 5.0


async def test_document_only_then_flag_on_no_retroactive_stock(db):
    """physical_stock.enabled=False create → enable → retry must stay document-only."""
    item, _, wh = await _seed(db, code="DOC")
    await update_physical_stock_settings(
        db,
        {"enabled": False, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    key = f"idem-doc-{uuid.uuid4().hex[:8]}"
    doc = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "idempotency_key": key,
        "lines": [{
            "master_item_id": item["id"],
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 3,
        }],
    }, USER)
    assert resolve_document_stock_authority(doc) == STOCK_AUTHORITY_NONE
    assert await get_product_stock_state(db, item["id"]) is None

    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    again = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "idempotency_key": key,
        "warehouse_id": wh["id"],
        "lines": [{
            "master_item_id": item["id"],
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 3,
        }],
    }, USER)
    assert resolve_document_stock_authority(again) == STOCK_AUTHORITY_NONE
    assert await get_product_stock_state(db, item["id"]) is None
    assert await _qty(db, item["id"], wh["id"]) == 0


async def test_mode_b_to_a_cancel_uses_document_engine(db):
    """Mode B Langsung then Mode A ON: cancel still reverses warehouse, not PSS."""
    item, _, wh = await _seed(db, code="B2A")
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    key = f"idem-b2a-{uuid.uuid4().hex[:8]}"
    doc = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "warehouse_id": wh["id"],
        "idempotency_key": key,
        "lines": [{
            "master_item_id": item["id"],
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 4,
        }],
    }, USER)
    assert resolve_document_stock_authority(doc) == STOCK_AUTHORITY_STANDALONE
    assert await _qty(db, item["id"], wh["id"]) == 4

    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    # Retry must not rewrite to PSS.
    again = await create_manual_purchase_receipt(db, {
        "supplier": "S",
        "receipt_date": "2026-10-18",
        "warehouse_id": wh["id"],
        "idempotency_key": key,
        "lines": [{
            "master_item_id": item["id"],
            "item_name": "Barang",
            "unit": "PCS",
            "received_qty": 4,
        }],
    }, USER)
    assert resolve_document_stock_authority(again) == STOCK_AUTHORITY_STANDALONE
    assert await get_product_stock_state(db, item["id"]) is None
    assert await _qty(db, item["id"], wh["id"]) == 4

    cancelled = await cancel_manual_purchase_receipt(
        db, doc["accurate_receipt_id"], USER, reason="transition cancel",
    )
    assert cancelled.get("cancelled") is True
    assert await _qty(db, item["id"], wh["id"]) == 0
    assert await get_product_stock_state(db, item["id"]) is None


async def test_mode_a_po_to_b_retry_no_warehouse(db):
    """Mode A PO GR then Mode B ON: idempotent retry must not post warehouse."""
    item, _, wh = await _seed(db, code="APO")
    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    po = await create_purchase_order(db, {
        "supplier": "S",
        "idempotency_key": f"po-{uuid.uuid4().hex[:8]}",
        "lines": [{
            "master_item_id": item["id"],
            "item_code": "APO",
            "item_name": "Barang",
            "unit": "PCS",
            "ordered_qty": 8,
            "unit_price": 100,
        }],
    }, USER)
    key = f"gr-a2b-{uuid.uuid4().hex[:8]}"
    gr = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": key,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 8}],
    }, USER)
    assert gr.get("stock_authority") == "legacy_pss"
    assert float((await get_product_stock_state(db, item["id"]))["total_qty"]) == 8.0
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == 0

    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    # Same payload (no warehouse on Mode A GR) — must not adopt live Mode B engine.
    again = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": key,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 8}],
    }, USER)
    assert again["id"] == gr["id"]
    assert resolve_document_stock_authority(again) == STOCK_AUTHORITY_LEGACY_PSS
    assert await _qty(db, item["id"], wh["id"]) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item["id"]}) == 0
    assert float((await get_product_stock_state(db, item["id"]))["total_qty"]) == 8.0


async def test_accurate_linked_history_blocks_first_receipt_bootstrap(db):
    """Accurate receipt lines keyed by accurate_item_id block FIRST_RECEIPT_AUTO."""
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "ACC-1",
        "item_name": "Cup Accurate",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "accurate_item_id": "991122",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(item)
    await db["accurate_purchase_receipts"].insert_one({
        "accurate_receipt_id": "acc-hist-1",
        "source": "accurate",
        "valid": True,
        "cancelled": False,
        "lines": [{
            "accurate_item_id": 991122,  # numeric form, no master_item_id
            "received_qty": 50,
        }],
    })
    assessment = await assess_first_receipt_eligibility(db, item["id"])
    assert assessment["eligible"] is False
    assert "accurate_purchase_receipts_by_accurate_item_id" in assessment["blockers"]
    with pytest.raises(HTTPException) as exc:
        await ensure_first_receipt_pss_bootstrap(db, item["id"], USER)
    assert exc.value.status_code == 400


async def test_first_receipt_auto_vs_manual_opening_race(db):
    """Manual opening after bootstrap fails 409; GR still uses initialized state."""
    item, _, _ = await _seed(db, code="RACE")
    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    boot = await ensure_first_receipt_pss_bootstrap(db, item["id"], USER)
    assert boot.get("bootstrap_source") == "FIRST_RECEIPT_AUTO"
    with pytest.raises(HTTPException) as exc:
        await initialize_opening_balance(
            db,
            item["id"],
            {"unprinted_qty": 10.0, "printed_qty": 0.0, "base_uom": "PCS"},
            user=USER,
        )
    assert exc.value.status_code == 409

    po = await create_purchase_order(db, {
        "supplier": "S",
        "idempotency_key": f"po-race-{uuid.uuid4().hex[:8]}",
        "lines": [{
            "master_item_id": item["id"],
            "item_code": "RACE",
            "item_name": "Barang",
            "unit": "PCS",
            "ordered_qty": 2,
            "unit_price": 1,
        }],
    }, USER)
    gr = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": f"gr-race-{uuid.uuid4().hex[:8]}",
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 2}],
    }, USER)
    state = await get_product_stock_state(db, item["id"])
    assert float(state["total_qty"]) == 2.0
    assert state.get("bootstrap_source") == "FIRST_RECEIPT_AUTO"
    assert await db.stock_movements.count_documents({
        "master_item_id": item["id"],
        "movement_type": "SALDO_AWAL",
    }) == 0
    assert gr.get("stock_authority") == "legacy_pss"


async def test_standalone_partial_resume_after_authority_flip_stays_standalone(db):
    """PARTIAL standalone GR resume under live Mode A still finishes warehouse, never PSS."""
    item, _, wh = await _seed(db, code="PART")
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    po = await create_purchase_order(db, {
        "supplier": "S",
        "idempotency_key": f"po-part-{uuid.uuid4().hex[:8]}",
        "lines": [{
            "master_item_id": item["id"],
            "item_code": "PART",
            "item_name": "Barang",
            "unit": "PCS",
            "ordered_qty": 10,
            "unit_price": 1,
        }],
    }, USER)
    key = f"gr-part-{uuid.uuid4().hex[:8]}"
    gr = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wh["id"],
        "idempotency_key": key,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 10}],
    }, USER)
    assert await _qty(db, item["id"], wh["id"]) == 10

    # Simulate PARTIAL by clearing ledger + marking posting PARTIAL.
    await db[LEDGER_COLLECTION].delete_many({"metadata.receipt_id": gr["id"]})
    await db[BALANCE_COLLECTION].update_one(
        {"master_item_id": item["id"], "warehouse_id": wh["id"]},
        {"$set": {"physical_qty": 0}},
    )
    await db["purchase_goods_receipts"].update_one(
        {"id": gr["id"]},
        {"$set": {
            "stock_authority": STOCK_AUTHORITY_STANDALONE,
            "stock_posting": {
                "status": "PARTIAL",
                "engine": "warehouse_standalone",
                "posted_count": 0,
                "failed_count": 1,
            },
        }},
    )

    # Flip live authority to Mode A — resume must still finish stamped standalone.
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    await update_physical_stock_settings(
        db,
        {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "delivery_confirmed"},
        user={"email": "t@test"},
    )
    again = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wh["id"],
        "idempotency_key": key,
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 10}],
    }, USER)
    assert again["id"] == gr["id"]
    assert resolve_document_stock_authority(again) == STOCK_AUTHORITY_STANDALONE
    assert await _qty(db, item["id"], wh["id"]) == 10
    assert await get_product_stock_state(db, item["id"]) is None
    assert await db.stock_movements.count_documents({"master_item_id": item["id"]}) == 0
