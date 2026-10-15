"""Warehouse stock standalone — safe on Mongo without multi-doc transactions.

Uses an isolated DB. Does not require Replica Set transactions for the
standalone engine (pending-event + unique ledger). Flag default OFF preserves
compatibility catalog behavior.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_warehouse_standalone"
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    MODE_WAREHOUSE_STANDALONE,
    adjust_to_target_standalone,
    apply_opname_lines_standalone,
    apply_purchase_receipt_lines_standalone,
    apply_sales_delivery_line_standalone,
    apply_standalone_movement,
    is_warehouse_stock_standalone_active,
    post_opening_balance_standalone,
    recover_pending_balance,
    set_fail_after_ledger,
    set_warehouse_stock_standalone_enabled,
)

TEST_DB = "eternal_packaging_test_warehouse_standalone"
MONGO_URL = os.environ["MONGO_URL"]

pytestmark = pytest.mark.anyio


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
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    set_fail_after_ledger(False)
    try:
        yield database
    finally:
        set_fail_after_ledger(False)
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db, *, code="A"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": f"SKU-{code}",
        "item_name": f"Barang {code}",
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "Yogyakarta", "active": True}
    wh_a = {
        "id": f"wh-a-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "A",
        "name": "Gudang A",
        "active": True,
    }
    wh_b = {
        "id": f"wh-b-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "B",
        "name": "Gudang B",
        "active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_many([dict(wh_a), dict(wh_b)])
    return item, branch, wh_a, wh_b


async def test_flag_off_keeps_compatibility_catalog(db):
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    assert await is_warehouse_stock_standalone_active(db) is False
    item, branch, wh_a, wh_b = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-ignored-when-flag-off-path",
        },
        actor_id="tester",
    )
    # Opening still wrote balances (direct API) but catalog must stay compatibility
    # when flag is off — production default.
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    catalog = await list_physical_stock_catalog(db)
    assert catalog["stock_read_mode"] == "compatibility"
    assert catalog.get("r2_authority_active") is False


async def test_uat_opening_opname_gr_delivery_isolation(db):
    """Scenarios 1–5, 12: A/B isolation + GR + delivery."""
    item, branch, wh_a, wh_b = await _seed(db)
    open_a = await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 1000,
            "idempotency_key": "open-a",
        },
        actor_id="u1",
    )
    open_b = await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_b["id"],
            "physical_qty": 500,
            "idempotency_key": "open-b",
        },
        actor_id="u1",
    )
    assert open_a["physical_qty"] == 1000
    assert open_b["physical_qty"] == 500

    # Opname A → 950
    await adjust_to_target_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 950,
            "reason": "Opname A",
            "idempotency_key": "opname-a",
        },
        actor_id="u1",
    )
    bal_a = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_a["id"]}, {"_id": 0}
    )
    bal_b = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_b["id"]}, {"_id": 0}
    )
    assert bal_a["physical_qty"] == 950
    assert bal_b["physical_qty"] == 500

    # GR A +40 → 990
    receipt = {
        "id": f"rc-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "purchase_order_id": "po-1",
        "lines": [{
            "line_id": "ln1",
            "master_item_id": item["id"],
            "received_qty": 40,
            "purchase_order_line_id": "pol1",
        }],
    }
    await apply_purchase_receipt_lines_standalone(db, receipt, actor_id="u1")
    bal_a = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_a["id"]}, {"_id": 0}
    )
    assert bal_a["physical_qty"] == 990

    # Delivery A -25 → 965
    await apply_sales_delivery_line_standalone(
        db,
        master_item_id=item["id"],
        branch_id=branch["id"],
        warehouse_id=wh_a["id"],
        qty=25,
        confirmation_id="conf-1",
        line_idx=0,
        actor_id="u1",
    )
    bal_a = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_a["id"]}, {"_id": 0}
    )
    bal_b = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_b["id"]}, {"_id": 0}
    )
    assert bal_a["physical_qty"] == 965
    assert bal_b["physical_qty"] == 500

    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"],
    )
    assert catalog["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert catalog.get("warehouse_stock_standalone") is True
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 965
    assert row["qty_tersedia_valid"] is True  # reconciled + no unverified belum


async def test_concurrent_adjust_and_idempotent_replay(db):
    item, branch, wh_a, _wh_b = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-c",
        },
        actor_id="u1",
    )

    async def adj(key, target):
        return await adjust_to_target_standalone(
            db,
            item["id"],
            {
                "branch_id": branch["id"],
                "warehouse_id": wh_a["id"],
                "target_physical_qty": target,
                "reason": f"adj-{target}",
                "idempotency_key": key,
            },
            actor_id="u1",
        )

    # Two different keys racing toward different targets — last writer with CAS retries.
    results = await asyncio.gather(adj("k1", 90), adj("k2", 80), return_exceptions=True)
    oks = [r for r in results if not isinstance(r, Exception)]
    assert len(oks) >= 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_a["id"]}, {"_id": 0}
    )
    assert bal["physical_qty"] in (80, 90)

    # Replay same key
    again = await adj("k1", 90)
    assert again["changed"] is True
    ledgers = await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "k1"})
    assert ledgers == 1


async def test_crash_after_ledger_then_recover(db):
    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 50,
            "idempotency_key": "open-crash",
        },
        actor_id="u1",
    )
    set_fail_after_ledger(True)
    with pytest.raises(Exception):
        await apply_standalone_movement(
            db,
            {
                "master_item_id": item["id"],
                "branch_id": branch["id"],
                "warehouse_id": wh_a["id"],
                "movement_type": "MANUAL_ADJUSTMENT",
                "qty_delta": 5,
                "reference_type": "MANUAL_ADJUSTMENT",
                "reference_id": "crash-1",
                "reason": "crash test",
                "idempotency_key": "crash-key",
                "metadata": {"target_physical_qty": 55},
            },
            actor_id="u1",
        )
    set_fail_after_ledger(False)
    identity = {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
    }
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal.get("pending_event")
    await recover_pending_balance(db, identity)
    bal2 = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal2.get("pending_event") in (None, {})
    assert bal2["physical_qty"] == 55
    # Replay is idempotent
    mov = await apply_standalone_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": "MANUAL_ADJUSTMENT",
            "qty_delta": 5,
            "reference_type": "MANUAL_ADJUSTMENT",
            "reference_id": "crash-1",
            "reason": "crash test",
            "idempotency_key": "crash-key",
            "metadata": {"target_physical_qty": 55},
        },
        actor_id="u1",
    )
    assert mov["balance_after"] == 55
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "crash-key"}) == 1


async def test_opname_partial_status(db):
    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-opn",
        },
        actor_id="u1",
    )
    ghost = {
        "id": "ghost-item",
        "item_code": "GHOST",
        "item_name": "Ghost",
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    # ghost master missing → line fails validation
    session = {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "opname_number": "SO-1",
        "mode": "freeze",
    }
    lines = [
        {
            "line_id": "l1",
            "master_item_id": item["id"],
            "difference_qty": -10,
            "physical_count_qty": 90,
            "book_qty": 100,
            "book_qty_at_posting": 100,
        },
        {
            "line_id": "l2",
            "master_item_id": ghost["id"],
            "difference_qty": 5,
            "physical_count_qty": 5,
            "book_qty": 0,
            "book_qty_at_posting": 0,
        },
    ]
    outcome = await apply_opname_lines_standalone(db, session, lines, actor_id="u1")
    assert outcome["status"] == "PARTIAL"
    assert outcome["posted_count"] == 1
    assert outcome["failed_count"] == 1
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item["id"], "warehouse_id": wh_a["id"]}, {"_id": 0}
    )
    assert bal["physical_qty"] == 90


async def test_service_addon_excluded_from_catalog_physical(db):
    item, branch, wh_a, _ = await _seed(db, code="INV")
    service = {
        "id": f"svc-{uuid.uuid4().hex[:8]}",
        "item_code": "ADDON",
        "item_name": "Warna",
        "unit": "PCS",
        "item_type": "SERVICE",
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(service)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 10,
            "idempotency_key": "open-svc",
        },
        actor_id="u1",
    )
    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    ids = {r["master_item_id"] for r in catalog["items"]}
    assert item["id"] in ids
    assert service["id"] not in ids


async def test_no_auto_copy_company_to_warehouse(db):
    item, branch, wh_a, _ = await _seed(db)
    await db.product_stock_state.insert_one({
        "_id": item["id"],
        "master_item_id": item["id"],
        "total_qty": 9999,
        "initialized": True,
        "status": "INITIALIZED",
    })
    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"],
    )
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    # No opening → book 0 / unreconciled — never 9999 from company
    assert row["physical_qty"] == 0
    assert row["qty_tersedia_valid"] is False
