"""Revision checks for claim indexes, authoritative duplicates, and currency."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = f"recon_phase1_revision_{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}_{uuid.uuid4().hex[:6]}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
import services.cash_bank_pair_match as pair_svc
import services.cash_bank_statement as statement_svc
from server import app, current_user
from services.cash_bank_claim_migration import ClaimMigrationError, migrate_claim_indexes, preflight_claim_indexes
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    claim_feature_status,
    ensure_indexes as ensure_recon,
    legacy_journal_unique_name,
)
from services.cash_bank_statement import (
    IMPORTS,
    LINES,
    PREVIEWS,
    ensure_indexes as ensure_statement,
)
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal

pytestmark = pytest.mark.anyio
PERIOD = {"start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    for name in (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, RECONCILIATIONS, RECON_MATCHES,
        IMPORTS, LINES, PREVIEWS, "cash_bank_manual_statement_balances",
    ):
        await db[name].drop()
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    await ensure_statement(db)
    pair_svc._before_pair_claim = pair_svc._before_pair_claim
    statement_svc._before_statement_lines_insert = statement_svc._before_statement_lines_insert
    statement_svc._before_duplicate_resolution = statement_svc._before_duplicate_resolution
    yield
    pair_svc._before_pair_claim = pair_svc._before_pair_claim.__func__ if False else _async_noop
    statement_svc._before_statement_lines_insert = _async_noop
    statement_svc._before_duplicate_resolution = _async_noop


async def _async_noop():
    return None


def _user(role="admin", perms=None):
    uid = f"u-{role}-{uuid.uuid4().hex[:8]}"
    if role == "admin":
        override = {}
    elif perms is not None:
        override = {p: True for p in perms}
    else:
        override = {}
    return {
        "id": uid, "email": f"{uid}@example.com", "name": role,
        "role": role if role != "viewer" else "operator",
        "status": "active", "user_id": uid, "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _setup(c, *, currency=None):
    user = _user()
    cash = await create_account(server.db, {"code": f"B{uuid.uuid4().hex[:6]}", "name": "Bank", "account_type": "BANK"}, user=user)
    income = await create_account(server.db, {"code": f"I{uuid.uuid4().hex[:6]}", "name": "Lawan", "account_type": "BANK"}, user=user)
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
        "amount": amount, "description": desc, "reference": f"REF-{amount}-{uuid.uuid4().hex[:4]}",
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
        "bank_txn_id_authoritative": False,
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


async def _import(c, bank_id, text, mapping=None):
    preview = await c.post(
        "/api/cash-bank/statements/preview",
        data={"cash_bank_id": bank_id},
        files={"file": ("mutasi.csv", text.encode(), "text/csv")},
    )
    assert preview.status_code == 201, preview.text
    committed = await c.post("/api/cash-bank/statements/imports", json={
        "preview_id": preview.json()["id"],
        "mapping": mapping or _mapping(),
    })
    return preview.json(), committed


async def _legacy_unique(db):
    await db[RECON_MATCHES].drop()
    await db[RECON_MATCHES].create_index("id", unique=True)
    await db[RECON_MATCHES].create_index(
        "journal_line_id", unique=True, name="journal_line_id_1"
    )


async def test_1_startup_keeps_legacy_unique_index():
    await _legacy_unique(server.db)
    await server.db[RECON_MATCHES].insert_one({
        "id": "legacy-1", "reconciliation_id": "rk-1", "journal_line_id": "line-1",
    })
    before = await server.db[RECON_MATCHES].index_information()
    await ensure_recon(server.db)
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) == "journal_line_id_1"
    assert legacy_journal_unique_name(before) == "journal_line_id_1"
    assert "uniq_journal_line_claim" not in info
    status = await claim_feature_status(server.db)
    assert status["ready"] is False
    assert await server.db[RECON_MATCHES].count_documents({"id": "legacy-1"}) == 1


async def test_2_unready_feature_rejects_mutation_and_account_create_still_works():
    await _legacy_unique(server.db)
    await ensure_recon(server.db)
    admin = _user()
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        blocked = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": "missing",
            "journal_line_id": "missing", "idempotency_key": "nope", **PERIOD,
        })
        assert blocked.status_code == 409
        assert "belum siap" in blocked.text
        listed = await c.get("/api/cash-bank/accounts")
        assert listed.status_code == 200
        assert any(item["id"] == bank["id"] for item in listed.json()["items"])
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) == "journal_line_id_1"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_3_migration_keeps_legacy_and_cancelled_documents():
    await _legacy_unique(server.db)
    await server.db[RECON_MATCHES].insert_many([
        {"id": "legacy-1", "reconciliation_id": "rk-1", "journal_line_id": "line-legacy", "cash_bank_id": "b1"},
        {
            "id": "pair-old", "match_kind": "pair", "status": "cancelled", "cash_bank_id": "b1",
            "history_journal_line_id": "line-old", "history_statement_line_id": "stmt-old",
        },
    ])
    report = await migrate_claim_indexes(server.db)
    assert report["ready"] is True
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) is None
    assert info["uniq_journal_line_claim"]["unique"] is True
    assert info["uniq_journal_line_claim"]["partialFilterExpression"]
    stored = await server.db[RECON_MATCHES].find({}, {"_id": 0, "id": 1, "journal_line_id": 1, "history_journal_line_id": 1}).to_list(10)
    by_id = {row["id"]: row for row in stored}
    assert by_id["legacy-1"]["journal_line_id"] == "line-legacy"
    assert by_id["pair-old"]["history_journal_line_id"] == "line-old"
    assert "journal_line_id" not in by_id["pair-old"]
    assert (await claim_feature_status(server.db))["ready"] is True


async def test_4_preflight_conflict_changes_nothing():
    await server.db[RECON_MATCHES].drop()
    await server.db[RECON_MATCHES].insert_many([
        {"id": "a", "journal_line_id": "same-line", "reconciliation_id": "rk-a"},
        {"id": "b", "journal_line_id": "same-line", "reconciliation_id": "rk-b"},
    ])
    report = await preflight_claim_indexes(server.db)
    assert report["blocked"] is True
    assert report["changed_documents"] == 0
    with pytest.raises(ClaimMigrationError) as caught:
        await migrate_claim_indexes(server.db)
    assert caught.value.report["blocked"] is True
    info = await server.db[RECON_MATCHES].index_information()
    assert "uniq_journal_line_claim" not in info
    assert await server.db[RECON_MATCHES].count_documents({"journal_line_id": "same-line"}) == 2


async def test_5_migration_resumes_after_partial_create():
    await _legacy_unique(server.db)
    await server.db[RECON_MATCHES].insert_one({
        "id": "legacy-1", "reconciliation_id": "rk-1", "journal_line_id": "line-1",
    })
    with pytest.raises(ClaimMigrationError) as caught:
        await migrate_claim_indexes(server.db, fail_after="partials_created")
    assert caught.value.report["resumable"] is True
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) == "journal_line_id_1"
    assert "uniq_journal_line_claim" in info
    assert (await claim_feature_status(server.db))["ready"] is False
    report = await migrate_claim_indexes(server.db)
    assert report["ready"] is True
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) is None
    assert await server.db[RECON_MATCHES].count_documents({"id": "legacy-1"}) == 1


def _barrier(expected):
    state = {"n": 0}
    release = asyncio.Event()

    async def hook():
        state["n"] += 1
        if state["n"] >= expected:
            release.set()
        await release.wait()

    return hook


async def test_6_and_7_concurrent_authoritative_imports_have_one_eligible_line():
    admin = _user()
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        left = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("a.csv", b"tanggal,uraian,masuk,keluar,referensi,id,saldo\n2026-09-05,Satu,750000,,REF,TX-SAME,\n", "text/csv")},
        )
        right = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("b.csv", b"tanggal,uraian,masuk,keluar,referensi,id,saldo\n2026-09-05,Dua,750000,,REF,TX-SAME,\n", "text/csv")},
        )
        assert left.status_code == 201 and right.status_code == 201
        statement_svc._before_statement_lines_insert = _barrier(2)
        mapping = _mapping(bank_txn_id_authoritative=True)

        async def commit(preview_id):
            return await statement_svc.commit_import(
                server.db, preview_id=preview_id, mapping_raw=mapping, user=admin,
            )

        await asyncio.wait_for(asyncio.gather(commit(left.json()["id"]), commit(right.json()["id"])), timeout=8)
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(10)
        assert len(rows) == 2
        eligible = [row for row in rows if row.get("authoritative_txn_id") == "TX-SAME"]
        definite = [row for row in rows if row.get("duplicate_status") == "definite_duplicate"]
        assert len(eligible) == 1
        assert len(definite) == 1
        assert definite[0].get("duplicate_of_line_id") == eligible[0]["id"]
        kept = await c.post(
            f"/api/cash-bank/statements/lines/{definite[0]['id']}/duplicate-resolution",
            json={"action": "keep", "reason": "bukan dua transaksi", "idempotency_key": "keep-definite"},
        )
        assert kept.status_code == 409
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]
        results = []
        for row in rows:
            results.append(await c.post("/api/cash-bank/reconciliations/pairs", json={
                "cash_bank_id": bank["id"], "statement_line_id": row["id"],
                "journal_line_id": book, "idempotency_key": f"m-{row['id']}", **PERIOD,
            }))
        assert sorted(item.status_code for item in results) == [201, 409]
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals + 1


async def test_8_identical_rows_without_id_are_not_deleted():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c, currency="IDR")
        text = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
            "2026-09-05,Transfer masuk,750000,,REF,,\n"
        )
        _preview, imported = await _import(c, bank["id"], text, _mapping(bank_txn_id_authoritative=False))
        assert imported.status_code == 201, imported.text
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(10)
        assert len(rows) == 2
        assert {row["duplicate_status"] for row in rows} == {"pending_review"}
        assert {row["identity_guarantee"] for row in rows} == {"tidak_dijamin"}


async def test_9_and_10_matched_line_cannot_be_excluded_and_race_stays_consistent():
    admin = _user()
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000)
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-RACE,\n"
        ))
        assert imported.status_code == 201, imported.text
        line_id = (await server.db[LINES].find_one({}, {"_id": 0, "id": 1}))["id"]
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]
        pair_svc._before_pair_claim = _barrier(2)
        statement_svc._before_duplicate_resolution = pair_svc._before_pair_claim

        async def match():
            return await c.post("/api/cash-bank/reconciliations/pairs", json={
                "cash_bank_id": bank["id"], "statement_line_id": line_id,
                "journal_line_id": book, "idempotency_key": "race-match", **PERIOD,
            })

        async def exclude():
            return await c.post(f"/api/cash-bank/statements/lines/{line_id}/duplicate-resolution", json={
                "action": "exclude", "reason": "bukan baris ini", "idempotency_key": "race-exclude",
            })

        matched, excluded = await asyncio.wait_for(asyncio.gather(match(), exclude()), timeout=8)
        line = await server.db[LINES].find_one({"id": line_id}, {"_id": 0})
        active = await server.db[RECON_MATCHES].count_documents({
            "statement_line_id": line_id, "status": "active",
        })
        assert not (line.get("duplicate_status") == "excluded" and active == 1)
        assert {matched.status_code, excluded.status_code} <= {200, 201, 409}
        if line.get("duplicate_status") == "excluded":
            assert active == 0
        if active == 1:
            denied = await c.post(f"/api/cash-bank/statements/lines/{line_id}/duplicate-resolution", json={
                "action": "exclude", "reason": "masih cocok", "idempotency_key": "exclude-after",
            })
            assert denied.status_code == 409
            assert "Batalkan pencocokan" in denied.text
            assert await server.db[RECON_MATCHES].count_documents({"statement_line_id": line_id, "status": "active"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals + 1


async def test_11_to_15_currency_contract():
    admin = _user()
    viewer = _user("viewer", ["cash_bank.view", "cash_bank.reconcile"])
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        assert bank["currency_code"] is None
        unknown = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "XYZ"})
        assert unknown.status_code == 400
        set_idr = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "IDR"})
        assert set_idr.status_code == 200, set_idr.text
        assert set_idr.json()["currency_history"][-1]["before"] is None
        assert set_idr.json()["currency_history"][-1]["after"] == "IDR"
        async with await client_for(viewer) as other:
            denied = await other.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "USD"})
            assert denied.status_code == 403
        app.dependency_overrides[current_user] = lambda: admin
        preview = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("mutasi.csv", b"tanggal,uraian,masuk,keluar,referensi,id,saldo\n2026-09-05,Transfer,750000,,REF,TX-C,\n", "text/csv")},
        )
        assert preview.json()["account_currency_code"] == "IDR"
        changed = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "USD"})
        assert changed.status_code == 200, changed.text
        rejected = await c.post("/api/cash-bank/statements/imports", json={
            "preview_id": preview.json()["id"], "mapping": _mapping(file_currency="USD"),
        })
        assert rejected.status_code == 409
        assert await server.db[LINES].count_documents({}) == 0
        await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "IDR"})
        fresh = await c.post(
            "/api/cash-bank/statements/preview",
            data={"cash_bank_id": bank["id"]},
            files={"file": ("mutasi.csv", b"tanggal,uraian,masuk,keluar,referensi,id,saldo\n2026-09-05,Transfer,750000,,REF,TX-C,\n", "text/csv")},
        )
        committed = await c.post("/api/cash-bank/statements/imports", json={
            "preview_id": fresh.json()["id"], "mapping": _mapping(),
        })
        assert committed.status_code == 201, committed.text
        history_before = (await c.get(f"/api/cash-bank/accounts/{bank['id']}")).json()["currency_history"]
        blocked = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "USD"})
        assert blocked.status_code == 409
        after = (await c.get(f"/api/cash-bank/accounts/{bank['id']}")).json()
        assert after["currency_code"] == "IDR"
        assert after["currency_history"] == history_before
        assert (await server.db[LINES].find_one({}, {"_id": 0, "currency_code": 1}))["currency_code"] == "IDR"
        await _receipt(c, bank["id"], income["id"], 750000)
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        paired = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"],
            "statement_line_id": ws.json()["bank_lines"][0]["id"],
            "journal_line_id": ws.json()["book_lines"][0]["journal_line_id"],
            "idempotency_key": "cur-pair",
            **PERIOD,
        })
        assert paired.status_code == 201, paired.text
        assert paired.json()["currency_code"] == "IDR"
        assert "Snapshot" in paired.json()["source_history"]["currency_note"]


async def test_16_and_17_legacy_pair_claim_and_stale_cancel():
    admin = _user()
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        other, other_income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000, desc="satu")
        await _receipt(c, bank["id"], income["id"], 1000, desc="dua")
        await _receipt(c, other["id"], other_income["id"], 750000, desc="lain rekening")
        ws_old = await c.get("/api/cash-bank/reconciliations/workspace", params={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-30", "statement_balance": "1",
        })
        legacy_line = next(row["journal_line_id"] for row in ws_old.json()["movements"] if row["amount"] == 1000)
        pair_line = next(row["journal_line_id"] for row in ws_old.json()["movements"] if row["amount"] == 750000)
        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"], "reconciliation_date": "2026-09-30",
            "statement_balance": 1, "journal_line_ids": [legacy_line],
        })
        assert created.status_code == 201, created.text
        _preview, imported = await _import(c, bank["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Transfer masuk,750000,,REF,TX-LEG,\n"
            "2026-09-06,Kecil,1000,,REF,TX-SMALL,\n"
        ))
        assert imported.status_code == 201, imported.text
        small = await server.db[LINES].find_one({"bank_txn_id": "TX-SMALL"}, {"_id": 0, "id": 1})
        blocked = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": small["id"],
            "journal_line_id": legacy_line, "idempotency_key": "shared-key", **PERIOD,
        })
        assert blocked.status_code == 409
        big = await server.db[LINES].find_one({"bank_txn_id": "TX-LEG"}, {"_id": 0, "id": 1})
        paired = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": big["id"],
            "journal_line_id": pair_line, "idempotency_key": "shared-key", **PERIOD,
        })
        assert paired.status_code == 201, paired.text
        other_ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": other["id"], **PERIOD})
        other_import = await _import(c, other["id"], (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Lain,750000,,REF,TX-OTHER,\n"
        ))
        assert other_import[1].status_code == 201
        other_line = (await server.db[LINES].find_one({"cash_bank_id": other["id"]}, {"_id": 0, "id": 1}))["id"]
        other_book = other_ws.json()["book_lines"][0]["journal_line_id"]
        same_key_other_account = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": other["id"], "statement_line_id": other_line,
            "journal_line_id": other_book, "idempotency_key": "shared-key", **PERIOD,
        })
        assert same_key_other_account.status_code == 201, same_key_other_account.text
        assert same_key_other_account.json()["id"] != paired.json()["id"]
        removed = await c.delete(f"/api/cash-bank/reconciliations/{created.json()['id']}")
        assert removed.status_code == 200, removed.text
        assert await server.db[RECON_MATCHES].count_documents({"id": paired.json()["id"], "status": "active"}) == 1
        cancelled = await c.post(f"/api/cash-bank/reconciliations/pairs/{paired.json()['id']}/cancel", json={
            "idempotency_key": "cancel-old", "reason": "salah pilih",
        })
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["claim_active"] is False
        assert cancelled.json()["source_history"]["journal_line_id"] == pair_line
        viewed = await c.get(f"/api/cash-bank/reconciliations/pairs/{paired.json()['id']}")
        assert viewed.status_code == 200
        assert viewed.json()["claim_active"] is False
        assert viewed.json()["journal_line_id"] is None
        assert viewed.json()["history_journal_line_id"] == pair_line
        rematch = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": big["id"],
            "journal_line_id": pair_line, "idempotency_key": "rematch", **PERIOD,
        })
        assert rematch.status_code == 201, rematch.text
        stale_cancel = await c.post(f"/api/cash-bank/reconciliations/pairs/{rematch.json()['id']}/cancel", json={
            "idempotency_key": "cancel-old", "reason": "salah pilih",
        })
        assert stale_cancel.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"id": rematch.json()["id"], "status": "active"}) == 1
        replay = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": big["id"],
            "journal_line_id": pair_line, "idempotency_key": "shared-key", **PERIOD,
        })
        assert replay.status_code == 201
        assert replay.json()["status"] == "cancelled"
        assert replay.json()["claim_active"] is False
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals + 3
