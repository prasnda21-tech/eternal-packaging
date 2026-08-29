"""Regression tests for Prediksi Order history import (CASE A–I)."""
from __future__ import annotations

import io
from datetime import date, datetime, timezone

import pytest
from openpyxl import Workbook

from services.import_parser import ImportParseError
from services.order_prediction_import import (
    build_history_import_preview,
    read_history_rows,
    validate_history_apply_rows,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _xlsx_bytes(sheets: dict, *, active: str | None = None) -> bytes:
    """Build multi-sheet workbook. sheets: name -> list of row tuples."""
    wb = Workbook()
    # remove default
    default = wb.active
    wb.remove(default)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for r in rows:
            ws.append(list(r))
    if active and active in wb.sheetnames:
        wb.active = wb[active]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _eternal_like_workbook(*, put_data_first: bool = False) -> bytes:
    """Mimic Prediksi_Order_Customer_Eternal-2.xlsx layout."""
    petunjuk = [
        ("TEMPLATE", None, None),
        ("LANGKAH", "YANG DIISI", "KETERANGAN"),
    ]
    data_order = [
        ("INPUT DATA ORDER CUSTOMER", None, None, None, None, None, None, None, None),
        ("Isi kolom berwarna putih.", None, None, None, None, None, None, None, None),
        (None, None, None, None, None, None, None, None, None),
        (
            "Nama Customer", "Cabang", "ID Customer (Otomatis)", "Tanggal Order",
            "Omzet", "Tanggal Order Sebelumnya", "Jarak Order (Hari)", "Sales", "Catatan",
        ),
        ("Kopi Nusantara", "Kemang", "Kopi Nusantara|Kemang", date(2026, 2, 16), 1160000, None, None, "Andi", None),
        ("Kopi Nusantara", "Kemang", "Kopi Nusantara|Kemang", date(2026, 2, 25), None, None, None, "Andi", "-"),
        ("Toko Sejahtera", "Yogyakarta", "Toko Sejahtera|Yogyakarta", date(2026, 3, 1), "-", None, None, None, None),
        ("Bad Row", None, None, None, None, None, None, None, None),  # missing outlet+date → INVALID
        (None, None, None, None, None, None, None, None, None),
    ]
    analisis = [
        ("ANALISIS", None),
        ("ID Customer", "Nama Customer", "Outlet/Cabang"),
        ("x", "y", "z"),
    ]
    parameter = [
        ("PARAMETER PREDIKSI ORDER", None, None, None),
        ("Parameter", "Nilai", "Satuan", "Kegunaan"),
        ("Minimum order untuk pola", 3, "order", "x"),
    ]
    if put_data_first:
        sheets = {
            "DATA_ORDER": data_order,
            "PETUNJUK": petunjuk,
            "ANALISIS": analisis,
            "PARAMETER": parameter,
        }
        active = "DATA_ORDER"
    else:
        sheets = {
            "PETUNJUK": petunjuk,
            "DATA_ORDER": data_order,
            "ANALISIS": analisis,
            "PARAMETER": parameter,
        }
        active = "PARAMETER"
    return _xlsx_bytes(sheets, active=active)


# ---------- CASE A: DATA_ORDER preferred ----------
def test_case_a_data_order_sheet_preferred():
    data = _eternal_like_workbook(put_data_first=False)
    headers, rows, sheet, _skipped = read_history_rows(data, "Prediksi_Order_Customer_Eternal-2.xlsx")
    assert sheet == "DATA_ORDER"
    assert "Nama Customer" in headers
    assert "Cabang" in headers
    preview = build_history_import_preview(
        data, "Prediksi_Order_Customer_Eternal-2.xlsx",
        live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["sheet_name"] == "DATA_ORDER"
    assert preview["valid_count"] >= 2
    assert preview["rows"][0]["customer"] == "Kopi Nusantara"
    assert preview["rows"][0]["outlet"] == "Kemang"
    assert preview["rows"][0]["order_date"] == "2026-02-16"


# ---------- CASE B: omzet null ----------
def test_case_b_omzet_empty_and_dash_null():
    data = _eternal_like_workbook()
    preview = build_history_import_preview(
        data, "hist.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    by_cust_date = {(r["customer"], r["order_date"]): r for r in preview["rows"]}
    assert by_cust_date[("Kopi Nusantara", "2026-02-25")]["omzet"] is None
    assert by_cust_date[("Toko Sejahtera", "2026-03-01")]["omzet"] is None
    assert by_cust_date[("Kopi Nusantara", "2026-02-16")]["omzet"] == 1160000


# ---------- CASE C: Excel serial date ----------
def test_case_c_excel_serial_date():
    # 2026-08-29 ≈ serial 46263 (Excel 1900 system / openpyxl origin)
    serial = (date(2026, 8, 29) - date(1899, 12, 30)).days
    sheets = {
        "OTHER": [("x", "y")],
        "DATA_ORDER": [
            ("Nama Customer", "Cabang", "Tanggal Order", "Omzet"),
            ("Serial Co", "Outlet A", serial, 1000),
        ],
    }
    data = _xlsx_bytes(sheets, active="OTHER")
    preview = build_history_import_preview(
        data, "serial.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["rows"][0]["status"] == "VALID"
    assert preview["rows"][0]["order_date"] == "2026-08-29"


# ---------- CASE D: DD/MM/YYYY (not MM/DD) ----------
def test_case_d_dd_mm_yyyy():
    csv = (
        "Nama Customer,Cabang / Outlet,Tanggal Order,Omzet\n"
        "Cafe A,Kemang,29/08/2026,1000\n"
        "Cafe B,Tebet,13/02/2026,\n"
    ).encode("utf-8")
    preview = build_history_import_preview(
        csv, "dmy.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["rows"][0]["order_date"] == "2026-08-29"
    assert preview["rows"][1]["order_date"] == "2026-02-13"
    assert preview["rows"][1]["omzet"] is None


# ---------- CASE E: empty rows skipped ----------
def test_case_e_empty_rows_skipped():
    csv = (
        "customer,outlet,tanggal_order,omzet\n"
        "\n"
        "Kopi,Kemang,2026-01-01,10\n"
        ",,,\n"
        "Kopi,Kemang,2026-01-10,\n"
    ).encode("utf-8")
    preview = build_history_import_preview(
        csv, "empty.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["total"] == 2
    assert preview["skipped_empty"] >= 1


# ---------- CASE F: partial invalid does not fail workbook ----------
def test_case_f_partial_invalid_keeps_valid():
    data = _eternal_like_workbook()
    preview = build_history_import_preview(
        data, "partial.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["valid_count"] >= 2
    assert preview["invalid_count"] >= 1
    statuses = {r["status"] for r in preview["rows"]}
    assert "VALID" in statuses
    assert "INVALID" in statuses


# ---------- CASE G: multi-sheet DATA_ORDER not first / not active ----------
def test_case_g_data_order_not_first_sheet():
    data = _eternal_like_workbook(put_data_first=False)
    headers, rows, sheet, _skipped = read_history_rows(data, "multi.xlsx")
    assert sheet == "DATA_ORDER"
    # Must NOT read PARAMETER headers
    assert headers[0] == "Nama Customer"
    preview = build_history_import_preview(
        data, "multi.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert all(r["customer"] != "Parameter" for r in preview["rows"])
    assert preview["sheet_name"] == "DATA_ORDER"


# ---------- CASE H: CSV no regression ----------
def test_case_h_csv_no_regression():
    csv = (
        b"customer,outlet,tanggal_order,no_po,omzet\n"
        b"Kopi,Kemang,2026-01-01,H1,\n"
        b"Kopi,Kemang,2026-01-10,H2,1500\n"
        b"Kopi,Kemang,2099-01-01,HF,1000\n"
    )
    preview = build_history_import_preview(
        csv, "h.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["total"] == 3
    assert preview["valid_count"] == 2
    assert preview["rows"][0]["omzet"] is None
    assert preview["rows"][1]["omzet"] == 1500
    assert preview["rows"][2]["status"] == "INVALID"
    accepted, _ = validate_history_apply_rows(
        [preview["rows"][0]], live_orders=[], existing_imports=[], now=NOW,
    )
    assert accepted[0]["omzet"] is None


# ---------- CASE I: duplicate ----------
def test_case_i_duplicate_rules():
    existing = [{"id": "h1", "customer": "Kopi", "outlet": "K", "order_date": "2026-01-01", "po_number": "H1"}]
    live = [{
        "id": "o1", "po_number": "PO-LIVE", "customer": "Kopi", "outlet": "K",
        "order_date": "2026-01-01",
    }]
    csv = (
        b"customer,outlet,tanggal_order,no_po\n"
        b"Kopi,K,2026-01-01,H1\n"
        b"Kopi,K,2026-01-01,PO-LIVE\n"
        b"Kopi,K,2026-01-02,H2\n"
        b"Kopi,K,2026-01-02,H2\n"
    )
    preview = build_history_import_preview(
        csv, "dup.csv", live_orders=live, existing_imports=existing, now=NOW,
    )
    assert preview["rows"][0]["status"] == "DUPLIKAT"
    assert preview["rows"][1]["status"] == "DUPLIKAT"
    assert preview["rows"][2]["status"] == "VALID"
    assert preview["rows"][3]["status"] == "DUPLIKAT"
    assert preview["rows"][2]["selectable"] is True
    assert preview["rows"][0]["selectable"] is False


def test_real_sample_xlsx_if_present():
    from pathlib import Path
    p = Path(r"C:\Users\Hype\Downloads\Prediksi_Order_Customer_Eternal-2.xlsx")
    if not p.exists():
        pytest.skip("sample xlsx not present")
    data = p.read_bytes()
    preview = build_history_import_preview(
        data, p.name, live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["sheet_name"] == "DATA_ORDER"
    assert preview["valid_count"] > 100
    assert preview["total"] < 20000  # must not ingest million empty formula rows
    assert preview["total"] == 3402 or preview["total"] > 1000
    # dates parsed (not raw serial strings)
    assert preview["rows"][0]["order_date"] and "-" in preview["rows"][0]["order_date"]
    assert preview["rows"][0]["customer"]
    assert preview["rows"][0]["outlet"]


def test_future_date_invalid_still():
    csv = b"customer,outlet,tanggal_order\nKopi,K,2099-01-01\n"
    preview = build_history_import_preview(
        csv, "h.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["rows"][0]["status"] == "INVALID"


def test_missing_history_headers_raises_clear_error():
    data = _xlsx_bytes({
        "PARAMETER": [
            ("Parameter", "Nilai"),
            ("x", 1),
        ]
    }, active="PARAMETER")
    with pytest.raises(ImportParseError) as ei:
        build_history_import_preview(
            data, "bad.xlsx", live_orders=[], existing_imports=[], now=NOW,
        )
    assert "header" in str(ei.value).lower() or "DATA_ORDER" in str(ei.value)
