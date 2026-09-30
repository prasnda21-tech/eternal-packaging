"""Closure races for weak imports, pair crashes, and currency commits."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
CLOSURE_DB = (
    f"recon_phase1_closure_{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}_{uuid.uuid4().hex[:6]}"
)
os.environ["DB_NAME"] = CLOSURE_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
import services.cash_bank_pair_match as pair_svc
import services.cash_bank_statement as statement_svc
from server import app, current_user
from services.cash_bank_claim_migration import migrate_claim_indexes
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_pair_match import PairConflictError, cancel_pair, create_pair
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    ensure_indexes as ensure_recon,
    legacy_journal_unique_name,
)
from services.cash_bank_statement import (
    IMPORT_FENCES,
    IMPORTS,
    LINES,
    PREVIEWS,
    StatementConflictError,
    ensure_indexes as ensure_statement,
    resolve_duplicate,
)
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal

pytestmark = pytest.mark.anyio
PERIOD = {"start_date": "2026-09-01", "end_date": "2026-09-30"}

_HOOKS = {
    "before_pin": statement_svc._before_currency_pin,
    "after_pin": statement_svc._after_currency_pin,
    "fence_busy": statement_svc._on_import_fence_busy,
    "before_insert": statement_svc._before_statement_lines_insert,
    "after_fence": statement_svc._after_import_fence_acquired,
    "after_line": statement_svc._after_statement_line_insert,
    "before_pair": pair_svc._before_pair_claim,
    "after_claim": pair_svc._after_statement_claim,
    "after_pair": pair_svc._after_pair_insert,
    "after_cancel": pair_svc._after_pair_cancel_stored,
    "before_release": pair_svc._before_failed_claim_release,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    os.environ["DB_NAME"] = CLOSURE_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[CLOSURE_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    for name in (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, RECONCILIATIONS, RECON_MATCHES,
        IMPORTS, LINES, PREVIEWS, IMPORT_FENCES, "cash_bank_manual_statement_balances",
    ):
        await db[name].drop()
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    await ensure_statement(db)
    await db["accounting_periods"].update_one(
        {"period_key": "2026-09"},
        {"$set": {"period_key": "2026-09", "status": "open", "writer_claims": []}},
        upsert=True,
    )
    _restore_hooks()
    yield
    _restore_hooks()


def _restore_hooks():
    statement_svc._before_currency_pin = _HOOKS["before_pin"]
    statement_svc._after_currency_pin = _HOOKS["after_pin"]
    statement_svc._on_import_fence_busy = _HOOKS["fence_busy"]
    statement_svc._before_statement_lines_insert = _HOOKS["before_insert"]
    statement_svc._after_import_fence_acquired = _HOOKS["after_fence"]
    statement_svc._after_statement_line_insert = _HOOKS["after_line"]
    pair_svc._before_pair_claim = _HOOKS["before_pair"]
    pair_svc._after_statement_claim = _HOOKS["after_claim"]
    pair_svc._after_pair_insert = _HOOKS["after_pair"]
    pair_svc._after_pair_cancel_stored = _HOOKS["after_cancel"]
    pair_svc._before_failed_claim_release = _HOOKS["before_release"]


def _user():
    uid = f"u-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid, "email": f"{uid}@example.com", "name": "admin", "role": "admin",
        "status": "active", "user_id": uid, "permissions_override": {},
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _mapping(**overrides):
    mapping = {
        "header_row": 0, "date_column": 0, "date_format": "YYYY-MM-DD",
        "description_column": 1, "reference_column": 4, "bank_txn_id_column": 5,
        "bank_txn_id_authoritative": False, "amount_mode": "in_out",
        "in_column": 2, "out_column": 3, "balance_column": 6,
        "decimal_mode": "dot_decimal", "file_currency": "IDR",
        "operator_coverage_confirmed": False,
    }
    mapping.update(overrides)
    return mapping


def _csv(reference, description="Transfer masuk", txn=""):
    return (
        "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
        f"2026-09-05,{description},750000,,{reference},{txn},\n"
    )


async def _setup(c, *, currency="IDR"):
    user = _user()
    cash = await create_account(
        server.db, {"code": f"B{uuid.uuid4().hex[:6]}", "name": "Bank", "account_type": "BANK"}, user=user,
    )
    income = await create_account(
        server.db, {"code": f"I{uuid.uuid4().hex[:6]}", "name": "Lawan", "account_type": "BANK"}, user=user,
    )
    bank = await c.post("/api/cash-bank/accounts", json={"name": "Bank Uji", "account_id": cash["id"], "active": True})
    assert bank.status_code == 201, bank.text
    if currency:
        patched = await c.patch(f"/api/cash-bank/accounts/{bank.json()['id']}", json={"currency_code": currency})
        assert patched.status_code == 200, patched.text
        return patched.json(), income
    return bank.json(), income


async def _receipt(c, bank_id, counter_id, amount, desc="Transfer masuk"):
    draft = await c.post("/api/cash-bank/receipts", json={
        "date": "2026-09-05", "cash_bank_id": bank_id, "counter_account_id": counter_id,
        "amount": amount, "description": desc, "reference": f"R-{uuid.uuid4().hex[:4]}",
    })
    assert draft.status_code == 201, draft.text
    posted = await c.post(f"/api/cash-bank/receipts/{draft.json()['id']}/post")
    assert posted.status_code == 200, posted.text
    return posted.json()


async def _preview(c, bank_id, name, text):
    response = await c.post(
        "/api/cash-bank/statements/preview",
        data={"cash_bank_id": bank_id},
        files={"file": (name, text.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body["_name"] = name
    body["_data"] = text.encode()
    return body


async def _commit(staged, user, mapping=None):
    return await statement_svc.commit_import(
        server.db,
        cash_bank_id=staged["cash_bank_id"],
        filename=staged["_name"],
        data=staged["_data"],
        fingerprint=staged["fingerprint"],
        mapping_raw=mapping or _mapping(),
        user=user,
        account_binding_value=staged["account_binding"],
    )


async def test_a_concurrent_weak_imports_require_review_then_keep():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        await _receipt(c, bank["id"], income["id"], 750000, desc="satu")
        await _receipt(c, bank["id"], income["id"], 750000, desc="dua")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        left = await _preview(c, bank["id"], "a.csv", _csv("REF-A"))
        right = await _preview(c, bank["id"], "b.csv", _csv("REF-B"))
        entered = asyncio.Event()
        release = asyncio.Event()
        busy = asyncio.Event()

        async def hold():
            entered.set()
            await release.wait()

        async def on_busy(_cash_bank_id):
            busy.set()

        statement_svc._before_statement_lines_insert = hold
        statement_svc._on_import_fence_busy = on_busy
        first = asyncio.create_task(_commit(left, admin))
        await entered.wait()
        second = asyncio.create_task(_commit(right, admin))
        await asyncio.wait_for(busy.wait(), timeout=5)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=8)
        rows = await server.db[LINES].find({}, {"_id": 0}).to_list(10)
        assert len(rows) == 1
        assert rows[0]["duplicate_status"] == "clear"
        assert rows[0]["identity_guarantee"] == "tidak_dijamin"
        assert await server.db[IMPORTS].count_documents({"status": "complete"}) == 2
        assert await server.db[IMPORT_FENCES].count_documents({}) == 0
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        books = [row["journal_line_id"] for row in ws.json()["book_lines"]]
        assert len(books) >= 2
        matched = await c.post("/api/cash-bank/reconciliations/pairs", json={
            "cash_bank_id": bank["id"], "statement_line_id": rows[0]["id"],
            "journal_line_id": books[0], "idempotency_key": "one-bank-line", **PERIOD,
        })
        assert matched.status_code == 201, matched.text
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        retry = await _commit(left, admin)
        assert retry["stored_count"] == 1
        assert await server.db[LINES].count_documents({}) == 1
        stored = await server.db[LINES].find_one({}, {"_id": 0, "id": 1, "duplicate_status": 1})
        assert stored["id"] == rows[0]["id"]
        assert stored["duplicate_status"] == "clear"
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_a_other_account_is_not_blocked_and_failure_releases_fence():
    admin = _user()
    async with await client_for(admin) as c:
        first, _income = await _setup(c)
        second, _other_income = await _setup(c)
        left = await _preview(c, first["id"], "a.csv", _csv("REF-A", description="Satu"))
        right = await _preview(c, second["id"], "b.csv", _csv("REF-B", description="Dua"))
        state = {"n": 0}
        release = asyncio.Event()

        async def hold():
            state["n"] += 1
            if state["n"] >= 2:
                release.set()
            await release.wait()

        statement_svc._before_statement_lines_insert = hold
        await asyncio.wait_for(asyncio.gather(_commit(left, admin), _commit(right, admin)), timeout=8)
        assert state["n"] == 2
        assert await server.db[LINES].count_documents({"duplicate_status": "clear"}) == 2
        assert await server.db[IMPORT_FENCES].count_documents({}) == 0

        failed_preview = await _preview(c, first["id"], "fail.csv", _csv("REF-F", description="Gagal"))

        async def crash():
            raise RuntimeError("impor gagal sebelum baris tersimpan")

        statement_svc._before_statement_lines_insert = crash
        with pytest.raises(RuntimeError):
            await _commit(failed_preview, admin)
        assert await server.db[IMPORT_FENCES].count_documents({"cash_bank_id": first["id"]}) == 0
        account = await server.db[CB_COLLECTION].find_one({"id": first["id"]}, {"_id": 0, "import_currency_readers": 1, "currency_writer": 1})
        assert not (account.get("import_currency_readers") or [])
        assert not account.get("currency_writer")
        statement_svc._before_statement_lines_insert = _HOOKS["before_insert"]
        recovered = await _preview(c, first["id"], "ok.csv", _csv("REF-OK", description="Pulih"))
        done = await _commit(recovered, admin)
        assert done["status"] == "complete"
        assert await server.db[IMPORT_FENCES].count_documents({}) == 0

        fresh, _fresh_income = await _setup(c)
        abandoned = await _preview(c, fresh["id"], "dead.csv", _csv("REF-DEAD", description="Mati"))
        await server.db[IMPORT_FENCES].insert_one({
            "cash_bank_id": fresh["id"],
            "holder": abandoned["fingerprint"],
            "operation_id": abandoned["fingerprint"],
            "batch_id": None,
            "file_hash": abandoned["fingerprint"],
            "at": "injected-killed-process",
        })
        await server.db[CB_COLLECTION].update_one(
            {"id": fresh["id"]},
            {"$set": {"import_currency_readers": [abandoned["fingerprint"]]}},
        )
        blocked = await c.patch(f"/api/cash-bank/accounts/{fresh['id']}", json={"currency_code": "USD"})
        assert blocked.status_code == 409
        assert abandoned["fingerprint"] in blocked.text
        resumed = await _commit(abandoned, admin)
        assert resumed["status"] == "complete"
        assert await server.db[IMPORT_FENCES].count_documents({"cash_bank_id": fresh["id"]}) == 0
        raw = await server.db[CB_COLLECTION].find_one(
            {"id": fresh["id"]}, {"_id": 0, "import_currency_readers": 1, "currency_code": 1},
        )
        assert not (raw.get("import_currency_readers") or [])
        assert raw["currency_code"] == "IDR"


async def test_b_crash_between_claim_and_pair_recovers_on_same_key():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        await _receipt(c, bank["id"], income["id"], 750000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        preview = await _preview(c, bank["id"], "a.csv", _csv("REF-A", txn="TX-B"))
        await _commit(preview, admin)
        line = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]

        async def crash_claim():
            raise RuntimeError("setelah active_claim")

        pair_svc._after_statement_claim = crash_claim
        with pytest.raises(RuntimeError):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
                journal_line_id=book, idempotency_key="pair-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        held = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1})
        assert held["active_claim"] == "pair-key"
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0
        with pytest.raises(PairConflictError):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
                journal_line_id=book, idempotency_key="other-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        held = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1})
        assert held["active_claim"] == "pair-key"
        pair_svc._after_statement_claim = _HOOKS["after_claim"]
        restored = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
            journal_line_id=book, idempotency_key="pair-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert restored["status"] == "active"
        assert restored["claim_active"] is True
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1

        async def crash_cancel():
            raise RuntimeError("setelah pair cancelled")

        pair_svc._after_pair_cancel_stored = crash_cancel
        with pytest.raises(RuntimeError):
            await cancel_pair(server.db, restored["id"], idempotency_key="cancel-1", reason="salah pilih", user=admin)
        cancelled = await server.db[RECON_MATCHES].find_one({"id": restored["id"]}, {"_id": 0})
        assert cancelled["status"] == "cancelled"
        assert cancelled.get("journal_line_id") is None
        assert cancelled["history_journal_line_id"] == book
        claim = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1})
        assert claim.get("active_claim")
        pair_svc._after_pair_cancel_stored = _HOOKS["after_cancel"]
        cleared = await cancel_pair(
            server.db, restored["id"], idempotency_key="cancel-1", reason="salah pilih", user=admin,
        )
        assert cleared["claim_active"] is False
        assert (await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1})).get("active_claim") is None
        rematch = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
            journal_line_id=book, idempotency_key="rematch", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert rematch["status"] == "active"
        with pytest.raises(PairConflictError):
            await cancel_pair(server.db, rematch["id"], idempotency_key="cancel-1", reason="salah pilih", user=admin)
        assert await server.db[RECON_MATCHES].count_documents({"id": rematch["id"], "status": "active"}) == 1
        replay = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
            journal_line_id=book, idempotency_key="pair-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert replay["id"] == restored["id"]
        assert replay["status"] == "cancelled"
        assert replay["claim_active"] is False
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_b_crash_after_pair_insert_retries_same_document():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        await _receipt(c, bank["id"], income["id"], 750000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        preview = await _preview(c, bank["id"], "a.csv", _csv("REF-A", txn="TX-C"))
        await _commit(preview, admin)
        line = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        ws = await c.get("/api/cash-bank/reconciliations/pairs/workspace", params={"cash_bank_id": bank["id"], **PERIOD})
        book = ws.json()["book_lines"][0]["journal_line_id"]

        async def crash_insert():
            raise RuntimeError("setelah dokumen pair")

        pair_svc._after_pair_insert = crash_insert
        with pytest.raises(RuntimeError):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
                journal_line_id=book, idempotency_key="insert-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        stored = await server.db[RECON_MATCHES].find_one({"idempotency_key": "insert-key"}, {"_id": 0})
        assert stored["status"] == "active"
        assert stored["journal_line_id"] == book
        claim = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1})
        assert claim["active_claim"] == "insert-key"
        pair_svc._after_pair_insert = _HOOKS["after_pair"]
        replay = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
            journal_line_id=book, idempotency_key="insert-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert replay["id"] == stored["id"]
        normalized = await server.db[LINES].find_one(
            {"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1},
        )
        assert normalized["active_claim"] == stored["id"]
        assert normalized["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_same_key_loser_does_not_release_winner_claim():
    """Two same-key creates pass the first lookup. The loser must not drop the claim."""
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        await _receipt(c, bank["id"], income["id"], 750000)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        preview = await _preview(c, bank["id"], "a.csv", _csv("REF-A", txn="TX-RACE"))
        await _commit(preview, admin)
        line = await server.db[LINES].find_one({}, {"_id": 0, "id": 1, "duplicate_status": 1})
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        book = ws.json()["book_lines"][0]["journal_line_id"]
        both_lookup = asyncio.Event()
        release_loser = asyncio.Event()
        winner_inserted = asyncio.Event()
        release_winner = asyncio.Event()
        arrived = {"n": 0}

        async def before_claim():
            arrived["n"] += 1
            if arrived["n"] == 1:
                await both_lookup.wait()
            else:
                both_lookup.set()
                await release_loser.wait()

        async def after_insert():
            winner_inserted.set()
            await release_winner.wait()

        pair_svc._before_pair_claim = before_claim
        pair_svc._after_pair_insert = after_insert
        kwargs = dict(
            cash_bank_id=bank["id"], statement_line_id=line["id"], journal_line_id=book,
            idempotency_key="same-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        first = asyncio.create_task(create_pair(server.db, **kwargs))
        second = asyncio.create_task(create_pair(server.db, **kwargs))
        await asyncio.wait_for(winner_inserted.wait(), timeout=5)
        assert arrived["n"] == 2
        before_loser = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert before_loser["active_claim"] == "same-key"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        release_loser.set()
        done, pending = await asyncio.wait({first, second}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1 and len(pending) == 1
        loser = done.pop().result()
        after_loser = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert after_loser["active_claim"] == loser["id"]
        assert after_loser["active_claim"] != "same-key"
        assert after_loser["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        with pytest.raises(StatementConflictError, match="Baris sudah dicocokkan"):
            await resolve_duplicate(
                server.db, line["id"], action="exclude", reason="bukan transaksi ini",
                idempotency_key="exclude-during-race", user=admin,
            )
        excluded = await server.db[LINES].find_one(
            {"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1},
        )
        assert excluded["duplicate_status"] == "clear"
        assert excluded["active_claim"] == loser["id"]
        assert await server.db[RECON_MATCHES].count_documents(
            {"statement_line_id": line["id"], "status": "active"},
        ) == 1

        from motor.motor_asyncio import AsyncIOMotorCollection
        cas = []
        original_update = AsyncIOMotorCollection.update_one

        async def spy_update(self, filter, update, *args, **kwargs):
            result = await original_update(self, filter, update, *args, **kwargs)
            if getattr(self, "name", None) == LINES and isinstance(filter, dict) and filter.get("active_claim") == "same-key":
                cas.append(result.matched_count)
            return result

        AsyncIOMotorCollection.update_one = spy_update
        try:
            release_winner.set()
            winner = (await asyncio.wait_for(pending.pop(), timeout=5))
        finally:
            AsyncIOMotorCollection.update_one = original_update
        assert winner["id"] == loser["id"]
        assert cas == [0]
        final_line = await server.db[LINES].find_one(
            {"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1},
        )
        assert final_line["active_claim"] == winner["id"]
        assert final_line["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_same_key_different_payload_conflicts_without_dropping_claim():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c)
        await _receipt(c, bank["id"], income["id"], 750000, desc="satu")
        await _receipt(c, bank["id"], income["id"], 750000, desc="dua")
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        preview = await _preview(c, bank["id"], "a.csv", _csv("REF-A", txn="TX-PAY"))
        await _commit(preview, admin)
        line = await server.db[LINES].find_one({}, {"_id": 0, "id": 1})
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        books = [row["journal_line_id"] for row in ws.json()["book_lines"]]
        assert len(books) >= 2
        created = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
            journal_line_id=books[0], idempotency_key="payload-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        with pytest.raises(PairConflictError, match="payload berbeda"):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=line["id"],
                journal_line_id=books[1], idempotency_key="payload-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        held = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert held["active_claim"] == created["id"]
        assert held["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair"}) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


def _csv_two():
    return (
        "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
        "2026-09-05,Transfer masuk,750000,,REF-1,TX-1,\n"
        "2026-09-06,Transfer masuk,750000,,REF-2,TX-2,\n"
    )


async def _two_clear_lines(c, admin):
    bank, income = await _setup(c)
    await _receipt(c, bank["id"], income["id"], 750000, desc="satu")
    await _receipt(c, bank["id"], income["id"], 750000, desc="dua")
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    preview = await _preview(c, bank["id"], "two.csv", _csv_two())
    await _commit(preview, admin, _mapping(bank_txn_id_authoritative=True))
    lines = await server.db[LINES].find(
        {"cash_bank_id": bank["id"]}, {"_id": 0, "id": 1, "duplicate_status": 1},
    ).sort("date", 1).to_list(10)
    assert [row["duplicate_status"] for row in lines] == ["clear", "clear"]
    ws = await c.get(
        "/api/cash-bank/reconciliations/pairs/workspace",
        params={"cash_bank_id": bank["id"], **PERIOD},
    )
    books = [row["journal_line_id"] for row in ws.json()["book_lines"]]
    assert len(books) >= 2
    return bank, lines[0]["id"], lines[1]["id"], books[0], books[1], journals


async def test_release_gap_keeps_claim_when_same_key_pair_commits():
    """Journal-index loss reads no pair; a same-key insert then commits before release."""
    admin = _user()
    async with await client_for(admin) as c:
        bank, s1, s2, j1, j2, journals = await _two_clear_lines(c, admin)
        info = await server.db[RECON_MATCHES].index_information()
        assert {"uniq_journal_line_claim", "uniq_pair_idempotency", "uniq_statement_line_claim"} <= set(info)
        blocker = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=s2, journal_line_id=j1,
            idempotency_key="block-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        release_cleanup = asyncio.Event()
        winner_inserted = asyncio.Event()
        release_winner = asyncio.Event()
        holder = {}

        async def before_release():
            claim = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1})
            assert claim["active_claim"] == "shared-key"
            assert await server.db[RECON_MATCHES].count_documents({"idempotency_key": "shared-key"}) == 0
            holder["winner"] = asyncio.create_task(create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j2,
                idempotency_key="shared-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            ))
            await winner_inserted.wait()
            release_cleanup.set()

        async def after_insert():
            winner_inserted.set()
            await release_winner.wait()

        pair_svc._before_failed_claim_release = before_release
        pair_svc._after_pair_insert = after_insert
        loser_task = asyncio.create_task(create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j1,
            idempotency_key="shared-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        ))
        await asyncio.wait_for(release_cleanup.wait(), timeout=5)
        with pytest.raises(PairConflictError, match="payload berbeda"):
            await loser_task
        held = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert held["active_claim"] == "shared-key"
        assert held["duplicate_status"] == "clear"
        with pytest.raises(StatementConflictError, match="Baris sudah dicocokkan"):
            await resolve_duplicate(
                server.db, s1, action="exclude", reason="bukan baris ini",
                idempotency_key="exclude-gap", user=admin,
            )
        release_winner.set()
        winner = await asyncio.wait_for(holder["winner"], timeout=5)
        final = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert final["active_claim"] == winner["id"]
        assert final["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"idempotency_key": "shared-key", "match_kind": "pair"}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"id": blocker["id"], "status": "active"}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair_release"}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals

        bank2, s1b, s2b, j1b, _j2b, journals2 = await _two_clear_lines(c, admin)
        await create_pair(
            server.db, cash_bank_id=bank2["id"], statement_line_id=s2b, journal_line_id=j1b,
            idempotency_key="block-2", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        done = {}

        async def finish_same_payload():
            await cancel_pair(
                server.db, (await server.db[RECON_MATCHES].find_one(
                    {"idempotency_key": "block-2"}, {"_id": 0, "id": 1},
                ))["id"],
                idempotency_key="cancel-block-2", reason="bebaskan buku", user=admin,
            )
            done["pair"] = await create_pair(
                server.db, cash_bank_id=bank2["id"], statement_line_id=s1b, journal_line_id=j1b,
                idempotency_key="same-payload", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )

        pair_svc._before_failed_claim_release = finish_same_payload
        pair_svc._after_pair_insert = _HOOKS["after_pair"]
        completed = await create_pair(
            server.db, cash_bank_id=bank2["id"], statement_line_id=s1b, journal_line_id=j1b,
            idempotency_key="same-payload", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert completed["id"] == done["pair"]["id"]
        assert completed["status"] == "active"
        line = await server.db[LINES].find_one({"id": s1b}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert line["active_claim"] == completed["id"]
        assert line["duplicate_status"] == "clear"
        with pytest.raises(StatementConflictError, match="Baris sudah dicocokkan"):
            await resolve_duplicate(
                server.db, s1b, action="exclude", reason="masih cocok",
                idempotency_key="exclude-same", user=admin,
            )
        replay = await create_pair(
            server.db, cash_bank_id=bank2["id"], statement_line_id=s1b, journal_line_id=j1b,
            idempotency_key="same-payload", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert replay["id"] == completed["id"]
        assert await server.db[RECON_MATCHES].count_documents({"status": "active", "match_kind": "pair", "cash_bank_id": bank2["id"]}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair_release"}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals2


async def test_journal_conflict_without_later_pair_still_releases_claim():
    admin = _user()
    async with await client_for(admin) as c:
        bank, s1, s2, j1, j2, journals = await _two_clear_lines(c, admin)
        await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=s2, journal_line_id=j1,
            idempotency_key="block-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        with pytest.raises(PairConflictError, match="sudah diklaim"):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j1,
                idempotency_key="failed-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        cleared = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1, "claim_slot": 1, "duplicate_status": 1})
        assert cleared.get("active_claim") is None
        assert cleared.get("claim_slot") is None
        assert cleared["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair_release"}) == 0
        recovered = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j2,
            idempotency_key="other-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        assert recovered["status"] == "active"
        held = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1})
        assert held["active_claim"] == recovered["id"]
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_stranded_release_slot_does_not_clear_published_claim():
    admin = _user()
    async with await client_for(admin) as c:
        bank, s1, _s2, j1, _j2, journals = await _two_clear_lines(c, admin)
        published = await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j1,
            idempotency_key="published-key", user=admin,
            start=PERIOD["start_date"], end=PERIOD["end_date"],
        )
        await server.db[RECON_MATCHES].insert_one({
            "id": "stranded-slot",
            "match_kind": "pair_release",
            "status": "releasing",
            "cash_bank_id": bank["id"],
            "idempotency_key": "orphan-key",
            "release_line_id": s1,
        })
        await server.db[LINES].update_one(
            {"id": s1},
            {"$set": {"claim_slot": "stranded-slot"}},
        )
        with pytest.raises(PairConflictError):
            await create_pair(
                server.db, cash_bank_id=bank["id"], statement_line_id=s1, journal_line_id=j1,
                idempotency_key="orphan-key", user=admin,
                start=PERIOD["start_date"], end=PERIOD["end_date"],
            )
        line = await server.db[LINES].find_one({"id": s1}, {"_id": 0, "active_claim": 1, "duplicate_status": 1})
        assert line["active_claim"] == published["id"]
        assert line["duplicate_status"] == "clear"
        assert await server.db[RECON_MATCHES].count_documents({"id": published["id"], "status": "active"}) == 1
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair_release"}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_c_currency_and_import_cannot_both_win():
    admin = _user()
    async with await client_for(admin) as c:
        bank, _income = await _setup(c)
        preview = await _preview(c, bank["id"], "a.csv", _csv("REF-A"))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def after_pin():
            entered.set()
            await release.wait()

        statement_svc._after_currency_pin = after_pin
        task = asyncio.create_task(_commit(preview, admin))
        await entered.wait()
        denied = await c.patch(f"/api/cash-bank/accounts/{bank['id']}", json={"currency_code": "USD"})
        assert denied.status_code == 409
        assert "pratinjau" in denied.text
        release.set()
        imported = await task
        assert imported["status"] == "complete"
        account = await c.get(f"/api/cash-bank/accounts/{bank['id']}")
        assert account.json()["currency_code"] == "IDR"
        line = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0})
        assert line["currency_code"] == "IDR"
        assert line["account_currency_code"] == "IDR"
        assert "USD" not in [item.get("after") for item in account.json()["currency_history"]]

        bank2, _income2 = await _setup(c)
        preview2 = await _preview(c, bank2["id"], "b.csv", _csv("REF-B"))
        entered2 = asyncio.Event()
        release2 = asyncio.Event()

        async def before_pin():
            entered2.set()
            await release2.wait()

        statement_svc._before_currency_pin = before_pin
        task2 = asyncio.create_task(_commit(preview2, admin))
        await entered2.wait()
        changed = await c.patch(f"/api/cash-bank/accounts/{bank2['id']}", json={"currency_code": "USD"})
        assert changed.status_code == 200, changed.text
        release2.set()
        with pytest.raises(StatementConflictError):
            await task2
        assert await server.db[LINES].count_documents({"cash_bank_id": bank2["id"]}) == 0
        assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank2["id"]}) == 0
        account2 = await c.get(f"/api/cash-bank/accounts/{bank2['id']}")
        assert account2.json()["currency_code"] == "USD"

        bank3, _income3 = await _setup(c)
        preview3 = await _preview(c, bank3["id"], "c.csv", _csv("REF-C"))

        async def crash_pin():
            raise RuntimeError("setelah pin mata uang")

        statement_svc._after_currency_pin = crash_pin
        with pytest.raises(RuntimeError):
            await _commit(preview3, admin)
        raw = await server.db[CB_COLLECTION].find_one({"id": bank3["id"]}, {"_id": 0})
        assert not (raw.get("import_currency_readers") or [])
        assert not raw.get("currency_writer")
        statement_svc._after_currency_pin = _HOOKS["after_pin"]
        recovered = await c.patch(f"/api/cash-bank/accounts/{bank3['id']}", json={"currency_code": "USD"})
        assert recovered.status_code == 200, recovered.text
        assert await server.db[LINES].count_documents({"cash_bank_id": bank3["id"]}) == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def test_baseline_startup_index_fails_after_two_cancelled_pairs():
    await server.db[RECON_MATCHES].drop()
    await server.db[RECON_MATCHES].create_index("id", unique=True)
    await server.db[RECON_MATCHES].create_index("journal_line_id", unique=True, name="journal_line_id_1")
    await server.db[RECON_MATCHES].insert_one({
        "id": "legacy-1", "reconciliation_id": "rk-1", "journal_line_id": "line-legacy",
    })
    await migrate_claim_indexes(server.db)
    await server.db[RECON_MATCHES].insert_many([
        {
            "id": "pair-1", "match_kind": "pair", "status": "cancelled", "cash_bank_id": "b1",
            "history_journal_line_id": "h1", "history_statement_line_id": "s1",
        },
        {
            "id": "pair-2", "match_kind": "pair", "status": "cancelled", "cash_bank_id": "b1",
            "history_journal_line_id": "h2", "history_statement_line_id": "s2",
        },
    ])
    before = await server.db[RECON_MATCHES].count_documents({})
    with pytest.raises(DuplicateKeyError):
        await server.db[RECON_MATCHES].create_index("journal_line_id", unique=True, name="journal_line_id_1")
    info = await server.db[RECON_MATCHES].index_information()
    assert "uniq_journal_line_claim" in info
    assert legacy_journal_unique_name(info) is None
    assert await server.db[RECON_MATCHES].count_documents({}) == before
    await ensure_recon(server.db)
    info = await server.db[RECON_MATCHES].index_information()
    assert legacy_journal_unique_name(info) is None
    assert "uniq_journal_line_claim" in info
