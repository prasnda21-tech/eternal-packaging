"""BCA CSV preview. Synthetic fixtures only. Preview must not write."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ["DB_NAME"] = f"bca_preview_{uuid.uuid4().hex[:8]}"

import server
from server import app, current_user
from services.cash_bank_statement import IMPORTS, LINES, PREVIEWS, StatementValidationError
from services.cash_bank_statement_bca import HEADER_ALIASES, ROLES, parse_bca_statement
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES

pytestmark = pytest.mark.anyio

PREAMBLE = """Bank,BCA
Nama,Contoh Sintetis
Periode,01/09/2026 - 30/09/2026

Tanggal Transaksi,Keterangan,Debit,Kredit,Saldo
01/09/2026,Setoran modal,"1.500.000",,"10.000.000"
02/09/2026,Bunga rekening,,"1500000","11.500.000"
03-09-2026,Pembelian bahan,"1,500,000",,"10.000.000,50"
04/09/2026,Koreksi desimal,"1.500.000,50",,"8.499.999,50"
"""

WATCHED = (JOURNAL_ENTRIES, JOURNAL_LINES, IMPORTS, LINES, PREVIEWS, "cash_bank_manual_statement_balances")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _parse(text: str, filename: str = "bca-sintetis.csv"):
    return parse_bca_statement(filename, text.encode("utf-8"))


def _user(role="admin", override=None):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": f"{uid}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": override or {},
    }


async def _client(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _snapshot():
    names = set(await server.db.list_collection_names())
    counts = {name: await server.db[name].count_documents({}) for name in names}
    for name in WATCHED:
        counts.setdefault(name, await server.db[name].count_documents({}))
    return names, counts


def test_alias_dictionary_covers_required_labels():
    assert HEADER_ALIASES["tanggal"] == ("tanggal transaksi", "tanggal", "tgl transaksi")
    assert HEADER_ALIASES["keterangan"] == ("keterangan", "berita", "deskripsi")
    assert HEADER_ALIASES["debit"] == ("debit", "debet")
    assert HEADER_ALIASES["kredit"] == ("kredit", "credit")
    assert HEADER_ALIASES["saldo"] == ("saldo", "saldo akhir")
    assert ROLES == ("tanggal", "keterangan", "debit", "kredit", "saldo")


def test_header_found_after_preamble():
    parsed = _parse(PREAMBLE)
    assert parsed["header_detected"] is True
    assert parsed["header_row"] == 5
    assert parsed["bank"] == "BCA"
    assert parsed["rows"][0]["tanggal"] == "2026-09-01"
    assert parsed["rows"][0]["keterangan"] == "Setoran modal"
    assert parsed["persisted_statement_lines"] is False
    assert parsed["stored"] is False


def test_debit_and_credit_columns_map_to_normalized_rows():
    parsed = _parse(PREAMBLE)
    assert [(row["keterangan"], row["debit"], row["kredit"]) for row in parsed["rows"]] == [
        ("Setoran modal", 1500000, None),
        ("Bunga rekening", None, 1500000),
        ("Pembelian bahan", 1500000, None),
        ("Koreksi desimal", 1500000.5, None),
    ]


def test_debit_is_outgoing():
    row = _parse(PREAMBLE)["rows"][0]
    assert row["debit"] == 1500000
    assert row["kredit"] is None
    assert row["direction"] == "out"


def test_credit_is_incoming():
    row = _parse(PREAMBLE)["rows"][1]
    assert row["debit"] is None
    assert row["kredit"] == 1500000
    assert row["direction"] == "in"


def test_indonesian_amounts():
    parsed = _parse(PREAMBLE)
    assert parsed["rows"][0]["debit"] == 1500000
    assert parsed["rows"][1]["kredit"] == 1500000
    assert parsed["rows"][2]["debit"] == 1500000
    assert parsed["rows"][3]["debit"] == 1500000.5
    dotted = _parse(
        "Tanggal;Keterangan;Debit;Kredit;Saldo\n"
        "01/09/2026;Transfer keluar;1.500.000;;2500000\n"
    )
    assert dotted["rows"][0]["debit"] == 1500000
    assert dotted["rows"][0]["saldo"] == 2500000


def test_balance_is_read():
    parsed = _parse(PREAMBLE)
    assert [row["saldo"] for row in parsed["rows"]] == [10000000, 11500000, 10000000.5, 8499999.5]


def test_alternate_aliases_are_recognized():
    parsed = _parse(
        "Tgl Transaksi,Berita,Debet,Credit,Saldo Akhir\n"
        "01-09-2026,Alias lengkap,2500,,9000\n"
    )
    assert parsed["header_detected"] is True
    row = parsed["rows"][0]
    assert row["tanggal"] == "2026-09-01"
    assert row["keterangan"] == "Alias lengkap"
    assert row["debit"] == 2500
    assert row["direction"] == "out"
    assert row["saldo"] == 9000


def test_file_without_header_is_rejected():
    with pytest.raises(StatementValidationError) as caught:
        _parse("01/09/2026,Setoran,1500,,9000\n")
    message = str(caught.value)
    assert message.startswith("Format CSV BCA tidak dikenali:")
    assert "kolom tanggal tidak ditemukan" in message
    assert "kolom debit tidak ditemukan" in message


def test_incomplete_debit_or_credit_columns_are_rejected():
    with pytest.raises(StatementValidationError) as missing_credit:
        _parse("Tanggal,Keterangan,Debit,Saldo\n01/09/2026,Setoran,1500,9000\n")
    assert "kolom kredit tidak ditemukan" in str(missing_credit.value)
    with pytest.raises(StatementValidationError) as missing_debit:
        _parse("Tanggal Transaksi,Keterangan,Kredit,Saldo\n01/09/2026,Bunga,1500,9000\n")
    assert "kolom debit tidak ditemukan" in str(missing_debit.value)


def test_row_with_both_or_neither_amount_is_not_a_valid_transaction():
    parsed = _parse(
        "Tanggal,Keterangan,Debit,Kredit,Saldo\n"
        "01/09/2026,Keduanya,1500,200,9000\n"
        "02/09/2026,Kosong,,,9000\n"
        "03/09/2026,Valid,1500,,7500\n"
    )
    assert [row["keterangan"] for row in parsed["rows"]] == ["Valid"]
    assert parsed["rows"][0]["direction"] == "out"
    assert [err["message"] for err in parsed["errors"]] == [
        "Baris tidak boleh berisi debit dan kredit sekaligus",
        "Debit dan kredit kosong",
    ]


def test_pend_and_short_date_stay_invalid_without_inference():
    parsed = _parse(
        "Periode,01/09/2026 - 30/09/2026\n"
        "Tanggal,Keterangan,Debit,Kredit,Saldo\n"
        "PEND,Uraian menyebut 01/09/2026,1500,,9000\n"
        "01/09,Tahun tidak diambil dari baris lain,1500,,9000\n"
    )
    assert parsed["rows"] == []
    assert [err["message"] for err in parsed["errors"]] == ["Tanggal tidak valid", "Tanggal tidak valid"]


def test_parser_source_does_not_import_or_post():
    source = Path(__file__).resolve().parents[1].joinpath("services", "cash_bank_statement_bca.py").read_text()
    for banned in ("commit_import", "create_pair", "insert_one", "get_db", "journal_core"):
        assert banned not in source


@pytest.fixture
async def mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    yield server.db
    app.dependency_overrides.pop(current_user, None)
    await server.client.drop_database(os.environ["DB_NAME"])
    server.client.close()


async def test_preview_endpoint_does_not_write_or_create_journal(mongo_db):
    del mongo_db
    before_names, before_counts = await _snapshot()
    async with await _client(_user()) as client:
        response = await client.post(
            "/api/cash-bank/statements/bca/preview",
            files={"file": ("bca-sintetis.csv", PREAMBLE.encode("utf-8"), "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["bank"] == "BCA"
        assert body["header_detected"] is True
        assert body["persisted_statement_lines"] is False
        assert body["stored"] is False
        assert body["rows"][0]["direction"] == "out"
        assert "id" not in body
        assert "preview_id" not in body
    app.dependency_overrides[current_user] = lambda: _user(override={"cash_bank.reconcile": False})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/api/cash-bank/statements/bca/preview",
            files={"file": ("bca-sintetis.csv", PREAMBLE.encode("utf-8"), "text/csv")},
        )
    assert denied.status_code == 403
    rejected = None
    app.dependency_overrides[current_user] = lambda: _user()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/cash-bank/statements/bca/preview",
            files={"file": ("tanpa-header.csv", b"01/09/2026,Setoran,1500,,9000\n", "text/csv")},
        )
    assert rejected.status_code == 400
    assert "kolom debit tidak ditemukan" in rejected.text
    after_names, after_counts = await _snapshot()
    assert after_names == before_names
    assert after_counts == before_counts
    assert after_counts.get(JOURNAL_ENTRIES, 0) == 0
    assert after_counts.get(JOURNAL_LINES, 0) == 0
    assert after_counts.get(LINES, 0) == 0
    assert after_counts.get(IMPORTS, 0) == 0
