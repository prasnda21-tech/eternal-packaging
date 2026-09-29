"""Recognized statement header vs generic CSV. No customer data."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.cash_bank_statement import (  # noqa: E402
    StatementValidationError,
    interpret_rows,
    normalize_mapping,
    parse_amount,
    read_matrix,
)
from services.cash_bank_statement_layout import classify_matrix, suggested_mapping  # noqa: E402

FIXTURE = """Account No.,=,1000000001
Name,=,NAMA CONTOH
Currency,=,IDR

Date,Description,Branch,Amount,,Balance
'05/06/2026,"BIAYA ADMIN, CABANG
LANJUTAN URAIAN",'0000,5000.00,DB,95000.00
'28/09/2026,SETORAN TUNAI SAMA,'0000,10000.00,CR,105000.00
'28/09/2026,SETORAN TUNAI SAMA,'0000,10000.00,CR,115000.00
'PEND,TRANSFER CR 2909/REF/BBBB        5000.00LANJUTAN,'0000,5000.00,CR,120000.00
Starting Balance,=,100000.00
Credit,=,25000.00
Debet,=,5000.00
Ending Balance,=,120000.00
"""


def _recognized(text: str = FIXTURE, name: str = "mutasi.csv"):
    matrix = read_matrix(name, text.encode())
    classified = classify_matrix(matrix)
    assert classified["status"] == "recognized", classified
    mapping = normalize_mapping(suggested_mapping(classified))
    return interpret_rows(matrix, mapping), mapping


def test_fixture_preserves_rows_dates_amounts_and_balance_equation():
    parsed, mapping = _recognized()
    assert mapping["bank_txn_id_authoritative"] is False
    assert mapping["bank_txn_id_column"] is None
    assert mapping["reference_column"] is None
    assert mapping["file_currency"] == "IDR"
    assert parsed["errors"] == []
    assert parsed["accepted_count"] == 4
    assert parsed["opening_balance"] == 100000.00
    assert parsed["footer_ending_balance"] == 120000.00
    assert parsed["total_in"] == 25000.00
    assert parsed["total_out"] == 5000.00
    assert parsed["balance_equation_ok"] is True
    assert [row["date"] for row in parsed["accepted"]] == [
        "2026-06-05", "2026-09-28", "2026-09-28", "2026-09-29",
    ]
    assert [row["direction"] for row in parsed["accepted"]] == ["out", "in", "in", "in"]
    assert [row["amount"] for row in parsed["accepted"]] == [5000.00, 10000.00, 10000.00, 5000.00]
    assert [row["running_balance"] for row in parsed["accepted"]] == [
        95000.00, 105000.00, 115000.00, 120000.00,
    ]
    assert all(row["bank_txn_id"] is None for row in parsed["accepted"])
    description = parsed["accepted"][0]["description"]
    assert description == "BIAYA ADMIN, CABANG\nLANJUTAN URAIAN"
    assert parsed["accepted"][1]["description"] == parsed["accepted"][2]["description"] == "SETORAN TUNAI SAMA"
    assert all(item["consistent"] for item in parsed["balance_checks"])
    assert "1000000001" not in str(parsed["accepted"])
    assert "NAMA CONTOH" not in str(parsed["accepted"])


def test_same_bytes_are_recognized_under_another_filename():
    parsed, _mapping = _recognized(name="rekening-umum.csv")
    assert parsed["accepted_count"] == 4
    assert parsed["errors"] == []


def test_duplicate_header_is_rejected_with_line_numbers():
    text = FIXTURE.replace(
        "Date,Description,Branch,Amount,,Balance\n",
        "Date,Description,Branch,Amount,,Balance\nDate,Description,Branch,Amount,,Balance\n",
        1,
    )
    matrix = read_matrix("mutasi.csv", text.encode())
    classified = classify_matrix(matrix)
    assert classified["status"] == "ambiguous"
    assert [item["row"] for item in classified["errors"]] == [5, 6]


def test_ambiguous_amount_is_not_rescaled():
    text = FIXTURE.replace("5000.00,DB,95000.00", "1.234,DB,95000.00", 1)
    parsed, _mapping = _recognized(text)
    assert parsed["accepted_count"] < 4
    amount_errors = [item for item in parsed["errors"] if "Nominal ambigu" in item["message"]]
    assert amount_errors and amount_errors[0]["row"] == 6
    assert "1234" not in amount_errors[0]["message"]
    assert all(row["amount"] != 1234 and row["amount"] != 1.23 for row in parsed["accepted"])


def test_inconsistent_balance_is_explained_and_not_rewritten():
    text = """Account No.,=,1000000001
Name,=,NAMA CONTOH
Currency,=,IDR

Date,Description,Branch,Amount,,Balance
'28/09/2026,SETORAN SATU,'0000,100.00,CR,1100.00
'28/09/2026,SETORAN DUA,'0000,100.00,CR,1100.00
Starting Balance,=,1000.00
Credit,=,200.00
Debet,=,0.00
Ending Balance,=,1200.00
"""
    parsed, _mapping = _recognized(text)
    assert [row["amount"] for row in parsed["accepted"]] == [100.00]
    assert parsed["accepted"][0]["running_balance"] == 1100.00
    mismatch = [item for item in parsed["errors"] if "tidak sesuai" in item["message"]]
    assert len(mismatch) == 1
    assert mismatch[0]["row"] == 7
    assert "1100.00" in mismatch[0]["message"]
    assert "100.00" in mismatch[0]["message"]
    assert "1200.00" not in mismatch[0]["message"]
    assert parsed["balance_equation_ok"] is not True


def test_pending_without_day_month_is_not_guessed():
    text = FIXTURE.replace(
        "'PEND,TRANSFER CR 2909/REF/BBBB        5000.00LANJUTAN,'0000,5000.00,CR,120000.00",
        "'PEND,TRANSFER TANPA TANGGAL,'0000,5000.00,CR,120000.00",
    )
    parsed, _mapping = _recognized(text)
    pend = [item for item in parsed["errors"] if "PEND" in item["message"]]
    assert pend and pend[0]["row"] == 10
    assert all(row["date"] != "2026-09-29" for row in parsed["accepted"])


def test_pending_year_is_not_taken_from_a_different_year_or_filename():
    text = """Account No.,=,1000000001
Name,=,NAMA CONTOH
Currency,=,IDR

Date,Description,Branch,Amount,,Balance
'28/12/2026,SETORAN AKHIR,'0000,100.00,CR,1100.00
'PEND,TRANSFER CR 0101/REF/CC        100.00LANJUTAN,'0000,100.00,CR,1200.00
Starting Balance,=,1000.00
Credit,=,200.00
Debet,=,0.00
Ending Balance,=,1200.00
"""
    parsed, _mapping = _recognized(text)
    assert all(row["date"] != "2027-01-01" for row in parsed["accepted"])
    assert any("Tahun tidak ditebak" in item["message"] for item in parsed["errors"])


def test_two_explicit_years_leave_pending_undated():
    text = """Account No.,=,1000000001
Name,=,NAMA CONTOH
Currency,=,IDR

Date,Description,Branch,Amount,,Balance
'31/12/2025,SETORAN LAMA,'0000,100.00,CR,1100.00
'02/01/2026,SETORAN BARU,'0000,100.00,CR,1200.00
'PEND,TRANSFER CR 0301/REF/CC        100.00LANJUTAN,'0000,100.00,CR,1300.00
Starting Balance,=,1000.00
Credit,=,300.00
Debet,=,0.00
Ending Balance,=,1300.00
"""
    parsed, _mapping = _recognized(text)
    assert [row["date"] for row in parsed["accepted"]] == ["2025-12-31", "2026-01-02"]
    assert any("tidak tunggal" in item["message"] for item in parsed["errors"])


def test_missing_currency_is_not_filled_with_idr():
    text = FIXTURE.replace("Currency,=,IDR\n", "")
    parsed, mapping = _recognized(text)
    assert mapping["file_currency"] is None
    assert parsed["errors"] == []
    assert all(row["currency_code"] is None for row in parsed["accepted"])


def test_unsupported_currency_is_not_replaced():
    text = FIXTURE.replace("Currency,=,IDR", "Currency,=,XYZ")
    matrix = read_matrix("mutasi.csv", text.encode())
    classified = classify_matrix(matrix)
    assert classified["status"] == "ambiguous"
    assert any("tidak diganti" in item["message"] for item in classified["errors"])


def test_generic_csv_and_hash_shape_stay_unchanged():
    matrix = read_matrix(
        "umum.csv",
        b"tanggal,uraian,masuk,keluar,referensi,id,saldo\n2026-09-05,Transfer masuk,750000,,REF,TX-1,\n",
    )
    assert classify_matrix(matrix)["status"] == "generic"
    mapping = normalize_mapping({
        "header_row": 0, "date_column": 0, "date_format": "YYYY-MM-DD",
        "description_column": 1, "reference_column": 4, "bank_txn_id_column": 5,
        "bank_txn_id_authoritative": False, "amount_mode": "in_out",
        "in_column": 2, "out_column": 3, "balance_column": 6,
        "decimal_mode": "dot_decimal", "file_currency": "IDR",
    })
    assert "indicator_column" not in mapping
    parsed = interpret_rows(matrix, mapping)
    assert parsed["errors"] == []
    assert parsed["accepted"][0]["date"] == "2026-09-05"
    assert parsed["accepted"][0]["direction"] == "in"
    assert parsed["accepted"][0]["amount"] == 750000.0


def test_separator_modes_do_not_rescale_ambiguous_numbers():
    assert parse_amount("70000000.00", "dot_decimal") == Decimal("70000000.00")
    with pytest.raises(StatementValidationError, match="tidak diubah"):
        parse_amount("70000000.00", "comma_decimal")
    with pytest.raises(StatementValidationError, match="tidak dihapus"):
        parse_amount("12,50", "dot_decimal")
    assert parse_amount("1.234.567", "comma_decimal") == Decimal("1234567.00")
    assert parse_amount("1,234.56", "dot_decimal") == Decimal("1234.56")


def test_footer_and_header_are_not_transactions():
    parsed, _mapping = _recognized()
    descriptions = [row["description"] for row in parsed["accepted"]]
    assert "Starting Balance" not in descriptions
    assert "Date" not in descriptions
    assert parsed["accepted_count"] == 4
