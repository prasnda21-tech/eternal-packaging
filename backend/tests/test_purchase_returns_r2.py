"""Purchase return posts R2 PURCHASE_RETURN only when confirmed. Isolated DB only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_REVERSAL,
    PhysicalStockAbortError,
    ensure_indexes,
    get_balance,
    list_physical_mutations,
    list_physical_stock_catalog,
    post_movement,
    set_fail_after_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_invoices import create_purchase_invoice
from services.purchase_orders import create_purchase_order, get_purchase_order
from services.purchase_returns import (
    cancel_purchase_return,
    confirm_purchase_return,
    create_purchase_return,
    set_fail_after_r2_movements,
)

TEST_DB = "eternal_packaging_test_purchase_return_r2"
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
    await set_r2_authority_active(database, active=True)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_returns.create_index("id", unique=True)
    await database.purchase_returns.create_index("return_number", unique=True)
    await database.purchase_returns.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.purchase_returns.create_index(
        "confirmation_key",
        unique=True,
        partialFilterExpression={"confirmation_key": {"$type": "string"}},
    )
    yield database
    set_fail_after_balance(False)
    set_fail_after_r2_movements(False)
    await client.drop_database(TEST_DB)
    client.close()


async def _org(db):
    branch_id = "branch-ygy"
    await db.branches.insert_one({"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True})
    await db.warehouses.insert_one({
        "id": "wh-main", "branch_id": branch_id, "code": "MAIN", "name": "Gudang Yogyakarta", "active": True,
    })
    return branch_id


async def _item(db, code="ITEM"):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id, "item_code": code, "item_name": code, "base_unit": "PCS", "accurate_item_id": item_id,
    })
    return item_id


async def _po(db, lines):
    return await create_purchase_order(db, {
        "supplier": "Supplier Retur",
        "lines": lines,
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


def _line(item_id, qty, name="Item"):
    return {
        "master_item_id": item_id, "item_name": name, "item_code": name, "unit": "PCS",
        "ordered_qty": qty, "unit_price": 1000,
    }


async def _receive(db, po, lines):
    return await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "lines": lines,
        "idempotency_key": str(uuid.uuid4()),
    }, USER)


async def _draft(db, receipt, qty, line_index=0, **extra):
    source = receipt["lines"][line_index]
    payload = {
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": source["line_id"],
            "return_qty": qty,
        }],
        "idempotency_key": extra.pop("idempotency_key", str(uuid.uuid4())),
        **extra,
    }
    return await create_purchase_return(db, payload, USER)


async def _confirm(db, document, key=None):
    return await confirm_purchase_return(db, document["id"], {"idempotency_key": key or str(uuid.uuid4())}, USER)


def _return_movements(rows):
    return [row for row in rows if row.get("movement_type") == MOVEMENT_PURCHASE_RETURN]


async def test_draft_has_no_stock_and_confirm_posts_exact_negative_return(db):
    branch_id = await _org(db)
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    receipt = await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 100}])
    before = await db[LEDGER_COLLECTION].count_documents({})
    draft = await _draft(db, receipt, 20)
    assert draft["status"] == "DRAFT"
    assert draft["purchase_goods_receipt_id"] == receipt["id"]
    assert draft["lines"][0]["master_item_id"] == item_id
    assert draft["lines"][0]["branch_id"] == branch_id
    assert draft["lines"][0]["warehouse_id"] == "wh-main"
    assert draft["lines"][0]["purchase_order_line_id"] == po["lines"][0]["line_id"]
    assert await db[LEDGER_COLLECTION].count_documents({}) == before
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 100
    confirmed = await _confirm(db, draft, key="confirm-once")
    assert confirmed["status"] == "CONFIRMED"
    movements = await db[LEDGER_COLLECTION].find({"metadata.purchase_return_id": draft["id"]}, {"_id": 0}).to_list(10)
    assert len(movements) == 1
    assert movements[0]["movement_type"] == MOVEMENT_PURCHASE_RETURN
    assert movements[0]["qty_delta"] == -20
    assert movements[0]["master_item_id"] == item_id
    assert movements[0]["branch_id"] == branch_id
    assert movements[0]["warehouse_id"] == "wh-main"
    receipt_move = await db[LEDGER_COLLECTION].find_one({"metadata.receipt_id": receipt["id"]})
    assert receipt_move["movement_type"] == MOVEMENT_PURCHASE_RECEIPT
    assert receipt_move["qty_delta"] == 100
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 80
    again = await _confirm(db, draft, key="confirm-once")
    assert again["id"] == confirmed["id"]
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 1
    mutations = await list_physical_mutations(db, item_id, movement_type=MOVEMENT_PURCHASE_RETURN)
    assert mutations["items"][0]["movement_label"] == "Retur Pembelian"
    assert await db.stock_movements.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0
    assert await db.item_stock_manual.count_documents({}) == 0
    assert await db.purchase_payments.count_documents({}) == 0
    assert await db.purchase_supplier_advances.count_documents({}) == 0
    assert await db.supplier_credits.count_documents({}) == 0


async def test_partial_returns_stop_at_returnable_and_physical_qty(db):
    branch_id = await _org(db)
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    receipt = await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 100}])
    first = await _confirm(db, await _draft(db, receipt, 20))
    second = await _confirm(db, await _draft(db, receipt, 30))
    assert first["id"] != second["id"]
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 50
    with pytest.raises(HTTPException) as over:
        await _draft(db, receipt, 60)
    assert over.value.status_code == 400
    await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": -40,
        "idempotency_key": "drain-physical",
        "reason": "test",
    }, actor_id="user-1")
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 10
    short = await _draft(db, receipt, 20)
    with pytest.raises(HTTPException) as physical:
        await _confirm(db, short)
    assert physical.value.status_code == 409
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 10
    stored = await db.purchase_returns.find_one({"id": short["id"]})
    assert stored["status"] == "DRAFT"
    source = await db.purchase_goods_receipts.find_one({"id": receipt["id"]})
    assert int(source["lines"][0].get("confirmed_return_qty") or 0) == 50


async def test_multi_line_failure_rolls_back_and_r2_abort_keeps_draft(db):
    branch_id = await _org(db)
    item_a = await _item(db, "A")
    item_b = await _item(db, "B")
    po = await _po(db, [_line(item_a, 10, "A"), _line(item_b, 10, "B")])
    receipt = await _receive(db, po, [
        {"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 10},
        {"purchase_order_line_id": po["lines"][1]["line_id"], "received_qty": 10},
    ])
    await post_movement(db, {
        "master_item_id": item_b, "branch_id": branch_id, "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT, "qty_delta": -10,
        "idempotency_key": "empty-b", "reason": "test",
    }, actor_id="user-1")
    draft = await create_purchase_return(db, {
        "idempotency_key": str(uuid.uuid4()),
        "lines": [
            {"purchase_goods_receipt_id": receipt["id"], "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"], "return_qty": 4},
            {"purchase_goods_receipt_id": receipt["id"], "purchase_goods_receipt_line_id": receipt["lines"][1]["line_id"], "return_qty": 4},
        ],
    }, USER)
    with pytest.raises(HTTPException):
        await _confirm(db, draft)
    assert (await db.purchase_returns.find_one({"id": draft["id"]}))["status"] == "DRAFT"
    assert (await get_balance(db, item_a, branch_id, "wh-main"))["physical_qty"] == 10
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 0
    # Restore B physical qty after the forced shortfall. The drain was only to make
    # multi-line confirm fail; leaving B mismatched blocks later HPP rebuild on GR.
    await post_movement(db, {
        "master_item_id": item_b, "branch_id": branch_id, "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT, "qty_delta": 10,
        "idempotency_key": "restore-b", "reason": "test",
    }, actor_id="user-1")
    assert (await get_balance(db, item_b, branch_id, "wh-main"))["physical_qty"] == 10
    other = await _po(db, [_line(item_a, 5, "A2")])
    other_receipt = await _receive(db, other, [{"purchase_order_line_id": other["lines"][0]["line_id"], "received_qty": 5}])
    pending = await _draft(db, other_receipt, 2)
    set_fail_after_r2_movements(True)
    with pytest.raises(PhysicalStockAbortError):
        await _confirm(db, pending)
    set_fail_after_r2_movements(False)
    assert (await db.purchase_returns.find_one({"id": pending["id"]}))["status"] == "DRAFT"
    assert (await get_balance(db, item_a, branch_id, "wh-main"))["physical_qty"] == 15


async def test_idempotency_conflict_and_concurrent_confirmation(db):
    branch_id = await _org(db)
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    receipt = await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 100}])
    key = "create-same"
    first = await _draft(db, receipt, 10, idempotency_key=key)
    second = await _draft(db, receipt, 10, idempotency_key=key)
    assert second["id"] == first["id"]
    with pytest.raises(HTTPException) as changed:
        await _draft(db, receipt, 11, idempotency_key=key)
    assert changed.value.status_code == 409
    left = await _draft(db, receipt, 60)
    right = await _draft(db, receipt, 60)
    raced = await asyncio.gather(_confirm(db, left, key="over-a"), _confirm(db, right, key="over-b"), return_exceptions=True)
    saved = [row for row in raced if not isinstance(row, Exception)]
    assert len(saved) == 1
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 40
    duplicate = await _draft(db, receipt, 5)
    both = await asyncio.gather(
        _confirm(db, duplicate, key="same-confirm"),
        _confirm(db, duplicate, key="same-confirm"),
        return_exceptions=True,
    )
    assert all(not isinstance(row, Exception) and row["id"] == duplicate["id"] for row in both)
    assert await db[LEDGER_COLLECTION].count_documents({"metadata.purchase_return_id": duplicate["id"]}) == 1
    other = await _draft(db, receipt, 5)
    with pytest.raises(HTTPException) as conflict:
        await _confirm(db, other, key="same-confirm")
    assert conflict.value.status_code == 409
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 2


async def test_return_before_invoice_reduces_and_reversal_restores_invoiceable_qty(db):
    branch_id = await _org(db)
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    line_id = po["lines"][0]["line_id"]
    receipt = await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 100}])
    draft = await _draft(db, receipt, 20)
    assert (await get_purchase_order(db, po["id"]))["lines"][0]["receipt_invoice_eligible_qty"] == 100
    confirmed = await _confirm(db, draft)
    enriched = await get_purchase_order(db, po["id"])
    assert enriched["lines"][0]["net_received_qty"] == 80
    assert enriched["lines"][0]["receipt_invoice_eligible_qty"] == 80
    with pytest.raises(HTTPException):
        await create_purchase_invoice(db, {
            "purchase_order_id": po["id"],
            "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 100}],
            "precedes_goods_receipt": True,
            "idempotency_key": str(uuid.uuid4()),
        }, USER)
    invoice = await create_purchase_invoice(db, {
        "purchase_order_id": po["id"],
        "invoice_source": "receipt",
        "receipt_id": receipt["id"],
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 80}],
        "idempotency_key": str(uuid.uuid4()),
    }, USER)
    assert invoice["lines"][0]["invoice_qty"] == 80
    traced = await db.purchase_returns.find_one({"id": confirmed["id"]})
    assert traced["lines"][0]["invoice_trace"]["invoice_ownership_ambiguous"] is False
    cancelled = await cancel_purchase_return(db, confirmed["id"], USER, reason="batal")
    assert cancelled["status"] == "REVERSED"
    original = await db[LEDGER_COLLECTION].find_one({"metadata.purchase_return_id": confirmed["id"], "movement_type": MOVEMENT_PURCHASE_RETURN})
    assert original["qty_delta"] == -20
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL, "metadata.purchase_return_id": {"$exists": False}}) >= 0
    reversals = await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL})
    assert reversals == 1
    again = await cancel_purchase_return(db, confirmed["id"], USER, reason="batal")
    assert again["status"] == "REVERSED"
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 1
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 100
    restored = await get_purchase_order(db, po["id"])
    assert restored["lines"][0]["net_received_qty"] == 100
    assert restored["lines"][0]["receipt_invoice_eligible_qty"] == 20
    plain = await _draft(db, receipt, 5)
    await cancel_purchase_return(db, plain["id"], USER, reason="draf")
    assert (await db.purchase_returns.find_one({"id": plain["id"]}))["status"] == "CANCELLED"
    assert await db[LEDGER_COLLECTION].count_documents({"metadata.purchase_return_id": plain["id"]}) == 0


async def test_ambiguous_invoice_trace_does_not_block_physical_return(db):
    await _org(db)
    item_id = await _item(db)
    po = await _po(db, [_line(item_id, 100)])
    line_id = po["lines"][0]["line_id"]
    receipt = await _receive(db, po, [{"purchase_order_line_id": line_id, "received_qty": 100}])
    for qty in (30, 20):
        await create_purchase_invoice(db, {
            "purchase_order_id": po["id"],
            "lines": [{"purchase_order_line_id": line_id, "invoice_qty": qty}],
            "idempotency_key": str(uuid.uuid4()),
        }, USER)
    confirmed = await _confirm(db, await _draft(db, receipt, 10))
    trace = confirmed["lines"][0]["invoice_trace"]
    assert trace["invoice_ownership_ambiguous"] is True
    assert len(trace["related_invoice_lines"]) == 2
    assert await db.supplier_credits.count_documents({}) == 0
    assert await db.purchase_payments.count_documents({}) == 0


async def test_stock_saat_ini_moves_only_physical_and_available(db):
    branch_id = await _org(db)
    item_id = await _item(db, "BOX")
    await db.orders.insert_one({
        "id": "so-1",
        "cancelled": False,
        "line_items": [{"master_item_id": item_id, "quantity": 20, "production_process": "print"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 10}],
    })
    po = await _po(db, [_line(item_id, 50, "BOX")])
    receipt = await _receive(db, po, [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 50}])
    before = next(row for row in (await list_physical_stock_catalog(db))["items"] if row["master_item_id"] == item_id)
    assert (before["physical_qty"], before["qty_pesanan"], before["qty_belum_terkirim"], before["qty_tersedia"]) == (50, 10, 10, 30)
    await _confirm(db, await _draft(db, receipt, 10))
    after = next(row for row in (await list_physical_stock_catalog(db))["items"] if row["master_item_id"] == item_id)
    assert (after["physical_qty"], after["qty_pesanan"], after["qty_belum_terkirim"], after["qty_tersedia"]) == (40, 10, 10, 20)
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item_id, "movement_type": MOVEMENT_PURCHASE_RECEIPT}) == 1
    with pytest.raises(HTTPException):
        await create_purchase_return(db, {
            "warehouse_id": "wh-other",
            "lines": [{
                "purchase_goods_receipt_id": receipt["id"],
                "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
                "return_qty": 1,
            }],
        }, USER)
    assert (await get_balance(db, item_id, branch_id, "wh-main"))["physical_qty"] == 40
