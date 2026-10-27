"""Stock Saat Ini catalog: page-scoped computation must preserve response contract.

Covers pagination totals, page slices, missing baseline, deficit, multi-warehouse,
and warehouse filter — without changing Excel formulas or ledger writes.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_catalog_page_scope"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_catalog_page_scope"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

N = 120
LIMIT = 50


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.db = database
    server.client = client
    await ensure_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed(db):
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
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
    await db[BRANCHES_COLLECTION].insert_one(branch)
    await db[WAREHOUSES_COLLECTION].insert_many([wh_a, wh_b])
    await set_r2_authority_active(db, active=False, actor={"id": "t", "name": "t"})
    await set_warehouse_stock_standalone_enabled(
        db, enabled=True, user={"id": "t", "name": "t"},
    )

    masters = []
    balances = []
    orders = []
    for i in range(N):
        mid = f"m-{i:04d}"
        masters.append({
            "id": mid,
            "item_code": f"PSC-{i:04d}",
            "item_name": f"Page Scope {i:04d}",
            "unit": "PCS",
            "item_type": "INVENTORY",
            "is_active": True,
            "accurate_item_id": f"ACC-{i:04d}",
        })
        # Every 3rd: balance on A (reconciled). Every 5th: also B.
        # Every 11th: deficit on A. Others: missing baseline (no balance).
        if i % 3 == 0:
            qty_a = -12 if i % 11 == 0 else (50 + i)
            balances.append({
                "id": f"bal-a-{mid}",
                "master_item_id": mid,
                "branch_id": branch["id"],
                "warehouse_id": wh_a["id"],
                "physical_qty": qty_a,
                "baseline_status": "RECONCILED",
                "updated_at": "2026-10-16T10:00:00+00:00",
            })
        if i % 5 == 0:
            balances.append({
                "id": f"bal-b-{mid}",
                "master_item_id": mid,
                "branch_id": branch["id"],
                "warehouse_id": wh_b["id"],
                "physical_qty": 20,
                "baseline_status": "RECONCILED",
                "updated_at": "2026-10-16T11:00:00+00:00",
            })
        if i % 4 == 0:
            orders.append({
                "id": f"ord-{mid}",
                "branch_id": branch["id"],
                "status": "confirmed",
                "cancelled": False,
                "lines": [{
                    "master_item_id": mid,
                    "qty": 8,
                    "process": "print",
                    "qty_completed": 0,
                }],
            })
    masters.append({
        "id": "m-svc",
        "item_code": "SVC-X",
        "item_name": "Service",
        "unit": "PCS",
        "item_type": "SERVICE",
        "is_active": True,
    })
    await db[MASTER_ITEMS_COLLECTION].insert_many(masters)
    if balances:
        await db[BALANCE_COLLECTION].insert_many(balances)
    if orders:
        await db.orders.insert_many(orders)
    return branch, wh_a, wh_b


def _row_fingerprint(row: dict) -> dict:
    """Stable comparable fields for SSI catalog contract (no internal keys)."""
    keys = [
        "master_item_id",
        "item_code",
        "physical_qty",
        "qty_pesanan",
        "qty_belum_terkirim",
        "qty_tersedia",
        "qty_tersedia_valid",
        "belum_terkirim_status",
        "baseline_status",
        "saldo_buku_negatif",
        "stock",
        "stock_dapat_dijual",
        "qty_tercetak",
        "qty_tercetak_status",
        "qty_polos",
        "qty_polos_status",
        "tercetak_belum_kirim",
        "cetak_sudah_dikirim",
        "physical_qty_scope",
        "demand_scope",
    ]
    out = {k: row.get(k) for k in keys}
    wh = []
    for wb in row.get("warehouse_balances") or []:
        wh.append({
            "warehouse_id": wb.get("warehouse_id"),
            "warehouse_code": wb.get("warehouse_code"),
            "physical_qty": wb.get("physical_qty"),
            "balance_present": wb.get("balance_present"),
            "baseline_status": wb.get("baseline_status"),
            "saldo_buku_negatif": wb.get("saldo_buku_negatif"),
        })
    out["warehouse_balances"] = wh
    return out


async def test_page_slices_match_full_catalog_window(db):
    branch, wh_a, wh_b = await _seed(db)
    full = await list_physical_stock_catalog(
        db, branch_id=branch["id"], page=1, limit=1000,
    )
    assert full["total"] == N
    assert full["master_item_count"] == N
    assert len(full["items"]) == N
    assert full["stock_read_mode"] == "warehouse_standalone"

    p1 = await list_physical_stock_catalog(
        db, branch_id=branch["id"], page=1, limit=LIMIT,
    )
    p2 = await list_physical_stock_catalog(
        db, branch_id=branch["id"], page=2, limit=LIMIT,
    )
    p3 = await list_physical_stock_catalog(
        db, branch_id=branch["id"], page=3, limit=LIMIT,
    )

    assert p1["total"] == N
    assert p1["page_count"] == 3
    assert len(p1["items"]) == LIMIT
    assert len(p2["items"]) == LIMIT
    assert len(p3["items"]) == N - 2 * LIMIT

    assert [_row_fingerprint(r) for r in p1["items"]] == [
        _row_fingerprint(r) for r in full["items"][:LIMIT]
    ]
    assert [_row_fingerprint(r) for r in p2["items"]] == [
        _row_fingerprint(r) for r in full["items"][LIMIT:2 * LIMIT]
    ]
    assert [_row_fingerprint(r) for r in p3["items"]] == [
        _row_fingerprint(r) for r in full["items"][2 * LIMIT:]
    ]

    # Missing baseline / deficit / multi-warehouse still present in page window.
    missing = next(
        r for r in full["items"]
        if r["item_code"] == "PSC-0001"
    )
    assert missing["baseline_status"] != "RECONCILED" or any(
        not wb.get("balance_present") for wb in missing["warehouse_balances"]
    )
    deficit = next(r for r in full["items"] if r["item_code"] == "PSC-0000")
    # 0 % 3 == 0 and 0 % 11 == 0 → deficit on A
    assert deficit["saldo_buku_negatif"] is True or any(
        wb.get("saldo_buku_negatif") for wb in deficit["warehouse_balances"]
    )
    dual = next(r for r in full["items"] if r["item_code"] == "PSC-0000")
    codes = {wb["warehouse_code"] for wb in dual["warehouse_balances"]}
    assert codes == {"A", "B"}

    only_a = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh_a["id"], page=1, limit=LIMIT,
    )
    assert only_a["total"] == N
    assert only_a["items"][0]["physical_qty_scope"] == "WAREHOUSE"
    assert only_a["items"][0]["warehouse_id"] == wh_a["id"]

    # Out-of-range page: empty items, stable totals.
    empty = await list_physical_stock_catalog(
        db, branch_id=branch["id"], page=99, limit=LIMIT,
    )
    assert empty["items"] == []
    assert empty["total"] == N
    assert empty["page_count"] == 3


async def test_search_filter_preserves_page_totals(db):
    branch, _, _ = await _seed(db)
    hit = await list_physical_stock_catalog(
        db, branch_id=branch["id"], q="PSC-0012", page=1, limit=LIMIT,
    )
    assert hit["total"] == 1
    assert hit["items"][0]["item_code"] == "PSC-0012"
    assert hit["page_count"] == 1
