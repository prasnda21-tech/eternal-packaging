"""Stock Opname Excel import/export — Stock Engine V2."""
from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook

from services.stock_opname_import import (
    apply_opname_import_batch,
    build_opname_import_preview,
    build_opname_template_xlsx,
    revalidate_opname_apply_rows,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _xlsx_bytes(headers: List[str], rows: List[List[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _stock_row(
    *,
    code: str,
    name: str,
    mid: str,
    physical: float,
    unprinted: float,
    printed: float,
    outstanding: float,
    initialized: bool = True,
) -> dict:
    available = max(physical - outstanding, 0)
    return {
        "item_code": code,
        "item_name": name,
        "unit": "PCS",
        "master_item_id": mid,
        "physical_qty": physical,
        "unprinted_qty": unprinted,
        "printed_qty": printed,
        "outstanding_so_qty": outstanding,
        "stock_current": available,
        "available_qty": available,
        "initialized": initialized,
    }


def _catalog_10() -> List[dict]:
    rows = []
    for i in range(1, 11):
        phys = 100.0 + i
        printed = 20.0
        unprinted = phys - printed
        outstanding = 10.0 if i % 3 == 0 else 0.0
        rows.append(
            _stock_row(
                code=f"SKU-{i:03d}",
                name=f"Item {i}",
                mid=f"m{i}",
                physical=phys,
                unprinted=unprinted,
                printed=printed,
                outstanding=outstanding,
            )
        )
    return rows


def test_export_template_columns_only():
    rows = _catalog_10()
    content = build_opname_template_xlsx(rows)
    wb = load_workbook(BytesIO(content))
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert headers == ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"]
    assert ws.max_row == 1 + len(rows)
    # Counted column empty for user fill
    assert ws.cell(row=2, column=4).value is None
    assert ws.cell(row=2, column=1).value == "SKU-001"
    assert ws.protection.sheet is True
    assert ws.cell(row=2, column=3).protection.locked is True
    assert ws.cell(row=2, column=4).protection.locked is False


def test_apply_rows_are_revalidated_against_live_catalog():
    catalog = _catalog_10()[:1]
    rows = [{
        "item_code": "SKU-001",
        "item_name": "Tampered",
        "master_item_id": "forged-master",
        "stock_physical_before": 99999,
        "stock_physical_counted": 120,
        "can_apply": True,
    }]
    validated = revalidate_opname_apply_rows(
        rows,
        stock_rows=catalog,
        batch_request_id="batch-1",
    )
    assert validated[0]["master_item_id"] == "m1"
    assert validated[0]["item_name"] == "Item 1"
    assert validated[0]["stock_physical_before"] == 101
    assert validated[0]["client_request_id"] == "batch-1:sku-001"


def test_preview_10_items_uses_physical_count_directly():
    catalog = _catalog_10()
    data_rows = [[r["item_code"], r["item_name"], r["physical_qty"], r["physical_qty"] + 5] for r in catalog]
    raw = _xlsx_bytes(["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"], data_rows)
    preview = build_opname_import_preview(raw, "opname.xlsx", stock_rows=catalog)

    assert preview["total_rows"] == 10
    assert preview["ok_count"] == 10
    assert preview["can_process"] is True
    assert preview["error_count"] == 0
    assert preview["block_count"] == 0

    for row, src in zip(preview["rows"], catalog):
        assert row["status"] == "OK"
        assert row["can_apply"] is True
        target_physical = src["physical_qty"] + 5
        assert row["stock_physical_counted"] == target_physical
        assert row["target_physical_qty"] == target_physical
        assert row["printed_qty"] == src["printed_qty"]


def test_preview_invalid_sku():
    catalog = _catalog_10()
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["NOPE-999", "Ghost", 0, 10]],
    )
    preview = build_opname_import_preview(raw, "bad.xlsx", stock_rows=catalog)
    assert preview["block_count"] == 1
    assert preview["can_process"] is False
    assert preview["rows"][0]["status"] == "BLOCK"
    assert "TIDAK DITEMUKAN" in preview["rows"][0]["message"]


def test_preview_duplicate_sku():
    catalog = _catalog_10()
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [
            ["SKU-001", "A", 101, 110],
            ["SKU-001", "A again", 101, 120],
        ],
    )
    preview = build_opname_import_preview(raw, "dup.xlsx", stock_rows=catalog)
    assert preview["rows"][0]["status"] == "BLOCK"
    assert preview["rows"][1]["status"] == "BLOCK"
    assert "DUPLIKAT" in preview["rows"][1]["message"]
    assert preview["ok_count"] == 0
    assert preview["block_count"] == 2


def test_preview_empty_and_negative_qty():
    catalog = _catalog_10()
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [
            ["SKU-001", "A", 101, ""],
            ["SKU-002", "B", 102, -5],
            ["SKU-003", "C", 93, "abc"],
        ],
    )
    preview = build_opname_import_preview(raw, "qty.xlsx", stock_rows=catalog)
    assert preview["block_count"] == 3
    msgs = {r["item_code"]: r["message"] for r in preview["rows"]}
    assert "KOSONG" in msgs["SKU-001"]
    assert "NEGATIF" in msgs["SKU-002"]
    assert ("BUKAN ANGKA" in msgs["SKU-003"]) or ("TIDAK VALID" in msgs["SKU-003"])


def test_preview_accepts_physical_hasil_hitung_header():
    catalog = _catalog_10()[:1]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK HASIL HITUNG"],
        [["SKU-001", "Item 1", catalog[0]["physical_qty"] + 1]],
    )
    preview = build_opname_import_preview(raw, "legacy.xlsx", stock_rows=catalog)
    assert preview["ok_count"] == 1
    assert preview["rows"][0]["status"] == "OK"


def test_preview_blocks_physical_count_below_printed_bucket():
    catalog = [
        _stock_row(
            code="SKU-P",
            name="Printed Heavy",
            mid="mp",
            physical=100,
            unprinted=20,
            printed=80,
            outstanding=5,
        )
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["SKU-P", "Printed Heavy", 100, 0]],
    )
    preview = build_opname_import_preview(raw, "block.xlsx", stock_rows=catalog)
    assert preview["block_count"] == 1
    assert preview["ok_count"] == 0
    assert preview["can_process"] is False
    row = preview["rows"][0]
    assert row["status"] == "BLOCK"
    assert row["stock_physical_counted"] == 0
    assert row["target_physical_qty"] == 0
    assert row["printed_qty"] == 80


def test_preview_outstanding_so_and_partial_delivery_style():
    """Outstanding SO reduces Available; physical opname does not touch SO."""
    catalog = [
        _stock_row(
            code="SKU-SO",
            name="With SO",
            mid="mso",
            physical=100,
            unprinted=70,
            printed=30,
            outstanding=25,  # ordered 40, shipped 15 → outstanding 25
        )
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["SKU-SO", "With SO", 100, 90]],
    )
    preview = build_opname_import_preview(raw, "so.xlsx", stock_rows=catalog)
    row = preview["rows"][0]
    assert row["status"] == "OK"
    assert row["outstanding_so_qty"] == 25
    assert row["stock_physical_counted"] == 90
    assert row["target_physical_qty"] == 90
    assert row["difference_qty"] == -10


def test_preview_unchanged_when_same_physical():
    catalog = [
        _stock_row(
            code="SKU-U",
            name="Same",
            mid="mu",
            physical=100,
            unprinted=60,
            printed=40,
            outstanding=0,
        )
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["SKU-U", "Same", 100, 100]],
    )
    preview = build_opname_import_preview(raw, "same.xlsx", stock_rows=catalog)
    assert preview["unchanged_count"] == 1
    assert preview["ok_count"] == 0
    assert preview["can_process"] is False
    assert preview["rows"][0]["status"] == "UNCHANGED"


def test_preview_zero_count_for_uninitialized_item_creates_opening():
    catalog = [
        _stock_row(
            code="SKU-NEW",
            name="Not Initialized",
            mid="m-new",
            physical=0,
            unprinted=0,
            printed=0,
            outstanding=0,
            initialized=False,
        )
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["SKU-NEW", "Not Initialized", 0, 0]],
    )
    preview = build_opname_import_preview(raw, "first.xlsx", stock_rows=catalog)

    assert preview["ok_count"] == 1
    assert preview["can_process"] is True
    assert preview["rows"][0]["can_apply"] is True
    assert preview["rows"][0]["opname_action"] == "SALDO_AWAL"


def test_preview_target_physical_does_not_add_qty_pesanan():
    catalog = [
        _stock_row(
            code="SKU-D",
            name="Double",
            mid="md",
            physical=200,
            unprinted=150,
            printed=50,
            outstanding=40,
        )
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA", "STOCK FISIK HASIL OPNAME"],
        [["SKU-D", "Double", 140]],
    )
    preview = build_opname_import_preview(raw, "d.xlsx", stock_rows=catalog)
    row = preview["rows"][0]
    assert row["stock_physical_counted"] == 140
    assert row["target_physical_qty"] == 140


@pytest.mark.anyio
async def test_apply_batch_calls_v2_opname_and_audits():
    catalog = _catalog_10()[:3]
    data_rows = [[r["item_code"], r["item_name"], r["physical_qty"], r["physical_qty"] + 2] for r in catalog]
    raw = _xlsx_bytes(["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"], data_rows)
    preview = build_opname_import_preview(raw, "batch.xlsx", stock_rows=catalog)

    db = MagicMock()
    batches = MagicMock()
    batches.insert_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(return_value=batches)

    # Patch apply_stock_opname used inside module
    import services.stock_opname_import as mod

    calls = []

    async def fake_apply(db_, **kwargs):
        calls.append(kwargs)
        return {"unchanged": False, "message": "ok"}

    original = mod.apply_stock_opname
    mod.apply_stock_opname = fake_apply
    try:
        result = await apply_opname_import_batch(
            db,
            preview_rows=preview["rows"],
            filename="batch.xlsx",
            user={"id": "u1", "email": "a@b.c", "name": "Admin"},
            note="test batch",
            valuation_rows=[],
        )
    finally:
        mod.apply_stock_opname = original

    assert result["success_count"] == 3
    assert result["failure_count"] == 0
    assert len(calls) == 3
    for call, src in zip(calls, catalog):
        assert call["stock_physical_counted"] == src["physical_qty"] + 2
        assert call["outstanding_so_qty"] == src["outstanding_so_qty"]
        assert call["master_item_id"] == src["master_item_id"]

    batches.insert_one.assert_awaited_once()
    batch_doc = batches.insert_one.await_args.args[0]
    assert batch_doc["filename"] == "batch.xlsx"
    assert batch_doc["uploaded_by"]["email"] == "a@b.c"
    assert batch_doc["success_count"] == 3
    assert batch_doc["jumlah_item"] == 3
    assert batch_doc["failed_count"] == 0
    assert batch_doc["stock_engine"] == "v2"


@pytest.mark.anyio
async def test_apply_skips_block_and_error_rows():
    catalog = [
        _stock_row(code="OK1", name="Ok", mid="m1", physical=100, unprinted=80, printed=20, outstanding=0),
        _stock_row(code="BLK", name="Blk", mid="m2", physical=100, unprinted=20, printed=80, outstanding=0),
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [
            ["OK1", "Ok", 100, 110],
            ["BLK", "Blk", 100, 50],
            ["GHOST", "Nope", 0, 1],  # error
        ],
    )
    preview = build_opname_import_preview(raw, "mix.xlsx", stock_rows=catalog)
    assert preview["ok_count"] == 1
    assert preview["block_count"] == 2
    assert preview["error_count"] == 0

    db = MagicMock()
    batches = MagicMock()
    batches.insert_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(return_value=batches)

    import services.stock_opname_import as mod

    calls = []

    async def fake_apply(db_, **kwargs):
        calls.append(kwargs)
        return {"unchanged": False, "message": "ok"}

    original = mod.apply_stock_opname
    mod.apply_stock_opname = fake_apply
    try:
        result = await apply_opname_import_batch(
            db,
            preview_rows=preview["rows"],
            filename="mix.xlsx",
            user={"email": "a@b.c"},
            valuation_rows=[],
        )
    finally:
        mod.apply_stock_opname = original

    assert len(calls) == 1
    assert calls[0]["master_item_id"] == "m1"
    assert result["success_count"] == 1
    assert result["skipped_count"] == 2


def test_missing_required_columns_raises():
    catalog = _catalog_10()
    raw = _xlsx_bytes(["KODE", "NAMA"], [["SKU-001", "A"]])
    with pytest.raises(HTTPException) as ei:
        build_opname_import_preview(raw, "bad.xlsx", stock_rows=catalog)
    assert ei.value.status_code == 400


def test_export_includes_stock_saat_ini_system_and_empty_target():
    rows = _catalog_10()
    content = build_opname_template_xlsx(rows)
    wb = load_workbook(BytesIO(content))
    ws = wb.active
    assert [c.value for c in ws[1]] == [
        "KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME",
    ]
    assert ws.cell(row=2, column=3).value == rows[0]["physical_qty"]
    assert ws.cell(row=2, column=4).value is None


def test_filter_active_masters_only():
    from services.stock_opname_import import filter_active_stock_rows
    masters = [
        {"id": "m1", "item_code": "A", "item_name": "Active", "unit": "PCS", "is_active": True},
        {"id": "m2", "item_code": "B", "item_name": "Off", "unit": "PCS", "is_active": False},
        {"id": "m3", "item_code": "C", "item_name": "Unknown", "unit": "BOX", "is_active": None},
    ]
    stock = [
        _stock_row(code="A", name="old", mid="m1", physical=9, unprinted=9, printed=0, outstanding=0),
    ]
    cat = filter_active_stock_rows(stock, masters)
    codes = [r["item_code"] for r in cat]
    assert codes == ["A", "C"]
    assert cat[0]["item_name"] == "Active"  # master name wins
    assert cat[1]["physical_qty"] == 0.0


def test_preview_uses_master_name_not_file_name():
    catalog = [
        _stock_row(code="SKU-001", name="Nama Master Resmi", mid="m1", physical=100, unprinted=80, printed=20, outstanding=0),
    ]
    raw = _xlsx_bytes(
        ["KODE", "NAMA BARANG", "STOCK FISIK SISTEM", "STOCK FISIK HASIL OPNAME"],
        [["SKU-001", "Nama Salah di Excel", 100, 110]],
    )
    preview = build_opname_import_preview(raw, "name.xlsx", stock_rows=catalog)
    assert preview["ok_count"] == 1
    assert preview["rows"][0]["item_name"] == "Nama Master Resmi"
    assert preview["rows"][0]["stock_physical_counted"] == 110
