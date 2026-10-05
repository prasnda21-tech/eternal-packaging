"""R2 warehouse stock opname. Isolated test database only."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    WAREHOUSES_COLLECTION,
    PhysicalStockAbortError,
    StockOpnameInProgressError,
    adjust_physical_stock_to_target,
    apply_confirmed_sales_delivery,
    apply_purchase_receipt_lines,
    apply_purchase_return_lines,
    ensure_indexes as ensure_physical_indexes,
    get_balance,
    list_physical_mutations,
    list_physical_stock_catalog,
    post_movement,
    reconcile_balance,
    reverse_movement_in_session,
    set_fail_after_balance,
    _client,
)
from services.stock_opname_sessions import (
    COLLECTION,
    cancel_stock_opname_session,
    confirm_stock_opname_session,
    create_stock_opname_session,
    ensure_indexes,
    get_stock_opname_session,
    record_stock_opname_counts,
    set_fail_after_adjustments,
    start_stock_opname_cutoff,
    submit_stock_opname_review,
)

TEST_DB = "eternal_packaging_test_stock_opname_r2"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "opname-tester"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_physical_indexes(database)
    await ensure_indexes(database)
    yield database
    set_fail_after_balance(False)
    set_fail_after_adjustments(False)
    await client.drop_database(TEST_DB)
    client.close()


async def _location(db, *, code="YGY", name="Yogyakarta", warehouse_code="MAIN"):
    branch_id = str(uuid.uuid4())
    warehouse_id = str(uuid.uuid4())
    await db[BRANCHES_COLLECTION].insert_one({
        "id": branch_id, "code": code, "name": name, "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": warehouse_id, "branch_id": branch_id, "code": warehouse_code,
        "name": f"Gudang {warehouse_code}", "active": True,
    })
    return branch_id, warehouse_id


async def _item(db, *, name, code, active=True, item_type="INVENTORY"):
    item_id = str(uuid.uuid4())
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": name,
        "unit": "PCS",
        "is_active": active,
        "item_type": item_type,
        "accurate_item_id": f"LOCAL-{item_id}",
    })
    return item_id


async def _open(db, item_id, branch_id, warehouse_id, qty):
    if qty == 0:
        return None
    return await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": qty,
        "reference_type": "test",
        "reference_id": f"open-{item_id}",
        "reason": "Saldo awal",
        "idempotency_key": f"open-{item_id}-{warehouse_id}",
    }, actor_id="tester")


async def _qty(db, item_id, branch_id, warehouse_id):
    doc = await get_balance(db, item_id, branch_id, warehouse_id)
    return int(doc["physical_qty"]) if doc else 0


def _line(session, item_id):
    for line in session["lines"]:
        if line["master_item_id"] == item_id:
            return line
    raise AssertionError(item_id)


async def _count_all(db, session, overrides):
    lines = []
    for line in session["lines"]:
        qty = overrides.get(line["master_item_id"], line["book_qty"])
        lines.append({
            "line_id": line["line_id"],
            "physical_count_qty": qty,
            "difference_qty": 999,
        })
    return await record_stock_opname_counts(db, session["id"], {"lines": lines}, USER)


async def test_session_cutoff_freeze_count_review_and_confirm(db):
    branch_id, warehouse_id = await _location(db)
    other_branch, other_warehouse = await _location(db, code="BDG", name="Bandung", warehouse_code="BDG")
    negative = await _item(db, name="Neg", code="NEG")
    positive = await _item(db, name="Pos", code="POS")
    zero_diff = await _item(db, name="Zero", code="ZERO")
    zero_book = await _item(db, name="Empty", code="EMPTY")
    service = await _item(db, name="Jasa", code="SVC", item_type="SERVICE")
    inactive = await _item(db, name="Off", code="OFF", active=False)
    inactive_stock = await _item(db, name="Old", code="OLD", active=False)
    reversible = await _item(db, name="Rev", code="REV")
    await _open(db, negative, branch_id, warehouse_id, 100)
    await _open(db, positive, branch_id, warehouse_id, 50)
    await _open(db, zero_diff, branch_id, warehouse_id, 25)
    await _open(db, inactive_stock, branch_id, warehouse_id, 4)
    await _open(db, reversible, branch_id, warehouse_id, 8)
    reversible_receipt = await post_movement(db, {
        "master_item_id": reversible,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 2,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "rev-receipt",
        "idempotency_key": "rev-receipt",
    }, actor_id="tester")
    await _open(db, negative, other_branch, other_warehouse, 10)

    with pytest.raises(HTTPException) as mismatch:
        await create_stock_opname_session(db, {
            "branch_id": other_branch,
            "warehouse_id": warehouse_id,
        }, USER)
    assert mismatch.value.status_code == 400

    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "notes": "opname",
    }, USER)
    assert created["status"] == "DRAFT"
    assert created["branch_id"] == branch_id
    assert created["warehouse_id"] == warehouse_id
    assert created["lines"] == []
    assert created["opname_number"].startswith("SOP-")

    with pytest.raises(HTTPException) as second:
        await create_stock_opname_session(db, {
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
        }, USER)
    assert second.value.status_code == 409

    other = await create_stock_opname_session(db, {
        "branch_id": other_branch,
        "warehouse_id": other_warehouse,
    }, USER)
    assert other["warehouse_id"] == other_warehouse

    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    assert counting["status"] == "COUNTING"
    assert counting["cutoff_at"]
    masters = {line["master_item_id"] for line in counting["lines"]}
    assert negative in masters
    assert positive in masters
    assert zero_diff in masters
    assert zero_book in masters
    assert inactive_stock in masters
    assert reversible in masters
    assert _line(counting, reversible)["book_qty"] == 10
    assert service not in masters
    assert inactive not in masters
    assert _line(counting, zero_book)["book_qty"] == 0
    assert _line(counting, negative)["book_qty"] == 100
    assert _line(counting, negative)["physical_count_qty"] is None

    await db[BALANCE_COLLECTION].update_one(
        {"master_item_id": negative, "warehouse_id": warehouse_id},
        {"$set": {"physical_qty": 1}},
    )
    again = await start_stock_opname_cutoff(db, created["id"], USER)
    assert _line(again, negative)["book_qty"] == 100
    await db[BALANCE_COLLECTION].update_one(
        {"master_item_id": negative, "warehouse_id": warehouse_id},
        {"$set": {"physical_qty": 100}},
    )

    with pytest.raises(StockOpnameInProgressError) as frozen_delivery:
        async with await _client(db).start_session() as mongo_session:
            async with mongo_session.start_transaction():
                await db.orders.insert_one({
                    "id": "order-1",
                    "status": "open",
                    "line_items": [{
                        "line_id": "l1",
                        "master_item_id": negative,
                        "quantity": 10,
                    }],
                    "operator_splits": [{"line_idx": 0, "qty_completed": 10}],
                }, session=mongo_session)
                await apply_confirmed_sales_delivery(
                    db,
                    mongo_session,
                    order={
                        "id": "order-1",
                        "status": "open",
                        "line_items": [{
                            "line_id": "l1",
                            "master_item_id": negative,
                            "quantity": 10,
                        }],
                        "operator_splits": [{"line_idx": 0, "qty_completed": 10}],
                    },
                    confirmation_id="conf-1",
                    lines=[{"line_idx": 0, "qty": 1}],
                    actor_id="tester",
                    branch_id=branch_id,
                    warehouse_id=warehouse_id,
                    write_order=True,
                )
    assert "STOCK_OPNAME_IN_PROGRESS" in str(frozen_delivery.value)
    assert await db.orders.count_documents({"id": "order-1"}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 0

    with pytest.raises(StockOpnameInProgressError):
        async with await _client(db).start_session() as mongo_session:
            async with mongo_session.start_transaction():
                receipt_id = str(uuid.uuid4())
                receipt = {
                    "id": receipt_id,
                    "branch_id": branch_id,
                    "warehouse_id": warehouse_id,
                    "lines": [{
                        "line_id": "r1",
                        "master_item_id": negative,
                        "received_qty": 1,
                        "purchase_order_line_id": "po-line",
                    }],
                }
                await db.purchase_goods_receipts.insert_one(receipt, session=mongo_session)
                await apply_purchase_receipt_lines(db, mongo_session, receipt, actor_id="tester")
    assert await db.purchase_goods_receipts.count_documents({}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "master_item_id": negative,
        "warehouse_id": warehouse_id,
    }) == 0

    with pytest.raises(StockOpnameInProgressError):
        async with await _client(db).start_session() as mongo_session:
            async with mongo_session.start_transaction():
                document = {
                    "id": "return-1",
                    "lines": [{
                        "line_id": "ret-1",
                        "master_item_id": negative,
                        "branch_id": branch_id,
                        "warehouse_id": warehouse_id,
                        "return_qty": 1,
                    }],
                }
                await db.purchase_returns.insert_one(document, session=mongo_session)
                await apply_purchase_return_lines(db, mongo_session, document, actor_id="tester")
    assert await db.purchase_returns.count_documents({}) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_PURCHASE_RETURN}) == 0

    with pytest.raises(StockOpnameInProgressError):
        await adjust_physical_stock_to_target(db, negative, {
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "target_physical_qty": 90,
            "reason": "manual",
            "idempotency_key": "manual-frozen",
        }, actor_id="tester")
    assert await _qty(db, negative, branch_id, warehouse_id) == 100
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_MANUAL_ADJUSTMENT}) == 0

    with pytest.raises(StockOpnameInProgressError):
        await post_movement(db, {
            "master_item_id": zero_book,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": 3,
            "idempotency_key": "opening-frozen",
        }, actor_id="tester")

    prior = await post_movement(db, {
        "master_item_id": negative,
        "branch_id": other_branch,
        "warehouse_id": other_warehouse,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 1,
        "reference_type": "purchase_goods_receipt",
        "reference_id": "other-receipt",
        "idempotency_key": "other-receipt",
    }, actor_id="tester")
    with pytest.raises(StockOpnameInProgressError):
        async with await _client(db).start_session() as mongo_session:
            async with mongo_session.start_transaction():
                await reverse_movement_in_session(
                    db, mongo_session, reversible_receipt["id"], actor_id="tester",
                    idempotency_key="reversal-frozen", reason="frozen",
                )
    assert await _qty(db, reversible, branch_id, warehouse_id) == 10
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
        "reference_id": reversible_receipt["id"],
    }) == 0

    with pytest.raises(HTTPException):
        await record_stock_opname_counts(db, counting["id"], {
            "line_id": _line(counting, negative)["line_id"],
            "physical_count_qty": -1,
        }, USER)
    with pytest.raises(HTTPException):
        await record_stock_opname_counts(db, counting["id"], {
            "line_id": _line(counting, negative)["line_id"],
            "physical_count_qty": "",
        }, USER)
    with pytest.raises(HTTPException):
        await record_stock_opname_counts(db, counting["id"], {
            "line_id": _line(counting, negative)["line_id"],
        }, USER)
    with pytest.raises(HTTPException):
        await record_stock_opname_counts(db, counting["id"], {
            "line_id": _line(counting, positive)["line_id"],
            "physical_count_qty": 1.5,
        }, USER)

    counted = await record_stock_opname_counts(db, counting["id"], {
        "line_id": _line(counting, negative)["line_id"],
        "physical_count_qty": 0,
        "book_qty": 1,
        "difference_qty": 999,
    }, USER)
    assert _line(counted, negative)["physical_count_qty"] == 0
    assert _line(counted, negative)["book_qty"] == 100
    assert _line(counted, negative)["difference_qty"] == -100
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT}) == 0

    with pytest.raises(HTTPException) as incomplete:
        await submit_stock_opname_review(db, counting["id"], USER)
    assert incomplete.value.status_code == 400

    counted = await _count_all(db, counted, {
        negative: 95,
        positive: 57,
        zero_diff: 25,
        zero_book: 5,
        inactive_stock: 4,
        reversible: 10,
    })
    assert _line(counted, negative)["difference_qty"] == -5
    assert _line(counted, positive)["difference_qty"] == 7
    assert _line(counted, zero_diff)["difference_qty"] == 0
    assert _line(counted, zero_book)["difference_qty"] == 5
    assert _line(counted, negative)["book_qty"] == 100

    review = await submit_stock_opname_review(db, counting["id"], USER)
    assert review["status"] == "REVIEW"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0
    with pytest.raises(StockOpnameInProgressError):
        await post_movement(db, {
            "master_item_id": negative,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_PURCHASE_RETURN,
            "qty_delta": -1,
            "idempotency_key": "return-still-frozen",
        }, actor_id="tester")

    set_fail_after_balance(True)
    with pytest.raises(PhysicalStockAbortError):
        await confirm_stock_opname_session(db, counting["id"], {"idempotency_key": "confirm-1"}, USER)
    set_fail_after_balance(False)
    assert (await get_stock_opname_session(db, counting["id"]))["status"] == "REVIEW"
    assert await _qty(db, negative, branch_id, warehouse_id) == 100
    assert await _qty(db, positive, branch_id, warehouse_id) == 50
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0

    confirmed = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "confirm-1"}, USER,
    )
    assert confirmed["status"] == "CONFIRMED"
    assert await _qty(db, negative, branch_id, warehouse_id) == 95
    assert await _qty(db, positive, branch_id, warehouse_id) == 57
    assert await _qty(db, zero_diff, branch_id, warehouse_id) == 25
    assert await _qty(db, zero_book, branch_id, warehouse_id) == 5
    assert await _qty(db, inactive_stock, branch_id, warehouse_id) == 4
    movements = await db[LEDGER_COLLECTION].find(
        {"movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT},
        {"_id": 0},
    ).to_list(20)
    assert len(movements) == 3
    by_item = {row["master_item_id"]: row for row in movements}
    assert reversible not in by_item
    assert await _qty(db, reversible, branch_id, warehouse_id) == 10
    assert by_item[negative]["qty_delta"] == -5
    assert by_item[positive]["qty_delta"] == 7
    assert by_item[zero_book]["qty_delta"] == 5
    assert zero_diff not in by_item
    for row in movements:
        assert row["branch_id"] == branch_id
        assert row["warehouse_id"] == warehouse_id
        assert row["metadata"]["stock_opname_session_id"] == confirmed["id"]
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": zero_diff,
    }) == 0

    retry = await confirm_stock_opname_session(
        db, counting["id"], {"idempotency_key": "confirm-1"}, USER,
    )
    assert retry["status"] == "CONFIRMED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 3
    with pytest.raises(HTTPException) as changed:
        await confirm_stock_opname_session(
            db, counting["id"], {"idempotency_key": "confirm-other"}, USER,
        )
    assert changed.value.status_code == 409

    with pytest.raises(HTTPException) as locked:
        await cancel_stock_opname_session(db, counting["id"], {}, USER)
    assert locked.value.status_code == 409
    assert (await get_stock_opname_session(db, counting["id"]))["status"] == "CONFIRMED"

    released = await post_movement(db, {
        "master_item_id": negative,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 1,
        "idempotency_key": "receipt-after-release",
    }, actor_id="tester")
    assert released["movement_type"] == MOVEMENT_PURCHASE_RECEIPT
    returned = await post_movement(db, {
        "master_item_id": positive,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_PURCHASE_RETURN,
        "qty_delta": -1,
        "idempotency_key": "return-after-release",
    }, actor_id="tester")
    assert returned["qty_delta"] == -1
    delivered = await post_movement(db, {
        "master_item_id": zero_diff,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -1,
        "idempotency_key": "delivery-after-release",
    }, actor_id="tester")
    assert delivered["movement_type"] == MOVEMENT_SALES_DELIVERY
    manual = await adjust_physical_stock_to_target(db, inactive_stock, {
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "target_physical_qty": 6,
        "reason": "manual after",
        "idempotency_key": "manual-after",
    }, actor_id="tester")
    assert manual["physical_qty"] == 6

    outside = await post_movement(db, {
        "master_item_id": negative,
        "branch_id": other_branch,
        "warehouse_id": other_warehouse,
        "movement_type": MOVEMENT_PURCHASE_RETURN,
        "qty_delta": -1,
        "idempotency_key": "return-other-warehouse",
    }, actor_id="tester")
    assert outside["warehouse_id"] == other_warehouse
    assert prior["id"]

    for item_id, warehouse in (
        (negative, warehouse_id),
        (positive, warehouse_id),
        (zero_book, warehouse_id),
        (negative, other_warehouse),
    ):
        report = await reconcile_balance(
            db, item_id, branch_id if warehouse == warehouse_id else other_branch, warehouse,
        )
        assert report["match"] is True

    mutations = await list_physical_mutations(
        db, negative, branch_id=branch_id, warehouse_id=warehouse_id,
        movement_type=MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    )
    assert mutations["items"][0]["movement_label"] == "Penyesuaian Stock Opname"
    assert mutations["items"][0]["reference_type"] == "stock_opname_session"
    assert await db[LEDGER_COLLECTION].count_documents({
        "id": prior["id"],
        "qty_delta": prior["qty_delta"],
    }) == 1


async def test_cancel_releases_freeze_without_adjustment(db):
    branch_id, warehouse_id = await _location(db, code="YGY", name="Yogyakarta")
    item_id = await _item(db, name="Cancel", code="CAN")
    await _open(db, item_id, branch_id, warehouse_id, 10)
    draft = await create_stock_opname_session(db, {
        "branch_id": branch_id, "warehouse_id": warehouse_id,
    }, USER)
    cancelled = await cancel_stock_opname_session(db, draft["id"], {"reason": "batal"}, USER)
    assert cancelled["status"] == "CANCELLED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0

    active = await create_stock_opname_session(db, {
        "branch_id": branch_id, "warehouse_id": warehouse_id,
    }, USER)
    counting = await start_stock_opname_cutoff(db, active["id"], USER)
    with pytest.raises(StockOpnameInProgressError):
        await post_movement(db, {
            "master_item_id": item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": 1,
            "idempotency_key": "blocked-before-cancel",
        }, actor_id="tester")
    stopped = await cancel_stock_opname_session(db, counting["id"], {}, USER)
    assert stopped["status"] == "CANCELLED"
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    }) == 0
    resumed = await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 1,
        "idempotency_key": "after-cancel",
    }, actor_id="tester")
    assert resumed["qty_delta"] == 1
    assert await _qty(db, item_id, branch_id, warehouse_id) == 11


async def test_concurrent_session_and_confirm(db):
    branch_id, warehouse_id = await _location(db)
    item_id = await _item(db, name="Race", code="RACE")
    await _open(db, item_id, branch_id, warehouse_id, 20)
    created = await asyncio.gather(
        create_stock_opname_session(db, {"branch_id": branch_id, "warehouse_id": warehouse_id}, USER),
        create_stock_opname_session(db, {"branch_id": branch_id, "warehouse_id": warehouse_id}, USER),
        return_exceptions=True,
    )
    successes = [row for row in created if isinstance(row, dict)]
    failures = [row for row in created if isinstance(row, HTTPException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 409
    session = await start_stock_opname_cutoff(db, successes[0]["id"], USER)
    await _count_all(db, session, {item_id: 15})
    await submit_stock_opname_review(db, session["id"], USER)
    results = await asyncio.gather(
        confirm_stock_opname_session(db, session["id"], {"idempotency_key": "race-confirm"}, USER),
        confirm_stock_opname_session(db, session["id"], {"idempotency_key": "race-confirm"}, USER),
        return_exceptions=True,
    )
    assert all(isinstance(row, dict) and row["status"] == "CONFIRMED" for row in results)
    assert await _qty(db, item_id, branch_id, warehouse_id) == 15
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "master_item_id": item_id,
    }) == 1
    report = await reconcile_balance(db, item_id, branch_id, warehouse_id)
    assert report["match"] is True


async def test_stock_saat_ini_follows_physical_difference(db):
    branch_id, warehouse_id = await _location(db, code="YGY", name="Yogyakarta")
    item_id = await _item(db, name="Avail", code="AVL")
    await _open(db, item_id, branch_id, warehouse_id, 100)
    await db.orders.insert_one({
        "id": "so-1",
        "status": "open",
        "line_items": [{"master_item_id": item_id, "quantity": 30, "quantity_unit": "PCS"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 10}],
    })
    session = await create_stock_opname_session(db, {
        "branch_id": branch_id, "warehouse_id": warehouse_id,
    }, USER)
    counting = await start_stock_opname_cutoff(db, session["id"], USER)
    await _count_all(db, counting, {item_id: 95})
    await submit_stock_opname_review(db, session["id"], USER)
    before = await list_physical_stock_catalog(db, limit=50)
    row_before = next(row for row in before["items"] if row["master_item_id"] == item_id)
    assert (row_before["physical_qty"], row_before["qty_pesanan"], row_before["qty_belum_terkirim"], row_before["qty_tersedia"]) == (100, 20, 10, 70)
    await confirm_stock_opname_session(db, session["id"], {"idempotency_key": "avail"}, USER)
    after = await list_physical_stock_catalog(db, limit=50)
    row = next(row for row in after["items"] if row["master_item_id"] == item_id)
    assert row["physical_qty"] == 95
    assert row["qty_pesanan"] == 20
    assert row["qty_belum_terkirim"] == 10
    assert row["qty_tersedia"] == 65
    assert MOVEMENT_REVERSAL
