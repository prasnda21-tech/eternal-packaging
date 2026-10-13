"""R2 ON: no PSS physical stock-out dual-write; no delivery PSS fallback.

Isolated replica-set DB only. Does not touch OWNER/preview/production data.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_r2_no_pss_dual_write"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    confirm_sales_delivery,
    ensure_indexes,
    get_balance,
    post_opening_balance,
    require_r2_delivery_stock_ready,
    PhysicalStockValidationError,
)
from services.physical_stock_engine import (  # noqa: E402
    handle_loading_confirmed,
    handle_operator_progress,
    handle_packing_completed,
    handle_sales_delivery_created,
    legacy_pss_stock_out_writes_enabled,
    should_claim_delivery_confirmed_stock,
    should_claim_loading_stock,
    should_claim_packing_stock,
    should_claim_sales_delivery_stock,
)
from services.product_stock_state import (  # noqa: E402
    get_product_stock_state,
    initialize_opening_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

TEST_DB = "eternal_packaging_test_r2_no_pss_dual_write"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
LEGACY = ("product_stock_state", "stock_movements")


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
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_locations(db):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "BOX-R2",
        "item_name": "Box R2",
        "unit": "PCS",
        "is_active": True,
    }
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
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


def _order(item, *, qty=100, completed=100, trigger="delivery_confirmed"):
    return {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "po_number": "PO-R2-DW",
        "cancelled": False,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": trigger,
        },
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": qty,
            "production_process": "print",
            "unit": "PCS",
            "name": "Box R2",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed, "operator_name": "Op"}],
    }


async def test_r2_off_legacy_stock_out_gates_still_active(db):
    await set_r2_authority_active(db, active=False)
    assert await legacy_pss_stock_out_writes_enabled(db) is True
    order = _order({"id": "x"}, trigger="packing_completed")
    assert await should_claim_packing_stock(db, order) is True
    order_l = _order({"id": "x"}, trigger="loading_confirmation")
    assert await should_claim_loading_stock(db, order_l) is True
    order_s = _order({"id": "x"}, trigger="sales_delivery")
    assert await should_claim_sales_delivery_stock(db, order_s) is True
    order_d = _order({"id": "x"}, trigger="delivery_confirmed")
    assert await should_claim_delivery_confirmed_stock(db, order_d) is True


async def test_r2_on_blocks_pss_stock_out_writers_keeps_status_cetak(db):
    item, branch, wh_a, wh_b = await _seed_locations(db)
    await set_r2_authority_active(db, active=True)
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "reason": "open",
            "idempotency_key": f"open-a-{item['id']}",
        },
        actor_id="t",
    )
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": 500,
            "reason": "open",
            "idempotency_key": f"open-b-{item['id']}",
        },
        actor_id="t",
    )
    await initialize_opening_balance(
        db, item["id"],
        {"unprinted_qty": 100.0, "printed_qty": 0.0, "base_uom": "PCS"},
        user={"email": "t@test"},
    )

    assert await legacy_pss_stock_out_writes_enabled(db) is False
    for trigger, claim_fn in (
        ("packing_completed", should_claim_packing_stock),
        ("loading_confirmation", should_claim_loading_stock),
        ("sales_delivery", should_claim_sales_delivery_stock),
        ("delivery_confirmed", should_claim_delivery_confirmed_stock),
    ):
        order = _order(item, trigger=trigger)
        assert await claim_fn(db, order) is False

    order = _order(item, trigger="packing_completed")
    assert await handle_packing_completed(
        db, order, [{"line_idx": 0, "qty": 25, "action_id": "pack-1"}], {"email": "t"},
    ) == []
    assert await handle_loading_confirmed(
        db, _order(item, trigger="loading_confirmation"),
        {"id": "load-1", "lines": [{"line_idx": 0, "qty": 25}]},
        {"email": "t"},
    ) == []
    assert await handle_sales_delivery_created(
        db, _order(item, trigger="sales_delivery"),
        {"id": "sj-1", "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 25, "production_process": "print"}]},
        {"email": "t"},
    ) == []

    pss_before = await get_product_stock_state(db, item["id"])
    assert float(pss_before["total_qty"]) == 100.0
    assert float(pss_before["unprinted_qty"]) == 100.0
    # STATUS_CETAK Δ0 still allowed (progress/demand path; not physical stock-out).
    mov = await handle_operator_progress(
        db, order, {"line_idx": 0, "operator_name": "Op"},
        increment=25, action_id="cetak-1", live_enabled=True,
    )
    assert mov is not None
    pss_after = await get_product_stock_state(db, item["id"])
    assert float(pss_after["total_qty"]) == 100.0
    assert float(pss_after["unprinted_qty"]) == 75.0
    assert float(pss_after["printed_qty"]) == 25.0
    # R2 physical unchanged by STATUS_CETAK / skipped stock-out handlers
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 100
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500


async def test_r2_on_delivery_25_once_no_pss_parallel_other_wh_intact(db):
    item, branch, wh_a, wh_b = await _seed_locations(db)
    await set_r2_authority_active(db, active=True)
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "reason": "open",
            "idempotency_key": f"open-a-{item['id']}",
        },
        actor_id="t",
    )
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": 500,
            "reason": "open",
            "idempotency_key": f"open-b-{item['id']}",
        },
        actor_id="t",
    )
    await initialize_opening_balance(
        db, item["id"],
        {"unprinted_qty": 100.0, "printed_qty": 50.0, "base_uom": "PCS"},
        user={"email": "t@test"},
    )
    order = _order(item, qty=100, completed=100, trigger="delivery_confirmed")
    await db.orders.insert_one(dict(order))
    legacy_before = {name: await db[name].count_documents({}) for name in LEGACY}
    pss_total_before = float((await get_product_stock_state(db, item["id"]))["total_qty"])

    conf_id = f"conf-{uuid.uuid4().hex[:8]}"
    result = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25}],
        actor_id="tester",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
    )
    assert result["replayed"] is False
    assert result["movements"][0]["qty_delta"] == -25
    assert result["movements"][0]["movement_type"] == MOVEMENT_SALES_DELIVERY
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 75
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1
    # No parallel PSS total reduction
    pss_after = await get_product_stock_state(db, item["id"])
    assert float(pss_after["total_qty"]) == pss_total_before
    # Replay idempotent
    replay = await confirm_sales_delivery(
        db,
        order_id=order["id"],
        confirmation_id=conf_id,
        lines=[{"line_idx": 0, "qty": 25}],
        actor_id="tester",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
    )
    assert replay["replayed"] is True
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 75
    assert await db[LEDGER_COLLECTION].count_documents({
        "master_item_id": item["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "warehouse_id": wh_a["id"],
    }) == 1
    assert {name: await db[name].count_documents({}) for name in LEGACY} == legacy_before


async def test_r2_unreadiness_rejects_without_fallback(db):
    item, branch, wh_a, wh_b = await _seed_locations(db)
    await set_r2_authority_active(db, active=True)
    # Only WH-B has balance; WH-A missing (not zero).
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": 500,
            "reason": "open",
            "idempotency_key": f"open-b-{item['id']}",
        },
        actor_id="t",
    )
    order = _order(item)
    await db.orders.insert_one(dict(order))

    with pytest.raises(PhysicalStockValidationError, match="belum tersedia"):
        await require_r2_delivery_stock_ready(
            db, order, warehouse_id=wh_a["id"], branch_id=branch["id"],
        )
    with pytest.raises(PhysicalStockValidationError, match="Pilih gudang"):
        await require_r2_delivery_stock_ready(
            db, order, warehouse_id=None, branch_id=branch["id"],
        )
    # Zero balance is ready (valid); outbound may still fail insufficient later.
    await post_opening_balance(
        db, item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 0,
            "reason": "open-zero",
            "idempotency_key": f"open-a-zero-{item['id']}",
        },
        actor_id="t",
    )
    br, wh = await require_r2_delivery_stock_ready(
        db, order, warehouse_id=wh_a["id"], branch_id=branch["id"],
    )
    assert br == branch["id"] and wh == wh_a["id"]
    assert (await get_balance(db, item["id"], branch["id"], wh_a["id"]))["physical_qty"] == 0
    assert (await get_balance(db, item["id"], branch["id"], wh_b["id"]))["physical_qty"] == 500
    doc = await db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert int(doc.get("qty_delivered") or 0) == 0
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_SALES_DELIVERY}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({"warehouse_id": wh_a["id"]}) == 1
