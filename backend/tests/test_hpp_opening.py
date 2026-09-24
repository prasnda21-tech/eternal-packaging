"""HPP Awal opening foundation — local-only template/preview/confirm."""
from __future__ import annotations

import os
import sys
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from services.hpp_opening import (
    OPENING_COLLECTION,
    SOURCE_TYPE,
    STATUS_ALREADY_INITIALIZED,
    STATUS_COST_BASIS_ONLY,
    STATUS_DUPLICATE,
    STATUS_INVALID_COST,
    STATUS_MISSING_COST,
    STATUS_OK,
    STATUS_SKU_NOT_FOUND,
    TEMPLATE_HEADERS,
    build_opening_import_preview,
    build_opening_template_xlsx,
    confirm_opening_import,
    ensure_opening_indexes,
)
from services.hpp_valuation import (
    LAYERS,
    METHOD_AVG,
    RECOG_GR,
    VERSIONS,
    moving_average_from_events,
    get_hpp_settings,
)
from services.purchase_common import money_round


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"] + "_hpp_opening"]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_opening_indexes(db)
    await db.hpp_cost_versions.create_index("id", unique=True)
    await db.hpp_cost_layers.create_index([("calculation_version", 1), ("layer_id", 1)], unique=True)
    for col in (
        OPENING_COLLECTION,
        "hpp_opening_batches",
        VERSIONS,
        LAYERS,
        "app_settings",
        "purchase_orders",
        "purchase_goods_receipts",
        "purchase_receipt_po_links",
        "master_items",
        "product_unit_settings",
        "product_stock_state",
        "stock_movements",
    ):
        await db[col].delete_many({})
    yield


def _xlsx_bytes(headers: List[str], rows: List[List[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _stock_row(*, code: str, name: str, mid: str, physical: float, unit: str = "PCS") -> dict:
    return {
        "item_code": code,
        "item_name": name,
        "unit": unit,
        "master_item_id": mid,
        "accurate_item_id": "",
        "physical_qty": physical,
        "unprinted_qty": physical,
        "printed_qty": 0.0,
        "outstanding_so_qty": 0.0,
        "stock_current": physical,
        "available_qty": max(physical, 0),
        "initialized": True,
        "is_active": True,
    }


@pytest.mark.anyio
async def test_template_columns_and_locked_stock_fisik():
    rows = [_stock_row(code="100398", name="10 oz Datar", mid="m1", physical=10000)]
    content = build_opening_template_xlsx(rows)
    wb = load_workbook(BytesIO(content))
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert headers == TEMPLATE_HEADERS
    assert ws.cell(row=2, column=1).value == "100398"
    assert ws.cell(row=2, column=4).value == 10000
    assert ws.cell(row=2, column=5).value is None
    assert ws.protection.sheet is True
    assert ws.cell(row=2, column=5).protection.locked is False


@pytest.mark.anyio
async def test_preview_validation_statuses():
    catalog = [
        _stock_row(code="A1", name="Item A", mid="mA", physical=100),
        _stock_row(code="B1", name="Item B", mid="mB", physical=50),
        _stock_row(code="C1", name="Item C", mid="mC", physical=20),
        _stock_row(code="D1", name="Item D", mid="mD", physical=10),
    ]
    content = _xlsx_bytes(
        TEMPLATE_HEADERS,
        [
            ["A1", "Item A", "PCS", 100, None],       # MISSING_COST
            ["B1", "Item B", "PCS", 50, 0],            # INVALID_COST
            ["C1", "Item C", "PCS", 20, -5],           # INVALID_COST
            ["ZZZ", "Ghost", "PCS", 1, 1000],          # SKU_NOT_FOUND
            ["D1", "Item D", "PCS", 10, 1000],         # OK then dup
            ["D1", "Item D", "PCS", 10, 2000],         # DUPLICATE
        ],
    )
    preview = build_opening_import_preview(
        content, "t.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    by_code_status = [(r["item_code"], r["status"], r["can_apply"]) for r in preview["rows"]]
    assert ("A1", STATUS_MISSING_COST, False) in by_code_status
    assert ("B1", STATUS_INVALID_COST, False) in by_code_status
    assert ("C1", STATUS_INVALID_COST, False) in by_code_status
    assert ("ZZZ", STATUS_SKU_NOT_FOUND, False) in by_code_status
    d_rows = [r for r in preview["rows"] if r["item_code"] == "D1"]
    assert all(r["status"] == STATUS_DUPLICATE for r in d_rows)
    assert all(r["can_apply"] is False for r in d_rows)
    assert preview["can_process"] is False


@pytest.mark.anyio
async def test_preview_positive_stock_nilai_awal():
    catalog = [_stock_row(code="A1", name="Item A", mid="mA", physical=100)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [["A1", "Item A", "PCS", 100, 1000]])
    preview = build_opening_import_preview(
        content, "t.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    row = preview["rows"][0]
    assert row["status"] == STATUS_OK
    assert row["can_apply"] is True
    assert row["hpp_awal"] == 1000
    assert row["nilai_awal"] == 100000
    assert row["physical_qty"] == 100
    # STOCK FISIK from catalog, not from file
    content2 = _xlsx_bytes(TEMPLATE_HEADERS, [["A1", "Item A", "PCS", 99999, 1000]])
    preview2 = build_opening_import_preview(
        content2, "t.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    assert preview2["rows"][0]["physical_qty"] == 100
    assert preview2["rows"][0]["nilai_awal"] == 100000


@pytest.mark.anyio
async def test_preview_zero_and_negative_stock_cost_basis_only():
    catalog = [
        _stock_row(code="Z0", name="Zero", mid="mZ", physical=0),
        _stock_row(code="N1", name="Neg", mid="mN", physical=-5),
    ]
    content = _xlsx_bytes(
        TEMPLATE_HEADERS,
        [
            ["Z0", "Zero", "PCS", 0, 1000],
            ["N1", "Neg", "PCS", -5, 1000],
        ],
    )
    preview = build_opening_import_preview(
        content, "t.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    assert all(r["status"] == STATUS_COST_BASIS_ONLY for r in preview["rows"])
    assert all(r["can_apply"] for r in preview["rows"])
    assert all(r["nilai_awal"] == 0 for r in preview["rows"])
    assert all(r["cost_basis_only"] for r in preview["rows"])


@pytest.mark.anyio
async def test_preview_already_initialized():
    catalog = [_stock_row(code="A1", name="Item A", mid="mA", physical=100)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [["A1", "Item A", "PCS", 100, 1000]])
    preview = build_opening_import_preview(
        content, "t.xlsx", stock_rows=catalog, initialized_master_ids=["mA"],
    )
    assert preview["rows"][0]["status"] == STATUS_ALREADY_INITIALIZED
    assert preview["rows"][0]["can_apply"] is False


@pytest.mark.anyio
async def test_moving_average_opening_plus_receipt():
    """Opening 100@1000 + receipt 50@1200 → avg 1066.666..."""
    events = [
        {
            "event_id": "opening:mA",
            "source_type": SOURCE_TYPE,
            "source_id": "o1",
            "source_line_id": "mA",
            "item_identity": "m:mA",
            "recognized_at": "2026-01-01",
            "recognized_qty": 100,
            "unit_cost": 1000,
            "total_cost": 100000,
            "item_name": "A",
            "item_code": "A1",
            "master_item_id": "mA",
            "accurate_item_id": "",
        },
        {
            "event_id": "gr:1:l1",
            "source_type": "goods_receipt",
            "source_id": "gr1",
            "source_line_id": "l1",
            "item_identity": "m:mA",
            "recognized_at": "2026-01-02",
            "recognized_qty": 50,
            "unit_cost": 1200,
            "total_cost": 60000,
            "item_name": "A",
            "item_code": "A1",
            "master_item_id": "mA",
            "accurate_item_id": "",
        },
    ]
    layers, _cogs, state = moving_average_from_events(events)
    st = state["m:mA"]
    assert float(st["qty"]) == 150
    assert abs(float(st["average_cost"]) - (160000 / 150)) < 1e-9
    assert money_round(st["average_cost"]) == money_round(160000 / 150)


@pytest.mark.anyio
async def test_confirm_positive_stock_creates_layer_no_stock_mutation():
    db = server.db
    mid = "m-pos"
    code = "POS1"
    # Seed stock state
    await db.product_stock_state.insert_one({
        "id": str(uuid.uuid4()),
        "master_item_id": mid,
        "item_code": code,
        "physical_qty": 100.0,
        "unprinted_qty": 100.0,
        "printed_qty": 0.0,
    })
    await db.stock_movements.insert_one({
        "id": str(uuid.uuid4()),
        "master_item_id": mid,
        "qty": 100,
        "event_type": "OPENING_BALANCE",
    })
    await db.master_items.insert_one({
        "id": mid,
        "item_code": code,
        "item_name": "Pos Item",
        "unit": "PCS",
        "item_type": "Persediaan",
        "is_active": True,
    })
    catalog = [_stock_row(code=code, name="Pos Item", mid=mid, physical=100)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [[code, "Pos Item", "PCS", 100, 1000]])
    preview = build_opening_import_preview(
        content, "ok.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    assert preview["can_process"]

    stock_before = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    mov_before = await db.stock_movements.count_documents({})

    result = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="ok.xlsx",
        user={"id": "u1", "name": "Admin", "email": "a@t.com", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert result["success_count"] == 1
    assert result["stock_mutated"] is False
    assert result["stock_movements_mutated"] is False

    stock_after = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    assert stock_after["physical_qty"] == stock_before["physical_qty"]
    assert await db.stock_movements.count_documents({}) == mov_before

    opening = await db[OPENING_COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})
    assert opening["opening_qty"] == 100
    assert opening["opening_unit_cost"] == 1000
    assert opening["opening_total_cost"] == 100000
    assert opening["source_type"] == SOURCE_TYPE
    assert opening["cost_basis_only"] is False

    settings = await get_hpp_settings(db)
    assert settings["method"] == METHOD_AVG
    assert settings["recognition_point"] == RECOG_GR
    assert settings["active_version"]

    layers = await db[LAYERS].find(
        {"calculation_version": settings["active_version"], "master_item_id": mid},
        {"_id": 0},
    ).to_list(10)
    assert len(layers) >= 1
    layer = next(l for l in layers if l.get("source_type") == SOURCE_TYPE)
    assert layer["recognized_qty"] == 100
    assert layer["unit_cost"] == 1000
    assert layer["total_cost"] == 100000


@pytest.mark.anyio
async def test_confirm_zero_stock_no_fake_qty():
    db = server.db
    mid = "m-zero"
    code = "Z1"
    await db.product_stock_state.insert_one({
        "id": str(uuid.uuid4()),
        "master_item_id": mid,
        "item_code": code,
        "physical_qty": 0.0,
        "unprinted_qty": 0.0,
        "printed_qty": 0.0,
    })
    await db.master_items.insert_one({
        "id": mid, "item_code": code, "item_name": "Zero", "unit": "PCS",
        "item_type": "Persediaan", "is_active": True,
    })
    catalog = [_stock_row(code=code, name="Zero", mid=mid, physical=0)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [[code, "Zero", "PCS", 0, 1000]])
    preview = build_opening_import_preview(
        content, "z.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    result = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="z.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert result["success_count"] == 1
    opening = await db[OPENING_COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})
    assert opening["cost_basis_only"] is True
    assert opening["opening_qty"] == 0
    assert opening["opening_total_cost"] == 0
    assert opening["opening_unit_cost"] == 1000

    settings = await get_hpp_settings(db)
    layers = await db[LAYERS].find(
        {"calculation_version": settings["active_version"], "master_item_id": mid},
        {"_id": 0},
    ).to_list(10)
    assert len(layers) == 1
    assert layers[0]["recognized_qty"] == 0
    assert layers[0]["remaining_qty"] == 0
    assert layers[0]["cost_basis_only"] is True
    assert layers[0]["unit_cost"] == 1000

    stock = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    assert stock["physical_qty"] == 0


@pytest.mark.anyio
async def test_confirm_negative_stock_no_negative_layer():
    db = server.db
    mid = "m-neg"
    code = "N1"
    await db.product_stock_state.insert_one({
        "id": str(uuid.uuid4()),
        "master_item_id": mid,
        "item_code": code,
        "physical_qty": -3.0,
        "unprinted_qty": 0.0,
        "printed_qty": 0.0,
    })
    await db.master_items.insert_one({
        "id": mid, "item_code": code, "item_name": "Neg", "unit": "PCS",
        "item_type": "Persediaan", "is_active": True,
    })
    catalog = [_stock_row(code=code, name="Neg", mid=mid, physical=-3)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [[code, "Neg", "PCS", -3, 500]])
    preview = build_opening_import_preview(
        content, "n.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    result = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="n.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert result["success_count"] == 1
    opening = await db[OPENING_COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})
    assert opening["cost_basis_only"] is True
    assert opening["opening_qty"] == 0
    settings = await get_hpp_settings(db)
    layers = await db[LAYERS].find(
        {"calculation_version": settings["active_version"], "master_item_id": mid},
        {"_id": 0},
    ).to_list(10)
    assert all(float(l.get("recognized_qty") or 0) >= 0 for l in layers)
    assert all(float(l.get("remaining_qty") or 0) >= 0 for l in layers)
    stock = await db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    assert stock["physical_qty"] == -3.0


@pytest.mark.anyio
async def test_duplicate_import_idempotent_no_double_layer():
    db = server.db
    mid = "m-dup"
    code = "D1"
    await db.master_items.insert_one({
        "id": mid, "item_code": code, "item_name": "Dup", "unit": "PCS",
        "item_type": "Persediaan", "is_active": True,
    })
    catalog = [_stock_row(code=code, name="Dup", mid=mid, physical=10)]
    content = _xlsx_bytes(TEMPLATE_HEADERS, [[code, "Dup", "PCS", 10, 200]])
    preview = build_opening_import_preview(
        content, "d.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    r1 = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="d.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert r1["success_count"] == 1

    # Same batch request id → idempotent batch return
    r1b = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="d.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert r1b.get("idempotent") is True

    # New batch but already initialized → preview blocks
    preview2 = build_opening_import_preview(
        content, "d2.xlsx", stock_rows=catalog,
        initialized_master_ids=[mid],
    )
    assert preview2["rows"][0]["status"] == STATUS_ALREADY_INITIALIZED
    r2 = await confirm_opening_import(
        db,
        preview_rows=preview2["rows"],
        filename="d2.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview2["batch_request_id"],
    )
    assert r2["success_count"] == 0
    assert await db[OPENING_COLLECTION].count_documents({"master_item_id": mid, "status": "ACTIVE"}) == 1

    settings = await get_hpp_settings(db)
    opening_layers = await db[LAYERS].count_documents({
        "calculation_version": settings["active_version"],
        "master_item_id": mid,
        "source_type": SOURCE_TYPE,
        "cost_basis_only": {"$ne": True},
    })
    assert opening_layers == 1


@pytest.mark.anyio
async def test_invalid_rows_not_posted():
    db = server.db
    mid = "m-ok"
    await db.master_items.insert_one({
        "id": mid, "item_code": "OK1", "item_name": "OK", "unit": "PCS",
        "item_type": "Persediaan", "is_active": True,
    })
    catalog = [
        _stock_row(code="OK1", name="OK", mid=mid, physical=5),
    ]
    content = _xlsx_bytes(
        TEMPLATE_HEADERS,
        [
            ["OK1", "OK", "PCS", 5, 100],
            ["MISS", "x", "PCS", 1, None],
            ["OK1", "dup", "PCS", 5, 200],
        ],
    )
    preview = build_opening_import_preview(
        content, "mix.xlsx", stock_rows=catalog, initialized_master_ids=[],
    )
    # After duplicate marking, OK1 may be blocked — ensure no silent zero
    statuses = {r["status"] for r in preview["rows"]}
    assert STATUS_MISSING_COST in statuses or STATUS_DUPLICATE in statuses
    # Only can_apply rows post
    result = await confirm_opening_import(
        db,
        preview_rows=preview["rows"],
        filename="mix.xlsx",
        user={"id": "u1", "role": "admin"},
        batch_request_id=preview["batch_request_id"],
    )
    assert result["success_count"] == sum(1 for r in preview["rows"] if r["can_apply"])
    for r in preview["rows"]:
        if r["status"] in (STATUS_MISSING_COST, STATUS_INVALID_COST, STATUS_SKU_NOT_FOUND, STATUS_DUPLICATE):
            assert r["can_apply"] is False
