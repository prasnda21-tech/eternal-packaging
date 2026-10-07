"""Bulk R2 physical opening-balance import. Isolated test database only."""
from __future__ import annotations

import io
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook, load_workbook

os.environ["DB_NAME"] = "eternal_packaging_test_physical_opening_import"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_opening_import import (  # noqa: E402
    HEADERS,
    STATUS_BLOCKED,
    STATUS_DUPLICATE,
    STATUS_OPENING,
    STATUS_QTY,
    STATUS_READY,
    STATUS_UNKNOWN,
    OpeningImportError,
    build_template_xlsx,
    confirm_opening_import,
    preview_opening_import,
)
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
    post_movement,
    set_fail_after_balance,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
import server  # noqa: E402
from server import app, current_user  # noqa: E402

import httpx  # noqa: E402

TEST_DB = "eternal_packaging_test_physical_opening_import"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

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
    await set_r2_authority_active(database, active=True)
    server.db = database
    set_fail_after_balance(False)
    try:
        yield database
    finally:
        set_fail_after_balance(False)
        await client.drop_database(TEST_DB)
        client.close()


def _xlsx(rows):
    book = Workbook()
    sheet = book.active
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _location(db, *, warehouses=None):
    branch = {"id": f"br-{uuid.uuid4().hex[:8]}", "code": "YGY", "name": "Yogyakarta", "active": True}
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    stored = []
    for code, name in warehouses or (("MAIN", "Gudang Yogyakarta"),):
        warehouse = {
            "id": f"wh-{uuid.uuid4().hex[:8]}",
            "branch_id": branch["id"],
            "code": code,
            "name": name,
            "active": True,
        }
        await db[WAREHOUSES_COLLECTION].insert_one(dict(warehouse))
        stored.append(warehouse)
    return branch, stored


async def _item(db, code, *, item_type="", name=None):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": name or code,
        "unit": "PCS",
        "base_unit": "PCS",
    }
    if item_type:
        item["item_type"] = item_type
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


def _line(item, branch, warehouse, qty):
    return {
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "physical_qty": qty,
        "item_code": item["item_code"],
    }


async def test_bulk_import_writes_opening_and_leaves_legacy_stock(db):
    branch, (warehouse, other) = await _location(db, warehouses=(("MAIN", "Gudang Yogyakarta"), ("SOPUAT", "Gudang Opname")))
    first = await _item(db, "OB-A")
    second = await _item(db, "OB-B")
    await db.product_stock_state.insert_one({"id": "legacy-state", "stock": 7})
    await db.stock_movements.insert_one({"id": "legacy-move", "qty": 3})
    content = _xlsx([
        ["OB-A", "Nama A", "PCS", "MAIN", 100],
        ["OB-B", "Nama B", "PCS", "Gudang Yogyakarta", 0],
    ])
    before = await db[LEDGER_COLLECTION].count_documents({})
    preview = await preview_opening_import(db, content, "saldo.xlsx")
    assert await db[LEDGER_COLLECTION].count_documents({}) == before
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert preview["can_confirm"] is True
    assert [row["status"] for row in preview["rows"]] == [STATUS_READY, STATUS_READY]
    assert preview["rows"][0]["warehouse_id"] == warehouse["id"]
    assert preview["rows"][0]["branch_id"] == branch["id"]
    assert preview["rows"][0]["warehouse_id"] != other["id"]
    assert preview["rows"][1]["physical_qty"] == 0
    result = await confirm_opening_import(
        db,
        {
            "idempotency_key": "batch-1",
            "lines": [_line(first, branch, warehouse, 100), _line(second, branch, warehouse, 0)],
        },
        actor_id="actor-1",
    )
    assert result["idempotent"] is False
    movements = await db[LEDGER_COLLECTION].find({}, {"_id": 0}).to_list(10)
    assert len(movements) == 2
    assert {row["movement_type"] for row in movements} == {MOVEMENT_OPENING_BALANCE}
    by_item = {row["master_item_id"]: row for row in movements}
    assert by_item[first["id"]]["qty_delta"] == 100
    assert by_item[first["id"]]["branch_id"] == branch["id"]
    assert by_item[first["id"]]["warehouse_id"] == warehouse["id"]
    assert by_item[first["id"]]["metadata"]["import_batch_id"] == result["import"]["id"]
    assert by_item[second["id"]]["qty_delta"] == 0
    assert by_item[second["id"]]["balance_after"] == 0
    balances = await db[BALANCE_COLLECTION].find({}, {"_id": 0}).to_list(10)
    assert {row["physical_qty"] for row in balances} == {100, 0}
    assert (await db.product_stock_state.find_one({"id": "legacy-state"}))["stock"] == 7
    assert (await db.stock_movements.find_one({"id": "legacy-move"}))["qty"] == 3


async def test_invalid_rows_do_not_write(db):
    branch, (warehouse, _other) = await _location(db, warehouses=(("MAIN", "Gudang Yogyakarta"), ("SOPUAT", "Gudang Opname")))
    await _item(db, "OB-A")
    await _item(db, "OB-NEG")
    await _item(db, "OB-UNIT")
    await _item(db, "OB-BLANK")
    await _item(db, "JASA-1", item_type="service")
    content = _xlsx([
        ["", "Kosong", "PCS", "MAIN", 5],
        ["TIDAK-ADA", "Hilang", "PCS", "MAIN", 5],
        ["OB-NEG", "Neg", "PCS", "MAIN", -1],
        ["OB-A", "A", "PCS", "MAIN", 10],
        ["OB-A", "A", "PCS", "MAIN", 10],
        ["JASA-1", "Jasa", "PCS", "MAIN", 4],
        ["OB-UNIT", "Unit", "DUS", "MAIN", 3],
        ["OB-BLANK", "Blank", "PCS", "", 8],
    ])
    preview = await preview_opening_import(db, content, "bad.xlsx")
    statuses = [row["status"] for row in preview["rows"]]
    assert statuses[0] == STATUS_UNKNOWN
    assert statuses[1] == STATUS_UNKNOWN
    assert statuses[2] == STATUS_QTY
    assert statuses[3] == STATUS_DUPLICATE
    assert statuses[4] == STATUS_DUPLICATE
    assert statuses[5] == STATUS_BLOCKED
    assert "jasa" in preview["rows"][5]["reason"].lower()
    assert statuses[6] == STATUS_BLOCKED
    assert preview["rows"][7]["reason"] == "Gudang wajib dipilih."
    assert preview["can_confirm"] is False
    with pytest.raises(OpeningImportError):
        await confirm_opening_import(
            db,
            {"idempotency_key": "bad", "lines": [_line({"id": "missing", "item_code": "TIDAK-ADA"}, branch, warehouse, 5)]},
            actor_id="actor-1",
        )
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0


async def test_existing_opening_and_history_are_blocked(db):
    branch, (warehouse,) = await _location(db)
    opened = await _item(db, "OB-OPEN")
    moved = await _item(db, "OB-MOVED")
    fresh = await _item(db, "OB-FRESH")
    await confirm_opening_import(
        db,
        {"idempotency_key": "first", "lines": [_line(opened, branch, warehouse, 40)]},
        actor_id="actor-1",
    )
    await post_movement(
        db,
        {
            "master_item_id": moved["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "qty_delta": 15,
            "reference_type": "TEST",
            "reference_id": "gr-1",
            "reason": "receipt",
            "idempotency_key": "receipt-1",
        },
        actor_id="actor-1",
    )
    preview = await preview_opening_import(
        db,
        _xlsx([
            ["OB-OPEN", "Open", "PCS", "MAIN", 99],
            ["OB-MOVED", "Moved", "PCS", "MAIN", 99],
            ["OB-FRESH", "Fresh", "PCS", "MAIN", 5],
        ]),
        "again.xlsx",
    )
    assert preview["rows"][0]["status"] == STATUS_OPENING
    assert preview["rows"][0]["reason"] == "SUDAH MEMILIKI SALDO AWAL"
    assert preview["rows"][1]["status"] == STATUS_BLOCKED
    assert "mutasi" in preview["rows"][1]["reason"]
    assert preview["can_confirm"] is False
    with pytest.raises(OpeningImportError):
        await confirm_opening_import(
            db,
            {
                "idempotency_key": "second",
                "lines": [
                    _line(opened, branch, warehouse, 99),
                    _line(fresh, branch, warehouse, 5),
                ],
            },
            actor_id="actor-1",
        )
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": opened["id"]}))["physical_qty"] == 40
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": moved["id"]}))["physical_qty"] == 15
    assert await db[BALANCE_COLLECTION].find_one({"master_item_id": fresh["id"]}) is None
    assert await db[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_OPENING_BALANCE}) == 1


async def test_idempotent_retry_and_changed_payload(db):
    branch, (warehouse,) = await _location(db)
    item = await _item(db, "OB-A")
    payload = {"idempotency_key": "same", "lines": [_line(item, branch, warehouse, 100)]}
    first = await confirm_opening_import(db, payload, actor_id="actor-1")
    second = await confirm_opening_import(db, payload, actor_id="actor-1")
    assert second["idempotent"] is True
    assert first["import"]["id"] == second["import"]["id"]
    assert first["import"]["lines"][0]["movement_id"] == second["import"]["lines"][0]["movement_id"]
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 100
    assert await db[LEDGER_COLLECTION].count_documents({}) == 1
    with pytest.raises(OpeningImportError) as conflict:
        await confirm_opening_import(
            db,
            {"idempotency_key": "same", "lines": [_line(item, branch, warehouse, 80)]},
            actor_id="actor-1",
        )
    assert conflict.value.status_code == 409
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 100
    with pytest.raises(OpeningImportError):
        await confirm_opening_import(
            db,
            {"idempotency_key": "other", "lines": [_line(item, branch, warehouse, 100)]},
            actor_id="actor-1",
        )
    assert (await db[BALANCE_COLLECTION].find_one({"master_item_id": item["id"]}))["physical_qty"] == 100


async def test_wrong_branch_and_abort_write_nothing(db):
    branch, (warehouse,) = await _location(db)
    other = {"id": "br-other", "code": "SMG", "name": "Semarang", "active": True}
    await db[BRANCHES_COLLECTION].insert_one(dict(other))
    item = await _item(db, "OB-A")
    with pytest.raises(OpeningImportError):
        await confirm_opening_import(
            db,
            {"idempotency_key": "wrong-branch", "lines": [_line(item, other, warehouse, 20)]},
            actor_id="actor-1",
        )
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    set_fail_after_balance(True)
    with pytest.raises(OpeningImportError):
        await confirm_opening_import(
            db,
            {"idempotency_key": "abort", "lines": [_line(item, branch, warehouse, 20)]},
            actor_id="actor-1",
        )
    set_fail_after_balance(False)
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0
    assert await db[BALANCE_COLLECTION].count_documents({}) == 0
    assert await db.physical_opening_imports.count_documents({}) == 0


async def test_import_does_not_change_order_buckets(db):
    branch, (warehouse,) = await _location(db)
    item = await _item(db, "OB-A")
    order = {
        "id": "ord-1",
        "status": "open",
        "line_items": [{"master_item_id": item["id"], "quantity": 10}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 4}],
    }
    await db.orders.insert_one(dict(order))
    await confirm_opening_import(
        db,
        {"idempotency_key": "orders", "lines": [_line(item, branch, warehouse, 100)]},
        actor_id="actor-1",
    )
    catalog = await list_physical_stock_catalog(db, q="OB-A")
    row = catalog["items"][0]
    assert row["physical_qty"] == 100
    assert row["qty_pesanan"] == 6
    assert row["qty_belum_terkirim"] == 4
    assert row["qty_tersedia"] == 90
    stored = await db.orders.find_one({"id": "ord-1"}, {"_id": 0})
    assert stored["line_items"] == order["line_items"]
    assert stored["operator_splits"] == order["operator_splits"]


def _admin():
    return {
        "id": "admin-open",
        "email": "admin@example.com",
        "name": "Admin",
        "role": "admin",
        "status": "active",
        "user_id": "admin-open",
        "permissions_override": {},
    }


def _sheet_rows(content: bytes):
    book = load_workbook(io.BytesIO(content), data_only=True)
    return [list(row) for row in book.active.iter_rows(values_only=True)]


async def test_template_prefills_eligible_items_and_writes_nothing(db):
    branch, (main, other) = await _location(
        db, warehouses=(("MAIN", "Gudang Yogyakarta"), ("SOPUAT", "Gudang Opname")),
    )
    ready = await _item(db, "OB-READY", name="Cup Siap")
    await _item(db, "JASA-1", item_type="service", name="Jasa Cetak")
    inactive = {
        "id": "item-off",
        "item_code": "OB-OFF",
        "item_name": "Tidak Aktif",
        "unit": "PCS",
        "is_active": False,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(inactive)
    opened = await _item(db, "OB-OPEN", name="Sudah Ada")
    await confirm_opening_import(
        db,
        {"idempotency_key": "template-open", "lines": [_line(opened, branch, main, 4)]},
        actor_id="actor-1",
    )
    duplicate = {"id": "dup-a", "item_code": "OB-DUP", "item_name": "Dup A", "unit": "PCS"}
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(duplicate))
    await db[MASTER_ITEMS_COLLECTION].insert_one(
        {"id": "dup-b", "item_code": "OB-DUP", "item_name": "Dup B", "unit": "PCS"},
    )
    before_ledger = await db[LEDGER_COLLECTION].count_documents({})
    before_balance = await db[BALANCE_COLLECTION].count_documents({})
    with pytest.raises(OpeningImportError, match="Pilih gudang"):
        await build_template_xlsx(db)
    content = await build_template_xlsx(db, warehouse_id=main["id"])
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance
    rows = _sheet_rows(content)
    assert rows[0] == ["Kode Barang", "Nama Barang", "Satuan Dasar", "Gudang", "Stock Fisik"]
    body = rows[1:]
    codes = [row[0] for row in body]
    assert codes == ["OB-READY"]
    assert body[0][1] == "Cup Siap"
    assert body[0][2] == "PCS"
    assert body[0][3] == "Gudang Yogyakarta"
    assert body[0][4] is None
    assert main["id"] not in body[0]
    assert "JASA-1" not in codes
    assert "OB-OFF" not in codes
    assert "OB-OPEN" not in codes
    assert "OB-DUP" not in codes
    assert len(codes) == len(set(codes))
    book = load_workbook(io.BytesIO(content))
    book.active.cell(row=2, column=5).value = 12
    filled = io.BytesIO()
    book.save(filled)
    preview = await preview_opening_import(db, filled.getvalue(), "saldo.xlsx")
    assert preview["can_confirm"] is True
    assert preview["rows"][0]["master_item_id"] == ready["id"]
    assert preview["rows"][0]["warehouse_id"] == main["id"]
    assert preview["rows"][0]["warehouse_id"] != other["id"]
    assert preview["rows"][0]["physical_qty"] == 12
    assert await db[LEDGER_COLLECTION].count_documents({}) == before_ledger
    assert await db[BALANCE_COLLECTION].count_documents({}) == before_balance


async def test_http_preview_does_not_write_and_confirm_posts(db):
    branch, (warehouse,) = await _location(db)
    item = await _item(db, "OB-HTTP")
    content = _xlsx([["OB-HTTP", "HTTP", "PCS", "MAIN", 12]])
    app.dependency_overrides[current_user] = lambda: _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/physical-opening-balances/import/template")
        template = await client.get(
            "/api/physical-opening-balances/import/template",
            params={"warehouse_id": warehouse["id"]},
        )
        preview = await client.post(
            "/api/physical-opening-balances/import/preview",
            files={"file": ("saldo.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert await db[LEDGER_COLLECTION].count_documents({}) == 0
        created = await client.post(
            "/api/physical-opening-balances/import",
            json={"idempotency_key": "http-batch", "lines": [_line(item, branch, warehouse, 12)]},
        )
    app.dependency_overrides.clear()
    assert missing.status_code == 400
    assert template.status_code == 200
    assert template.content[:2] == b"PK"
    assert preview.status_code == 200
    assert preview.json()["can_confirm"] is True
    assert preview.json()["rows"][0]["status"] == STATUS_READY
    assert created.status_code == 200
    movement = await db[LEDGER_COLLECTION].find_one({"master_item_id": item["id"]}, {"_id": 0})
    assert movement["movement_type"] == MOVEMENT_OPENING_BALANCE
    assert movement["qty_delta"] == 12
    assert movement["warehouse_id"] == warehouse["id"]
