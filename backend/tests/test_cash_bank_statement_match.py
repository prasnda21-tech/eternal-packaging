"""Phase 1 bank statement lines and one-to-one matches. Isolated Mongo."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
MATCH_DB = f"recon_phase1_isolated_{_worker}_{uuid.uuid4().hex[:6]}"
os.environ["DB_NAME"] = MATCH_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_pair_match import PairConflictError, _claim_statement_line, create_pair
import services.cash_bank_pair_match as pair_svc

_ORIGINAL_BEFORE_PAIR_CLAIM = pair_svc._before_pair_claim
from services.cash_bank_reconciliation import RECON_MATCHES, RECONCILIATIONS, ensure_indexes as ensure_recon
from services.cash_bank_statement import (
    IMPORT_FENCES,
    IMPORTS,
    LINES,
    MANUAL_BALANCES,
    PREVIEWS,
    _after_statement_line_insert,
    _before_reimport_write,
    _while_reimport_hold,
    public_import,
    ensure_indexes as ensure_statement,
)
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.general_ledger import get_general_ledger
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
import services.cash_bank_statement as statement_svc

pytestmark = pytest.mark.anyio

PERIOD = {"start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    os.environ["DB_NAME"] = MATCH_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[MATCH_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    await ensure_statement(db)
    names = (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, RECONCILIATIONS, RECON_MATCHES,
        IMPORTS, LINES, PREVIEWS, "cash_bank_manual_statement_balances",
    )
    for name in names:
        await db[name].delete_many({})
    await db["accounting_periods"].update_one(
        {"period_key": "2026-09"},
        {"$set": {"period_key": "2026-09", "status": "open", "writer_claims": []}},
        upsert=True,
    )
    yield
    for name in names:
        await db[name].delete_many({})
    statement_svc._after_statement_line_insert = _after_statement_line_insert
    statement_svc._before_reimport_write = _before_reimport_write
    statement_svc._while_reimport_hold = _while_reimport_hold
    pair_svc._before_pair_claim = _ORIGINAL_BEFORE_PAIR_CLAIM


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    if role == "admin":
        override = {}
    elif perms is not None:
        override = {p: True for p in perms}
    else:
        override = {}
    return {
        "id": uid, "email": f"{uid}@example.com", "name": role, "role": role if role != "viewer" else "operator",
        "status": "active", "user_id": uid, "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name):
    return await create_account(server.db, {"code": code, "name": name, "account_type": "BANK"}, user=_user())


async def _setup(c, *, currency=None):
    cash = await _coa(f"B{uuid.uuid4().hex[:6]}", "Bank")
    income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Lawannya")
    bank = await c.post("/api/cash-bank/accounts", json={"name": "Bank Uji", "account_id": cash["id"], "active": True})
    assert bank.status_code == 201, bank.text
    body = bank.json()
    if currency:
        patched = await c.patch(f"/api/cash-bank/accounts/{body['id']}", json={"currency_code": currency})
        assert patched.status_code == 200, patched.text
        body = patched.json()
    return body, income


async def _receipt(c, bank_id, counter_id, amount, date="2026-09-05", desc="Transfer masuk"):
    draft = await c.post("/api/cash-bank/receipts", json={
        "date": date, "cash_bank_id": bank_id, "counter_account_id": counter_id,
        "amount": amount, "description": desc, "reference": f"REF-{amount}",
    })
    assert draft.status_code == 201, draft.text
    posted = await c.post(f"/api/cash-bank/receipts/{draft.json()['id']}/post")
    assert posted.status_code == 200, posted.text
    return posted.json()


def _mapping(**overrides):
    mapping = {
        "header_row": 0,
        "date_column": 0,
        "date_format": "YYYY-MM-DD",
        "description_column": 1,
        "reference_column": 4,
        "bank_txn_id_column": 5,
        "amount_mode": "in_out",
        "in_column": 2,
        "out_column": 3,
        "balance_column": 6,
        "decimal_mode": "dot_decimal",
        "file_currency": "IDR",
        "operator_coverage_confirmed": False,
    }
    mapping.update(overrides)
    return mapping


async def _preview_upload(c, bank_id, payload, filename="mutasi.csv", content_type="text/csv"):
    data = payload.encode() if isinstance(payload, str) else payload
    preview = await c.post(
        "/api/cash-bank/statements/preview",
        data={"cash_bank_id": bank_id},
        files={"file": (filename, data, content_type)},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["persisted_statement_lines"] is False
    assert "id" not in body
    assert "file_bytes" not in body
    body["_filename"] = filename
    body["_data"] = data
    body["_content_type"] = content_type
    return body


def _import_fields(bank_id, preview, mapping=None, *, fingerprint=None, rows=None, currency_sent=True):
    fields = {
        "cash_bank_id": bank_id,
        "fingerprint": preview["fingerprint"] if fingerprint is None else fingerprint,
        "mapping": json.dumps(mapping or _mapping()),
    }
    if currency_sent:
        fields["account_binding"] = preview["account_binding"]
    if rows is not None:
        fields["rows"] = json.dumps(rows)
    return fields


async def _commit_upload(c, bank_id, preview, mapping=None, *, data=None, filename=None, fingerprint=None, rows=None, send_file=True):
    fields = _import_fields(bank_id, preview, mapping, fingerprint=fingerprint, rows=rows)
    kwargs = {"data": fields}
    if send_file:
        payload = preview["_data"] if data is None else data
        kwargs["files"] = {"file": (filename or preview["_filename"], payload, preview.get("_content_type") or "text/csv")}
    return await c.post("/api/cash-bank/statements/imports", **kwargs)


async def _import(c, bank_id, text, mapping=None):
    body = await _preview_upload(c, bank_id, text)
    committed = await _commit_upload(c, bank_id, body, mapping)
    return body, committed


async def test_a_match_750_does_not_create_journal():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF-750000,TX-750,\n"
        ))
        assert imported.status_code == 201, imported.text
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], **PERIOD,
        })
        assert ws.status_code == 200, ws.text
        bank_line = ws.json()["bank_lines"][0]["id"]
        book_line = ws.json()["book_lines"][0]["journal_line_id"]
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"],
            "statement_line_id": bank_line,
            "journal_line_id": book_line,
            "idempotency_key": "a-key",
            **PERIOD,
        })
        assert matched.status_code == 201, matched.text
        assert matched.json()["status"] == "active"
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1


async def test_b_two_book_candidates_are_not_auto_selected():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000, desc="Satu")
        await _receipt(c, bank["id"], income["id"], 750000, desc="Dua")
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,LAIN,TX-B,\n"
        ))
        assert imported.status_code == 201, imported.text
        line_id = (await server.db[LINES].find_one({}, {"_id": 0, "id": 1}))["id"]
        before = await server.db[RECON_MATCHES].count_documents({})
        suggestions = await c.get("/api/cash-bank/reconciliations/pairs/suggestions", params={
            "statement_line_id": line_id, **PERIOD,
        })
        assert suggestions.status_code == 200, suggestions.text
        body = suggestions.json()
        assert body["applied"] is False
        assert body["ambiguous"] is True
        assert len(body["suggestions"]) == 2
        assert await server.db[RECON_MATCHES].count_documents({}) == before


async def test_c_identical_import_returns_same_batch():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-C,\n"
        )
        _p1, first = await _import(c, bank["id"], text)
        _p2, second = await _import(c, bank["id"], text)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert await server.db[LINES].count_documents({"import_id": first.json()["id"]}) == 1


async def test_d_two_legitimate_rows_with_same_text_both_remain():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        _preview, imported = await _import(c, bank["id"], text)
        assert imported.status_code == 201, imported.text
        rows = await server.db[LINES].find({}, {"_id": 0, "duplicate_status": 1}).to_list(10)
        assert len(rows) == 2
        assert {row["duplicate_status"] for row in rows} == {"clear"}


async def test_e_concurrent_pair_has_one_winner():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-E,\n"
        ))
        assert imported.status_code == 201, imported.text
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        bank_line = ws.json()["bank_lines"][0]["id"]
        book_line = ws.json()["book_lines"][0]["journal_line_id"]

        async def attempt(key):
            try:
                return await create_pair(
                    server.db, cash_bank_id=bank["id"], statement_line_id=bank_line,
                    journal_line_id=book_line, idempotency_key=key, start="2026-09-01",
                    end="2026-09-30", user=admin,
                )
            except PairConflictError as exc:
                return exc

        left, right = await asyncio.gather(attempt("e-1"), attempt("e-2"))
        outcomes = [left, right]
        assert sum(isinstance(item, PairConflictError) for item in outcomes) == 1
        assert sum(isinstance(item, dict) and item.get("status") == "active" for item in outcomes) == 1
        assert await server.db[RECON_MATCHES].count_documents({"journal_line_id": book_line}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"statement_line_id": bank_line}) == 1


async def test_f_legacy_and_new_share_one_claim():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        ws_old = await c.get("/api/cash-bank/reconciliations/workspace", params={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-30", "statement_balance": "750000",
        })
        line_id = ws_old.json()["movements"][0]["journal_line_id"]
        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-30",
            "statement_balance": 750000, "journal_line_ids": [line_id],
        })
        assert created.status_code == 201, created.text
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-F,\n"
        ))
        bank_line = (await server.db[LINES].find_one({}, {"_id": 0, "id": 1}))["id"]
        blocked = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": line_id, "idempotency_key": "f-new", **PERIOD,
        })
        assert blocked.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"journal_line_id": line_id}) == 1
        info = await server.db[RECON_MATCHES].index_information()
        assert info["uniq_journal_line_claim"]["unique"] is True
        assert info["uniq_journal_line_claim"]["partialFilterExpression"]


async def test_g_and_h_cancel_retry_and_idempotency_payload():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        await _receipt(c, bank["id"], income["id"], 1000, desc="lain")
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-G,\n"
        ))
        assert imported.status_code == 201, imported.text
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        bank_line = next(row["id"] for row in ws.json()["bank_lines"])
        book_750 = next(row["journal_line_id"] for row in ws.json()["book_lines"] if row["absolute_amount"] == 750000)
        book_other = next(row["journal_line_id"] for row in ws.json()["book_lines"] if row["absolute_amount"] == 1000)
        first = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_750, "idempotency_key": "same-key", **PERIOD,
        })
        assert first.status_code == 201, first.text
        replay = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_750, "idempotency_key": "same-key", **PERIOD,
        })
        assert replay.status_code == 201
        assert replay.json()["id"] == first.json()["id"]
        changed = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_other, "idempotency_key": "same-key", **PERIOD,
        })
        assert changed.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"idempotency_key": "same-key"}) == 1
        cancelled = await c.post(f"/api/cash-bank/reconciliations/pairs/{first.json()['id']}/cancel", json={
            "idempotency_key": "cancel-1", "reason": "salah pilih",
        })
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        again = await c.post(f"/api/cash-bank/reconciliations/pairs/{first.json()['id']}/cancel", json={
            "idempotency_key": "cancel-1", "reason": "salah pilih",
        })
        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        stale = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_750, "idempotency_key": "same-key", **PERIOD,
        })
        assert stale.status_code == 201
        assert stale.json()["status"] == "cancelled"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active"}) == 0
        rematch = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_750, "idempotency_key": "new-key", **PERIOD,
        })
        assert rematch.status_code == 201, rematch.text
        assert rematch.json()["status"] == "active"
        assert rematch.json()["id"] != first.json()["id"]
        retry_old_cancel = await c.post(
            f"/api/cash-bank/reconciliations/pairs/{rematch.json()['id']}/cancel",
            json={"idempotency_key": "cancel-1", "reason": "salah pilih"},
        )
        assert retry_old_cancel.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"id": rematch.json()["id"], "status": "active"}) == 1


async def test_i_partial_import_retry_completes_without_duplicates():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Satu,100,,REF,TX-1,\n"
            "2026-09-06,Dua,200,,REF,TX-2,\n"
        )
        body = await _preview_upload(c, bank["id"], text)

        async def crash(index):
            if index == 0:
                raise RuntimeError("crash after first line")

        statement_svc._after_statement_line_insert = crash
        with pytest.raises(RuntimeError):
            await statement_svc.commit_import(
                server.db,
                cash_bank_id=bank["id"],
                filename="mutasi.csv",
                data=text.encode(),
                fingerprint=body["fingerprint"],
                mapping_raw=_mapping(),
                user=admin,
                account_binding_value=body["account_binding"],
            )
        assert await server.db[IMPORTS].count_documents({"status": "importing"}) == 1
        assert await server.db[LINES].count_documents({}) == 1
        statement_svc._after_statement_line_insert = _after_statement_line_insert
        done = await _commit_upload(c, bank["id"], body)
        assert done.status_code == 201, done.text
        assert done.json()["status"] == "complete"
        assert done.json()["stored_count"] == 2
        assert await server.db[LINES].count_documents({"import_id": done.json()["id"]}) == 2


async def test_j_repeat_reuses_the_line_and_keeps_the_new_transaction():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
            "2026-09-07,Beda,10,,REF,TX-UNIK,\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1, "duplicate_status": 1})
        imported = (await _import(c, bank["id"], second))[1]
        assert imported.status_code == 201, imported.text
        rows = await server.db[LINES].find({}, {"_id": 0, "id": 1, "description": 1, "duplicate_status": 1}).to_list(10)
        assert len(rows) == 2
        assert await server.db[LINES].count_documents({"duplicate_status": "pending_review"}) == 0
        kept = next(row for row in rows if row["id"] == original["id"])
        added = next(row for row in rows if row["id"] != original["id"])
        assert kept["duplicate_status"] == "clear"
        assert added["description"] == "Beda"
        assert added["duplicate_status"] == "clear"
        assert imported.json()["reused_count"] == 1
        assert imported.json()["stored_count"] == 1
        assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"], "status": "complete"}) == 2


async def test_same_file_five_times_stays_one_transaction():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        batch_ids = []
        for _ in range(5):
            _preview, imported = await _import(c, bank["id"], text)
            assert imported.status_code == 201, imported.text
            batch_ids.append(imported.json()["id"])
        assert len(set(batch_ids)) == 1
        assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 1
        assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 1


async def test_different_file_same_transaction_keeps_history_without_a_second_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,200\n"
        )
        _preview, imported = await _import(c, bank["id"], first)
        assert imported.status_code == 201, imported.text
        original = await server.db[LINES].find_one({}, {"_id": 0})
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["id"] != imported.json()["id"]
        assert again.json()["stored_count"] == 0
        assert again.json()["reused_count"] == 1
        assert again.json()["status"] == "complete"
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 1
        assert rows[0]["id"] == original["id"]
        assert rows[0]["description"] == original["description"]
        assert rows[0]["amount"] == original["amount"]
        assert rows[0]["date"] == original["date"]
        assert rows[0]["duplicate_status"] == "clear"
        assert rows[0]["running_balance"] == 200
        assert rows[0]["import_id"] == original["import_id"]
        assert rows[0]["last_import_id"] == again.json()["id"]
        assert len(rows[0].get("import_updates") or []) == 1
        assert again.json()["updated_count"] == 1
        assert again.json()["new_count"] == 0
        assert again.json()["fully_accepted"] is True
        assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"], "status": "complete"}) == 2


async def test_same_amount_different_reference_is_a_different_transaction():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
            "2026-09-05,Transfer masuk,750000,,LAIN,,\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0})
        imported = (await _import(c, bank["id"], second))[1]
        assert imported.status_code == 201, imported.text
        assert imported.json()["stored_count"] == 1
        assert imported.json()["reused_count"] == 1
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 2
        kept = next(row for row in rows if row["id"] == original["id"])
        extra = next(row for row in rows if row["id"] != original["id"])
        assert kept["duplicate_status"] == "clear"
        assert kept["id"] == original["id"]
        assert kept["date"] == original["date"]
        assert kept["amount"] == original["amount"]
        assert kept["description"] == original["description"]
        assert extra["duplicate_status"] == "clear"
        assert extra["reference"] == "LAIN"
        assert await server.db[LINES].count_documents({"duplicate_status": "pending_review"}) == 0
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        visible = {row["id"]: row for row in ws.json()["bank_lines"]}
        assert visible[extra["id"]]["matchable"] is True
        assert visible[kept["id"]]["matchable"] is True


async def test_different_authoritative_ids_are_not_swallowed():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-1,\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-2,\n"
        )
        _preview, imported = await _import(c, bank["id"], text, _mapping(bank_txn_id_authoritative=True))
        assert imported.status_code == 201, imported.text
        rows = await server.db[LINES].find({}, {"_id": 0, "authoritative_txn_id": 1, "duplicate_status": 1}).to_list(5)
        assert sorted(row["authoritative_txn_id"] for row in rows) == ["TX-1", "TX-2"]
        assert {row["duplicate_status"] for row in rows} == {"clear"}


async def test_matched_line_is_unchanged_after_reimport():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-M,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-M,200\n"
        )
        _preview, imported = await _import(c, bank["id"], first)
        assert imported.status_code == 201, imported.text
        before = await server.db[LINES].find_one({}, {"_id": 0})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book_line = ws.json()["book_lines"][0]["journal_line_id"]
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": before["id"],
            "journal_line_id": book_line, "idempotency_key": "keep-match", **PERIOD,
        })
        assert matched.status_code == 201, matched.text
        pair_id = matched.json()["id"]
        claimed = await server.db[LINES].find_one({"id": before["id"]}, {"_id": 0})
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        after = await server.db[LINES].find_one({"id": before["id"]}, {"_id": 0})
        assert await server.db[LINES].count_documents({}) == 1
        assert after["id"] == claimed["id"]
        assert after["date"] == claimed["date"]
        assert after["description"] == claimed["description"]
        assert after["amount"] == claimed["amount"]
        assert after["direction"] == claimed["direction"]
        assert after["duplicate_status"] == claimed["duplicate_status"]
        assert after.get("active_claim") == claimed.get("active_claim")
        active = await server.db[RECON_MATCHES].find_one({"id": pair_id}, {"_id": 0, "status": 1, "statement_line_id": 1})
        assert active["status"] == "active"
        assert active["statement_line_id"] == before["id"]


async def test_k_l_incomplete_journal_follows_general_ledger():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000, date="2026-08-31", desc="awal")
        await _receipt(c, bank["id"], income["id"], 750000)
        account_id = bank["account_id"]
        inc_id = str(uuid.uuid4())
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": inc_id, "number": "JE-INC", "date": "2026-09-15", "status": "incomplete",
            "source_type": "probe", "source_id": "probe", "description": "incomplete", "reversal_of": None,
        })
        await server.db[JOURNAL_LINES].insert_one({
            "id": str(uuid.uuid4()), "journal_id": inc_id, "account_id": account_id,
            "debit": 5000, "credit": 0, "description": "incomplete",
        })
        gl = await get_general_ledger(server.db, account_id=account_id, start_date="2026-09-01", end_date="2026-09-30")
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        assert ws.status_code == 200, ws.text
        body = ws.json()
        assert body["book_opening_balance"] == gl["opening_balance"] == 750000
        assert body["book_ending_balance"] == gl["ending_balance"] == 1500000
        assert all(row.get("journal_id") != inc_id for row in body["book_lines"])
        assert gl["ending_balance"] != 1505000


async def test_m_reversal_marks_review_and_keeps_journal():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        receipt = await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-M,\n"
        ))
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        bank_line = ws.json()["bank_lines"][0]["id"]
        book_line = ws.json()["book_lines"][0]["journal_line_id"]
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line,
            "journal_line_id": book_line, "idempotency_key": "m-1", **PERIOD,
        })
        assert matched.status_code == 201, matched.text
        before = await server.db[JOURNAL_ENTRIES].count_documents({})

        async def reverse():
            async with await client_for(admin) as c2:
                return await c2.post(f"/api/cash-bank/receipts/{receipt['id']}/reverse")

        async def second_match():
            return await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=bank_line,
                journal_line_id=book_line, idempotency_key="m-race", start="2026-09-01",
                end="2026-09-30", user=admin,
            )

        reversed_res, raced = await asyncio.gather(reverse(), second_match(), return_exceptions=True)
        assert reversed_res.status_code == 200, getattr(reversed_res, "text", reversed_res)
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before + 1
        current = await server.db[RECON_MATCHES].find_one({"id": matched.json()["id"]}, {"_id": 0})
        assert current["status"] == "active"
        assert current.get("journal_line_id")
        ws2 = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        pair = ws2.json()["matched_pairs"][0]
        assert pair["review_state"] == "perlu_tinjauan"
        assert pair["book_snapshot"]["label"].startswith("Snapshot")
        assert ws2.json()["book_ending_balance"] == 0
        assert isinstance(raced, PairConflictError) or raced.get("id") == matched.json()["id"]


async def test_n_unrecorded_bank_fee_stays_unmatched():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-08,Biaya admin,,15000,ADM,TX-FEE,\n"
        ))
        assert imported.status_code == 201, imported.text
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        row = ws.json()["bank_lines"][0]
        assert row["direction"] == "out"
        assert row["amount"] == 15000
        assert row["claim_id"] is None
        assert ws.json()["unmatched_bank_count"] == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[PAYMENTS].count_documents({}) == 0


async def test_o_matched_rows_do_not_declare_balance_match():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-08-31", desc="awal")
        await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-O,750000\n"
        ))
        assert imported.status_code == 201, imported.text
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], "start_date": "2026-09-01", "end_date": "2026-09-05",
        })
        body = ws.json()
        bank_line = body["bank_lines"][0]["id"]
        book_line = next(row["journal_line_id"] for row in body["book_lines"] if row["absolute_amount"] == 750000)
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": bank_line, "journal_line_id": book_line,
            "idempotency_key": "o-1", "start_date": "2026-09-01", "end_date": "2026-09-05",
        })
        assert matched.status_code == 201, matched.text
        again = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], "start_date": "2026-09-01", "end_date": "2026-09-05",
        })
        viewed = again.json()
        assert viewed["book_opening_balance"] == 100000
        assert viewed["book_ending_balance"] == 850000
        assert viewed["statement_closing"]["amount"] == 750000
        assert viewed["difference"] == -100000
        assert viewed["balance_match_declared"] is False
        assert viewed["completion_status"] == "tidak_dinilai"
        manual = await c.put("/api/cash-bank/statements/manual-balance", json={
            "cash_bank_id": bank["id"], "as_of": "2026-09-06", "amount": 1,
        })
        assert manual.json()["source"] == "manual"
        missing = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], "start_date": "2026-09-01", "end_date": "2026-09-06",
        })
        closing = missing.json()["statement_closing"]
        assert closing["label"] == "Belum tersedia"
        assert closing["amount"] is None
        assert closing["manual"]["label"] == "Input manual"


async def test_p_unknown_or_different_currency_rejects_match():
    admin = _user()
    manager_only = _user("viewer", perms=["cash_bank.view"])
    reconcile_only = _user("viewer", perms=["cash_bank.view", "cash_bank.reconcile"])
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        assert bank["currency_code"] is None
        await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-P,\n"
        ))
        assert imported.status_code == 201, imported.text
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        assert ws.json()["bank_lines"][0]["matchable"] is False
        denied = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"],
            "statement_line_id": ws.json()["bank_lines"][0]["id"],
            "journal_line_id": ws.json()["book_lines"][0]["journal_line_id"],
            "idempotency_key": "p-1", **PERIOD,
        })
        assert denied.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0
        usd = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "USD"})
        assert usd.status_code == 409, usd.text
        unchanged = await c.get(f"/api/cash-bank/accounts/{bank['id']}")
        assert unchanged.json()["currency_code"] is None
        assert unchanged.json()["currency_history"] == []
        still = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"],
            "statement_line_id": ws.json()["bank_lines"][0]["id"],
            "journal_line_id": ws.json()["book_lines"][0]["journal_line_id"],
            "idempotency_key": "p-2", **PERIOD,
        })
        assert still.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0
    async with await client_for(manager_only) as c:
        denied_currency = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "IDR"})
        assert denied_currency.status_code == 403
    async with await client_for(reconcile_only) as c:
        denied_currency = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "IDR"})
        assert denied_currency.status_code == 403


async def test_r_s_search_page_and_get_do_not_change_totals_or_write():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        for amount, desc in ((100, "Alpha"), (200, "Beta"), (300, "Gamma")):
            await _receipt(c, bank["id"], income["id"], amount, desc=desc)
        counts = {
            "journals": await server.db[JOURNAL_ENTRIES].count_documents({}),
            "matches": await server.db[RECON_MATCHES].count_documents({}),
            "lines": await server.db[LINES].count_documents({}),
            "imports": await server.db[IMPORTS].count_documents({}),
        }
        full = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], **PERIOD, "page": 1, "page_size": 1,
        })
        searched = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], **PERIOD, "q": "Alpha", "page": 1, "page_size": 1,
        })
        page2 = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], **PERIOD, "page": 2, "page_size": 1,
        })
        assert full.json()["book_total"] == searched.json()["book_total"] == page2.json()["book_total"] == 3
        assert full.json()["book_ending_balance"] == searched.json()["book_ending_balance"] == page2.json()["book_ending_balance"]
        assert full.json()["unmatched_book_count"] == searched.json()["unmatched_book_count"]
        assert searched.json()["visible_book_count"] == 1
        assert len(full.json()["book_lines"]) == 1
        assert len(page2.json()["book_lines"]) == 1
        assert full.json()["book_lines"][0]["journal_line_id"] != page2.json()["book_lines"][0]["journal_line_id"]
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == counts["journals"]
        assert await server.db[RECON_MATCHES].count_documents({}) == counts["matches"]
        assert await server.db[LINES].count_documents({}) == counts["lines"]
        assert await server.db[IMPORTS].count_documents({}) == counts["imports"]


async def test_q_forbidden_and_mapping_conflict_do_not_fake_success():
    admin = _user()
    viewer = _user("viewer", perms=["cash_bank.view"])
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,10,,REF,TX-Q,\n"
        )
        assert (await _import(c, bank["id"], text))[1].status_code == 201
        preview = await _preview_upload(c, bank["id"], text)
        other = _mapping(reference_column=None, description_column=1)
        changed = await _commit_upload(c, bank["id"], preview, other)
        assert changed.status_code == 409
        assert await server.db[IMPORTS].count_documents({}) == 1
        bad = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "bukan-tanggal,Rusak,10,,REF,TX-BAD,\n"
        )
        preview_bad = await _preview_upload(c, bank["id"], bad, filename="rusak.csv")
        rejected = await _commit_upload(c, bank["id"], preview_bad)
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["errors"]
        assert await server.db[LINES].count_documents({"description": "Rusak"}) == 0
    async with await client_for(viewer) as c:
        forbidden = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": "x", "journal_line_id": "y",
            "idempotency_key": "nope", **PERIOD,
        })
        assert forbidden.status_code == 403


async def test_t_legacy_reconciliation_contract_keeps_claims():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 40000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-10", "statement_balance": 40000,
        })
        assert created.status_code == 201, created.text
        ws = await c.get("/api/cash-bank/reconciliations/workspace", params={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-10",
        })
        line_id = ws.json()["movements"][0]["journal_line_id"]
        saved = await c.put(f"/api/cash-bank/reconciliations/{created.json()['id']}/matches", json={
            "journal_line_ids": [line_id],
        })
        assert saved.status_code == 200, saved.text
        fin = await c.post(f"/api/cash-bank/reconciliations/{created.json()['id']}/finalize")
        assert fin.status_code == 200
        assert fin.json()["status"] == "finalized"
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        immutable = await c.patch(f"/api/cash-bank/reconciliations/{created.json()['id']}", json={"statement_balance": 1})
        assert immutable.status_code == 409
        other = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-10", "statement_balance": 1,
        })
        conflict = await c.put(f"/api/cash-bank/reconciliations/{other.json()['id']}/matches", json={
            "journal_line_ids": [line_id],
        })
        assert conflict.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"journal_line_id": line_id}) == 1
        legacy = await c.get("/api/cash-bank/reconciliations/legacy", params={"cash_bank_id": bank["id"]})
        assert legacy.status_code == 200
        assert legacy.json()["items"][0]["kind"] == "legacy_balance"


async def test_xlsx_formula_rejected_and_plain_xlsx_imports():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        formula = Workbook()
        sheet = formula.active
        sheet["A1"] = "=1+1"
        buf = io.BytesIO()
        formula.save(buf)
        rejected = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("formula.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert rejected.status_code == 400
        plain = Workbook()
        ws = plain.active
        ws.append(["tanggal", "uraian", "masuk", "keluar", "referensi", "id", "saldo"])
        ws.append(["2026-09-05", "Transfer masuk", 750000, None, "REF", "TX-X", None])
        out = io.BytesIO()
        plain.save(out)
        preview = await _preview_upload(
            c, bank["id"], out.getvalue(), filename="mutasi.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        committed = await _commit_upload(c, bank["id"], preview)
        assert committed.status_code == 201, committed.text
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def test_ambiguous_date_and_debit_label_are_not_guessed():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = "tanggal,uraian,masuk,keluar\n13/13/2026,Rusak,10,0\n"
        preview = await _preview_upload(c, bank["id"], text, filename="tgl.csv")
        result = await _commit_upload(
            c, bank["id"], preview,
            _mapping(date_format="DD/MM/YYYY", balance_column=None, reference_column=None, bank_txn_id_column=None),
        )
        assert result.status_code == 400
        assert await server.db[LINES].count_documents({}) == 0


async def _collection_snapshot():
    names = set(await server.db.list_collection_names())
    watched = (
        JOURNAL_ENTRIES, JOURNAL_LINES, RECEIPTS, PAYMENTS, TRANSFERS, RECON_MATCHES, RECONCILIATIONS,
        LINES, IMPORTS, PREVIEWS, IMPORT_FENCES, MANUAL_BALANCES, CB_COLLECTION, COA_COLLECTION,
        "accounting_periods",
    )
    docs = {}
    for name in watched:
        if name not in names:
            docs[name] = None
            continue
        docs[name] = await server.db[name].find({}, {"_id": 0}).sort("id", 1).to_list(500)
    return {"names": names, "docs": docs}


async def test_preview_and_get_do_not_write_any_collection():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        payload = b"tanggal,uraian,masuk,keluar\n2026-09-05,Transfer,10,0\n"
        before = await _collection_snapshot()
        preview = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("mutasi.csv", payload, "text/csv")},
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["persisted_statement_lines"] is False
        assert body["stored"] is False
        assert "id" not in body
        assert "preview_id" not in body
        assert "file_bytes" not in body
        assert body["fingerprint"] == hashlib.sha256(payload).hexdigest()
        validated = await c.post(
            "/api/cash-bank/statements/preview/validate",
            data={
                "cash_bank_id": bank["id"],
                "mapping": json.dumps(_mapping(balance_column=None, reference_column=None, bank_txn_id_column=None)),
                "account_binding": body["account_binding"],
                "fingerprint": body["fingerprint"],
            },
            files={"file": ("mutasi.csv", payload, "text/csv")},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["ok"] is True
        assert validated.json()["fingerprint"] == body["fingerprint"]
        listed = await c.get("/api/cash-bank/statements/imports", params={"cash_bank_id": bank["id"]})
        workspace = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        assert listed.status_code == 200
        assert workspace.status_code == 200
        after = await _collection_snapshot()
        assert after["names"] == before["names"]
        assert PREVIEWS not in after["names"]
        assert after["docs"] == before["docs"]
        assert listed.json()["items"] == []


async def test_import_rechecks_file_and_ignores_client_rows():
    admin = _user()
    viewer = _user("viewer", perms=["cash_bank.view"])
    text = (
        "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
        "2026-09-05,Transfer masuk,10,,REF,TX-RECHECK,\n"
    )
    changed = text.replace("10", "99")
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        other, _other_income = await _setup(c, currency="USD")
        same, _same_income = await _setup(c, currency="IDR")
        preview = await _preview_upload(c, bank["id"], text)
        assert preview["account_binding"] != preview["fingerprint"]
        assert preview["account_currency_code"] not in preview["account_binding"]
        missing = await c.post(
            "/api/cash-bank/statements/imports",
            data=_import_fields(bank["id"], preview),
        )
        assert missing.status_code == 400
        assert await server.db[IMPORTS].count_documents({}) == 0
        mismatch = await _commit_upload(c, bank["id"], preview, data=changed.encode())
        assert mismatch.status_code == 409
        assert "Sidik jari" in mismatch.text
        wrong = await _commit_upload(c, bank["id"], preview, fingerprint="0" * 64)
        assert wrong.status_code == 409
        drifted = await _commit_upload(c, other["id"], preview)
        assert drifted.status_code == 409
        assert "Mata uang" in drifted.text
        assert await server.db[LINES].count_documents({"cash_bank_id": other["id"]}) == 0
        switched = await _commit_upload(c, same["id"], preview)
        assert switched.status_code == 409
        assert "Rekening tidak boleh diganti" in switched.text
        assert await server.db[LINES].count_documents({"cash_bank_id": same["id"]}) == 0
        assert await server.db[IMPORTS].count_documents({"cash_bank_id": same["id"]}) == 0
        same_preview = await _preview_upload(c, same["id"], text)
        assert same_preview["account_binding"] != preview["account_binding"]
        same_imported = await _commit_upload(c, same["id"], same_preview)
        assert same_imported.status_code == 201, same_imported.text
        assert await server.db[LINES].count_documents({"cash_bank_id": same["id"]}) == 1
        fake_rows = [{"date": "2026-09-05", "description": "Palsu", "amount": 1, "direction": "in"}]
        imported = await _commit_upload(c, bank["id"], preview, rows=fake_rows)
        assert imported.status_code == 201, imported.text
        stored = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "amount": 1, "description": 1})
        assert stored["amount"] == 10
        assert stored["description"] == "Transfer masuk"
        assert await server.db[PREVIEWS].count_documents({}) == 0
        again = await _commit_upload(c, bank["id"], preview, rows=fake_rows)
        assert again.status_code == 201
        assert again.json()["id"] == imported.json()["id"]
        assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 1
        assert "preview_id" not in imported.json()
    async with await client_for(viewer) as c:
        denied = await c.post(
            "/api/cash-bank/statements/imports",
            data=_import_fields(bank["id"], preview),
            files={"file": ("mutasi.csv", text.encode(), "text/csv")},
        )
        assert denied.status_code == 403
        assert await server.db[IMPORTS].count_documents({}) == 2


async def test_closed_period_rejects_receipt_post_without_journal():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await server.db["accounting_periods"].update_one(
            {"period_key": "2026-09"},
            {"$set": {"period_key": "2026-09", "status": "closed"}},
            upsert=True,
        )
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        draft = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05", "cash_bank_id": bank["id"], "counter_account_id": income["id"],
            "amount": 750000, "description": "Transfer masuk", "reference": "REF-CLOSED",
        })
        assert draft.status_code == 201, draft.text
        posted = await c.post(f"/api/cash-bank/receipts/{draft.json()['id']}/post")
        assert posted.status_code == 409
        assert "sudah ditutup" in posted.text
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


def _snapshot(docs):
    return sorted(docs, key=lambda doc: str(doc.get("id") or ""))


async def test_duplicate_groups_are_read_only_and_resolution_uses_existing_endpoint():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        account = bank["id"]
        await server.db[IMPORTS].insert_many([
            {"id": "imp-a", "cash_bank_id": account, "filename": "mutasi-2809.csv", "status": "complete", "created_at": "2026-09-28T02:14:00+00:00", "file_sha256": "a" * 64},
            {"id": "imp-b", "cash_bank_id": account, "filename": "mutasi-2809-ulang.csv", "status": "complete", "created_at": "2026-09-29T01:02:00+00:00", "file_sha256": "b" * 64},
        ])
        description = "PEMINDAHAN DANA RAKHA EDY SYAH"
        shared = {
            "cash_bank_id": account, "date": "2026-09-28", "description": description,
            "direction": "out", "amount": 70000000, "duplicate_status": "pending_review",
            "identity_guarantee": "tidak_dijamin", "sealed": True, "resolutions": [], "active_claim": None,
        }
        await server.db[LINES].insert_many([
            {**shared, "id": "line-a", "import_id": "imp-a", "created_at": "2026-09-28T02:14:00+00:00"},
            {**shared, "id": "line-b", "import_id": "imp-b", "created_at": "2026-09-29T01:02:00+00:00"},
            {**shared, "id": "line-alone", "import_id": "imp-a", "description": "TRANSFER LAIN", "amount": 1000, "created_at": "2026-09-28T03:00:00+00:00"},
            {**shared, "id": "line-excluded", "import_id": "imp-b", "description": "TRANSFER LAIN DIKECUALIKAN", "duplicate_status": "excluded", "created_at": "2026-09-28T04:00:00+00:00"},
            {**shared, "id": "line-clear", "import_id": "imp-a", "duplicate_status": "clear", "created_at": "2026-09-28T05:00:00+00:00"},
        ])
        before = {
            "lines": _snapshot(await server.db[LINES].find({}, {"_id": 0}).to_list(20)),
            "imports": _snapshot(await server.db[IMPORTS].find({}, {"_id": 0}).to_list(20)),
            "matches": await server.db[RECON_MATCHES].count_documents({}),
            "journals": await server.db[JOURNAL_ENTRIES].count_documents({}),
        }
        listed = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        assert listed.status_code == 200, listed.text
        after = {
            "lines": _snapshot(await server.db[LINES].find({}, {"_id": 0}).to_list(20)),
            "imports": _snapshot(await server.db[IMPORTS].find({}, {"_id": 0}).to_list(20)),
            "matches": await server.db[RECON_MATCHES].count_documents({}),
            "journals": await server.db[JOURNAL_ENTRIES].count_documents({}),
        }
        assert after == before
        body = listed.json()
        assert len(body["groups"]) == 1
        group = body["groups"][0]
        assert group["candidate_count"] == 2
        assert group["amount"] == 70000000
        assert group["direction"] == "out"
        assert group["business_date"] == "2026-09-28"
        assert group["normalized_description"] == "pemindahan dana rakha edy syah"
        assert [line["statement_line_id"] for line in group["lines"]] == ["line-a", "line-b"]
        assert group["lines"][0]["filename"] == "mutasi-2809.csv"
        assert group["lines"][0]["imported_at"] == "2026-09-28T02:14:00+00:00"
        assert group["lines"][0]["duplicate_status"] == "pending_review"
        assert group["lines"][0]["active_claim"] is None
        assert group["lines"][0]["reconciliation_status"] == "unmatched"
        assert "line-excluded" not in str(body)
        assert "line-clear" not in str(body)
        assert "line-alone" not in str(body)

        kept = await c.post("/api/cash-bank/statements/lines/line-a/duplicate-resolution", json={
            "action": "keep", "reason": "transaksi asli", "idempotency_key": "keep-line-a",
        })
        assert kept.status_code == 200, kept.text
        stored_a = await server.db[LINES].find_one({"id": "line-a"}, {"_id": 0})
        assert stored_a["duplicate_status"] == "kept"
        assert stored_a["amount"] == 70000000
        assert stored_a["date"] == "2026-09-28"
        assert stored_a["description"] == description
        assert len(stored_a["resolutions"]) == 1
        assert stored_a["resolutions"][0]["action"] == "keep"
        assert stored_a["resolutions"][0]["reason"] == "transaksi asli"
        replay = await c.post("/api/cash-bank/statements/lines/line-a/duplicate-resolution", json={
            "action": "keep", "reason": "transaksi asli", "idempotency_key": "keep-line-a",
        })
        assert replay.status_code == 200, replay.text
        assert len((await server.db[LINES].find_one({"id": "line-a"}, {"_id": 0, "resolutions": 1}))["resolutions"]) == 1
        conflict = await c.post("/api/cash-bank/statements/lines/line-a/duplicate-resolution", json={
            "action": "exclude", "reason": "alasan berbeda", "idempotency_key": "keep-line-a",
        })
        assert conflict.status_code == 409
        stayed = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        assert stayed.status_code == 200, stayed.text
        stayed_lines = {line["statement_line_id"]: line for line in stayed.json()["groups"][0]["lines"]}
        assert stayed_lines["line-a"]["duplicate_status"] == "kept"
        assert stayed_lines["line-a"]["resolution"]["reason"] == "transaksi asli"
        assert stayed_lines["line-a"]["resolution"]["actor"]["name"] == "admin"
        assert stayed_lines["line-a"]["resolution"]["at"]
        assert stayed_lines["line-b"]["duplicate_status"] == "pending_review"
        assert stayed_lines["line-b"]["resolution"] is None
        assert "line-clear" not in stayed.text
        assert "line-alone" not in stayed.text

        excluded = await c.post("/api/cash-bank/statements/lines/line-b/duplicate-resolution", json={
            "action": "exclude", "reason": "salinan impor ulang", "idempotency_key": "exclude-line-b",
        })
        assert excluded.status_code == 200, excluded.text
        stored_b = await server.db[LINES].find_one({"id": "line-b"}, {"_id": 0})
        assert stored_b["duplicate_status"] == "excluded"
        assert stored_b["amount"] == 70000000
        assert await server.db[LINES].count_documents({"id": "line-b"}) == 1
        closed = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        assert closed.status_code == 200, closed.text
        assert closed.json()["groups"] == []

        await server.db[LINES].insert_one({
            **shared, "id": "line-matched", "import_id": "imp-a", "created_at": "2026-09-28T06:00:00+00:00",
        })
        await server.db[RECON_MATCHES].insert_one({
            "id": "match-1", "statement_line_id": "line-matched", "status": "active",
            "cash_bank_id": account, "journal_line_id": "journal-line-1",
        })
        denied = await c.post("/api/cash-bank/statements/lines/line-matched/duplicate-resolution", json={
            "action": "exclude", "reason": "masih cocok", "idempotency_key": "exclude-matched",
        })
        assert denied.status_code == 409
        assert "Batalkan pencocokan" in denied.text
        matched_line = await server.db[LINES].find_one({"id": "line-matched"}, {"_id": 0, "duplicate_status": 1, "amount": 1})
        assert matched_line["duplicate_status"] == "pending_review"
        assert matched_line["amount"] == 70000000
        assert await server.db[RECON_MATCHES].count_documents({"id": "match-1", "status": "active"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before["journals"]


async def test_duplicate_group_remains_until_no_pending_candidate_is_left():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        account = bank["id"]
        await server.db[IMPORTS].insert_many([
            {"id": "imp-c", "cash_bank_id": account, "filename": "mutasi-c.csv", "status": "complete", "created_at": "2026-09-28T02:00:00+00:00", "file_sha256": "c" * 64},
            {"id": "imp-d", "cash_bank_id": account, "filename": "mutasi-d.csv", "status": "complete", "created_at": "2026-09-29T02:00:00+00:00", "file_sha256": "d" * 64},
            {"id": "imp-e", "cash_bank_id": account, "filename": "mutasi-e.csv", "status": "complete", "created_at": "2026-09-30T02:00:00+00:00", "file_sha256": "e" * 64},
        ])
        pair = {
            "cash_bank_id": account, "date": "2026-09-28", "description": "SETORAN KEDUA",
            "direction": "in", "amount": 1500000, "duplicate_status": "pending_review",
            "sealed": True, "resolutions": [], "active_claim": None, "identity_guarantee": "tidak_dijamin",
        }
        await server.db[LINES].insert_many([
            {**pair, "id": "keep-a", "import_id": "imp-c", "created_at": "2026-09-28T02:00:00+00:00"},
            {**pair, "id": "keep-b", "import_id": "imp-d", "created_at": "2026-09-29T02:00:00+00:00"},
            {**pair, "id": "clear-sibling", "import_id": "imp-c", "description": "TRANSFER BIASA", "duplicate_status": "clear", "created_at": "2026-09-28T02:10:00+00:00"},
            {**pair, "id": "pending-only", "import_id": "imp-d", "description": "TRANSFER BIASA", "created_at": "2026-09-28T02:11:00+00:00"},
            {**pair, "id": "crowd-a", "import_id": "imp-c", "description": "TIGA KANDIDAT", "created_at": "2026-09-28T03:00:00+00:00"},
            {**pair, "id": "crowd-b", "import_id": "imp-d", "description": "TIGA KANDIDAT", "created_at": "2026-09-28T03:01:00+00:00"},
            {**pair, "id": "crowd-c", "import_id": "imp-e", "description": "TIGA KANDIDAT", "created_at": "2026-09-28T03:02:00+00:00"},
            {**pair, "id": "same-a", "import_id": "imp-c", "description": "SATU BERKAS", "created_at": "2026-09-28T04:00:00+00:00"},
            {**pair, "id": "same-b", "import_id": "imp-c", "description": "SATU BERKAS", "created_at": "2026-09-28T04:01:00+00:00"},
        ])
        excluded = await c.post("/api/cash-bank/statements/lines/keep-b/duplicate-resolution", json={
            "action": "exclude", "reason": "salinan berkas kedua", "idempotency_key": "exclude-keep-b",
        })
        assert excluded.status_code == 200, excluded.text
        replay = await c.post("/api/cash-bank/statements/lines/keep-b/duplicate-resolution", json={
            "action": "exclude", "reason": "salinan berkas kedua", "idempotency_key": "exclude-keep-b",
        })
        assert replay.status_code == 200, replay.text
        assert len((await server.db[LINES].find_one({"id": "keep-b"}, {"_id": 0, "resolutions": 1}))["resolutions"]) == 1
        refreshed = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        assert refreshed.status_code == 200, refreshed.text
        groups = {group["description"]: group for group in refreshed.json()["groups"]}
        assert "TRANSFER BIASA" not in groups
        assert "pending-only" not in refreshed.text
        pair_lines = {line["statement_line_id"]: line for line in groups["SETORAN KEDUA"]["lines"]}
        assert pair_lines["keep-a"]["duplicate_status"] == "pending_review"
        assert pair_lines["keep-b"]["duplicate_status"] == "excluded"
        assert pair_lines["keep-b"]["resolution"]["reason"] == "salinan berkas kedua"
        assert pair_lines["keep-b"]["resolution"]["actor"]["name"] == "admin"
        kept = await c.post("/api/cash-bank/statements/lines/keep-a/duplicate-resolution", json={
            "action": "keep", "reason": "baris yang dipakai", "idempotency_key": "keep-keep-a",
        })
        assert kept.status_code == 200, kept.text
        finished = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        finished_names = {group["description"] for group in finished.json()["groups"]}
        assert "SETORAN KEDUA" not in finished_names
        crowd = next(group for group in finished.json()["groups"] if group["description"] == "TIGA KANDIDAT")
        assert crowd["candidate_count"] == 3
        await c.post("/api/cash-bank/statements/lines/crowd-a/duplicate-resolution", json={
            "action": "keep", "reason": "pertama dari tiga", "idempotency_key": "keep-crowd-a",
        })
        await c.post("/api/cash-bank/statements/lines/same-a/duplicate-resolution", json={
            "action": "keep", "reason": "baris berkas sama", "idempotency_key": "keep-same-a",
        })
        again = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        by_name = {group["description"]: group for group in again.json()["groups"]}
        assert by_name["TIGA KANDIDAT"]["candidate_count"] == 3
        assert {line["duplicate_status"] for line in by_name["TIGA KANDIDAT"]["lines"]} == {"kept", "pending_review"}
        assert {line["statement_line_id"] for line in by_name["TIGA KANDIDAT"]["lines"] if line["duplicate_status"] == "pending_review"} == {"crowd-b", "crowd-c"}
        same = by_name["SATU BERKAS"]
        assert same["candidate_count"] == 2
        assert {line["import_id"] for line in same["lines"]} == {"imp-c"}
        assert {line["duplicate_status"] for line in same["lines"]} == {"kept", "pending_review"}
        assert await server.db[LINES].count_documents({"id": "keep-b"}) == 1


async def test_same_amount_different_reference_stays_two_transactions():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-28,Transfer 70 juta,,70000000,WS95271,,\n"
            "2026-09-28,Transfer 70 juta,,70000000,WS95300,,\n"
        )
        _preview, imported = await _import(c, bank["id"], text)
        assert imported.status_code == 201, imported.text
        rows = await server.db[LINES].find({}, {"_id": 0}).sort("reference", 1).to_list(5)
        assert len(rows) == 2
        assert [row["reference"] for row in rows] == ["WS95271", "WS95300"]
        assert {row["duplicate_status"] for row in rows} == {"clear"}
        assert {row["amount"] for row in rows} == {70000000}
        assert {row["description"] for row in rows} == {"Transfer 70 juta"}
        assert await server.db[LINES].count_documents({"duplicate_status": "pending_review"}) == 0
        folded = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-28,Transfer 70 juta,,70000000,  ws95271  ,,100\n"
        )
        again = (await _import(c, bank["id"], folded))[1]
        assert again.status_code == 201, again.text
        assert again.json()["stored_count"] == 0
        assert again.json()["reused_count"] == 1
        after = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(after) == 2
        kept = next(row for row in after if row["reference"] == "WS95271")
        original = next(row for row in rows if row["reference"] == "WS95271")
        assert kept["id"] == original["id"]
        assert kept["date"] == original["date"]
        assert kept["amount"] == original["amount"]
        assert kept["description"] == original["description"]
        assert kept["reference"] == "WS95271"
        batch = await server.db[IMPORTS].find_one({"id": again.json()["id"]}, {"_id": 0, "reused_line_ids": 1})
        assert batch["reused_line_ids"] == [original["id"]]


async def test_reimport_of_reconciled_line_leaves_the_match_unchanged():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 70000000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        journal_lines = await server.db[JOURNAL_LINES].count_documents({})
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-28,Transfer 70 juta,70000000,,WS95271,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-28,Transfer 70 juta,70000000,,WS95271,,200\n"
        )
        _preview, imported = await _import(c, bank["id"], first)
        assert imported.status_code == 201, imported.text
        before = await server.db[LINES].find_one({}, {"_id": 0})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book_line = ws.json()["book_lines"][0]["journal_line_id"]
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": before["id"],
            "journal_line_id": book_line, "idempotency_key": "keep-ws95271", **PERIOD,
        })
        assert matched.status_code == 201, matched.text
        pair = await server.db[RECON_MATCHES].find_one({"id": matched.json()["id"]}, {"_id": 0})
        claimed = await server.db[LINES].find_one({"id": before["id"]}, {"_id": 0, "active_claim": 1})
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["stored_count"] == 0
        assert again.json()["reused_count"] == 1
        assert again.json()["protected_count"] == 1
        assert again.json()["updated_count"] == 0
        assert again.json()["new_count"] == 0
        assert again.json()["fully_accepted"] is True
        after = await server.db[LINES].find_one({"id": before["id"]}, {"_id": 0})
        assert await server.db[LINES].count_documents({}) == 1
        assert after["id"] == before["id"]
        assert after["date"] == before["date"]
        assert after["amount"] == before["amount"]
        assert after["description"] == before["description"]
        assert after["duplicate_status"] == "clear"
        assert after["running_balance"] == before["running_balance"] == 100
        assert after.get("import_updates") in (None, [])
        assert after.get("active_claim") == claimed.get("active_claim") == pair["id"]
        active = await server.db[RECON_MATCHES].find_one({"id": pair["id"]}, {"_id": 0})
        assert active["status"] == "active"
        assert active["statement_line_id"] == before["id"]
        assert active["journal_line_id"] == pair["journal_line_id"]
        assert await server.db[RECON_MATCHES].count_documents({}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[JOURNAL_LINES].count_documents({}) == journal_lines
        batch = await server.db[IMPORTS].find_one({"id": again.json()["id"]}, {"_id": 0, "reused_line_ids": 1})
        assert batch["reused_line_ids"] == [before["id"]]


async def test_extra_identical_copy_stays_a_second_clear_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0})
        imported = (await _import(c, bank["id"], second))[1]
        assert imported.status_code == 201, imported.text
        assert imported.json()["reused_count"] == 1
        assert imported.json()["stored_count"] == 1
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 2
        kept = next(row for row in rows if row["id"] == original["id"])
        extra = next(row for row in rows if row["id"] != original["id"])
        assert kept["duplicate_status"] == "clear"
        assert kept["date"] == original["date"]
        assert kept["amount"] == original["amount"]
        assert kept["description"] == original["description"]
        assert extra["duplicate_status"] == "clear"
        assert extra.get("duplicate_reason") is None
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        visible = {row["id"]: row for row in ws.json()["bank_lines"]}
        assert visible[kept["id"]]["matchable"] is True
        assert visible[extra["id"]]["matchable"] is True
        twins = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-06,Tanpa pembeda,,100,,,\n"
            "2026-09-06,Tanpa pembeda,,100,,,\n"
        )
        twin_import = (await _import(c, bank["id"], twins))[1]
        assert twin_import.status_code == 201, twin_import.text
        ambiguous = await server.db[LINES].find({"description": "Tanpa pembeda"}, {"_id": 0, "duplicate_status": 1}).to_list(5)
        assert len(ambiguous) == 2
        assert {row["duplicate_status"] for row in ambiguous} == {"clear"}


async def test_suggestions_stay_within_seven_days_and_do_not_match():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        books = (
            ("2026-09-30", "Masuk WS95271"),
            ("2026-09-30", "Masuk lain"),
            ("2026-09-29", "Beda satu"),
            ("2026-09-25", "Beda lima"),
            ("2026-09-23", "Beda tujuh"),
            ("2026-09-22", "Beda delapan"),
        )
        for book_date, desc in books:
            await _receipt(c, bank["id"], income["id"], 880000, date=book_date, desc=desc)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        journal_lines = await server.db[JOURNAL_LINES].count_documents({})
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-30,Transfer 880,880000,,WS95271,,\n"
        ))
        assert imported.status_code == 201, imported.text
        line_id = (await server.db[LINES].find_one({}, {"_id": 0, "id": 1}))["id"]
        before = await server.db[RECON_MATCHES].count_documents({})
        suggestions = await c.get("/api/cash-bank/reconciliations/pairs/suggestions", params={
            "statement_line_id": line_id, **PERIOD,
        })
        assert suggestions.status_code == 200, suggestions.text
        body = suggestions.json()
        assert body["applied"] is False
        assert body["ambiguous"] is True
        found = [item["description"] for item in body["suggestions"]]
        assert found == ["Masuk WS95271", "Masuk lain", "Beda satu", "Beda lima", "Beda tujuh"]
        assert "Beda delapan" not in found
        assert found.index("Beda satu") < found.index("Beda lima")
        assert await server.db[RECON_MATCHES].count_documents({}) == before
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[JOURNAL_LINES].count_documents({}) == journal_lines


async def test_same_reference_different_description_stays_two_clear_lines():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer jasa,750000,,REF-SAMA,,\n"
            "2026-09-05,Transfer akomodasi,750000,,REF-SAMA,,\n"
        )
        imported = (await _import(c, bank["id"], text))[1]
        assert imported.status_code == 201, imported.text
        assert imported.json()["conflict_count"] == 0
        assert imported.json()["fully_accepted"] is True
        assert imported.json()["new_count"] == 2
        rows = await server.db[LINES].find({}, {"_id": 0}).sort("description", 1).to_list(5)
        assert len(rows) == 2
        assert [row["description"] for row in rows] == ["Transfer akomodasi", "Transfer jasa"]
        assert {row["reference"] for row in rows} == {"REF-SAMA"}
        assert {row["duplicate_status"] for row in rows} == {"clear"}


async def test_authoritative_id_with_different_amount_is_a_reported_conflict():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        journal_lines = await server.db[JOURNAL_LINES].count_documents({})
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-1,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-06,Transfer masuk,100,,REF,TX-1,900\n"
        )
        mapping = _mapping(bank_txn_id_authoritative=True)
        assert (await _import(c, bank["id"], first, mapping))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0})
        again = (await _import(c, bank["id"], second, mapping))[1]
        assert again.status_code == 201, again.text
        body = again.json()
        assert body["fully_accepted"] is False
        assert body["conflict_count"] == 1
        assert body["new_count"] == 0
        assert body["updated_count"] == 0
        assert "belum menerima" in body["result_note"]
        assert "semua berhasil" not in body["result_note"].lower()
        conflict = body["conflicts"][0]
        assert conflict["source_row"] == 2
        assert conflict["reason"] == "id_otoritatif_bertentangan"
        assert conflict["bank_txn_id"] == "TX-1"
        assert conflict["statement_line_id"] == original["id"]
        assert "nominal berbeda" in conflict["message"]
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 1
        assert rows[0]["id"] == original["id"]
        assert rows[0]["amount"] == original["amount"]
        assert rows[0]["date"] == original["date"]
        assert rows[0]["running_balance"] == original["running_balance"]
        assert rows[0].get("import_updates") in (None, [])
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[JOURNAL_LINES].count_documents({}) == journal_lines


async def test_repeated_authoritative_id_in_one_file_is_reported():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        same = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-1,100\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-1,100\n"
        )
        disagree = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-2,100\n"
            "2026-09-06,Transfer lain,100,,REF,TX-2,50\n"
        )
        mapping = _mapping(bank_txn_id_authoritative=True)
        imported = (await _import(c, bank["id"], same, mapping))[1]
        assert imported.status_code == 201, imported.text
        assert imported.json()["new_count"] == 1
        assert imported.json()["conflict_count"] == 1
        assert imported.json()["fully_accepted"] is False
        assert imported.json()["conflicts"][0]["reason"] == "id_otoritatif_berulang"
        assert await server.db[LINES].count_documents({"authoritative_txn_id": "TX-1"}) == 1
        blocked = (await _import(c, bank["id"], disagree, mapping))[1]
        assert blocked.status_code == 201, blocked.text
        assert blocked.json()["new_count"] == 0
        assert blocked.json()["conflict_count"] == 2
        assert blocked.json()["fully_accepted"] is False
        assert {row["reason"] for row in blocked.json()["conflicts"]} == {"id_otoritatif_bertentangan"}
        assert await server.db[LINES].count_documents({"authoritative_txn_id": "TX-2"}) == 0


async def test_fewer_identical_copies_do_not_delete_the_extra_stored_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,250\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        originals = await server.db[LINES].find({}, {"_id": 0, "id": 1}).sort("id", 1).to_list(5)
        assert len(originals) == 2
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["new_count"] == 0
        assert again.json()["updated_count"] == 1
        assert again.json()["stored_count"] == 0
        rows = await server.db[LINES].find({}, {"_id": 0}).sort("id", 1).to_list(5)
        assert [row["id"] for row in rows] == [row["id"] for row in originals]
        assert {row["duplicate_status"] for row in rows} == {"clear"}
        assert rows[0]["running_balance"] == 250
        assert rows[1]["running_balance"] == 100


async def test_excluded_line_is_not_reactivated_or_used_as_the_clear_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,180\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        resolutions = [{"action": "exclude", "reason": "bukan transaksi ini", "at": "2026-09-06T00:00:00+00:00"}]
        await server.db[LINES].update_one(
            {"id": original["id"]},
            {"$set": {"duplicate_status": "excluded", "resolutions": resolutions}},
        )
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["new_count"] == 1
        assert again.json()["updated_count"] == 0
        assert again.json()["fully_accepted"] is True
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 2
        excluded = next(row for row in rows if row["id"] == original["id"])
        added = next(row for row in rows if row["id"] != original["id"])
        assert excluded["duplicate_status"] == "excluded"
        assert excluded["resolutions"] == resolutions
        assert excluded["running_balance"] == 100
        assert added["duplicate_status"] == "clear"
        assert added["running_balance"] == 180


async def test_historical_pending_review_occupies_a_slot_and_is_not_rewritten():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,180\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0})
        resolutions = [{"action": "note", "reason": "menunggu keputusan lama", "at": "2026-09-06T00:00:00+00:00"}]
        await server.db[LINES].update_one(
            {"id": original["id"]},
            {"$set": {"duplicate_status": "pending_review", "resolutions": resolutions, "running_balance": 100}},
        )
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["new_count"] == 0
        assert again.json()["updated_count"] == 0
        assert again.json()["unchanged_count"] == 1
        assert again.json()["conflict_count"] == 0
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 1
        assert rows[0]["id"] == original["id"]
        assert rows[0]["duplicate_status"] == "pending_review"
        assert rows[0]["resolutions"] == resolutions
        assert rows[0]["running_balance"] == 100
        assert rows[0]["description"] == original["description"]
        assert rows[0].get("import_updates") in (None, [])


async def test_claim_taken_before_overwrite_blocks_the_write():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1, "running_balance": 1})

        claimed = await _claim_statement_line(server.db, original["id"], "matcher-hidup")
        assert claimed == "created"
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["protected_count"] == 1
        assert again.json()["updated_count"] == 0
        assert again.json()["new_count"] == 0
        assert again.json()["stored_count"] == 0
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 1
        assert rows[0]["id"] == original["id"]
        assert rows[0]["running_balance"] == 100
        assert rows[0]["active_claim"] == "matcher-hidup"
        assert rows[0].get("import_updates") in (None, [])
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_match_created_while_import_holds_the_line_skips_the_write():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})

        async def plant(line_id):
            held = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert str(held.get("active_claim") or "").startswith("import-hold:")
            await server.db[RECON_MATCHES].insert_one({
                "id": "race-match",
                "statement_line_id": line_id,
                "journal_line_id": "book-race",
                "cash_bank_id": bank["id"],
                "status": "active",
                "match_kind": "pair",
            })

        statement_svc._while_reimport_hold = plant
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["protected_count"] == 1
        assert again.json()["updated_count"] == 0
        assert again.json()["new_count"] == 0
        row = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0})
        assert row["running_balance"] == 100
        assert row.get("active_claim") in (None, "")
        assert row.get("import_updates") in (None, [])
        assert await server.db[LINES].count_documents({}) == 1
        active = await server.db[RECON_MATCHES].find_one({"id": "race-match"}, {"_id": 0, "status": 1})
        assert active["status"] == "active"


async def test_reimport_retry_does_not_append_a_second_audit():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        journal_lines = await server.db[JOURNAL_LINES].count_documents({})
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        updated = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,200\n"
        )
        same_balance = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,200.00\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        changed = (await _import(c, bank["id"], updated))[1]
        assert changed.status_code == 201, changed.text
        assert changed.json()["updated_count"] == 1
        line = await server.db[LINES].find_one({}, {"_id": 0, "import_updates": 1, "import_id": 1, "running_balance": 1})
        assert line["running_balance"] == 200
        assert len(line["import_updates"]) == 1
        audit_id = line["import_updates"][0]["id"]
        assert line["import_updates"][0]["source_batch_id"] == changed.json()["id"]
        assert line["import_updates"][0]["before"]["running_balance"] == 100
        assert line["import_updates"][0]["after"]["running_balance"] == 200
        replay = (await _import(c, bank["id"], updated))[1]
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == changed.json()["id"]
        assert replay.json()["unchanged_count"] == 1
        assert replay.json()["updated_count"] == 0
        assert replay.json()["result_note"] == "Berkas yang sama sudah diimpor. Tidak ada transaksi baru."
        third = (await _import(c, bank["id"], same_balance))[1]
        assert third.status_code == 201, third.text
        assert third.json()["id"] != changed.json()["id"]
        assert third.json()["updated_count"] == 0
        assert third.json()["unchanged_count"] == 1
        assert third.json()["new_count"] == 0
        after = await server.db[LINES].find_one({}, {"_id": 0})
        assert len(after["import_updates"]) == 1
        assert after["import_updates"][0]["id"] == audit_id
        assert after["import_id"] == line["import_id"]
        assert after["running_balance"] == 200
        assert await server.db[LINES].count_documents({}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        assert await server.db[JOURNAL_LINES].count_documents({}) == journal_lines


async def test_authoritative_id_on_an_excluded_line_conflicts_without_a_copy():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-EX,100\n"
        )
        later = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Uraian baru,750000,,LAIN,TX-EX,400\n"
        )
        mapping = _mapping(bank_txn_id_authoritative=True)
        assert (await _import(c, bank["id"], text, mapping))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        resolutions = [{"action": "exclude", "reason": "dikecualikan", "at": "2026-09-06T00:00:00+00:00"}]
        await server.db[LINES].update_one(
            {"id": original["id"]},
            {"$set": {"duplicate_status": "excluded", "resolutions": resolutions}},
        )
        again = (await _import(c, bank["id"], later, mapping))[1]
        assert again.status_code == 201, again.text
        assert again.json()["fully_accepted"] is False
        assert again.json()["conflict_count"] == 1
        assert again.json()["new_count"] == 0
        assert again.json()["conflicts"][0]["reason"] == "id_otoritatif_pada_baris_dikecualikan"
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(5)
        assert len(rows) == 1
        assert rows[0]["duplicate_status"] == "excluded"
        assert rows[0]["resolutions"] == resolutions
        assert rows[0]["description"] == "Transfer masuk"
        assert rows[0]["running_balance"] == 100


async def test_claim_arriving_after_classification_blocks_the_cas():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})

        async def steal(line_id):
            won = await _claim_statement_line(server.db, line_id, "matcher-hidup")
            assert won == "created"

        statement_svc._before_reimport_write = steal
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert again.json()["protected_count"] == 1
        assert again.json()["updated_count"] == 0
        assert again.json()["new_count"] == 0
        row = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0})
        assert row["running_balance"] == 100
        assert row["active_claim"] == "matcher-hidup"
        assert row.get("import_updates") in (None, [])
        assert await server.db[LINES].count_documents({}) == 1


async def test_import_hold_blocks_pair_then_matching_uses_the_updated_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        journal_lines = await server.db[JOURNAL_LINES].count_documents({})
        await _receipt(c, bank["id"], income["id"], 750000)
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]
        seen = {}

        async def during(line_id):
            held = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1, "running_balance": 1})
            assert str(held["active_claim"]).startswith("import-hold:")
            assert held["running_balance"] == 100
            try:
                await create_pair(
                    server.db, cash_bank_id=bank["id"], statement_line_id=line_id,
                    journal_line_id=book, idempotency_key="during-hold",
                    start=PERIOD["start_date"], end=PERIOD["end_date"], user=admin,
                )
            except PairConflictError as exc:
                seen["error"] = str(exc)
            else:
                seen["error"] = ""
            still = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert still["active_claim"] == held["active_claim"]
            assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0

        statement_svc._while_reimport_hold = during
        again = (await _import(c, bank["id"], second))[1]
        assert again.status_code == 201, again.text
        assert "diklaim" in seen["error"]
        assert again.json()["updated_count"] == 1
        released = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0})
        assert released["running_balance"] == 260
        assert released.get("active_claim") in (None, "")
        paired = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=original["id"],
            journal_line_id=book, idempotency_key="after-release",
            start=PERIOD["start_date"], end=PERIOD["end_date"], user=admin,
        )
        assert paired["status"] == "active"
        assert paired["statement_line_id"] == original["id"]
        assert paired["amount"] == 750000
        stored = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0, "running_balance": 1, "amount": 1})
        assert stored["running_balance"] == 260
        assert stored["amount"] == 750000
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals + 1
        assert await server.db[JOURNAL_LINES].count_documents({}) == journal_lines + 2


async def test_pair_rereads_after_import_instead_of_using_the_old_snapshot():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]

        async def update_before_claim():
            imported = (await _import(c, bank["id"], second))[1]
            assert imported.status_code == 201, imported.text
            assert imported.json()["updated_count"] == 1
            current = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0, "running_balance": 1, "active_claim": 1})
            assert current["running_balance"] == 260
            assert current.get("active_claim") in (None, "")

        pair_svc._before_pair_claim = update_before_claim
        paired = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=original["id"],
            journal_line_id=book, idempotency_key="after-update",
            start=PERIOD["start_date"], end=PERIOD["end_date"], user=admin,
        )
        assert paired["status"] == "active"
        assert paired["statement_line_id"] == original["id"]
        assert paired["amount"] == 750000
        stored = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0, "running_balance": 1})
        assert stored["running_balance"] == 260
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 1


async def test_pair_conflicts_when_the_fresh_line_no_longer_matches_the_snapshot():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        assert (await _import(c, bank["id"], text))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]

        async def change_amount():
            await server.db[LINES].update_one({"id": original["id"]}, {"$set": {"amount": 1}})

        pair_svc._before_pair_claim = change_amount
        with pytest.raises(PairConflictError, match="Nominal"):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=original["id"],
                journal_line_id=book, idempotency_key="stale-snapshot",
                start=PERIOD["start_date"], end=PERIOD["end_date"], user=admin,
            )
        row = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0, "amount": 1, "active_claim": 1})
        assert row["amount"] == 1
        assert row.get("active_claim") in (None, "")
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0


async def test_import_failure_after_claim_releases_only_its_own_token():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        first = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,100\n"
        )
        second = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,260\n"
        )
        assert (await _import(c, bank["id"], first))[1].status_code == 201
        original = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})

        async def explode(line_id):
            held = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert str(held["active_claim"]).startswith("import-hold:")
            raise RuntimeError("setelah klaim impor")

        statement_svc._while_reimport_hold = explode
        try:
            failed = (await _import(c, bank["id"], second))[1]
        except RuntimeError as exc:
            assert "setelah klaim impor" in str(exc)
        else:
            assert failed.status_code >= 500
        row = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0})
        assert row["running_balance"] == 100
        assert row.get("active_claim") in (None, "")
        assert row.get("import_updates") in (None, [])
        assert await server.db[LINES].count_documents({}) == 1

        async def replace_token(line_id):
            held = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            replaced = await server.db[LINES].update_one(
                {"id": line_id, "active_claim": held["active_claim"]},
                {"$set": {"active_claim": "matcher-lain"}},
            )
            assert replaced.modified_count == 1
            raise RuntimeError("token berpindah")

        statement_svc._while_reimport_hold = replace_token
        third = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,270\n"
        )
        try:
            failed_again = (await _import(c, bank["id"], third))[1]
        except RuntimeError as exc:
            assert "token berpindah" in str(exc)
        else:
            assert failed_again.status_code >= 500
        kept = await server.db[LINES].find_one({"id": original["id"]}, {"_id": 0})
        assert kept["active_claim"] == "matcher-lain"
        assert kept["running_balance"] == 100
        assert kept.get("import_updates") in (None, [])


async def test_old_complete_batch_has_no_false_success_counts():
    body = public_import({
        "id": "batch-lama",
        "cash_bank_id": "bank-lama",
        "filename": "lama.csv",
        "status": "complete",
        "accepted_count": 2,
        "stored_count": 2,
        "reused_count": 0,
        "operator_coverage_confirmed": False,
    })
    assert body["stored_count"] == 2
    assert body["status"] == "complete"
    assert body["new_count"] is None
    assert body["updated_count"] is None
    assert body["protected_count"] is None
    assert body["unchanged_count"] is None
    assert body["conflict_count"] is None
    assert body["conflicts"] is None
    assert body["fully_accepted"] is None
    assert body["result_note"] == ""
    recorded = public_import({
        **body,
        "outcome_ready": True,
        "new_count": 0,
        "updated_count": 0,
        "protected_count": 0,
        "unchanged_count": 0,
        "conflict_count": 1,
        "conflicts": [{"source_row": 2, "message": "bentrok"}],
        "fully_accepted": False,
        "result_note": "Impor belum menerima semua transaksi. Jurnal tidak dibuat.",
    })
    assert recorded["fully_accepted"] is False
    assert recorded["conflict_count"] == 1
    assert "belum menerima" in recorded["result_note"]


@pytest.mark.anyio
async def test_running_balance_is_not_the_period_statement_balance():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-RUN,750000\n"
            "2026-09-20,Biaya admin,,1000,ADM,TX-FEE,740000\n"
        ))
        assert imported.status_code == 201, imported.text
        wide = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        assert wide.status_code == 200, wide.text
        body = wide.json()
        balances = {row["description"]: row["running_balance"] for row in body["bank_lines"]}
        assert balances["Transfer masuk"] == 750000
        assert balances["Biaya admin"] == 740000
        assert body["statement_closing"]["amount"] is None
        assert body["statement_closing"]["label"] == "Belum tersedia"
        assert body["difference"] is None
        assert body["book_ending_balance"] == 750000
        assert body["balance_match_declared"] is False
        assert body["completion_status"] == "tidak_dinilai"
        assert "belum disediakan" in body["period_close_note"]
        on_line_date = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={
            "cash_bank_id": bank["id"], "start_date": "2026-09-01", "end_date": "2026-09-20",
        })
        viewed = on_line_date.json()
        assert viewed["statement_closing"]["amount"] == 740000
        assert viewed["statement_closing"]["status"] == "dari_kolom_saldo_impor"
        assert viewed["book_ending_balance"] == 750000
        assert viewed["difference"] == -10000
        assert viewed["completion_status"] == "tidak_dinilai"
        zero_bank, zero_income = await _setup(c, currency="IDR")
        _preview, zero_import = await _import(c, zero_bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-30,Saldo nol,,1000,REF,TX-ZERO,0\n"
        ))
        assert zero_import.status_code == 201, zero_import.text
        zero = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": zero_bank["id"], **PERIOD})
        zero_body = zero.json()
        assert zero_body["statement_closing"]["amount"] == 0
        assert zero_body["book_ending_balance"] == 0
        assert zero_body["difference"] == 0
        assert zero_body["bank_lines"][0]["running_balance"] == 0
        assert zero_income["id"]


@pytest.mark.anyio
async def test_pending_review_access_lists_hidden_lines_without_writing():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        other, _other_income = await _setup(c, currency="IDR")
        account = bank["id"]
        await server.db[IMPORTS].insert_many([
            {"id": "imp-access-a", "cash_bank_id": account, "filename": "mutasi-a.csv", "status": "complete", "created_at": "2026-09-02T01:00:00+00:00", "file_sha256": "c" * 64},
            {"id": "imp-access-b", "cash_bank_id": account, "filename": "mutasi-b.csv", "status": "complete", "created_at": "2026-09-03T01:00:00+00:00", "file_sha256": "d" * 64},
        ])
        shared = {
            "cash_bank_id": account, "date": "2026-09-28", "description": "PEMINDAHAN DANA",
            "direction": "out", "amount": 70000000, "duplicate_status": "pending_review",
            "identity_guarantee": "tidak_dijamin", "sealed": True, "resolutions": [], "active_claim": None,
        }
        await server.db[LINES].insert_many([
            {**shared, "id": "access-a", "import_id": "imp-access-a", "created_at": "2026-09-28T02:00:00+00:00"},
            {**shared, "id": "access-b", "import_id": "imp-access-b", "created_at": "2026-09-28T03:00:00+00:00"},
            {**shared, "id": "access-solo", "import_id": "imp-access-a", "description": "TRANSFER TUNGGAL", "amount": 1000, "created_at": "2026-09-28T04:00:00+00:00"},
            {**shared, "id": "access-outside", "import_id": "imp-access-a", "description": "DI LUAR PERIODE", "date": "2026-08-15", "amount": 2000},
            {**shared, "id": "access-unsealed", "import_id": "imp-access-a", "description": "BELUM TERSEGEL", "sealed": False, "amount": 3000},
            {**shared, "id": "access-nodate", "import_id": "imp-access-a", "description": "TANPA TANGGAL", "date": "", "amount": 4000},
            {**shared, "id": "access-other", "cash_bank_id": other["id"], "import_id": "imp-access-a", "description": "REKENING LAIN", "amount": 5000},
        ])
        before = _snapshot(await server.db[LINES].find({}, {"_id": 0}).to_list(20))
        groups = await c.get("/api/cash-bank/statements/duplicate-groups", params={"cash_bank_id": account, **PERIOD})
        assert groups.status_code == 200, groups.text
        assert [line["statement_line_id"] for line in groups.json()["groups"][0]["lines"]] == ["access-a", "access-b"]
        assert "access-solo" not in groups.text
        access = await c.get("/api/cash-bank/statements/pending-review", params={"cash_bank_id": account, **PERIOD})
        assert access.status_code == 200, access.text
        after = _snapshot(await server.db[LINES].find({}, {"_id": 0}).to_list(20))
        assert after == before
        body = access.json()
        assert body["truncated"] is False
        assert [line["statement_line_id"] for line in body["solo_lines"]] == ["access-solo"]
        assert body["solo_lines"][0]["filename"] == "mutasi-a.csv"
        assert body["solo_lines"][0]["active_claim"] is None
        assert body["solo_lines"][0]["reconciliation_status"] == "unmatched"
        assert "tunggal" in body["solo_lines"][0]["screen_reason"]
        assert [line["statement_line_id"] for line in body["outside_period"]] == ["access-outside"]
        assert "Di luar periode" in body["outside_period"][0]["screen_reason"]
        unreachable = {line["statement_line_id"]: line["screen_reason"] for line in body["unreachable"]}
        assert "tersegel" in unreachable["access-unsealed"]
        assert "Tanggal" in unreachable["access-nodate"]
        assert body["other_accounts"] == [{
            "cash_bank_id": other["id"],
            "name": other["name"],
            "pending_count": 1,
        }]
        assert "access-a" not in [line["statement_line_id"] for line in body["solo_lines"]]
        kept = await c.post("/api/cash-bank/statements/lines/access-solo/duplicate-resolution", json={
            "action": "keep", "reason": "baris asli", "idempotency_key": "keep-access-solo",
        })
        assert kept.status_code == 200, kept.text
        again = await c.get("/api/cash-bank/statements/pending-review", params={"cash_bank_id": account, **PERIOD})
        assert [line["statement_line_id"] for line in again.json()["solo_lines"]] == []
        stored = await server.db[LINES].find_one({"id": "access-solo"}, {"_id": 0, "duplicate_status": 1, "amount": 1})
        assert stored["duplicate_status"] == "kept"
        assert stored["amount"] == 1000
