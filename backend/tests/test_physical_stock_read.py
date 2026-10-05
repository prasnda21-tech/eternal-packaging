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
    assert len(all_rows["items"]) == 1
    assert all_rows["items"][0]["physical_qty"] == 6
    assert all_rows["items"][0]["base_unit"] == "PCS"
    assert all_rows["items"][0]["qty_pesanan"] == 0
    assert all_rows["items"][0]["qty_tersedia"] == 6
    ignored_branch = await list_physical_stock_catalog(db, branch_id=smg["id"])
    assert ignored_branch["items"][0]["physical_qty"] == 6
    filtered = await list_physical_stock_catalog(db, branch_id=ygy["id"], warehouse_id=main["id"], q="Dus")
    assert filtered["items"][0]["physical_qty"] == 6
    assert "branch_code" not in filtered["items"][0]
    other = await _seed_item(db, item_code="CUP", item_name="Cup")
    missing = await list_physical_stock_catalog(db, q="Cup")
    assert missing["items"][0]["master_item_id"] == other["id"]
    assert missing["items"][0]["physical_qty"] == 0
    assert missing["items"][0]["qty_tersedia"] == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    source = inspect.getsource(list_physical_stock_catalog)
    for name in ("product_stock_state", "stock_movements", "item_stock_manual"):
        assert name not in source
    for name in ("qty_po", "printed_qty", "available_qty", "order_qty", "stock_current"):
        assert name not in source
    assert "warehouse_id" not in all_rows["items"][0]


def _order(order_id, item, quantity, completed=0, cancelled=False, status=None):
    doc = {
        "id": order_id,
        "cancelled": cancelled,
        "line_items": [{
            "master_item_id": item["id"],
            "quantity": quantity,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }
    if status:
        doc["status"] = status
    return doc


def _expect_buckets(row, *, physical, pesanan, belum, tersedia):
    assert row["physical_qty"] == physical
    assert row["qty_pesanan"] == pesanan
    assert row["qty_belum_terkirim"] == belum
    assert row["qty_tersedia"] == tersedia


async def test_operational_stock_buckets(db):
    ygy = await _seed_branch(db, "YGY")
    await db[BRANCHES_COLLECTION].update_one({"id": ygy["id"]}, {"$set": {"name": "Yogyakarta"}})
    smg = await _seed_branch(db, "SMG")
    main = await _seed_warehouse(db, ygy, "MAIN")
    second = await _seed_warehouse(db, ygy, "WHB")
    smg_wh = await _seed_warehouse(db, smg, "SMG1")

    item_a = await _seed_item(db, item_code="A", item_name="Case A", unit="pcs")
    item_b = await _seed_item(db, item_code="B", item_name="Case B", unit="pcs")
    item_c = await _seed_item(db, item_code="C", item_name="Case C", unit="pcs")
    item_d = await _seed_item(db, item_code="D", item_name="Case D", unit="pcs")
    item_e = await _seed_item(db, item_code="E", item_name="Case E", unit="pcs")
    item_f = await _seed_item(db, item_code="F", item_name="Case F", unit="pcs")
    item_g = await _seed_item(db, item_code="G", item_name="Case G", unit="pcs")
    item_h = await _seed_item(db, item_code="H", item_name="Case H", unit="pcs")
    item_i = await _seed_item(db, item_code="I", item_name="Case I", unit="pcs")
    item_legacy = await _seed_item(db, item_code="L", item_name="Legacy Delivery", unit="pcs")

    for item, qty in (
        (item_a, 1000), (item_b, 1000), (item_c, 1000), (item_d, 1000),
        (item_e, 100), (item_f, 100), (item_g, 1000), (item_h, 1000),
        (item_i, 40), (item_legacy, 1000),
    ):
        await _seed_balance(db, item, ygy, main, qty)
    await _seed_balance(db, item_a, smg, smg_wh, 999)
    await _seed_balance(db, item_c, ygy, second, 0)

    await db.orders.insert_many([
        _order("b", item_b, 300, 0),
        _order("c", item_c, 300, 200),
        _order("d", item_d, 300, 300),
        _order("e", item_e, 300, 0),
        _order("f", item_f, 300, 200),
        _order("g", item_g, 300, 300, cancelled=True),
        _order("g-void", item_g, 80, 80, status="void"),
        _order("h1", item_h, 200, 50),
        _order("h2", item_h, 100, 100),
        _order("i", item_i, 10, 4),
        _order("legacy", item_legacy, 300, 200),
        _order("mix", item_b, 0, 0),
    ])
    await db.orders.update_one({"id": "mix"}, {"$set": {
        "line_items": [
            {"master_item_id": item_b["id"], "quantity": 0, "production_process": "print"},
            {"master_item_id": item_i["id"], "quantity": 5, "production_process": "print"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 0},
            {"line_idx": 1, "qty_completed": 1},
        ],
    }})
    await db.sales_deliveries.insert_one({
        "id": "legacy-del",
        "po_id": "legacy",
        "line_items": [{"line_idx": 0, "qty": 200, "master_item_id": item_legacy["id"]}],
    })

    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    before_orders = await db.orders.count_documents({})
    before_legacy = {
        name: await db[name].count_documents({})
        for name in ("product_stock_state", "stock_movements", "item_stock_manual")
    }
    rows = await list_physical_stock_catalog(db)
    by_code = {row["item_code"]: row for row in rows["items"]}
    assert len(by_code) == 10
    _expect_buckets(by_code["A"], physical=1000, pesanan=0, belum=0, tersedia=1000)
    _expect_buckets(by_code["B"], physical=1000, pesanan=300, belum=0, tersedia=700)
    _expect_buckets(by_code["C"], physical=1000, pesanan=100, belum=200, tersedia=700)
    assert by_code["C"]["qty_tersedia"] == by_code["B"]["qty_tersedia"]
    _expect_buckets(by_code["D"], physical=1000, pesanan=0, belum=300, tersedia=700)
    _expect_buckets(by_code["E"], physical=100, pesanan=300, belum=0, tersedia=-200)
    _expect_buckets(by_code["F"], physical=100, pesanan=100, belum=200, tersedia=-200)
    _expect_buckets(by_code["G"], physical=1000, pesanan=0, belum=0, tersedia=1000)
    _expect_buckets(by_code["H"], physical=1000, pesanan=150, belum=150, tersedia=700)
    _expect_buckets(by_code["I"], physical=40, pesanan=10, belum=5, tersedia=25)
    _expect_buckets(by_code["L"], physical=1000, pesanan=100, belum=200, tersedia=700)
    assert by_code["I"]["qty_pesanan"] != by_code["B"]["qty_pesanan"]
    assert by_code["A"]["base_unit"] == "PCS"
    for row in rows["items"]:
        assert "branch_id" not in row
        assert "printed_qty" not in row
        assert "stock_current" not in row
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db.orders.count_documents({}) == before_orders
    for name, count in before_legacy.items():
        assert await db[name].count_documents({}) == count


async def test_operational_warehouse_scope_excludes_non_operational_balances(db):
    from services.org_locations import public_warehouse, warehouse_is_operational
    from services.physical_stock import operational_physical_qty_by_item

    assert warehouse_is_operational({}) is True
    assert warehouse_is_operational({"is_operational": None}) is True
    assert warehouse_is_operational({"is_operational": True}) is True
    assert warehouse_is_operational({"is_operational": False}) is False
    assert public_warehouse({"id": "w", "code": "OLD", "name": "Old"})["is_operational"] is True

    ygy = await _seed_branch(db, "YGY")
    await db[BRANCHES_COLLECTION].update_one({"id": ygy["id"]}, {"$set": {"name": "Yogyakarta"}})
    missing = await _seed_warehouse(db, ygy, "MAIN")
    explicit = await _seed_warehouse(db, ygy, "OPS")
    await db[WAREHOUSES_COLLECTION].update_one({"id": explicit["id"]}, {"$set": {"is_operational": True}})
    hidden = await _seed_warehouse(db, ygy, "SOPUAT")
    await db[WAREHOUSES_COLLECTION].update_one({"id": hidden["id"]}, {"$set": {"is_operational": False}})
    item = await _seed_item(db, item_code="SCOPE", item_name="Scope Item", unit="pcs")
    await _seed_balance(db, item, ygy, missing, 10)
    await _seed_balance(db, item, ygy, explicit, 5)
    await _seed_balance(db, item, ygy, hidden, 99)
    before_balances = await db[BALANCE_COLLECTION].find({}, {"_id": 0}).to_list(20)
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})

    catalog = await list_physical_stock_catalog(db, q="Scope")
    row = catalog["items"][0]
    assert row["physical_qty"] == 15
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 0
    assert row["qty_tersedia"] == 15
    assert catalog["warehouse_count"] == 2
    valued = await operational_physical_qty_by_item(db, master_ids=[item["id"]])
    assert valued[item["id"]] == 15

    after_balances = await db[BALANCE_COLLECTION].find({}, {"_id": 0}).to_list(20)
    assert after_balances == before_balances
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    hidden_qty = await db[BALANCE_COLLECTION].find_one({"warehouse_id": hidden["id"]}, {"_id": 0})
    assert hidden_qty["physical_qty"] == 99
