"""Gate 3 / D1-A — standalone SSI ATP commitment uses ledger line attribution.

T1–T14 acceptance matrix (local UAT only). Numbers labeled SIMULATED are
fixtures inspired by prod recon 100032/100001 — not live production reads.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_atp_d1a"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    adjust_physical_stock_to_target,
    confirm_sales_delivery,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_saat_ini_excel import (  # noqa: E402
    REASON_WIP_COMPOSITION_UNPROVEN,
    aggregate_active_so_commitment,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    apply_confirmed_sales_delivery_standalone,
    reverse_confirmed_sales_delivery_standalone,
    set_warehouse_stock_standalone_enabled,
)

TEST_DB = "eternal_packaging_test_ssi_atp_d1a"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    # Production-like: standalone ON, R2 OFF
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db, *, code: str = "BOX-ATP", stock: float = 10_000):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": f"Item {code}",
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
        "code": "MAIN",
        "name": "Utama",
        "active": True,
        "is_operational": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one({**wh, "is_default": True})
    if stock != 0:
        await adjust_physical_stock_to_target(
            db,
            item["id"],
            {
                "branch_id": branch["id"],
                "warehouse_id": wh["id"],
                "target_physical_qty": stock,
                "reason": "UAT seed",
                "idempotency_key": f"seed-{item['id']}",
            },
            actor_id="t",
        )
    return item, branch, wh


async def _deliver(db, *, order: dict, branch_id: str, wh_id: str, lines: list, conf=None):
    """Post standalone SALES_DELIVERY for commitment UAT (not R2 confirm)."""
    confirmation_id = conf or f"conf-{uuid.uuid4().hex[:8]}"
    return await apply_confirmed_sales_delivery_standalone(
        db,
        order=order,
        confirmation_id=confirmation_id,
        lines=lines,
        actor_id="t",
        warehouse_id=wh_id,
        branch_id=branch_id,
        reason="Konfirmasi terkirim",
        sales_delivery_id=f"sj-{uuid.uuid4().hex[:8]}",
    ), confirmation_id


async def _restock(db, item_id: str, branch_id: str, wh_id: str, qty: float, key: str):
    await adjust_physical_stock_to_target(
        db,
        item_id,
        {
            "branch_id": branch_id,
            "warehouse_id": wh_id,
            "target_physical_qty": qty,
            "reason": "restock",
            "idempotency_key": key,
        },
        actor_id="t",
    )


async def _catalog_row(db, branch_id: str, wh_id: str, item_id: str, q: str):
    catalog = await list_physical_stock_catalog(
        db, branch_id=branch_id, warehouse_id=wh_id, q=q,
    )
    assert catalog.get("warehouse_stock_standalone") is True
    assert catalog.get("r2_authority_active") is False
    return next(r for r in catalog["items"] if r["master_item_id"] == item_id)


# --- T1 pending / partial / full ---

async def test_t1_partial_delivery_reduces_only_matching_line(db):
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-t1-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-t1",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    await _deliver(
        db, order=so, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 2_000, "master_item_id": item["id"], "line_id": "ln-t1"}], conf=f"conf-t1-{uuid.uuid4().hex[:6]}",
    )
    # Restock book so Stock stays readable; commitment is independent
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-t1-{item['id']}",
        },
        actor_id="t",
    )
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 3_000
    assert row["stock"] == 10_000
    assert row["stock_dapat_dijual"] == 7_000


async def test_t1b_pending_full_commitment(db):
    item, branch, wh = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-pend-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": item["id"],
            "quantity": 4_000,
            "production_process": "non_print",
        }],
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 4_000
    assert row["stock_dapat_dijual"] == 6_000


async def test_t1c_fully_delivered_zero_commitment(db):
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-full-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-f",
            "master_item_id": item["id"],
            "quantity": 1_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    await _deliver(
        db, order=so, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 1_000, "master_item_id": item["id"], "line_id": "ln-f"}], conf=f"conf-f-{uuid.uuid4().hex[:6]}",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-f-{item['id']}",
        },
        actor_id="t",
    )
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 0
    assert row["stock_dapat_dijual"] == 10_000


# --- T2 cross-SO isolation ---

async def test_t2_delivery_on_so_a_does_not_reduce_so_b(db):
    item, branch, wh = await _seed(db)
    so_a = {
        "id": f"so-a-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-a",
            "master_item_id": item["id"],
            "quantity": 3_000,
            "production_process": "non_print",
        }],
    }
    so_b = {
        "id": f"so-b-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-b",
            "master_item_id": item["id"],
            "quantity": 7_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_many([dict(so_a), dict(so_b)])
    await _deliver(
        db, order=so_a, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 3_000, "master_item_id": item["id"], "line_id": "ln-a"}], conf=f"conf-a-{uuid.uuid4().hex[:6]}",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-t2-{item['id']}",
        },
        actor_id="t",
    )
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 7_000
    assert row["stock_dapat_dijual"] == 3_000


# --- T3 cancelled ---

async def test_t3_cancelled_so_zero_and_no_cross_hit(db):
    item, branch, wh = await _seed(db)
    so_a = {
        "id": f"so-ca-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-ca",
            "master_item_id": item["id"],
            "quantity": 4_000,
            "production_process": "non_print",
        }],
    }
    so_b = {
        "id": f"so-cb-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-cb",
            "master_item_id": item["id"],
            "quantity": 10_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_many([dict(so_a), dict(so_b)])
    await _deliver(
        db, order=so_a, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 4_000, "master_item_id": item["id"], "line_id": "ln-ca"}], conf=f"conf-ca-{uuid.uuid4().hex[:6]}",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-t3-{item['id']}",
        },
        actor_id="t",
    )
    await db.orders.update_one({"id": so_a["id"]}, {"$set": {"cancelled": True}})
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 10_000
    assert row["stock"] == 10_000
    assert row["stock_dapat_dijual"] == 0


# --- T4 cup + lid ---

async def test_t4_cup_lid_only_cup_reduces_cup_master(db):
    item, branch, wh = await _seed(db)
    lid = {
        "id": f"lid-{uuid.uuid4().hex[:8]}",
        "item_code": "LID-ATP",
        "item_name": "Lid",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(lid))
    so = {
        "id": f"so-cl-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [
            {
                "line_id": "ln-cup",
                "master_item_id": item["id"],
                "quantity": 2_000,
                "production_process": "non_print",
            },
            {
                "line_id": "ln-lid",
                "master_item_id": lid["id"],
                "quantity": 2_000,
                "production_process": "non_print",
            },
        ],
    }
    await db.orders.insert_one(dict(so))
    await _deliver(
        db, order=so, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 500, "master_item_id": item["id"], "line_id": "ln-cup"}], conf=f"conf-cl-{uuid.uuid4().hex[:6]}",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-t4-{item['id']}",
        },
        actor_id="t",
    )
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 1_500


# --- T5 Add On / SERVICE ---

async def test_t5_addon_warna_does_not_multiply_parent_commitment(db):
    item, branch, wh = await _seed(db)
    svc = {
        "id": f"svc-{uuid.uuid4().hex[:8]}",
        "item_code": "ADDON-W",
        "item_name": "Add On Warna Sablon",
        "item_type": "SERVICE",
        "unit": "PCS",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(svc))
    await db.orders.insert_one({
        "id": f"so-ao-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": item["id"],
                "quantity": 1_000,
                "production_process": "print",
                "item_type": "INVENTORY",
            },
            {
                "line_id": "ln-w1",
                "master_item_id": svc["id"],
                "parent_line_id": "ln-p",
                "quantity": 1_000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
            {
                "line_id": "ln-w2",
                "master_item_id": svc["id"],
                "parent_line_id": "ln-p",
                "quantity": 1_000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "quantity": 1_000, "qty_completed": 1_000},
            {"line_idx": 1, "quantity": 1_000, "qty_completed": 1_000},
            {"line_idx": 2, "quantity": 1_000, "qty_completed": 1_000},
        ],
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 1_000
    assert row["stock_dapat_dijual"] == 9_000


# --- T6 / T7 fail closed ---

async def test_t6_ledger_without_order_id_fail_closed(db):
    item, branch, wh = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-fc-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-fc",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "production_process": "non_print",
        }],
    })
    # Orphan ledger row — no order_id
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"led-{uuid.uuid4().hex[:8]}",
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "qty_delta": -2_000,
        "metadata": {},
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 5_000


async def test_t7_ambiguous_multi_line_same_master_without_line_id_fail_closed(db):
    """Pure aggregate: delivery with order+master only cannot split across 2 lines."""
    mid = "M-AMB"
    oid = "SO-AMB"
    active = [
        {"order_id": oid, "master_item_id": mid, "ordered_qty": 1000, "line_id": "ln-0", "line_idx": 0},
        {"order_id": oid, "master_item_id": mid, "ordered_qty": 2000, "line_id": "ln-1", "line_idx": 1},
    ]
    # No line_id / line_idx on delivery → fail closed
    delivered = [{"order_id": oid, "master_item_id": mid, "qty": 500}]
    out = aggregate_active_so_commitment(
        active_lines=active, attributed_deliveries=delivered,
    )
    assert out.get(mid) == 3_000


# --- T8 reverse restores once ---

async def test_t8_reversal_restores_commitment_exactly_once(db):
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-rv-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-rv",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    conf = f"conf-rv-{uuid.uuid4().hex[:6]}"
    await _deliver(
        db, order=so, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": 1_500, "master_item_id": item["id"], "line_id": "ln-rv"}], conf=conf,
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock1",
            "idempotency_key": f"rs-rv1-{item['id']}",
        },
        actor_id="t",
    )
    row1 = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row1["open_so_commitment"] == 3_500

    await reverse_confirmed_sales_delivery_standalone(
        db, confirmation_id=conf, actor_id="t", reason="batal",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock2",
            "idempotency_key": f"rs-rv2-{item['id']}",
        },
        actor_id="t",
    )
    row2 = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row2["open_so_commitment"] == 5_000
    assert await db[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
    }) == 1


# --- T9 duplicate identity: two ledger rows same order/line both count qty (real two deliveries);
#     reversed duplicate must not double-restore. Covered by T8 + orphan T14. ---

async def test_t9_two_partial_ledger_rows_same_line_sum_once_each(db):
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-dup-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-d",
            "master_item_id": item["id"],
            "quantity": 10_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    for i, qty in enumerate((3_000, 2_000)):
        await _deliver(
        db, order=so, branch_id=branch["id"], wh_id=wh["id"],
        lines=[{"line_idx": 0, "qty": qty, "master_item_id": item["id"], "line_id": "ln-d"}], conf=f"conf-d{i}-{uuid.uuid4().hex[:6]}",
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-t9-{item['id']}",
        },
        actor_id="t",
    )
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 5_000


# --- T10 standalone == ledger aggregate; SIMULATED 100032/100001 ---

async def test_t10_simulated_100032_helper_remainders(db):
    """SIMULATED (not live prod): 100032-shaped active lines + ledger attr → 19350."""
    mid = "a88fb167-sim-100032"
    active = [
        {"order_id": "c1a77004", "master_item_id": mid, "ordered_qty": 10000, "line_idx": 0},
        {"order_id": "2b1fa246", "master_item_id": mid, "ordered_qty": 5000, "line_idx": 0},
        {"order_id": "9881decc", "master_item_id": mid, "ordered_qty": 3000, "line_idx": 0},
        {"order_id": "e3f8bffc", "master_item_id": mid, "ordered_qty": 3000, "line_idx": 0},
        {"order_id": "90d493ca", "master_item_id": mid, "ordered_qty": 1500, "line_idx": 0},
        {"order_id": "96b10c97", "master_item_id": mid, "ordered_qty": 1000, "line_idx": 0},
        {"order_id": "8ac9b109", "master_item_id": mid, "ordered_qty": 500, "line_idx": 0},
        {"order_id": "8706d3d3", "master_item_id": mid, "ordered_qty": 300, "line_idx": 0},
        {"order_id": "20974294", "master_item_id": mid, "ordered_qty": 50, "line_idx": 2},
    ]
    # Only active-matched delivery (orphan 3300 excluded)
    delivered = [
        {"order_id": "2b1fa246", "master_item_id": mid, "qty": 5000, "line_idx": 0},
    ]
    out = aggregate_active_so_commitment(
        active_lines=active, attributed_deliveries=delivered,
    )
    assert out[mid] == 19_350
    # Orphans must not reduce
    orphans = [
        {"order_id": "86b5d770", "master_item_id": mid, "qty": 1000, "line_idx": 0},
        {"order_id": "bf0e0047", "master_item_id": mid, "qty": 1000, "line_idx": 0},
    ]
    out2 = aggregate_active_so_commitment(
        active_lines=active, attributed_deliveries=delivered + orphans,
    )
    assert out2[mid] == 19_350


async def test_t10b_simulated_100001_helper_remainders(db):
    """SIMULATED: 100001 partial/full classes → 192103."""
    mid = "958bb5e9-sim-100001"
    active = [
        {"order_id": "0f916f1b", "master_item_id": mid, "ordered_qty": 10000, "line_idx": 0},
        {"order_id": "0f916f1b", "master_item_id": mid, "ordered_qty": 23, "line_idx": 2},
        {"order_id": "1317f659", "master_item_id": mid, "ordered_qty": 10000, "line_idx": 0},
        {"order_id": "f2e75ccd", "master_item_id": mid, "ordered_qty": 5000, "line_idx": 0},
        {"order_id": "557d7278", "master_item_id": mid, "ordered_qty": 5000, "line_idx": 0},
    ]
    # Pad undelivered to match helper undelivered bucket 177103
    # remain from above after deliv: 5000+0+8000+2000+0 = 15000; need +177103 = 192103
    active.append({
        "order_id": "undeliv-pad",
        "master_item_id": mid,
        "ordered_qty": 177_103,
        "line_idx": 0,
    })
    delivered = [
        {"order_id": "0f916f1b", "master_item_id": mid, "qty": 5000, "line_idx": 0},
        {"order_id": "0f916f1b", "master_item_id": mid, "qty": 23, "line_idx": 2},
        {"order_id": "1317f659", "master_item_id": mid, "qty": 2000, "line_idx": 0},
        {"order_id": "f2e75ccd", "master_item_id": mid, "qty": 3000, "line_idx": 0},
        {"order_id": "557d7278", "master_item_id": mid, "qty": 5000, "line_idx": 0},
    ]
    out = aggregate_active_so_commitment(
        active_lines=active, attributed_deliveries=delivered,
    )
    assert out[mid] == 192_103


# --- T11 Polos UNVERIFIED preserved ---

async def test_t11_incomplete_print_polos_unverified_standalone(db):
    item, branch, wh = await _seed(db)
    await db.orders.insert_one({
        "id": f"so-pol-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-pol",
            "master_item_id": item["id"],
            "quantity": 5_000,
            "production_process": "print",
        }],
        "operator_splits": [
            {"line_idx": 0, "quantity": 5_000, "qty_completed": 2_000},
        ],
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["qty_polos"] is None
    assert row["qty_polos_status"] == "UNVERIFIED"
    assert row["qty_polos_reason"] == REASON_WIP_COMPOSITION_UNPROVEN
    assert row["open_so_commitment"] == 5_000


# --- T12 stock book unchanged by commitment path ---

async def test_t12_negative_stock_and_unreconciled_preserved(db):
    item, branch, wh = await _seed(db, stock=0)
    # Force negative book via balance stamp + baseline (no inventing opening)
    await db[BALANCE_COLLECTION].update_one(
        {"master_item_id": item["id"], "warehouse_id": wh["id"]},
        {"$set": {
            "physical_qty": -8_300,
            "baseline_status": "unreconciled",
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "master_item_id": item["id"],
        }},
        upsert=True,
    )
    await db.orders.insert_one({
        "id": f"so-neg-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-n",
            "master_item_id": item["id"],
            "quantity": 14_000,
            "production_process": "non_print",
        }],
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["stock"] == -8_300
    assert row["baseline_status"] == "unreconciled"
    assert row["open_so_commitment"] == 14_000
    assert row["stock_dapat_dijual"] == -22_300


# --- T13 R2 path still ledger (regression) ---

async def test_t13_r2_mode_still_ledger_attribution(db):
    await set_r2_authority_active(db, active=True)
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    item, branch, wh = await _seed(db)
    so = {
        "id": f"so-r2-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-r2",
            "master_item_id": item["id"],
            "quantity": 4_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(so))
    await confirm_sales_delivery(
        db,
        order_id=so["id"],
        confirmation_id=f"conf-r2-{uuid.uuid4().hex[:6]}",
        lines=[{"line_idx": 0, "qty": 1_000, "master_item_id": item["id"], "line_id": "ln-r2"}],
        actor_id="t",
        warehouse_id=wh["id"],
        branch_id=branch["id"],
    )
    await adjust_physical_stock_to_target(
        db,
        item["id"],
        {
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "target_physical_qty": 10_000,
            "reason": "restock",
            "idempotency_key": f"rs-r2-{item['id']}",
        },
        actor_id="t",
    )
    catalog = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh["id"], q="BOX-ATP",
    )
    assert catalog.get("r2_authority_active") is True
    row = next(r for r in catalog["items"] if r["master_item_id"] == item["id"])
    assert row["open_so_commitment"] == 3_000


# --- T14 orphan ---

async def test_t14_orphan_delivery_does_not_reduce_active(db):
    item, branch, wh = await _seed(db)
    active_so = {
        "id": f"so-act-{uuid.uuid4().hex[:6]}",
        "cancelled": False,
        "branch_id": branch["id"],
        "line_items": [{
            "line_id": "ln-act",
            "master_item_id": item["id"],
            "quantity": 8_000,
            "production_process": "non_print",
        }],
    }
    await db.orders.insert_one(dict(active_so))
    # Ledger pointing at a foreign/non-active order id
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"led-orp-{uuid.uuid4().hex[:8]}",
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "qty_delta": -3_000,
        "metadata": {
            "order_id": "so-ghost-not-in-orders",
            "line_id": "ln-x",
            "line_idx": 0,
        },
    })
    row = await _catalog_row(db, branch["id"], wh["id"], item["id"], "BOX-ATP")
    assert row["open_so_commitment"] == 8_000


async def test_t14b_line_idx_fallback_when_line_id_only_on_ledger(db):
    """Validated line_idx fallback when active line has idx and ledger stamps idx."""
    mid = "M-IDX"
    active = [
        {"order_id": "SO1", "master_item_id": mid, "ordered_qty": 1000, "line_idx": 0},
    ]
    delivered = [
        {"order_id": "SO1", "master_item_id": mid, "qty": 400, "line_idx": 0, "line_id": "0"},
    ]
    out = aggregate_active_so_commitment(
        active_lines=active, attributed_deliveries=delivered,
    )
    assert out[mid] == 600
