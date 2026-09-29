"""BCA CSV preview. Synthetic fixtures only. Preview must not write."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import json

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
from services.cash_bank_master import ensure_indexes as ensure_cash_bank_indexes
from services.cash_bank_reconciliation import RECON_MATCHES, ensure_indexes as ensure_recon_indexes
from services.cash_bank_statement import IMPORTS, LINES, PREVIEWS, StatementValidationError, ensure_indexes as ensure_statement_indexes
from services.cash_bank_statement_bca import HEADER_ALIASES, ROLES, parse_bca_statement, statement_rows_from_bca
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa_indexes
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal_indexes

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
    assert HEADER_ALIASES["jumlah"] == ("jumlah",)
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


PEND_CSV = (
    "Periode,01/09/2026 - 30/09/2026\n"
    "Tanggal,Keterangan,Debit,Kredit,Saldo\n"
    "PEND,Uraian menyebut 01/09/2026,1500,,9000\n"
    "02/09/2026,Transfer valid,,500,9500\n"
)


def test_pend_stays_pending_and_short_date_is_not_inferred():
    parsed = _parse(
        "Periode,01/09/2026 - 30/09/2026\n"
        "Tanggal,Keterangan,Debit,Kredit,Saldo\n"
        "PEND,Uraian menyebut 01/09/2026,1500,,9000\n"
        "01/09,Tahun tidak diambil dari baris lain,1500,,9000\n"
    )
    assert [row["keterangan"] for row in parsed["rows"]] == ["Uraian menyebut 01/09/2026"]
    pend = parsed["rows"][0]
    assert pend["tanggal"] is None
    assert pend["original_date"] == "PEND"
    assert pend["date_status"] == "pending_confirmation"
    assert "2026-09-01" not in json.dumps(pend)
    assert [err["message"] for err in parsed["errors"]] == ["Tanggal tidak valid"]


def test_pend_confirmation_requires_calendar_date_and_reason():
    payload = PEND_CSV.encode("utf-8")
    pending = statement_rows_from_bca("bca-pend.csv", payload, "IDR")
    assert pending["pending"][0]["tanggal"] is None
    assert pending["accepted"][0]["description"] == "Transfer valid"
    assert pending["errors"][0]["message"] == "Baris PEND belum dikonfirmasi"
    with pytest.raises(StatementValidationError, match="Alasan konfirmasi wajib"):
        statement_rows_from_bca("bca-pend.csv", payload, "IDR", confirmations=[{
            "source_row": 3, "tanggal": "2026-09-30", "alasan": "   ",
        }])
    with pytest.raises(StatementValidationError, match="Tanggal tidak valid"):
        statement_rows_from_bca("bca-pend.csv", payload, "IDR", confirmations=[{
            "source_row": 3, "tanggal": "2026-02-31", "alasan": "Konfirmasi dari rekening koran BCA",
        }])
    with pytest.raises(StatementValidationError, match="Konfirmasi tidak sesuai baris PEND"):
        statement_rows_from_bca("bca-pend.csv", payload, "IDR", confirmations=[{
            "source_row": 4, "tanggal": "2026-09-30", "alasan": "Bukan baris PEND",
        }])
    confirmed = statement_rows_from_bca("bca-pend.csv", payload, "IDR", confirmations=[{
        "source_row": 3, "tanggal": "2026-09-30", "alasan": "Konfirmasi dari rekening koran BCA",
    }], actor={"id": "operator-1"}, confirmed_at="2026-09-29T00:00:00Z")
    assert confirmed["errors"] == []
    assert confirmed["pending"] == []
    pend = next(row for row in confirmed["accepted"] if row["original_date"] == "PEND")
    assert pend["date"] == "2026-09-30"
    assert pend["date"] != "2026-09-01"
    assert pend["date_status"] == "confirmed"
    assert pend["date_confirmation"]["reason"] == "Konfirmasi dari rekening koran BCA"
    assert pend["date_confirmation"]["confirmed_by"] == {"id": "operator-1"}


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


async def _ready(db):
    await ensure_coa_indexes(db)
    await ensure_cash_bank_indexes(db)
    await ensure_journal_indexes(db)
    await ensure_recon_indexes(db)
    await ensure_statement_indexes(db)


async def _account(client, *, currency="IDR"):
    await _ready(server.db)
    cash = await create_account(server.db, {
        "code": f"B{uuid.uuid4().hex[:6]}",
        "name": "Bank uji",
        "account_type": "BANK",
    }, user=_user())
    created = await client.post("/api/cash-bank/accounts", json={
        "name": f"Rekening {uuid.uuid4().hex[:4]}",
        "account_id": cash["id"],
        "active": True,
    })
    assert created.status_code == 201, created.text
    body = created.json()
    if currency:
        patched = await client.patch(
            f"/api/cash-bank/accounts/{body['id']}",
            json={"currency_code": currency},
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
    return body


def _preview_form(bank_id, payload, filename="bca-sintetis.csv"):
    return {
        "data": {"cash_bank_id": bank_id},
        "files": {"file": (filename, payload, "text/csv")},
    }


def _import_form(bank_id, preview, payload, *, fingerprint=None, filename="bca-sintetis.csv", confirmations=None):
    data = {
        "cash_bank_id": bank_id,
        "fingerprint": preview["fingerprint"] if fingerprint is None else fingerprint,
        "account_binding": preview["account_binding"],
    }
    if confirmations is not None:
        data["confirmations"] = json.dumps(confirmations)
    return {
        "data": data,
        "files": {"file": (filename, payload, "text/csv")},
    }


def test_normalized_bca_rows_use_statement_direction():
    parsed = statement_rows_from_bca("bca-sintetis.csv", PREAMBLE.encode("utf-8"), "IDR")
    assert parsed["errors"] == []
    assert parsed["accepted"][0]["direction"] == "out"
    assert parsed["accepted"][0]["date"] == "2026-09-01"
    assert parsed["accepted"][0]["description"] == "Setoran modal"
    assert parsed["accepted"][0]["amount"] == 1500000
    assert parsed["accepted"][0]["currency_code"] == "IDR"
    assert parsed["accepted"][1]["direction"] == "in"


KLIKBCA_CSV = """No. Rekening,=,4491150000,,,
Nama,=,Contoh Sintetis,,,
Periode,=,01/09/2026 - 30/09/2026,,,
Mata Uang,=,IDR,,,
,,,,,
Tanggal,Keterangan,Cabang,Jumlah,,Saldo
'04/09,TRSF E-BANKING DB 0409/FTSCY/WS95011 akomodasi,'0449,"500,000",DB,"56,392,000"
'05/09,TRSF E-BANKING CR 0509/FTSCY/WS95011 jasa,'0008,"76,000,000",CR,"131,590,170"
PEND,BI-FAST DB menunggu,'0000,"100,000",DB,"131,490,170"
04-09,Transfer singkat,0000,"1.500",DB,"131,488,670"
Saldo Awal,=,"56,892,000",,,
Kredit,=,"91,062,154",,,
Debet,=,"59,319,861",,,
Saldo Akhir,=,"88,634,293",,,
"""


def test_klikbca_jumlah_layout_reads_rows_and_skips_footer():
    parsed = _parse(KLIKBCA_CSV, "klikbca.csv")
    assert parsed["header_row"] == 6
    assert parsed["errors"] == []
    assert [row["keterangan"] for row in parsed["rows"]] == [
        "TRSF E-BANKING DB 0409/FTSCY/WS95011 akomodasi",
        "TRSF E-BANKING CR 0509/FTSCY/WS95011 jasa",
        "BI-FAST DB menunggu",
        "Transfer singkat",
    ]
    assert "0449" not in json.dumps(parsed["rows"])
    assert [row["saldo"] for row in parsed["rows"]] == [56392000, 131590170, 131490170, 131488670]


def test_klikbca_db_is_outgoing_and_cr_is_incoming():
    parsed = _parse(KLIKBCA_CSV, "klikbca.csv")
    outgoing = parsed["rows"][0]
    incoming = parsed["rows"][1]
    assert outgoing["debit"] == 500000
    assert outgoing["kredit"] is None
    assert outgoing["direction"] == "out"
    assert incoming["debit"] is None
    assert incoming["kredit"] == 76000000
    assert incoming["direction"] == "in"
    described = _parse(
        "Periode,01/09/2026 - 30/09/2026\n"
        "Tanggal,Keterangan,Cabang,Jumlah,,Saldo\n"
        "04/09,Keterangan menyebut CR bukan DB,0000,\"500,000\",DB,\"1,000\"\n"
        "05/09,Keterangan menyebut DB bukan CR,0000,\"-1\",CR,\"1,000\"\n"
    )
    assert described["rows"][0]["direction"] == "out"
    assert described["rows"][0]["debit"] == 500000
    assert [err["message"] for err in described["errors"]] == ["Nominal tidak valid"]


def test_klikbca_short_date_uses_only_the_periode_year():
    parsed = _parse(KLIKBCA_CSV, "klikbca.csv")
    assert parsed["rows"][0]["tanggal"] == "2026-09-04"
    assert parsed["rows"][0]["original_date"] == "'04/09"
    assert parsed["rows"][3]["tanggal"] == "2026-09-04"
    assert parsed["rows"][3]["original_date"] == "04-09"
    crossed = _parse(
        "Periode,=,01/12/2025 - 05/01/2026,,,\n"
        "Tanggal,Keterangan,Cabang,Jumlah,,Saldo\n"
        "15/12,Desember,0000,1000,DB,2000\n"
        "04/01,Januari,0000,500,CR,2500\n"
        "20/01,Di luar periode,0000,100,DB,2600\n"
    )
    assert [(row["keterangan"], row["tanggal"]) for row in crossed["rows"]] == [
        ("Desember", "2025-12-15"),
        ("Januari", "2026-01-04"),
    ]
    assert [err["message"] for err in crossed["errors"]] == ["Tanggal tidak valid"]
    legacy = _parse(
        "Periode,01/09/2026 - 30/09/2026\n"
        "Tanggal Transaksi,Keterangan,Debit,Kredit,Saldo\n"
        "04/09,Tahun tidak diambil untuk layout lama,1500,,9000\n"
    )
    assert legacy["rows"] == []
    assert legacy["errors"] == [{"row": 3, "message": "Tanggal tidak valid"}]
    missing_periode = _parse(
        "Tanggal,Keterangan,Cabang,Jumlah,,Saldo\n"
        "04/09,Tanpa periode,0000,\"500,000\",DB,\"1,000\"\n"
    )
    assert missing_periode["rows"] == []
    assert missing_periode["errors"] == [{"row": 2, "message": "Tanggal tidak valid"}]


def test_klikbca_pend_does_not_take_the_periode_year():
    parsed = _parse(KLIKBCA_CSV, "klikbca.csv")
    pend = parsed["rows"][2]
    assert pend["tanggal"] is None
    assert pend["original_date"] == "PEND"
    assert pend["date_status"] == "pending_confirmation"
    assert "2026-09-01" not in json.dumps(pend)
    payload = KLIKBCA_CSV.encode("utf-8")
    pending = statement_rows_from_bca("klikbca.csv", payload, "IDR")
    assert pending["pending"][0]["tanggal"] is None
    assert pending["pending"][0]["original_date"] == "PEND"
    assert [row["description"] for row in pending["accepted"]] == [
        "TRSF E-BANKING DB 0409/FTSCY/WS95011 akomodasi",
        "TRSF E-BANKING CR 0509/FTSCY/WS95011 jasa",
        "Transfer singkat",
    ]
    assert pending["accepted"][0]["direction"] == "out"
    assert pending["accepted"][0]["amount"] == 500000
    assert pending["accepted"][0]["date"] == "2026-09-04"
    assert pending["accepted"][0]["reference"] is None
    assert pending["errors"][0]["message"] == "Baris PEND belum dikonfirmasi"
    confirmed = statement_rows_from_bca("klikbca.csv", payload, "IDR", confirmations=[{
        "source_row": pend["source_row"],
        "tanggal": "2026-09-30",
        "alasan": "Konfirmasi dari rekening koran BCA",
    }])
    stored = next(row for row in confirmed["accepted"] if row.get("original_date") == "PEND")
    assert stored["date"] == "2026-09-30"
    assert stored["date_status"] == "confirmed"
    assert stored["direction"] == "out"
    assert stored["amount"] == 100000


def test_klikbca_marker_and_missing_jumlah_are_rejected():
    empty_marker = _parse(
        "Periode,01/09/2026 - 30/09/2026\n"
        "Tanggal,Keterangan,Cabang,Jumlah,,Saldo\n"
        "04/09,Penanda kosong,0000,\"500,000\",,\"1,000\"\n"
        "05/09,Penanda asing,0000,\"500,000\",XX,\"1,000\"\n"
        "06/09,Jumlah kosong,0000,,DB,\"1,000\"\n"
    )
    assert empty_marker["rows"] == []
    assert [err["message"] for err in empty_marker["errors"]] == [
        "Penanda DB/CR kosong",
        "Penanda DB/CR tidak dikenal",
        "Nominal kosong",
    ]
    with pytest.raises(StatementValidationError) as missing_jumlah:
        _parse("Tanggal,Keterangan,Cabang,Saldo\n04/09/2026,Tanpa jumlah,0000,1000\n")
    message = str(missing_jumlah.value)
    assert message.startswith("Format CSV BCA tidak dikenali:")
    assert "kolom debit tidak ditemukan" in message
    assert "kolom kredit tidak ditemukan" in message
    with pytest.raises(StatementValidationError, match="penanda DB/CR tidak ditemukan"):
        _parse("Tanggal,Keterangan,Cabang,Jumlah,Saldo\n04/09/2026,Tanpa kolom penanda,0000,500,1000\n")


async def test_preview_endpoint_does_not_write_or_create_journal(mongo_db):
    del mongo_db
    payload = PREAMBLE.encode("utf-8")
    async with await _client(_user()) as client:
        bank = await _account(client)
        before_names, before_counts = await _snapshot()
        response = await client.post("/api/cash-bank/statements/bca/preview", **_preview_form(bank["id"], payload))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["bank"] == "BCA"
        assert body["header_detected"] is True
        assert body["cash_bank_id"] == bank["id"]
        assert body["account_currency_code"] == "IDR"
        assert body["fingerprint"]
        assert body["account_binding"]
        assert body["account_binding"] != body["fingerprint"]
        assert "IDR" not in body["account_binding"]
        assert body["persisted_statement_lines"] is False
        assert body["stored"] is False
        assert body["rows"][0]["direction"] == "out"
        assert "id" not in body
        assert "preview_id" not in body
        rejected = await client.post(
            "/api/cash-bank/statements/bca/preview",
            **_preview_form(bank["id"], b"01/09/2026,Setoran,1500,,9000\n", filename="tanpa-header.csv"),
        )
    assert rejected.status_code == 400
    assert "kolom debit tidak ditemukan" in rejected.text
    app.dependency_overrides[current_user] = lambda: _user(override={"cash_bank.reconcile": False})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/api/cash-bank/statements/bca/preview",
            **_preview_form(bank["id"], payload),
        )
    assert denied.status_code == 403
    after_names, after_counts = await _snapshot()
    assert after_names == before_names
    assert after_counts == before_counts
    assert after_counts.get(JOURNAL_ENTRIES, 0) == 0
    assert after_counts.get(JOURNAL_LINES, 0) == 0
    assert after_counts.get(LINES, 0) == 0
    assert after_counts.get(IMPORTS, 0) == 0
    assert after_counts.get(RECON_MATCHES, 0) == 0


async def test_bca_preview_binding_rejects_other_account_file_and_currency(mongo_db):
    del mongo_db
    payload = PREAMBLE.encode("utf-8")
    async with await _client(_user()) as client:
        first = await _account(client, currency="IDR")
        second = await _account(client, currency="IDR")
        preview = await client.post("/api/cash-bank/statements/bca/preview", **_preview_form(first["id"], payload))
        assert preview.status_code == 200, preview.text
        shown = preview.json()
        other = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(second["id"], shown, payload),
        )
        assert other.status_code == 409
        assert "Rekening tidak boleh diganti" in other.text
        changed = payload.replace(b"Setoran modal", b"Setoran lain")
        fingerprint = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(first["id"], shown, changed),
        )
        assert fingerprint.status_code == 409
        assert "Sidik jari" in fingerprint.text
        patched = await client.patch(
            f"/api/cash-bank/accounts/{first['id']}",
            json={"currency_code": "USD"},
        )
        assert patched.status_code == 200, patched.text
        currency = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(first["id"], shown, payload),
        )
        assert currency.status_code == 409
        assert "Mata uang" in currency.text
        assert await server.db[LINES].count_documents({}) == 0
        assert await server.db[IMPORTS].count_documents({}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
        assert await server.db[JOURNAL_LINES].count_documents({}) == 0
        assert await server.db[RECON_MATCHES].count_documents({}) == 0
    async with await _client(_user()) as client:
        current = await client.get(f"/api/cash-bank/accounts/{first['id']}")
        assert current.status_code == 200
        assert current.json()["currency_code"] == "USD"
        rebound = await client.post("/api/cash-bank/statements/bca/preview", **_preview_form(first["id"], payload))
        assert rebound.status_code == 200, rebound.text
        body = rebound.json()
        assert body["account_binding"] != shown["account_binding"]
        imported = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(first["id"], body, payload),
        )
        assert imported.status_code == 201, imported.text
        assert await server.db[LINES].count_documents({"cash_bank_id": first["id"]}) == 4
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
        assert await server.db[RECON_MATCHES].count_documents({}) == 0
        stored = await server.db[LINES].find_one(
            {"cash_bank_id": first["id"], "description": "Setoran modal"},
            {"_id": 0, "date": 1, "direction": 1, "amount": 1, "currency_code": 1},
        )
        assert stored == {"date": "2026-09-01", "direction": "out", "amount": 1500000, "currency_code": "USD"}


async def test_unconfirmed_pend_rejects_the_whole_bca_import(mongo_db):
    del mongo_db
    payload = PEND_CSV.encode("utf-8")
    async with await _client(_user()) as client:
        bank = await _account(client)
        preview = await client.post("/api/cash-bank/statements/bca/preview", **_preview_form(bank["id"], payload, filename="bca-pend.csv"))
        assert preview.status_code == 200, preview.text
        shown = preview.json()
        pend = next(row for row in shown["rows"] if row["date_status"] == "pending_confirmation")
        assert pend["tanggal"] is None
        assert pend["original_date"] == "PEND"
        blocked = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(bank["id"], shown, payload, filename="bca-pend.csv"),
        )
        assert blocked.status_code == 400
        assert "belum dikonfirmasi" in blocked.text
        assert await server.db[LINES].count_documents({}) == 0
        assert await server.db[IMPORTS].count_documents({}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
        assert await server.db[RECON_MATCHES].count_documents({}) == 0
        imported = await client.post(
            "/api/cash-bank/statements/bca/imports",
            **_import_form(bank["id"], shown, payload, filename="bca-pend.csv", confirmations=[{
                "source_row": pend["source_row"],
                "tanggal": "2026-09-30",
                "alasan": "Konfirmasi dari rekening koran BCA",
            }]),
        )
        assert imported.status_code == 201, imported.text
        assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 2
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
        assert await server.db[RECON_MATCHES].count_documents({}) == 0
        stored = await server.db[LINES].find_one(
            {"cash_bank_id": bank["id"], "original_date": "PEND"},
            {"_id": 0, "date": 1, "original_date": 1, "date_status": 1, "date_confirmation": 1, "description": 1},
        )
        assert stored["date"] == "2026-09-30"
        assert stored["date"] != "2026-09-01"
        assert stored["description"] == "Uraian menyebut 01/09/2026"
        assert stored["date_status"] == "confirmed"
        assert stored["date_confirmation"]["reason"] == "Konfirmasi dari rekening koran BCA"
        assert stored["date_confirmation"]["confirmed_at"]
        assert stored["date_confirmation"]["confirmed_by"]["id"]
