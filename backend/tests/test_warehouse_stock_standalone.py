"""Warehouse stock standalone — integrity on real MongoDB standalone (no Replica Set).

These tests intentionally target an isolated mongod without --replSet so the
pending-event engine is proven without multi-document transactions.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

# Prefer dedicated standalone mongod (no RS). Override with STANDALONE_MONGO_URL.
# Do NOT mutate process-wide MONGO_URL/DB_NAME at import — that can poison sibling
# xdist workers when this module is collected alongside other suites (see CI note
# on sales_invoice_edit_revision isolation).
STANDALONE_URL = os.environ.get(
    "STANDALONE_MONGO_URL", "mongodb://127.0.0.1:27018"
)
TEST_DB = "eternal_packaging_test_warehouse_standalone"
MONGO_URL = STANDALONE_URL

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    PhysicalStockConflictError,
    PhysicalStockRetryError,
    SALES_DELIVERY_CONFIRMATIONS,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    MODE_WAREHOUSE_STANDALONE,
    adjust_to_target_standalone,
    apply_confirmed_sales_delivery_standalone,
    apply_opname_lines_standalone,
    apply_purchase_receipt_lines_standalone,
    apply_sales_delivery_line_standalone,
    apply_standalone_movement,
    cancel_sales_delivery_document_standalone,
    ensure_receipt_movements_complete,
    is_warehouse_stock_standalone_active,
    post_opening_balance_standalone,
    recover_pending_balance,
    set_fail_after_claim,
    set_fail_after_ledger,
    set_warehouse_stock_standalone_enabled,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    hello = await client.admin.command("hello")
    # Hard requirement: real standalone mongod, not Replica Set.
    assert not hello.get("setName"), (
        f"Integrity tests must run on standalone mongod; got setName={hello.get('setName')!r}. "
        f"Start mongod without --replSet (e.g. port 27018) and set STANDALONE_MONGO_URL."
    )
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    set_fail_after_ledger(False)
    set_fail_after_claim(False)
    try:
        yield database
    finally:
        set_fail_after_ledger(False)
        set_fail_after_claim(False)
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


async def _qty(db, item_id, warehouse_id) -> int:
    bal = await db[BALANCE_COLLECTION].find_one(
        {"master_item_id": item_id, "warehouse_id": warehouse_id}, {"_id": 0},
    )
    return int((bal or {}).get("physical_qty") or 0)


async def test_topology_is_standalone(db):
    hello = await db.client.admin.command("hello")
    assert hello.get("setName") in (None, "")


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
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    catalog = await list_physical_stock_catalog(db)
    assert catalog["stock_read_mode"] == "compatibility"
    assert catalog.get("r2_authority_active") is False


async def test_uat_opening_opname_gr_delivery_isolation(db):
    """A: opening 1000 → GR +40 → delivery -50 → adjust -25 = 965; B stays 500."""
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
    assert await _qty(db, item["id"], wh_a["id"]) == 1040
    assert await _qty(db, item["id"], wh_b["id"]) == 500

    await apply_sales_delivery_line_standalone(
        db,
        master_item_id=item["id"],
        branch_id=branch["id"],
        warehouse_id=wh_a["id"],
        qty=50,
        confirmation_id="conf-1",
        line_idx=0,
        actor_id="u1",
    )
    assert await _qty(db, item["id"], wh_a["id"]) == 990

    await adjust_to_target_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "target_physical_qty": 965,
            "reason": "Opname/Adjustment A -25",
            "idempotency_key": "opname-a",
        },
        actor_id="u1",
    )
    assert await _qty(db, item["id"], wh_a["id"]) == 965
    assert await _qty(db, item["id"], wh_b["id"]) == 500

    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"],
    )
    assert catalog["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert catalog.get("warehouse_stock_standalone") is True
    assert catalog.get("r2_authority_active") is False
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["physical_qty"] == 965
    assert row["qty_tersedia_valid"] is True

    # No dual-write into legacy COMPANY / product_stock_state.
    assert await db.product_stock_state.count_documents({}) == 0


async def test_standalone_receipt_cancel_reverses_once(db):
    from services.warehouse_stock_standalone import cancel_purchase_receipt_standalone

    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-cancel-rcpt",
        },
        actor_id="u1",
    )
    receipt = {
        "id": f"rc-cancel-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "purchase_order_id": "po-cancel",
        "lines": [{
            "line_id": "ln1",
            "master_item_id": item["id"],
            "received_qty": 15,
            "purchase_order_line_id": "pol1",
        }],
    }
    await apply_purchase_receipt_lines_standalone(db, receipt, actor_id="u1")
    assert await _qty(db, item["id"], wh_a["id"]) == 115
    first = await cancel_purchase_receipt_standalone(
        db, receipt, actor_id="u1", reason="batal uji",
    )
    assert first["engine"] == MODE_WAREHOUSE_STANDALONE
    assert len(first["reversals"]) == 1
    assert await _qty(db, item["id"], wh_a["id"]) == 100
    second = await cancel_purchase_receipt_standalone(
        db, receipt, actor_id="u1", reason="batal uji",
    )
    assert second["reversals"] == first["reversals"]
    assert await _qty(db, item["id"], wh_a["id"]) == 100


async def test_active_claim_vs_recovery_interleaving(db):
    """Active claim must not be released by a concurrent recover; lease fences it."""
    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-claim",
        },
        actor_id="u1",
    )
    identity = {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
    }
    set_fail_after_claim(True)
    with pytest.raises(Exception):
        await apply_standalone_movement(
            db,
            {
                "master_item_id": item["id"],
                "branch_id": branch["id"],
                "warehouse_id": wh_a["id"],
                "movement_type": "MANUAL_ADJUSTMENT",
                "qty_delta": 7,
                "reference_type": "MANUAL_ADJUSTMENT",
                "reference_id": "claim-race",
                "reason": "claim race",
                "idempotency_key": "owner-key",
                "metadata": {"target_physical_qty": 107},
            },
            actor_id="u1",
        )
    set_fail_after_claim(False)
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal.get("pending_event")
    assert str(bal["pending_event"].get("idempotency_key")) == "owner-key"
    # No ledger yet — foreign recover must refuse while lease is active.
    with pytest.raises(PhysicalStockRetryError):
        await recover_pending_balance(db, identity, for_idempotency_key="other-key")
    bal2 = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal2.get("pending_event")
    assert await _qty(db, item["id"], wh_a["id"]) == 100

    # Owner may release and retry; then succeeds.
    await recover_pending_balance(db, identity, for_idempotency_key="owner-key")
    mov = await apply_standalone_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": "MANUAL_ADJUSTMENT",
            "qty_delta": 7,
            "reference_type": "MANUAL_ADJUSTMENT",
            "reference_id": "claim-race",
            "reason": "claim race",
            "idempotency_key": "owner-key",
            "metadata": {"target_physical_qty": 107},
        },
        actor_id="u1",
    )
    assert mov["balance_after"] == 107
    assert await _qty(db, item["id"], wh_a["id"]) == 107


async def test_crash_after_ledger_then_recover_and_retry_finalizes(db):
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
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "crash-key"}) == 1
    # Balance still stale until finalize.
    assert bal["physical_qty"] == 50

    # Retry with same key must finalize — not return success with leftover balance.
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
    bal2 = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal2.get("pending_event") in (None, {})
    assert bal2["physical_qty"] == 55
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "crash-key"}) == 1


async def test_gr_multiline_partial_then_retry(db):
    """Receipt exists with only some line ledgers — same idempotency retry completes rest."""
    item, branch, wh_a, _ = await _seed(db)
    item2 = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "SKU-2",
        "item_name": "Barang 2",
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item2))
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 10,
            "idempotency_key": "open-gr-1",
        },
        actor_id="u1",
    )
    await post_opening_balance_standalone(
        db,
        item2["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 20,
            "idempotency_key": "open-gr-2",
        },
        actor_id="u1",
    )
    receipt = {
        "id": f"rc-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "purchase_order_id": "po-gr",
        "lines": [
            {
                "line_id": "ln1",
                "master_item_id": item["id"],
                "received_qty": 5,
                "purchase_order_line_id": "pol1",
            },
            {
                "line_id": "ln2",
                "master_item_id": item2["id"],
                "received_qty": 3,
                "purchase_order_line_id": "pol2",
            },
        ],
        "stock_posting": {"status": "PARTIAL", "posted_count": 1, "failed_count": 1},
    }
    await db["purchase_goods_receipts"].insert_one(dict(receipt))
    # Simulate crash after first line ledger only.
    await apply_standalone_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": "PURCHASE_RECEIPT",
            "qty_delta": 5,
            "reference_type": "purchase_goods_receipt",
            "reference_id": f"{receipt['id']}:ln1",
            "reason": "Penerimaan pembelian",
            "idempotency_key": f"purchase-receipt:{receipt['id']}:ln1",
            "metadata": {
                "receipt_id": receipt["id"],
                "line_id": "ln1",
                "purchase_order_id": "po-gr",
                "purchase_order_line_id": "pol1",
            },
        },
        actor_id="u1",
    )
    assert await _qty(db, item["id"], wh_a["id"]) == 15
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt['id']}:ln2"}
    ) == 0

    # Idempotent ensure must NOT assume receipt row means stock is complete.
    retry = await ensure_receipt_movements_complete(db, receipt, actor_id="u1")
    assert retry["status"] == "COMPLETE"
    assert await _qty(db, item["id"], wh_a["id"]) == 15
    assert await _qty(db, item2["id"], wh_a["id"]) == 23
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt['id']}:ln1"}
    ) == 1
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt['id']}:ln2"}
    ) == 1
    posting = await db["purchase_goods_receipts"].find_one(
        {"id": receipt["id"]}, {"_id": 0, "stock_posting": 1},
    )
    assert posting["stock_posting"]["status"] == "COMPLETE"


async def test_adjustment_race_with_balance_change(db):
    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-adj",
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

    results = await asyncio.gather(adj("k1", 90), adj("k2", 80), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    oks = [r for r in results if not isinstance(r, Exception)]
    # At least one succeeds; loser retries or conflicts without applying stale delta.
    assert len(oks) >= 1
    bal = await _qty(db, item["id"], wh_a["id"])
    assert bal in (80, 90)
    # Winner lands exactly on its target (atomic claim-time delta).
    for ok in oks:
        assert ok["physical_qty"] == ok["target_physical_qty"]
    # Stale snapshot must not leave a non-target balance.
    assert bal in {r["target_physical_qty"] for r in oks} or isinstance(
        errors[0] if errors else None, (PhysicalStockConflictError, PhysicalStockRetryError)
    )

    again = await adj("k1", 90)
    assert again["changed"] is True
    assert await db[LEDGER_COLLECTION].count_documents({"idempotency_key": "k1"}) == 1


async def test_opname_partial_then_resume(db):
    item, branch, wh_a, _ = await _seed(db)
    item2 = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "SKU-OP2",
        "item_name": "Barang OP2",
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item2))
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
    await post_opening_balance_standalone(
        db,
        item2["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 50,
            "idempotency_key": "open-opn-2",
        },
        actor_id="u1",
    )
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
            "master_item_id": "ghost-missing",
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
    assert await _qty(db, item["id"], wh_a["id"]) == 90

    # Mark line statuses as a PARTIAL session would, then resume.
    lines[0]["post_status"] = "POSTED"
    lines[0]["movement_id"] = outcome["lines"][0]["movement"]["id"]
    lines[1]["post_status"] = "FAILED"
    lines[1]["master_item_id"] = item2["id"]
    lines[1]["difference_qty"] = -5
    lines[1]["physical_count_qty"] = 45
    lines[1]["book_qty"] = 50
    lines[1]["book_qty_at_posting"] = 50

    resumed = await apply_opname_lines_standalone(
        db, session, lines, actor_id="u1", resume=True,
    )
    assert resumed["status"] == "CONFIRMED"
    assert any(r.get("skipped_resume") for r in resumed["lines"])
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"stock-opname:{session['id']}:l1"}
    ) == 1
    assert await _qty(db, item["id"], wh_a["id"]) == 90
    assert await _qty(db, item2["id"], wh_a["id"]) == 45


async def test_http_confirm_delivery_and_retry_and_cancel(db):
    """Exercise durable delivery apply + idempotent retry + cancel (not helper-only)."""
    item, branch, wh_a, wh_b = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 100,
            "idempotency_key": "open-del",
        },
        actor_id="u1",
    )
    # Mark warehouse as default so location resolver works without session.
    await db[WAREHOUSES_COLLECTION].update_one(
        {"id": wh_a["id"]}, {"$set": {"is_default": True, "code": "MAIN"}},
    )
    order = {
        "id": f"ord-{uuid.uuid4().hex[:8]}",
        "cancelled": False,
        "line_items": [{
            "master_item_id": item["id"],
            "line_id": "oli-1",
            "quantity": 100,
            "production_process": "non_print",
            "name": "Barang",
        }],
    }
    await db.orders.insert_one(dict(order))
    confirmation_id = f"conf-{uuid.uuid4().hex[:8]}"
    sj_id = f"sj-{uuid.uuid4().hex[:8]}"
    lines = [{"line_idx": 0, "qty": 25, "master_item_id": item["id"], "line_id": "oli-1"}]

    first = await apply_confirmed_sales_delivery_standalone(
        db,
        order=order,
        confirmation_id=confirmation_id,
        lines=lines,
        actor_id="u1",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
        reason="Konfirmasi terkirim",
        sales_delivery_id=sj_id,
    )
    assert first["engine"] == MODE_WAREHOUSE_STANDALONE
    assert first["replayed"] is False
    assert len(first["movements"]) == 1
    assert await _qty(db, item["id"], wh_a["id"]) == 75
    conf = await db[SALES_DELIVERY_CONFIRMATIONS].find_one({"id": confirmation_id}, {"_id": 0})
    assert conf is not None
    assert conf.get("stock_posting", {}).get("status") == "COMPLETE"

    # Idempotent HTTP retry — same confirmation, no double debit.
    retry = await apply_confirmed_sales_delivery_standalone(
        db,
        order=order,
        confirmation_id=confirmation_id,
        lines=lines,
        actor_id="u1",
        warehouse_id=wh_a["id"],
        branch_id=branch["id"],
        reason="Konfirmasi terkirim",
        sales_delivery_id=sj_id,
    )
    assert retry["replayed"] is True
    assert await _qty(db, item["id"], wh_a["id"]) == 75
    assert await db[LEDGER_COLLECTION].count_documents(
        {"metadata.confirmation_id": confirmation_id, "movement_type": "SALES_DELIVERY"}
    ) == 1

    # Cancel / reverse delivery restores stock.
    await db.sales_deliveries.insert_one({
        "id": sj_id,
        "sj_number": "SJ-TEST-1",
        "order_id": order["id"],
        "po_id": order["id"],
        "is_cancelled": False,
        "status": "TERKIRIM",
    })
    cancelled = await cancel_sales_delivery_document_standalone(
        db,
        {"id": sj_id, "sj_number": "SJ-TEST-1", "order_id": order["id"], "po_id": order["id"]},
        actor_id="u1",
        reason="Pembatalan Surat Jalan",
    )
    assert cancelled["soft_cancelled"] is True
    assert cancelled["engine"] == MODE_WAREHOUSE_STANDALONE
    assert await _qty(db, item["id"], wh_a["id"]) == 100
    conf2 = await db[SALES_DELIVERY_CONFIRMATIONS].find_one({"id": confirmation_id}, {"_id": 0})
    assert conf2.get("reversed") is True
    # B untouched throughout.
    assert await _qty(db, item["id"], wh_b["id"]) == 0


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
    assert row["physical_qty"] == 0
    assert row["qty_tersedia_valid"] is False


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


async def test_stale_lease_allows_foreign_recovery(db):
    item, branch, wh_a, _ = await _seed(db)
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 10,
            "idempotency_key": "open-stale",
        },
        actor_id="u1",
    )
    identity = {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
    }
    stale = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    await db[BALANCE_COLLECTION].update_one(
        identity,
        {"$set": {
            "pending_event": {
                "event_id": "standalone:stale-key",
                "idempotency_key": "stale-key",
                "qty_delta": 3,
                "claimed_at": stale,
                "lease_expires_at": stale,
            }
        }},
    )
    await recover_pending_balance(db, identity, for_idempotency_key="other")
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    assert bal.get("pending_event") in (None, {})


async def test_standalone_on_blocks_legacy_pss_dual_write(db):
    """R2 OFF + standalone ON + physical_stock.enabled → no PSS dual-write."""
    from services.physical_stock_engine import (
        handle_delivery_confirmed,
        handle_goods_receipt_created,
        handle_loading_confirmed,
        handle_manual_receipt_created,
        handle_operator_progress,
        handle_packing_completed,
        handle_purchase_order_created,
        handle_sales_delivery_created,
        is_operator_cetak_physical_stock_active,
        legacy_pss_company_stock_writes_enabled,
        legacy_pss_stock_out_writes_enabled,
        should_claim_delivery_confirmed_stock,
        should_claim_loading_stock,
        should_claim_packing_stock,
        should_claim_sales_delivery_stock,
    )
    from services.product_stock_state import initialize_opening_balance, get_product_stock_state
    from services.physical_stock_settings import update_physical_stock_settings

    item, branch, wh_a, _ = await _seed(db)
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    await update_physical_stock_settings(
        db,
        {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "delivery_confirmed",
        },
        user={"email": "t@test"},
    )
    await initialize_opening_balance(
        db, item["id"],
        {"unprinted_qty": 100.0, "printed_qty": 50.0, "base_uom": "PCS"},
        user={"email": "t@test"},
    )
    pss_before = await get_product_stock_state(db, item["id"])
    legacy_before = await db.stock_movements.count_documents({})

    assert await legacy_pss_stock_out_writes_enabled(db) is False
    assert await legacy_pss_company_stock_writes_enabled(db) is False

    order = {
        "id": f"so-{uuid.uuid4().hex[:8]}",
        "po_number": "PO-SA-DW",
        "cancelled": False,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "delivery_confirmed",
        },
        "line_items": [{
            "line_id": "ln-0",
            "master_item_id": item["id"],
            "quantity": 100,
            "production_process": "print",
            "unit": "PCS",
            "name": "Barang",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 50, "operator_name": "Op"}],
    }
    assert await is_operator_cetak_physical_stock_active(db, order, live_enabled=True) is False
    assert await should_claim_packing_stock(
        db, {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "packing_completed"}},
    ) is False
    assert await should_claim_loading_stock(
        db, {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "loading_confirmation"}},
    ) is False
    assert await should_claim_sales_delivery_stock(
        db, {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "sales_delivery"}},
    ) is False
    assert await should_claim_delivery_confirmed_stock(db, order) is False

    assert await handle_packing_completed(
        db,
        {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "packing_completed"}},
        [{"line_idx": 0, "qty": 10, "action_id": "pack-1"}],
        {"email": "t"},
    ) == []
    assert await handle_loading_confirmed(
        db,
        {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "loading_confirmation"}},
        {"id": "load-1", "lines": [{"line_idx": 0, "qty": 10}]},
        {"email": "t"},
    ) == []
    assert await handle_sales_delivery_created(
        db,
        {**order, "physical_stock_snapshot": {**order["physical_stock_snapshot"], "stock_out_trigger": "sales_delivery"}},
        {"id": "sj-1", "line_items": [{"line_idx": 0, "master_item_id": item["id"], "qty": 10, "production_process": "print"}]},
        {"email": "t"},
    ) == []
    assert await handle_delivery_confirmed(
        db, order, {"id": "conf-1", "lines": [{"line_idx": 0, "qty": 10}]}, {"email": "t"},
    ) == []
    assert await handle_operator_progress(
        db, order, {"line_idx": 0, "operator_name": "Op"},
        increment=10, action_id="cetak-1", live_enabled=True,
    ) is None

    po = {
        "id": f"po-{uuid.uuid4().hex[:8]}",
        "po_number": "PO-IN",
        "supplier": "SUP",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "purchase_order",
        },
        "lines": [{
            "line_id": "pol1",
            "line_idx": 0,
            "master_item_id": item["id"],
            "ordered_qty": 5,
            "unit": "PCS",
        }],
    }
    assert await handle_purchase_order_created(db, po, {"email": "t"}) == []
    await db.purchase_orders.insert_one({
        "id": "po-gr-dw",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
        },
    })
    assert await handle_goods_receipt_created(
        db,
        {
            "id": "rc-dw",
            "purchase_order_id": "po-gr-dw",
            "receipt_number": "GR-1",
            "lines": [{"line_id": "l1", "master_item_id": item["id"], "received_qty": 5, "unit": "PCS"}],
        },
        {"email": "t"},
    ) == []
    assert await handle_manual_receipt_created(
        db,
        {
            "accurate_receipt_id": "man-1",
            "receipt_number": "MR-1",
            "supplier": "SUP",
            "lines": [{"line_id": "l1", "master_item_id": item["id"], "received_qty": 5, "unit": "PCS"}],
        },
        {"email": "t"},
    ) == []

    pss_after = await get_product_stock_state(db, item["id"])
    assert float(pss_after["total_qty"]) == float(pss_before["total_qty"])
    assert float(pss_after["unprinted_qty"]) == float(pss_before["unprinted_qty"])
    assert float(pss_after["printed_qty"]) == float(pss_before["printed_qty"])
    assert await db.stock_movements.count_documents({}) == legacy_before
    # Warehouse path still authoritative under standalone.
    await post_opening_balance_standalone(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "physical_qty": 7,
            "idempotency_key": "open-dw-auth",
        },
        actor_id="u1",
    )
    assert await _qty(db, item["id"], wh_a["id"]) == 7


async def test_standalone_off_keeps_legacy_pss_gates(db):
    """R2 OFF + standalone OFF → legacy PSS stock-out gates remain active."""
    from services.physical_stock_engine import (
        legacy_pss_company_stock_writes_enabled,
        legacy_pss_stock_out_writes_enabled,
        should_claim_delivery_confirmed_stock,
        should_claim_packing_stock,
    )

    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    assert await is_warehouse_stock_standalone_active(db) is False
    assert await legacy_pss_stock_out_writes_enabled(db) is True
    assert await legacy_pss_company_stock_writes_enabled(db) is True
    order = {
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "packing_completed",
        },
    }
    assert await should_claim_packing_stock(db, order) is True
    order_d = {
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "delivery_confirmed",
        },
    }
    assert await should_claim_delivery_confirmed_stock(db, order_d) is True


async def test_create_gr_idempotency_resumes_partial_standalone(db):
    """create_purchase_goods_receipt early-return must finish PARTIAL movements."""
    from services.purchase_goods_receipts import create_purchase_goods_receipt
    from services.purchase_orders import create_purchase_order

    item, branch, wh_a, _ = await _seed(db)
    item2 = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": "SKU-GR2",
        "item_name": "Barang GR2",
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item2))
    await db[WAREHOUSES_COLLECTION].update_one(
        {"id": wh_a["id"]}, {"$set": {"is_default": True, "code": "MAIN"}},
    )
    for mid, key, qty in (
        (item["id"], "open-create-gr-1", 10),
        (item2["id"], "open-create-gr-2", 20),
    ):
        await post_opening_balance_standalone(
            db, mid,
            {
                "branch_id": branch["id"],
                "warehouse_id": wh_a["id"],
                "physical_qty": qty,
                "idempotency_key": key,
            },
            actor_id="u1",
        )

    user = {"id": "admin-1", "email": "admin@test.local", "name": "Admin", "role": "admin"}
    po = await create_purchase_order(db, {
        "supplier": "Supplier SA",
        "lines": [
            {
                "master_item_id": item["id"],
                "item_name": "Barang A",
                "item_code": "SKU-A",
                "unit": "PCS",
                "ordered_qty": 5,
                "unit_price": 1000,
            },
            {
                "master_item_id": item2["id"],
                "item_name": "Barang B",
                "item_code": "SKU-GR2",
                "unit": "PCS",
                "ordered_qty": 3,
                "unit_price": 1000,
            },
        ],
        "idempotency_key": f"po-create-gr-{uuid.uuid4().hex[:8]}",
    }, user)
    pol1 = po["lines"][0]["line_id"]
    pol2 = po["lines"][1]["line_id"]
    idem = f"gr-partial-{uuid.uuid4().hex[:8]}"
    receipt_id = f"rc-{uuid.uuid4().hex[:8]}"
    receipt = {
        "id": receipt_id,
        "idempotency_key": idem,
        "purchase_order_id": po["id"],
        "po_number": po.get("po_number") or "PO-X",
        "branch_id": branch["id"],
        "warehouse_id": wh_a["id"],
        "warehouse_standalone": True,
        "r2_physical": False,
        "lines": [
            {
                "line_id": "ln1",
                "line_idx": 0,
                "purchase_order_line_id": pol1,
                "master_item_id": item["id"],
                "received_qty": 5,
                "unit": "PCS",
                "item_name": "Barang A",
                "item_code": "SKU-A",
                "unit_price": 1000,
            },
            {
                "line_id": "ln2",
                "line_idx": 1,
                "purchase_order_line_id": pol2,
                "master_item_id": item2["id"],
                "received_qty": 3,
                "unit": "PCS",
                "item_name": "Barang B",
                "item_code": "SKU-GR2",
                "unit_price": 1000,
            },
        ],
        "stock_posting": {"status": "PARTIAL", "posted_count": 1, "failed_count": 1},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user,
    }
    await db["purchase_goods_receipts"].create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await db["purchase_goods_receipts"].insert_one(dict(receipt))
    await apply_standalone_movement(
        db,
        {
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh_a["id"],
            "movement_type": "PURCHASE_RECEIPT",
            "qty_delta": 5,
            "reference_type": "purchase_goods_receipt",
            "reference_id": f"{receipt_id}:ln1",
            "reason": "Penerimaan pembelian",
            "idempotency_key": f"purchase-receipt:{receipt_id}:ln1",
            "metadata": {
                "receipt_id": receipt_id,
                "line_id": "ln1",
                "purchase_order_id": po["id"],
                "purchase_order_line_id": pol1,
            },
        },
        actor_id="u1",
    )
    assert await _qty(db, item["id"], wh_a["id"]) == 15
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt_id}:ln2"}
    ) == 0

    # Main create path (not ensure_receipt_movements_complete alone).
    retry = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wh_a["id"],
        "lines": [
            {"purchase_order_line_id": pol1, "received_qty": 5},
            {"purchase_order_line_id": pol2, "received_qty": 3},
        ],
        "idempotency_key": idem,
    }, user)
    assert retry["id"] == receipt_id
    assert str(retry.get("purchase_order_id")) == po["id"]
    assert str(retry.get("warehouse_id")) == wh_a["id"]
    assert await _qty(db, item["id"], wh_a["id"]) == 15
    assert await _qty(db, item2["id"], wh_a["id"]) == 23
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt_id}:ln1"}
    ) == 1
    assert await db[LEDGER_COLLECTION].count_documents(
        {"idempotency_key": f"purchase-receipt:{receipt_id}:ln2"}
    ) == 1
    posting = await db["purchase_goods_receipts"].find_one(
        {"id": receipt_id}, {"_id": 0, "stock_posting": 1},
    )
    assert posting["stock_posting"]["status"] == "COMPLETE"
    # Second retry must not double-post movements.
    again = await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "warehouse_id": wh_a["id"],
        "lines": [
            {"purchase_order_line_id": pol1, "received_qty": 5},
            {"purchase_order_line_id": pol2, "received_qty": 3},
        ],
        "idempotency_key": idem,
    }, user)
    assert again["id"] == receipt_id
    assert await _qty(db, item["id"], wh_a["id"]) == 15
    assert await _qty(db, item2["id"], wh_a["id"]) == 23
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": "PURCHASE_RECEIPT", "warehouse_id": wh_a["id"]}
    ) == 2
