"""One complete R2 inventory chain on an isolated replica-set test DB.

Never uses eternal_packaging_preview or production data.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = "eternal_packaging_test_r2_inventory_e2e"
assert "eternal_packaging_preview" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    PhysicalStockConflictError,
    _client,
    adjust_physical_stock_to_target,
    apply_purchase_receipt_lines,
    apply_stock_opname_adjustments,
    confirm_sales_delivery,
    ensure_indexes,
    get_balance,
    list_physical_stock_catalog,
    require_r2_delivery_stock_ready,
    reverse_movement,
)
from services.physical_stock_engine import legacy_pss_stock_out_writes_enabled  # noqa: E402
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.product_stock_state import (  # noqa: E402
    get_product_stock_state,
    initialize_opening_balance,
)

MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

LEGACY = ("product_stock_state", "stock_movements", "item_stock_manual")
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _masters(db):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "E2E-BOX",
        "item_name": "E2E Box",
        "unit": "PCS",
        "is_active": True,
    }
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "Yogyakarta", "active": True}
    wh_a = {
        "id": f"wh-a-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "MAIN",
        "active": True,
    }
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": "WHB",
        "name": "WHB",
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    return item, branch, wh_a, wh_b


async def _post_gr(db, *, item, branch, warehouse, qty, receipt_id=None):
    receipt_id = receipt_id or f"gr-{uuid.uuid4().hex[:8]}"
    receipt = {
        "id": receipt_id,
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "purchase_order_id": f"po-{receipt_id}",
        "lines": [{
            "line_id": "r1",
            "master_item_id": item["id"],
            "received_qty": qty,
            "purchase_order_line_id": "po-line-1",
        }],
    }
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            await db.purchase_goods_receipts.insert_one(dict(receipt), session=session)
            movements = await apply_purchase_receipt_lines(db, session, receipt, actor_id="e2e")
    return receipt, movements


def _order(item, *, ordered=100, completed=100):
    return {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": ordered,
            "production_process": "print",
            "unit": "PCS",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }


async def test_r2_inventory_full_chain_isolated(db):
    await set_r2_authority_active(db, active=True)
    assert await legacy_pss_stock_out_writes_enabled(db) is False

    item, branch, wh_a, wh_b = await _masters(db)
    # Seed WH-B only (isolation control); WH-A starts empty then GR.
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-b-{uuid.uuid4().hex[:6]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_b["id"],
        "physical_qty": 500,
        "baseline_status": BASELINE_RECONCILED,
        "version": 1,
    })
    await initialize_opening_balance(
        db, item["id"],
        {"unprinted_qty": 100.0, "printed_qty": 0.0, "base_uom": "PCS"},
        user={"email": "e2e@test"},
    )
    pss_total_before = float((await get_product_stock_state(db, item["id"]))["total_qty"])
    legacy_before = {name: await db[name].count_documents({}) for name in LEGACY}

    # 3–4 Purchase GR → ledger + Stock Saat Ini
    _receipt, gr_movements = await _post_gr(db, item=item, branch=branch, warehouse=wh_a, qty=10)
    assert gr_movements[0]["movement_type"] == MOVEMENT_PURCHASE_RECEIPT
    assert gr_movements[0]["qty_delta"] == 10
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 10
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], warehouse_id=wh_a["id"])
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 10
    assert catalog.get("r2_authority_active") is True

    # 5–8 Sales delivery with shortage warning, negative book, idempotent replay
    order = _order(item, ordered=100, completed=100)
    await db.orders.insert_one(dict(order))
    _br, _wh, warnings = await require_r2_delivery_stock_ready(
        db, order, warehouse_id=wh_a["id"], branch_id=branch["id"],
        lines=[{"master_item_id": item["id"], "qty": 25}],
    )
    assert warnings
    assert warnings[0]["blocks_delivery"] is False
    assert warnings[0]["kekurangan"] == 15

    conf_id = f"conf-{uuid.uuid4().hex[:8]}"
    delivered = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25}],
        actor_id="e2e",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
    )
    assert delivered["replayed"] is False
    assert delivered["movements"][0]["qty_delta"] == -25
    assert delivered["movements"][0]["balance_after"] == -15
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == -15
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1
    # No PSS dual-write / parallel total drop
    pss_after = await get_product_stock_state(db, item["id"])
    assert float(pss_after["total_qty"]) == pss_total_before
    assert {name: await db[name].count_documents({}) for name in LEGACY} == legacy_before

    replay = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25}],
        actor_id="e2e",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
    )
    assert replay["replayed"] is True
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == -15
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1

    # 9 Opname/adjustment reconciles to physical count
    adj = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 12,
            "reason": "Opname koreksi E2E",
            "idempotency_key": f"adj-{item['id']}",
        },
        actor_id="e2e",
    )
    assert adj["previous_physical_qty"] == -15
    assert adj["qty_delta"] == 27
    assert adj["physical_qty"] == 12
    bal = await get_balance(db, item["id"], branch["id"], wh_a["id"])
    assert bal["physical_qty"] == 12
    assert bal.get("baseline_status") == BASELINE_RECONCILED

    # 12 Reversal of GR path (post a separate receipt then reverse it)
    receipt2, gr2 = await _post_gr(
        db, item=item, branch=branch, warehouse=wh_a, qty=5, receipt_id=f"gr2-{item['id']}",
    )
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 17
    rev = await reverse_movement(
        db,
        gr2[0]["id"],
        actor_id="e2e",
        idempotency_key=f"rev-{receipt2['id']}",
        reason="batalkan GR uji",
    )
    assert rev["movement_type"] == MOVEMENT_REVERSAL
    assert rev["qty_delta"] == -5
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 12
    with pytest.raises(PhysicalStockConflictError):
        await reverse_movement(
            db,
            gr2[0]["id"],
            actor_id="e2e",
            idempotency_key=f"rev2-{receipt2['id']}",
        )
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500


async def test_zero_target_unreconciled_and_opname_zero_diff(db):
    await set_r2_authority_active(db, active=True)
    item, branch, wh_a, _wh_b = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:6]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    adj = await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 0,
            "reason": "Hitung fisik 0",
            "idempotency_key": f"zero-{item['id']}",
        },
        actor_id="e2e",
    )
    assert adj["changed"] is True
    assert adj["qty_delta"] == 0
    bal = await get_balance(db, item["id"], branch["id"], wh_a["id"])
    assert bal["baseline_status"] == BASELINE_RECONCILED
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 0,
        "warehouse_id": wh_a["id"],
    }) == 1

    # Fresh unreconciled row for opname zero-diff path
    item2, branch2, wh2, _ = await _masters(db)
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal2-{uuid.uuid4().hex[:6]}",
        "master_item_id": item2["id"],
        "branch_id": branch2["id"],
        "warehouse_id": wh2["id"],
        "physical_qty": 0,
        "baseline_status": BASELINE_UNRECONCILED,
        "version": 1,
    })
    session_doc = {
        "id": f"opn-{uuid.uuid4().hex[:8]}",
        "opname_number": "OP-E2E-0",
        "branch_id": branch2["id"],
        "warehouse_id": wh2["id"],
        "mode": "full",
    }
    lines = [{
        "line_id": "L1",
        "master_item_id": item2["id"],
        "book_qty": 0,
        "physical_count_qty": 0,
        "difference_qty": 0,
    }]
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            moved = await apply_stock_opname_adjustments(
                db, session, session_doc, lines, actor_id="e2e", occurred_at="2026-10-13T00:00:00+00:00",
            )
    assert moved == []
    bal2 = await get_balance(db, item2["id"], branch2["id"], wh2["id"])
    assert bal2["physical_qty"] == 0
    assert bal2["baseline_status"] == BASELINE_RECONCILED


async def test_r2_off_legacy_stock_out_gate_still_active(db):
    await set_r2_authority_active(db, active=False)
    assert await legacy_pss_stock_out_writes_enabled(db) is True
