"""Session Excel count import — preview, partial apply, zero vs missing."""
from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from services.stock_opname_count_import import (
    STATUS_MISSING,
    STATUS_READY,
    STATUS_REJECTED,
    build_count_import_preview,
    build_count_template_xlsx,
    parse_count_rows,
)


def _session(lines):
    return {
        "id": "sess-1",
        "warehouse_id": "wh-ygy-main",
        "opname_number": "SOP-TEST-001",
        "status": "COUNTING",
        "lines": lines,
    }


def _line(mid, code, book=100):
    return {
        "line_id": f"ln-{mid}",
        "master_item_id": mid,
        "item_code": code,
        "item_name": code,
        "base_uom": "PCS",
        "book_qty": book,
        "qty_pesanan": 0,
        "qty_belum_terkirim": 0,
    }


def _fill_template(session, counts_by_code: dict):
    raw = build_count_template_xlsx(session)
    wb = load_workbook(BytesIO(raw))
    ws = wb["Hitung Fisik"]
    # headers: Kode, Nama, Satuan, Buku, Pesanan, Belum, Stock Fisik, ...
    for row in ws.iter_rows(min_row=2):
        code = str(row[0].value or "").strip()
        if code in counts_by_code:
            row[6].value = counts_by_code[code]
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_template_binds_session_and_leaves_count_blank():
    session = _session([_line("m1", "100089", 50)])
    raw = build_count_template_xlsx(session)
    wb = load_workbook(BytesIO(raw))
    assert "Hitung Fisik" in wb.sheetnames
    assert wb["Sesi"]["A1"].value == "session_id"
    assert wb["Sesi"]["B1"].value == "sess-1"
    assert wb["Hitung Fisik"].cell(2, 7).value is None


def test_operational_template_defers_final_math_to_system_review():
    session = dict(_session([_line("m1", "SKU-A", 100)]), mode="operational")
    wb = load_workbook(BytesIO(build_count_template_xlsx(session)))
    sheet = wb["Hitung Fisik"]
    assert sheet["H2"].value == '=IF(G2="","","Lihat Review sistem")'
    assert sheet["I2"].value == '=IF(G2="","","Lihat Review sistem")'
    meta = dict(wb["Sesi"].values)
    assert meta["mode"] == "operational"
    assert "mutasi bersih" in meta["formula_note"]
    assert "stok buku saat posting" in meta["formula_note"]


def test_freeze_template_does_not_subtract_unknown_belum_as_zero():
    line = dict(_line("m1", "SKU-A", 100), qty_belum_terkirim=None)
    session = _session([line])
    wb = load_workbook(BytesIO(build_count_template_xlsx(session)))
    sheet = wb["Hitung Fisik"]
    assert sheet["F2"].value is None
    assert sheet["I2"].value == '=IF(AND(ISNUMBER(G2),ISNUMBER(E2),ISNUMBER(F2)),G2-E2-F2,"")'
    assert sheet["H2"].value == '=IF(AND(ISNUMBER(G2),ISNUMBER(D2)),G2-D2,"")'
    assert sheet["I1"].comment is not None


def test_preview_ready_zero_vs_missing_and_rejected_unknown():
    session = _session([
        _line("m1", "100089", 500),
        _line("m2", "100032", 10000),
        _line("m3", "100397", 1000),
    ])
    raw = _fill_template(session, {
        "100089": 500,   # zero delta READY
        "100032": 0,     # explicit zero READY
        # 100397 blank → MISSING
    })
    # Append unknown SKU row
    wb = load_workbook(BytesIO(raw))
    ws = wb["Hitung Fisik"]
    ws.append(["999999", "Unknown", "PCS", 0, 0, 0, 10, None, None, "nope"])
    buf = BytesIO()
    wb.save(buf)
    preview = build_count_import_preview(buf.getvalue(), session)
    assert preview["ready_count"] == 2
    assert preview["missing_count"] == 1
    assert preview["rejected_count"] == 1
    by_code = {r["item_code"]: r for r in preview["ready"] + preview["missing"] + preview["rejected"]}
    assert by_code["100089"]["status"] == STATUS_READY
    assert by_code["100089"]["difference_qty"] == 0
    assert by_code["100032"]["status"] == STATUS_READY
    assert by_code["100032"]["target_physical_qty"] == 0
    assert by_code["100032"]["difference_qty"] == -10000
    assert by_code["100397"]["status"] == STATUS_MISSING
    assert by_code["999999"]["status"] == STATUS_REJECTED
    assert preview["can_apply"] is True


def test_preview_positive_and_negative_delta():
    session = _session([_line("m1", "SKU-A", 100)])
    raw = _fill_template(session, {"SKU-A": 130})
    preview = build_count_import_preview(raw, session)
    assert preview["ready"][0]["difference_qty"] == 30
    raw2 = _fill_template(session, {"SKU-A": 40})
    preview2 = build_count_import_preview(raw2, session)
    assert preview2["ready"][0]["difference_qty"] == -60


def test_duplicate_sku_in_file_rejected_other_rows_ready():
    session = _session([_line("m1", "SKU-A", 10), _line("m2", "SKU-B", 20)])
    raw = build_count_template_xlsx(session)
    wb = load_workbook(BytesIO(raw))
    ws = wb["Hitung Fisik"]
    # set both counts then duplicate SKU-A
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "SKU-A":
            row[6].value = 11
        if row[0].value == "SKU-B":
            row[6].value = 22
    ws.append(["SKU-A", "dup", "PCS", 10, 0, 0, 99, None, None, "m1"])
    buf = BytesIO()
    wb.save(buf)
    preview = build_count_import_preview(buf.getvalue(), session)
    assert preview["ready_count"] == 2
    assert preview["rejected_count"] == 1
    assert any(r["status"] == STATUS_REJECTED and "duplikat" in (r.get("reason") or "") for r in preview["rejected"])


def test_wrong_session_or_warehouse_file_level_error():
    session = _session([_line("m1", "SKU-A", 1)])
    other = dict(session, id="other-sess")
    raw = build_count_template_xlsx(other)
    with pytest.raises(Exception) as ei:
        build_count_import_preview(raw, session)
    assert "bukan untuk sesi" in str(ei.value.message).lower() or "sesi" in str(ei.value.message).lower()


def test_parse_count_rows_only_ready():
    session = _session([_line("m1", "A", 5), _line("m2", "B", 5)])
    raw = _fill_template(session, {"A": 7})  # B missing
    counts = parse_count_rows(raw, session)
    assert len(counts) == 1
    assert counts[0]["physical_count_qty"] == 7


def test_composition_note_combined_physical_not_double_add():
    """Excel Stock Fisik is combined total — SSI composition stays formula-based."""
    session = _session([_line("m1", "100089", 100)])
    raw = _fill_template(session, {"100089": 100})
    preview = build_count_import_preview(raw, session)
    assert "gabungan" in (preview.get("note") or "").lower() or "polos" in (preview.get("note") or "").lower()
    assert preview["ready"][0]["target_physical_qty"] == 100
