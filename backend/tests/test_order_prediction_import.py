"""Regression tests for Prediksi Order history import (CASE A–K)."""
from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.import_parser import ImportParseError
from services.order_prediction_import import (
    build_history_import_preview,
    read_history_rows,
    validate_history_apply_rows,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).resolve().parents[2] / "_preview_fixtures" / "Prediksi_Order_Customer_Eternal-2.xlsx"


def _xlsx_bytes(sheets: dict, *, active: str | None = None) -> bytes:
    wb = Workbook()
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
        ("Bad Row", None, None, None, None, None, None, None, None),
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


def test_case_a_data_order_preferred():
    data = _eternal_like_workbook(put_data_first=False)
    headers, rows, sheet, _skipped, header_row = read_history_rows(data, "Prediksi_Order_Customer_Eternal-2.xlsx")
    assert sheet == "DATA_ORDER"
    assert header_row == 4
    assert "Nama Customer" in headers
    preview = build_history_import_preview(
        data, "Prediksi_Order_Customer_Eternal-2.xlsx",
        live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["sheet_name"] == "DATA_ORDER"
    assert preview["header_row"] == 4
    assert preview["valid_count"] >= 2
    assert preview["rows"][0]["customer"] == "Kopi Nusantara"


def test_case_b_data_order_not_first_sheet():
    data = _eternal_like_workbook(put_data_first=False)
    headers, _rows, sheet, _sk, _hr = read_history_rows(data, "multi.xlsx")
    assert sheet == "DATA_ORDER"
    assert headers[0] == "Nama Customer"
    preview = build_history_import_preview(
        data, "multi.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert all(r["customer"] != "Parameter" for r in preview["rows"])


def test_case_c_header_not_first_row():
    data = _eternal_like_workbook()
    _h, _r, _s, _sk, header_row = read_history_rows(data, "x.xlsx")
    assert header_row == 4


def test_case_d_title_blank_safe():
    data = _eternal_like_workbook()
    preview = build_history_import_preview(
        data, "x.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["valid_count"] >= 2


def test_case_e_excel_serial_date():
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


def test_case_f_dd_mm_yyyy():
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


def test_case_g_omzet_blank_null():
    data = _eternal_like_workbook()
    preview = build_history_import_preview(
        data, "hist.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    by_cust_date = {(r["customer"], r["order_date"]): r for r in preview["rows"]}
    assert by_cust_date[("Kopi Nusantara", "2026-02-25")]["omzet"] is None
    assert by_cust_date[("Toko Sejahtera", "2026-03-01")]["omzet"] is None
    assert by_cust_date[("Kopi Nusantara", "2026-02-16")]["omzet"] == 1160000


def test_case_h_partial_invalid_keeps_valid():
    data = _eternal_like_workbook()
    preview = build_history_import_preview(
        data, "partial.xlsx", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["valid_count"] >= 2
    assert preview["invalid_count"] >= 1


def test_case_i_duplicate_live_order():
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


def test_case_j_future_date_invalid():
    csv = b"customer,outlet,tanggal_order\nKopi,K,2099-01-01\n"
    preview = build_history_import_preview(
        csv, "h.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["rows"][0]["status"] == "INVALID"


def test_case_k_malformed_safe_error():
    with pytest.raises(ImportParseError) as ei:
        build_history_import_preview(
            b"not-an-xlsx", "bad.xlsx", live_orders=[], existing_imports=[], now=NOW,
        )
    msg = str(ei.value).lower()
    assert "traceback" not in msg
    assert "file excel" in msg or "tidak dapat" in msg or "sheet" in msg


def test_outlet_optional_still_valid():
    csv = b"Nama Customer,Tanggal Order,Omzet\nCafe Only,2026-01-15,\n"
    preview = build_history_import_preview(
        csv, "no-outlet.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    assert preview["rows"][0]["status"] == "VALID"
    assert preview["rows"][0]["outlet"] == ""
    assert preview["rows"][0]["omzet"] is None


def test_empty_rows_skipped():
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
    assert "sheet" in str(ei.value).lower() or "header" in str(ei.value).lower() or "histori" in str(ei.value).lower()


def test_apply_selected_only():
    csv = (
        b"customer,outlet,tanggal_order,no_po,omzet\n"
        b"Kopi,Kemang,2026-01-01,H1,\n"
        b"Kopi,Kemang,2026-01-10,H2,1500\n"
    )
    preview = build_history_import_preview(
        csv, "h.csv", live_orders=[], existing_imports=[], now=NOW,
    )
    accepted, _ = validate_history_apply_rows(
        [preview["rows"][0]], live_orders=[], existing_imports=[], now=NOW,
    )
    assert len(accepted) == 1
    assert accepted[0]["omzet"] is None
    assert accepted[0]["po_number"] == "H1"


def test_exact_user_xlsx_if_present():
    if not FIXTURE.exists():
        pytest.skip("exact user xlsx not in fixtures")
    data = FIXTURE.read_bytes()
    import time
    t0 = time.time()
    preview = build_history_import_preview(
        data, FIXTURE.name, live_orders=[], existing_imports=[], now=NOW,
    )
    elapsed = time.time() - t0
    assert preview["sheet_name"] == "DATA_ORDER"
    assert preview["header_row"] == 4
    assert preview["valid_count"] > 100
    assert preview["total"] < 20000
    assert preview["total"] >= 1000
    assert elapsed < 30, f"parse too slow: {elapsed}s"
    assert preview["rows"][0]["order_date"] and "-" in preview["rows"][0]["order_date"]
    assert preview["rows"][0]["customer"]
