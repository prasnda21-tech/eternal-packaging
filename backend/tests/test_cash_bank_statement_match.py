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
from services.cash_bank_pair_match import PairConflictError, create_pair
from services.cash_bank_reconciliation import RECON_MATCHES, RECONCILIATIONS, ensure_indexes as ensure_recon
from services.cash_bank_statement import (
    IMPORT_FENCES,
    IMPORTS,
    LINES,
    MANUAL_BALANCES,
    PREVIEWS,
    _after_statement_line_insert,
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
        assert {row["duplicate_status"] for row in rows} == {"pending_review"}


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


async def test_j_overlap_without_bank_id_needs_review():
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
        imported = (await _import(c, bank["id"], second))[1]
        assert imported.status_code == 201, imported.text
        pending = await server.db[LINES].find_one({"duplicate_status": "pending_review"}, {"_id": 0})
        assert pending
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        blocked = next(row for row in ws.json()["bank_lines"] if row["id"] == pending["id"])
        assert blocked["matchable"] is False
        book_line = ws.json()["book_lines"][0]["journal_line_id"]
        denied = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": pending["id"],
            "journal_line_id": book_line, "idempotency_key": "j-1", **PERIOD,
        })
        assert denied.status_code == 409
        kept = await c.post(f"/api/cash-bank/statements/lines/{pending['id']}/duplicate-resolution", json={
            "action": "keep", "reason": "dua transaksi sah", "idempotency_key": "keep-j",
        })
        assert kept.status_code == 200, kept.text
        assert kept.json()["duplicate_status"] == "kept"
        assert len(kept.json()["resolutions"]) == 1


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
