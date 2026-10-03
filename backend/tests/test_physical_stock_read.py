"""Read APIs for physical stock balances and ledger. Isolated test database only."""
from __future__ import annotations

import inspect
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_physical_stock_read"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    PhysicalStockNotFoundError,
    PhysicalStockValidationError,
    list_physical_mutations,
    list_physical_stock_catalog,
    read_physical_stock,
)
import server  # noqa: E402
from server import app, current_user  # noqa: E402

import httpx  # noqa: E402

TEST_DB = "eternal_packaging_test_physical_stock_read"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

LEGACY_COLLECTIONS = ("product_stock_state", "stock_movements", "item_stock_manual")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.db = database
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


def _item(db, **extra):
    doc = {"id": extra.pop("id", f"item-{uuid.uuid4().hex[:8]}"), "name": "Box", "unit": "pcs"}
    doc.update(extra)
    return doc


async def _seed_item(db, **extra):
    doc = _item(db, **extra)
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(doc))
    return doc


async def _seed_branch(db, code="YGY", active=True):
    doc = {"id": f"br-{code.lower()}-{uuid.uuid4().hex[:6]}", "code": code, "name": code, "active": active}
    await db[BRANCHES_COLLECTION].insert_one(dict(doc))
    return doc


async def _seed_warehouse(db, branch, code="MAIN", active=True):
    doc = {
        "id": f"wh-{code.lower()}-{uuid.uuid4().hex[:6]}",
        "branch_id": branch["id"],
        "code": code,
        "name": code,
        "active": active,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(doc))
    return doc


async def _seed_balance(db, item, branch, warehouse, qty):
    doc = {
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": qty,
    }
    await db[BALANCE_COLLECTION].insert_one(dict(doc))
    return doc


async def _seed_ledger(db, item, branch, warehouse, *, occurred_at, movement_type, qty_delta, reason=""):
    doc = {
        "id": f"mov-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "movement_type": movement_type,
        "qty_delta": qty_delta,
        "balance_before": 0,
        "balance_after": qty_delta,
        "reference_type": "TEST",
        "reference_id": "ref-1",
        "reason": reason,
        "actor_id": "actor-1",
        "metadata": {"note": reason or movement_type},
    }
    await db[LEDGER_COLLECTION].insert_one(dict(doc))
    return doc


async def test_no_balance_returns_zero_and_writes_nothing(db):
    item = await _seed_item(db)
    before = await db[BALANCE_COLLECTION].count_documents({})
    result = await read_physical_stock(db, item["id"])
    assert result["total_physical_qty"] == 0
    assert result["items"] == []
    assert result["base_unit"] == "PCS"
    assert await db[BALANCE_COLLECTION].count_documents({}) == before


async def test_specific_location_without_balance_returns_zero_row(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db)
    warehouse = await _seed_warehouse(db, branch)
    before = await db[BALANCE_COLLECTION].count_documents({})
    result = await read_physical_stock(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert result["total_physical_qty"] == 0
    assert len(result["items"]) == 1
    assert result["items"][0]["physical_qty"] == 0
    assert result["items"][0]["branch_code"] == "YGY"
    assert result["items"][0]["warehouse_code"] == "MAIN"
    assert await db[BALANCE_COLLECTION].count_documents({}) == before


async def test_one_location_reads_balance_qty(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db)
    warehouse = await _seed_warehouse(db, branch)
    await _seed_balance(db, item, branch, warehouse, 12)
    await db.product_stock_state.insert_one({"master_item_id": item["id"], "stock": 999})
    await db.stock_movements.insert_one({"master_item_id": item["id"], "qty": 999})
    result = await read_physical_stock(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert result["items"][0]["physical_qty"] == 12
    assert "printed_qty" not in result["items"][0]
    assert "unprinted_qty" not in result["items"][0]


async def test_warehouses_and_branches_stay_separate(db):
    item = await _seed_item(db)
    ygy = await _seed_branch(db, "YGY")
    smg = await _seed_branch(db, "SMG")
    main = await _seed_warehouse(db, ygy, "MAIN")
    alt = await _seed_warehouse(db, ygy, "ALT")
    smg_main = await _seed_warehouse(db, smg, "MAIN")
    await _seed_balance(db, item, ygy, main, 5)
    await _seed_balance(db, item, ygy, alt, 7)
    await _seed_balance(db, item, smg, smg_main, 9)
    all_rows = await read_physical_stock(db, item["id"])
    assert sorted(row["physical_qty"] for row in all_rows["items"]) == [5, 7, 9]
    branch_rows = await read_physical_stock(db, item["id"], branch_id=ygy["id"])
    assert sorted(row["physical_qty"] for row in branch_rows["items"]) == [5, 7]
    one = await read_physical_stock(db, item["id"], branch_id=ygy["id"], warehouse_id=alt["id"])
    assert one["items"][0]["physical_qty"] == 7
    assert one["items"][0]["warehouse_code"] == "ALT"


async def test_invalid_item_branch_warehouse_and_mismatch(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db, "YGY")
    other = await _seed_branch(db, "SMG")
    warehouse = await _seed_warehouse(db, branch, "MAIN")
    with pytest.raises(PhysicalStockNotFoundError):
        await read_physical_stock(db, "missing-item")
    with pytest.raises(PhysicalStockNotFoundError):
        await read_physical_stock(db, item["id"], branch_id="missing-branch")
    with pytest.raises(PhysicalStockNotFoundError):
        await read_physical_stock(db, item["id"], warehouse_id="missing-warehouse")
    with pytest.raises(PhysicalStockValidationError):
        await read_physical_stock(
            db, item["id"], branch_id=other["id"], warehouse_id=warehouse["id"],
        )


async def test_inactive_location_remains_readable(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db, "OLD", active=False)
    warehouse = await _seed_warehouse(db, branch, "OLD", active=False)
    await _seed_balance(db, item, branch, warehouse, 4)
    result = await read_physical_stock(
        db, item["id"], branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    assert result["items"][0]["physical_qty"] == 4
    assert result["items"][0]["branch_code"] == "OLD"


async def test_mutation_filters_sort_and_pagination(db):
    item = await _seed_item(db)
    ygy = await _seed_branch(db, "YGY")
    smg = await _seed_branch(db, "SMG")
    main = await _seed_warehouse(db, ygy, "MAIN")
    alt = await _seed_warehouse(db, ygy, "ALT")
    smg_main = await _seed_warehouse(db, smg, "MAIN")
    await _seed_ledger(
        db, item, ygy, main, occurred_at="2026-03-01T10:00:00+00:00",
        movement_type="OPENING_BALANCE", qty_delta=5, reason="open",
    )
    await _seed_ledger(
        db, item, ygy, alt, occurred_at="2026-03-03T10:00:00+00:00",
        movement_type="PURCHASE_RECEIPT", qty_delta=2, reason="buy",
    )
    await _seed_ledger(
        db, item, smg, smg_main, occurred_at="2026-03-02T10:00:00+00:00",
        movement_type="SALES_DELIVERY", qty_delta=-1, reason="sell",
    )
    before = await db[LEDGER_COLLECTION].count_documents({})
    listed = await list_physical_mutations(db, item["id"])
    assert [row["occurred_at"] for row in listed["items"]] == [
        "2026-03-03T10:00:00+00:00",
        "2026-03-02T10:00:00+00:00",
        "2026-03-01T10:00:00+00:00",
    ]
    assert listed["items"][0]["warehouse_code"] == "ALT"
    assert listed["items"][0]["metadata"]["note"] == "buy"
    assert "GUDANG_UTAMA" not in str(listed)
    branch_only = await list_physical_mutations(db, item["id"], branch_id=ygy["id"])
    assert branch_only["total"] == 2
    warehouse_only = await list_physical_mutations(
        db, item["id"], branch_id=ygy["id"], warehouse_id=main["id"],
    )
    assert warehouse_only["total"] == 1
    assert warehouse_only["items"][0]["movement_type"] == "OPENING_BALANCE"
    typed = await list_physical_mutations(db, item["id"], movement_type="SALES_DELIVERY")
    assert typed["total"] == 1
    dated = await list_physical_mutations(db, item["id"], from_date="2026-03-02", to_date="2026-03-02")
    assert dated["total"] == 1
    assert dated["items"][0]["movement_type"] == "SALES_DELIVERY"
    page1 = await list_physical_mutations(db, item["id"], page=1, limit=2)
    page2 = await list_physical_mutations(db, item["id"], page=2, limit=2)
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert await db[LEDGER_COLLECTION].count_documents({}) == before


async def test_mutation_validation_and_legacy_sources_unused(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db)
    warehouse = await _seed_warehouse(db, branch)
    other = await _seed_branch(db, "SMG")
    with pytest.raises(PhysicalStockNotFoundError):
        await list_physical_mutations(db, "missing")
    with pytest.raises(PhysicalStockNotFoundError):
        await list_physical_mutations(db, item["id"], branch_id="missing")
    with pytest.raises(PhysicalStockNotFoundError):
        await list_physical_mutations(db, item["id"], warehouse_id="missing")
    with pytest.raises(PhysicalStockValidationError):
        await list_physical_mutations(
            db, item["id"], branch_id=other["id"], warehouse_id=warehouse["id"],
        )
    with pytest.raises(PhysicalStockValidationError):
        await list_physical_mutations(db, item["id"], movement_type="NOT_A_TYPE")
    source = inspect.getsource(read_physical_stock) + inspect.getsource(list_physical_mutations)
    for name in ("product_stock_state", "stock_movements", "item_stock_manual", "current-stock"):
        assert name not in source
    for name in LEGACY_COLLECTIONS:
        assert name not in inspect.getsource(read_physical_stock)


def _admin():
    return {
        "id": "admin-read",
        "email": "admin@example.com",
        "name": "Admin",
        "role": "admin",
        "status": "active",
        "user_id": "admin-read",
        "permissions_override": {},
    }


async def test_http_physical_stock_and_mutations(db):
    item = await _seed_item(db)
    branch = await _seed_branch(db, "YGY")
    warehouse = await _seed_warehouse(db, branch, "MAIN")
    await _seed_balance(db, item, branch, warehouse, 8)
    await _seed_ledger(
        db, item, branch, warehouse, occurred_at="2026-04-01T00:00:00+00:00",
        movement_type="MANUAL_ADJUSTMENT", qty_delta=8, reason="adj",
    )
    app.dependency_overrides[current_user] = lambda: _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        stock = await client.get(f"/api/master-items/{item['id']}/physical-stock")
        missing = await client.get("/api/master-items/nope/physical-stock")
        mutations = await client.get(
            f"/api/master-items/{item['id']}/physical-mutations",
            params={"movement_type": "MANUAL_ADJUSTMENT", "page": 1, "limit": 10},
        )
    app.dependency_overrides.clear()
    assert stock.status_code == 200
    assert stock.json()["items"][0]["physical_qty"] == 8
    assert missing.status_code == 404
    assert mutations.status_code == 200
    assert mutations.json()["total"] == 1
    assert mutations.json()["items"][0]["reason"] == "adj"


async def test_catalog_returns_zero_without_writing_and_filters_locations(db):
    item = await _seed_item(db, item_code="BOX", item_name="Dus", accurate_item_id="ACC-1")
    ygy = await _seed_branch(db, "YGY")
    smg = await _seed_branch(db, "SMG")
    main = await _seed_warehouse(db, ygy, "MAIN")
    await _seed_balance(db, item, ygy, main, 6)
    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    all_rows = await list_physical_stock_catalog(db)
    assert all_rows["master_item_count"] == 1
    assert all_rows["warehouse_count"] == 1
    assert all_rows["items"][0]["physical_qty"] == 6
    assert all_rows["items"][0]["base_unit"] == "PCS"
    smg_rows = await list_physical_stock_catalog(db, branch_id=smg["id"])
    assert smg_rows["warehouse_count"] == 0
    assert smg_rows["items"] == []
    assert smg_rows["master_item_count"] == 1
    empty_wh = await _seed_warehouse(db, smg, "NONE")
    await db[WAREHOUSES_COLLECTION].delete_one({"id": empty_wh["id"]})
    zero_branch = await list_physical_stock_catalog(db, branch_id=smg["id"])
    assert zero_branch["warehouse_count"] == 0
    assert zero_branch["items"] == []
    filtered = await list_physical_stock_catalog(db, branch_id=ygy["id"], warehouse_id=main["id"], q="Dus")
    assert filtered["items"][0]["physical_qty"] == 6
    assert filtered["items"][0]["branch_code"] == "YGY"
    other = await _seed_item(db, item_code="CUP", item_name="Cup")
    missing = await list_physical_stock_catalog(db, q="Cup")
    assert missing["items"][0]["master_item_id"] == other["id"]
    assert missing["items"][0]["physical_qty"] == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    source = inspect.getsource(list_physical_stock_catalog)
    for name in ("product_stock_state", "stock_movements", "item_stock_manual"):
        assert name not in source
