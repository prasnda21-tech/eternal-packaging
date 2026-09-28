"""Attempt ownership and recovery after a dead import. Isolated Mongo only."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
OWNER_DB = os.environ.get("OWNER_FORCE_DB") or (
    f"recon_phase1_owner_{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}_{uuid.uuid4().hex[:6]}"
)
os.environ["DB_NAME"] = OWNER_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
import services.cash_bank_statement as statement_svc
from server import app, current_user
from services.cash_bank_master import (
    COLLECTION as CB_COLLECTION,
    CashBankConflictError,
    ensure_indexes as ensure_cb,
    update_cash_bank,
)
from services.cash_bank_pair_match import PairConflictError, create_pair
from services.cash_bank_reconciliation import RECON_MATCHES, ensure_indexes as ensure_recon
from services.cash_bank_statement import (
    IMPORT_FENCES,
    IMPORTS,
    LINES,
    PREVIEWS,
    StatementConflictError,
    StatementValidationError,
    _unpin_import_currency,
    commit_import,
    create_preview,
    ensure_indexes as ensure_statement,
    line_matchable,
    list_ready_lines,
    resolve_duplicate,
)
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, ensure_indexes as ensure_journal

pytestmark = pytest.mark.anyio
PAST = "2000-01-01T00:00:00+00:00"
PERIOD = {"start": "2026-09-01", "end": "2026-09-30"}

_HOOKS = {
    "after_fence": statement_svc._after_import_fence_acquired,
    "before_insert": statement_svc._before_statement_lines_insert,
    "after_line": statement_svc._after_statement_line_insert,
    "busy": statement_svc._on_import_fence_busy,
    "before_write": statement_svc._before_fenced_write,
    "after_pin": statement_svc._after_currency_pin,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    os.environ["DB_NAME"] = OWNER_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[OWNER_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    assert db.name == OWNER_DB
    for name in (CB_COLLECTION, COA_COLLECTION, IMPORTS, LINES, PREVIEWS, IMPORT_FENCES, JOURNAL_ENTRIES, RECON_MATCHES):
        await db[name].drop()
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_recon(db)
    await ensure_statement(db)
    await ensure_journal(db)
    _restore()
    yield
    _restore()


def _restore():
    statement_svc._after_import_fence_acquired = _HOOKS["after_fence"]
    statement_svc._before_statement_lines_insert = _HOOKS["before_insert"]
    statement_svc._after_statement_line_insert = _HOOKS["after_line"]
    statement_svc._on_import_fence_busy = _HOOKS["busy"]
    statement_svc._before_fenced_write = _HOOKS["before_write"]
    statement_svc._after_currency_pin = _HOOKS["after_pin"]


def _user():
    uid = f"u-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid, "email": f"{uid}@example.com", "name": "admin", "role": "admin",
        "status": "active", "user_id": uid, "permissions_override": {},
    }


def _mapping():
    return {
        "header_row": 0, "date_column": 0, "date_format": "YYYY-MM-DD",
        "description_column": 1, "reference_column": 4, "bank_txn_id_column": 5,
        "bank_txn_id_authoritative": False, "amount_mode": "in_out",
        "in_column": 2, "out_column": 3, "balance_column": 6,
        "decimal_mode": "dot_decimal", "file_currency": "IDR",
        "operator_coverage_confirmed": False,
    }


def _csv(reference, description="Transfer masuk", amount="750000"):
    return (
        "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
        f"2026-09-05,{description},{amount},,{reference},,\n"
    ).encode()


def _csv_two():
    return (
        "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
        "2026-09-05,Transfer masuk,100000,,R1,,\n"
        "2026-09-06,Transfer keluar,,50000,R2,,\n"
    ).encode()


async def _bank():
    user = _user()
    cash = await create_account(
        server.db, {"code": f"B{uuid.uuid4().hex[:6]}", "name": "Bank", "account_type": "BANK"}, user=user,
    )
    from services.cash_bank_master import create_cash_bank
    bank = await create_cash_bank(
        server.db, {"name": "Bank Uji", "account_id": cash["id"], "active": True}, user=user,
    )
    return await update_cash_bank(server.db, bank["id"], {"currency_code": "IDR"}, user=user)


async def _fence(cash_bank_id):
    return await server.db[IMPORT_FENCES].find_one({"cash_bank_id": cash_bank_id}, {"_id": 0})


async def _account(cash_bank_id):
    return await server.db[CB_COLLECTION].find_one({"id": cash_bank_id}, {"_id": 0})


def _reader_ids(account):
    return [
        item.get("attempt_id") if isinstance(item, dict) else item
        for item in (account.get("import_currency_readers") or [])
    ]


async def _commit(preview_id, user):
    return await commit_import(server.db, preview_id=preview_id, mapping_raw=_mapping(), user=user)


async def test_same_preview_cleanup_does_not_release_the_live_attempt():
    user = _user()
    bank = await _bank()
    preview_p = await create_preview(server.db, cash_bank_id=bank["id"], filename="p.csv", data=_csv("REF-P"), user=user)
    preview_q = await create_preview(server.db, cash_bank_id=bank["id"], filename="q.csv", data=_csv("REF-Q"), user=user)
    entered = asyncio.Event()
    release = asyncio.Event()
    busy = asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()

    async def on_busy(_cash_bank_id):
        busy.set()

    statement_svc._after_import_fence_acquired = hold
    statement_svc._on_import_fence_busy = on_busy
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task_a = asyncio.create_task(_commit(preview_p["id"], user))
    await asyncio.wait_for(entered.wait(), timeout=8)
    held = await _fence(bank["id"])
    assert held["holder"] != preview_p["id"]
    assert held["operation_id"] == preview_p["id"]
    assert held["batch_id"] is None
    readers = _reader_ids(await _account(bank["id"]))
    assert readers == [held["holder"]]

    with pytest.raises(StatementConflictError, match="tidak mengambil alih"):
        await _commit(preview_p["id"], user)
    after_b = await _fence(bank["id"])
    assert after_b["holder"] == held["holder"]
    assert _reader_ids(await _account(bank["id"])) == [held["holder"]]
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 0

    task_c = asyncio.create_task(_commit(preview_q["id"], user))
    await asyncio.wait_for(busy.wait(), timeout=5)
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 0
    task_c.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_c
    assert (await _fence(bank["id"]))["holder"] == held["holder"]
    assert _reader_ids(await _account(bank["id"])) == [held["holder"]]

    with pytest.raises(Exception, match="pratinjau"):
        await update_cash_bank(server.db, bank["id"], {"currency_code": "USD"}, user=user)
    assert (await _account(bank["id"]))["currency_code"] == "IDR"

    release.set()
    imported = await asyncio.wait_for(task_a, timeout=8)
    assert imported["status"] == "complete"
    assert imported["preview_id"] == preview_p["id"]
    other = await _commit(preview_q["id"], user)
    assert other["status"] == "complete"
    rows = await server.db[LINES].find({"cash_bank_id": bank["id"]}, {"_id": 0, "duplicate_status": 1}).to_list(10)
    assert len(rows) == 2
    assert {row["duplicate_status"] for row in rows} == {"pending_review"}
    replay = await _commit(preview_p["id"], user)
    assert replay["id"] == imported["id"]
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 2
    assert await server.db[IMPORT_FENCES].count_documents({"cash_bank_id": bank["id"]}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
    assert (await _account(bank["id"]))["currency_code"] == "IDR"


async def _crash_image_from_pause(kind):
    """Run a real commit until the pause, then restore the documents a hard kill would leave.

    Cancelling the task runs finally, so the restored fence and reader — not the
    exception — are the crash image. lease_until is moved to the past afterwards.
    """
    user = _user()
    bank = await _bank()
    data = _csv_two() if kind == "partial" else _csv("REF-DEAD")
    preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename=f"{kind}.csv", data=data, user=user,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    if kind == "partial":
        async def pause(index):
            if index == 0:
                entered.set()
                await release.wait()

        statement_svc._after_statement_line_insert = pause
    else:
        async def pause():
            entered.set()
            await release.wait()

        statement_svc._after_import_fence_acquired = pause

    task = asyncio.create_task(_commit(preview["id"], user))
    await asyncio.wait_for(entered.wait(), timeout=8)
    fence = await _fence(bank["id"])
    account = await _account(bank["id"])
    batches = await server.db[IMPORTS].find({"cash_bank_id": bank["id"]}).to_list(5)
    lines = await server.db[LINES].find({"cash_bank_id": bank["id"]}).to_list(10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    _restore()
    await server.db[IMPORT_FENCES].delete_many({"cash_bank_id": bank["id"]})
    if fence:
        fence["lease_until"] = PAST
        fence.pop("_id", None)
        await server.db[IMPORT_FENCES].insert_one(fence)
    readers = []
    for item in account.get("import_currency_readers") or []:
        if isinstance(item, dict):
            item = dict(item)
            item["until"] = PAST
        readers.append(item)
    await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"]},
        {"$set": {"import_currency_readers": readers}, "$unset": {"currency_writer": "", "currency_writer_until": ""}},
    )
    return user, bank, preview, batches, lines


async def test_dead_owner_before_batch_does_not_stick_after_preview_expiry():
    user, bank, preview, batches, _lines = await _crash_image_from_pause("before_batch")
    assert batches == []
    await server.db[PREVIEWS].update_one({"id": preview["id"]}, {"$set": {"expires_at": PAST}})
    live = await _fence(bank["id"])
    live_until = "2999-01-01T00:00:00+00:00"
    await server.db[IMPORT_FENCES].update_one({"cash_bank_id": bank["id"]}, {"$set": {"lease_until": live_until}})
    with pytest.raises(StatementConflictError, match="masih berjalan"):
        await _commit(preview["id"], user)
    assert (await _fence(bank["id"]))["holder"] == live["holder"]

    await server.db[IMPORT_FENCES].update_one({"cash_bank_id": bank["id"]}, {"$set": {"lease_until": PAST}})
    with pytest.raises(StatementValidationError, match="kedaluwarsa"):
        await _commit(preview["id"], user)
    assert await server.db[IMPORT_FENCES].count_documents({"cash_bank_id": bank["id"]}) == 0
    assert not _reader_ids(await _account(bank["id"]))
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 0
    again = await create_preview(server.db, cash_bank_id=bank["id"], filename="new.csv", data=_csv("REF-NEW", description="Baru"), user=user)
    done = await _commit(again["id"], user)
    assert done["status"] == "complete"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def test_partial_import_resumes_after_preview_expires_or_disappears():
    user, bank, preview, batches, lines = await _crash_image_from_pause("partial")
    assert len(batches) == 1
    assert batches[0]["status"] == "importing"
    assert batches[0].get("file_bytes")
    assert len(lines) == 1
    amount_before = lines[0]["amount"]
    await server.db[PREVIEWS].update_one({"id": preview["id"]}, {"$set": {"expires_at": PAST}})
    from server import app, current_user
    import httpx
    app.dependency_overrides[current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/cash-bank/statements/imports", params={"cash_bank_id": bank["id"]})
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["status"] == "importing"
    assert item["preview_id"] == preview["id"]
    assert item["stored_count"] == 0
    assert "file_bytes" not in item
    ready = await list_ready_lines(server.db, cash_bank_id=bank["id"], start="2026-09-01", end="2026-09-30")
    assert ready == []
    with pytest.raises(PairConflictError, match="belum selesai"):
        await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=lines[0]["id"],
            journal_line_id="missing-book", idempotency_key="no-partial", user=user, **PERIOD,
        )
    resumed = await _commit(preview["id"], user)
    assert resumed["status"] == "complete"
    assert resumed["preview_id"] == preview["id"]
    stored = await server.db[LINES].find({"cash_bank_id": bank["id"]}, {"_id": 0, "amount": 1}).to_list(10)
    assert sorted(row["amount"] for row in stored) == [50000.0, 100000.0]
    assert amount_before in [row["amount"] for row in stored]
    finished = await server.db[IMPORTS].find_one({"id": resumed["id"]}, {"_id": 0, "status": 1, "file_bytes": 1})
    assert finished["status"] == "complete"
    assert "file_bytes" not in finished
    assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair"}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0

    user2 = _user()
    bank2 = await _bank()
    preview2 = await create_preview(server.db, cash_bank_id=bank2["id"], filename="gone.csv", data=_csv_two(), user=user2)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause(index):
        if index == 0:
            entered.set()
            await release.wait()

    statement_svc._after_statement_line_insert = pause
    task = asyncio.create_task(_commit(preview2["id"], user2))
    await entered.wait()
    fence = await _fence(bank2["id"])
    account = await _account(bank2["id"])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    _restore()
    fence["lease_until"] = PAST
    fence.pop("_id", None)
    await server.db[IMPORT_FENCES].delete_many({"cash_bank_id": bank2["id"]})
    await server.db[IMPORT_FENCES].insert_one(fence)
    readers = []
    for item in account.get("import_currency_readers") or []:
        if isinstance(item, dict):
            item = dict(item)
            item["until"] = PAST
        readers.append(item)
    await server.db[CB_COLLECTION].update_one(
        {"id": bank2["id"]}, {"$set": {"import_currency_readers": readers}},
    )
    await server.db[PREVIEWS].delete_one({"id": preview2["id"]})
    resumed2 = await _commit(preview2["id"], user2)
    assert resumed2["status"] == "complete"
    assert resumed2["preview_id"] == preview2["id"]
    assert await server.db[LINES].count_documents({"cash_bank_id": bank2["id"]}) == 2
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def test_old_attempt_cannot_overwrite_the_takeover():
    user = _user()
    bank = await _bank()
    preview = await create_preview(server.db, cash_bank_id=bank["id"], filename="p.csv", data=_csv("REF-OLD"), user=user)
    entered = asyncio.Event()
    release = asyncio.Event()
    state = {"n": 0}

    async def hold():
        state["n"] += 1
        if state["n"] == 1:
            entered.set()
            await release.wait()

    statement_svc._after_import_fence_acquired = hold
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task = asyncio.create_task(_commit(preview["id"], user))
    await entered.wait()
    owner = (await _fence(bank["id"]))["holder"]
    await server.db[IMPORT_FENCES].update_one(
        {"cash_bank_id": bank["id"]}, {"$set": {"lease_until": PAST}},
    )
    await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"]},
        {"$set": {"import_currency_readers.$[r].until": PAST}},
        array_filters=[{"r.attempt_id": owner}],
    )
    winner = await _commit(preview["id"], user)
    assert winner["status"] == "complete"
    winner_lines = await server.db[LINES].count_documents({"cash_bank_id": bank["id"]})
    release.set()
    with pytest.raises(StatementConflictError, match="tidak lagi dimiliki"):
        await task
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 1
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == winner_lines == 1
    row = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "amount": 1, "duplicate_status": 1, "currency_code": 1})
    assert row["amount"] == 750000.0
    assert row["duplicate_status"] == "clear"
    assert row["currency_code"] == "IDR"
    assert (await _account(bank["id"]))["currency_code"] == "IDR"
    assert await _fence(bank["id"]) is None
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_dead_currency_writer_can_be_replaced_and_cannot_write_later():
    user = _user()
    bank = await _bank()
    await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"]},
        {"$set": {"currency_writer": "dead-writer", "currency_writer_until": PAST}},
    )
    changed = await update_cash_bank(server.db, bank["id"], {"currency_code": "USD"}, user=user)
    assert changed["currency_code"] == "USD"
    late = await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"], "currency_writer": "dead-writer"},
        {"$set": {"currency_code": "JPY"}},
    )
    assert late.matched_count == 0
    raw = await _account(bank["id"])
    assert raw["currency_code"] == "USD"
    assert raw.get("currency_writer") in (None, "")

    bank2 = await _bank()
    await server.db[CB_COLLECTION].update_one(
        {"id": bank2["id"]},
        {"$set": {"currency_writer": "stuck-writer"}},
    )
    preview = await create_preview(server.db, cash_bank_id=bank2["id"], filename="w.csv", data=_csv("REF-W"), user=user)
    done = await _commit(preview["id"], user)
    assert done["status"] == "complete"
    raw2 = await _account(bank2["id"])
    assert raw2["currency_code"] == "IDR"
    assert raw2.get("currency_writer") in (None, "")
    assert not (raw2.get("import_currency_readers") or [])
    line = await server.db[LINES].find_one({"cash_bank_id": bank2["id"]}, {"_id": 0, "currency_code": 1, "amount": 1})
    assert line["currency_code"] == "IDR"
    assert line["amount"] == 750000.0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def test_import_without_stored_bytes_is_not_marked_complete():
    user = _user()
    bank = await _bank()
    preview = await create_preview(server.db, cash_bank_id=bank["id"], filename="thin.csv", data=_csv_two(), user=user)
    batch_id = str(uuid.uuid4())
    await server.db[IMPORTS].insert_one({
        "id": batch_id,
        "preview_id": preview["id"],
        "cash_bank_id": bank["id"],
        "filename": "thin.csv",
        "file_sha256": preview["file_sha256"],
        "mapping_hash": "different",
        "status": "importing",
        "accepted_count": 2,
        "stored_count": 1,
        "total_in": 100000,
        "total_out": 50000,
    })
    await server.db[PREVIEWS].delete_one({"id": preview["id"]})
    with pytest.raises(StatementConflictError, match="tidak tersimpan"):
        await _commit(preview["id"], user)
    left = await server.db[IMPORTS].find_one({"id": batch_id}, {"_id": 0, "status": 1, "stored_count": 1})
    assert left["status"] == "importing"
    assert left["stored_count"] == 1
    ready = await list_ready_lines(server.db, cash_bank_id=bank["id"], start="2026-09-01", end="2026-09-30")
    assert ready == []


def _pause_first(kind, entered, release, state):
    async def pause(seen_kind):
        if seen_kind == kind and not state["paused"]:
            state["paused"] = True
            entered.set()
            await release.wait()

    return pause


async def _expire_lease(cash_bank_id, holder):
    """Simulation of lease eligibility. The paused attempt is still the live worker."""
    await server.db[IMPORT_FENCES].update_one(
        {"cash_bank_id": cash_bank_id}, {"$set": {"lease_until": PAST}},
    )
    await server.db[CB_COLLECTION].update_one(
        {"id": cash_bank_id},
        {"$set": {"import_currency_readers.$[r].until": PAST}},
        array_filters=[{"r.attempt_id": holder}],
    )


async def test_stale_line_seal_does_not_create_a_second_clear_line():
    user = _user()
    bank = await _bank()
    preview_a = await create_preview(server.db, cash_bank_id=bank["id"], filename="a.csv", data=_csv("REF-A"), user=user)
    preview_b = await create_preview(server.db, cash_bank_id=bank["id"], filename="b.csv", data=_csv("REF-B"), user=user)
    entered = asyncio.Event()
    release = asyncio.Event()
    statement_svc._before_fenced_write = _pause_first("line", entered, release, {"paused": False})
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task = asyncio.create_task(_commit(preview_a["id"], user))
    await asyncio.wait_for(entered.wait(), timeout=8)
    owner = (await _fence(bank["id"]))["holder"]
    slot = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0})
    assert slot["sealed"] is False
    assert slot["duplicate_status"] == "unsealed"
    assert "amount" not in slot
    await _expire_lease(bank["id"], owner)
    winner = await _commit(preview_b["id"], user)
    assert winner["status"] == "complete"
    release.set()
    with pytest.raises(StatementConflictError, match="tidak lagi dimiliki"):
        await task
    lines = await server.db[LINES].find({"cash_bank_id": bank["id"]}, {"_id": 0}).to_list(10)
    business = [row for row in lines if row.get("sealed") is not False]
    assert len(business) == 1
    assert business[0]["amount"] == 750000.0
    assert business[0]["duplicate_status"] == "clear"
    assert business[0]["currency_code"] == "IDR"
    assert business[0]["import_id"] == winner["id"]
    leftover = [row for row in lines if row.get("sealed") is False]
    assert len(leftover) == 1
    assert leftover[0]["duplicate_status"] == "unsealed"
    assert "amount" not in leftover[0]
    assert (await server.db[IMPORTS].find_one({"id": slot["import_id"]}))["status"] == "importing"
    ready = await list_ready_lines(server.db, cash_bank_id=bank["id"], start="2026-09-01", end="2026-09-30")
    assert [row["id"] for row in ready] == [business[0]["id"]]
    with pytest.raises(PairConflictError, match="belum selesai"):
        await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=leftover[0]["id"],
            journal_line_id="missing-book", idempotency_key="stale-slot", user=user, **PERIOD,
        )
    assert (await _account(bank["id"]))["currency_code"] == "IDR"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
    assert await server.db[RECON_MATCHES].count_documents({}) == 0


async def test_stale_twin_update_does_not_reclassify_or_drop_keep():
    user = _user()
    bank = await _bank()
    basis = await create_preview(server.db, cash_bank_id=bank["id"], filename="basis.csv", data=_csv("REF-BASIS"), user=user)
    stored = await _commit(basis["id"], user)
    victim = await server.db[LINES].find_one({"import_id": stored["id"]}, {"_id": 0, "id": 1, "amount": 1})
    kept_preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="kept.csv",
        data=_csv("REF-KEPT", description="Biaya admin", amount="15000"), user=user,
    )
    kept_batch = await _commit(kept_preview["id"], user)
    kept = await server.db[LINES].find_one({"import_id": kept_batch["id"]}, {"_id": 0, "id": 1})
    await resolve_duplicate(
        server.db, kept["id"], action="keep", reason="dipertahankan operator",
        idempotency_key="keep-stale", user=user,
    )
    preview_a = await create_preview(server.db, cash_bank_id=bank["id"], filename="twin.csv", data=_csv("REF-TWIN"), user=user)
    preview_b = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="other.csv",
        data=_csv("REF-OTHER", description="Setoran tunai", amount="22000"), user=user,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    statement_svc._before_fenced_write = _pause_first("twin", entered, release, {"paused": False})
    from motor.motor_asyncio import AsyncIOMotorCollection
    original = AsyncIOMotorCollection.update_many
    seen = []

    async def wrapped(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        setting = (args[1] or {}).get("$set") or {} if len(args) > 1 else {}
        if self.name == LINES and setting.get("duplicate_status") == "pending_review":
            seen.append({"filter": args[0], "set": setting, "matched": result.matched_count})
        return result

    AsyncIOMotorCollection.update_many = wrapped
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task = asyncio.create_task(_commit(preview_a["id"], user))
    try:
        await asyncio.wait_for(entered.wait(), timeout=8)
        owner = (await _fence(bank["id"]))["holder"]
        await _expire_lease(bank["id"], owner)
        winner = await _commit(preview_b["id"], user)
        assert winner["status"] == "complete"
        release.set()
        with pytest.raises(StatementConflictError, match="tidak lagi dimiliki"):
            await task
    finally:
        AsyncIOMotorCollection.update_many = original
        release.set()
    assert len(seen) == 1
    assert seen[0]["matched"] == 0
    assert seen[0]["filter"]["duplicate_status"] == "clear"
    assert seen[0]["filter"]["active_claim"] is None
    assert set(seen[0]["set"]) == {"duplicate_status", "duplicate_reason"}
    victim_after = await server.db[LINES].find_one({"id": victim["id"]}, {"_id": 0})
    kept_after = await server.db[LINES].find_one({"id": kept["id"]}, {"_id": 0})
    assert victim_after["duplicate_status"] == "clear"
    assert victim_after["amount"] == 750000.0
    assert kept_after["duplicate_status"] == "kept"
    assert kept_after["amount"] == 15000.0
    assert len(kept_after.get("resolutions") or []) == 1
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"], "duplicate_status": "clear"}) == 2
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_stale_finish_does_not_complete_and_cleanup_keeps_the_winner():
    user = _user()
    bank = await _bank()
    preview_a = await create_preview(server.db, cash_bank_id=bank["id"], filename="fin-a.csv", data=_csv("REF-F"), user=user)
    preview_b = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="fin-b.csv",
        data=_csv("REF-G", description="Setoran tunai", amount="22000"), user=user,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    statement_svc._before_fenced_write = _pause_first("finish", entered, release, {"paused": False})
    b_entered = asyncio.Event()
    b_release = asyncio.Event()

    async def hold_winner():
        b_entered.set()
        await b_release.wait()

    from motor.motor_asyncio import AsyncIOMotorCollection
    original = AsyncIOMotorCollection.update_one
    seen = []

    async def wrapped(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        setting = (args[1] or {}).get("$set") or {} if len(args) > 1 else {}
        if self.name == IMPORTS and setting.get("status") in ("complete", "failed"):
            seen.append({
                "filter": args[0],
                "set": {key: setting.get(key) for key in ("status", "stored_count", "completed_at")},
                "unset": list((args[1].get("$unset") or {}).keys()),
                "matched": result.matched_count,
            })
        return result

    AsyncIOMotorCollection.update_one = wrapped
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task = asyncio.create_task(_commit(preview_a["id"], user))
    try:
        await asyncio.wait_for(entered.wait(), timeout=8)
        owner = (await _fence(bank["id"]))["holder"]
        batch_id = (await _fence(bank["id"]))["batch_id"]
        await _expire_lease(bank["id"], owner)
        statement_svc._after_import_fence_acquired = hold_winner
        winner_task = asyncio.create_task(_commit(preview_b["id"], user))
        await asyncio.wait_for(b_entered.wait(), timeout=8)
        release.set()
        with pytest.raises(StatementConflictError, match="tidak lagi dimiliki"):
            await task
        held = await _fence(bank["id"])
        assert held["holder"] != owner
        assert held["holder"] in _reader_ids(await _account(bank["id"]))
        assert owner not in _reader_ids(await _account(bank["id"]))
        stale_batch = await server.db[IMPORTS].find_one(
            {"id": batch_id}, {"_id": 0, "status": 1, "stored_count": 1, "file_bytes": 1},
        )
        assert stale_batch["status"] == "importing"
        assert stale_batch["stored_count"] == 0
        assert stale_batch.get("file_bytes")
        b_release.set()
        statement_svc._after_import_fence_acquired = _HOOKS["after_fence"]
        winner = await winner_task
    finally:
        AsyncIOMotorCollection.update_one = original
        release.set()
        b_release.set()
    loser_writes = [row for row in seen if row["filter"].get("id") == batch_id]
    assert len(loser_writes) == 1
    assert loser_writes[0]["matched"] == 0
    assert loser_writes[0]["filter"]["status"] == "importing"
    assert loser_writes[0]["filter"]["writer_attempt"] == owner
    assert set(loser_writes[0]["set"]) == {"status", "stored_count", "completed_at"}
    assert loser_writes[0]["unset"] == ["file_bytes"]
    ready = await list_ready_lines(server.db, cash_bank_id=bank["id"], start="2026-09-01", end="2026-09-30")
    assert [row["import_id"] for row in ready] == [winner["id"]]
    stale_line = await server.db[LINES].find_one(
        {"import_id": batch_id}, {"_id": 0, "id": 1, "amount": 1, "duplicate_status": 1},
    )
    assert stale_line["amount"] == 750000.0
    assert stale_line["duplicate_status"] == "clear"
    with pytest.raises(PairConflictError, match="belum selesai"):
        await create_pair(
            server.db, cash_bank_id=bank["id"], statement_line_id=stale_line["id"],
            journal_line_id="missing-book", idempotency_key="stale-finish", user=user, **PERIOD,
        )
    assert await _fence(bank["id"]) is None
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


def _run_child(mode, *, preview_id, user, ready):
    env = os.environ.copy()
    env["OWNER_FORCE_DB"] = OWNER_DB
    env["DB_NAME"] = OWNER_DB
    env["OWNER_CHILD_MODE"] = mode
    env["OWNER_CHILD_PREVIEW"] = preview_id
    env["OWNER_CHILD_USER"] = json.dumps(user)
    env["OWNER_CHILD_READY"] = str(ready)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "child"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


async def test_killed_process_before_seal_is_resumed_by_takeover():
    user = _user()
    bank = await _bank()
    preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="kill.csv", data=_csv("REF-KILL"), user=user,
    )
    ready = Path(f"/tmp/owner-kill-{uuid.uuid4().hex}")
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    proc = _run_child("seal", preview_id=preview["id"], user=user, ready=ready)
    assert proc.returncode == -signal.SIGKILL, proc.stderr
    fence = await _fence(bank["id"])
    assert fence and fence["holder"]
    assert fence["holder"] in _reader_ids(await _account(bank["id"]))
    slot = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0})
    assert slot["sealed"] is False
    assert "amount" not in slot
    batch = await server.db[IMPORTS].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "status": 1, "file_bytes": 1})
    assert batch["status"] == "importing"
    assert batch.get("file_bytes")
    # Lease eligibility is simulated. The child was already killed, so its finally did not run.
    await server.db[IMPORT_FENCES].update_one({"cash_bank_id": bank["id"]}, {"$set": {"lease_until": PAST}})
    await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"]},
        {"$set": {"import_currency_readers.$[r].until": PAST}},
        array_filters=[{"r.attempt_id": fence["holder"]}],
    )
    resumed = await _commit(preview["id"], user)
    assert resumed["status"] == "complete"
    lines = await server.db[LINES].find({"cash_bank_id": bank["id"]}, {"_id": 0}).to_list(10)
    assert len(lines) == 1
    assert lines[0]["id"] == slot["id"]
    assert lines[0]["sealed"] is True
    assert lines[0]["amount"] == 750000.0
    assert lines[0]["duplicate_status"] == "clear"
    assert lines[0]["currency_code"] == "IDR"
    assert await _fence(bank["id"]) is None
    assert not _reader_ids(await _account(bank["id"]))
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_reader_without_fence_recovers_without_pulling_a_live_reader():
    user = _user()
    bank = await _bank()
    preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="pin.csv", data=_csv("REF-PIN"), user=user,
    )
    ready = Path(f"/tmp/owner-pin-{uuid.uuid4().hex}")
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    proc = _run_child("pin", preview_id=preview["id"], user=user, ready=ready)
    assert proc.returncode == -signal.SIGKILL, proc.stderr
    account = await _account(bank["id"])
    readers = account.get("import_currency_readers") or []
    assert len(readers) == 1
    assert isinstance(readers[0], dict)
    killed_attempt = readers[0]["attempt_id"]
    assert await _fence(bank["id"]) is None
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 0
    with pytest.raises(CashBankConflictError, match="pratinjau"):
        await update_cash_bank(server.db, bank["id"], {"currency_code": "USD"}, user=user)
    assert (await _account(bank["id"]))["currency_code"] == "IDR"
    # Simulation: the killed attempt's lease is eligible. Another request's reader stays live.
    await server.db[CB_COLLECTION].update_one(
        {"id": bank["id"]},
        {"$set": {"import_currency_readers.$[r].until": PAST}},
        array_filters=[{"r.attempt_id": killed_attempt}],
    )
    live = {"attempt_id": "live-other", "operation_id": "op-other", "until": "2999-01-01T00:00:00+00:00"}
    await server.db[CB_COLLECTION].update_one({"id": bank["id"]}, {"$push": {"import_currency_readers": live}})
    with pytest.raises(CashBankConflictError, match="pratinjau"):
        await update_cash_bank(server.db, bank["id"], {"currency_code": "USD"}, user=user)
    still = _reader_ids(await _account(bank["id"]))
    assert killed_attempt in still
    assert "live-other" in still
    await _unpin_import_currency(server.db, bank["id"], "live-other")
    changed = await update_cash_bank(server.db, bank["id"], {"currency_code": "USD"}, user=user)
    assert changed["currency_code"] == "USD"
    after_ids = _reader_ids(await _account(bank["id"]))
    assert killed_attempt not in after_ids
    assert "live-other" not in after_ids
    with pytest.raises(StatementConflictError, match="Mata uang"):
        await _commit(preview["id"], user)
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 0
    usd_mapping = _mapping()
    usd_mapping["file_currency"] = "USD"
    fresh = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="usd.csv", data=_csv("REF-USD", description="USD masuk"), user=user,
    )
    done = await commit_import(server.db, preview_id=fresh["id"], mapping_raw=usd_mapping, user=user)
    assert done["status"] == "complete"
    line = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "currency_code": 1, "amount": 1})
    assert line["currency_code"] == "USD"
    assert line["amount"] == 750000.0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals

    other = await _bank()
    paused = await create_preview(server.db, cash_bank_id=other["id"], filename="cont.csv", data=_csv("REF-CONT"), user=user)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def after_pin():
        entered.set()
        await release.wait()

    statement_svc._after_currency_pin = after_pin
    task = asyncio.create_task(_commit(paused["id"], user))
    await asyncio.wait_for(entered.wait(), timeout=8)
    assert await _fence(other["id"]) is None
    holder = _reader_ids(await _account(other["id"]))[0]
    await server.db[CB_COLLECTION].update_one(
        {"id": other["id"]},
        {"$set": {"import_currency_readers.$[r].until": PAST}},
        array_filters=[{"r.attempt_id": holder}],
    )
    changed_other = await update_cash_bank(server.db, other["id"], {"currency_code": "USD"}, user=user)
    assert changed_other["currency_code"] == "USD"
    release.set()
    with pytest.raises(StatementConflictError, match="Mata uang|pembaca impor"):
        await task
    assert await server.db[LINES].count_documents({"cash_bank_id": other["id"]}) == 0
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": other["id"]}) == 0
    assert await _fence(other["id"]) is None
    assert (await _account(other["id"]))["currency_code"] == "USD"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


def _child_main():
    async def run():
        mode = os.environ["OWNER_CHILD_MODE"]
        user = json.loads(os.environ["OWNER_CHILD_USER"])
        preview_id = os.environ["OWNER_CHILD_PREVIEW"]
        ready = Path(os.environ["OWNER_CHILD_READY"])
        if mode == "seal":
            async def before_write(kind):
                if kind == "line":
                    ready.write_text("ready")
                    os.kill(os.getpid(), signal.SIGKILL)

            statement_svc._before_fenced_write = before_write
        elif mode == "pin":
            async def after_pin():
                ready.write_text("ready")
                os.kill(os.getpid(), signal.SIGKILL)

            statement_svc._after_currency_pin = after_pin
        else:
            raise SystemExit(f"unknown child mode {mode}")
        await _commit(preview_id, user)

    try:
        asyncio.run(run())
    except Exception:
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "child":
    _child_main()


def _pause_reassign_once(entered, release, state):
    original = statement_svc._reassign_stale_import_writers

    async def wrapped(*args, **kwargs):
        if not state["paused"]:
            state["paused"] = True
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    statement_svc._reassign_stale_import_writers = wrapped
    return original


async def test_stale_classification_misses_after_fence_moves_before_writer_adopt():
    user = _user()
    bank = await _bank()
    basis = await create_preview(server.db, cash_bank_id=bank["id"], filename="basis.csv", data=_csv("REF-B"), user=user)
    stored = await _commit(basis["id"], user)
    victim = await server.db[LINES].find_one({"import_id": stored["id"]}, {"_id": 0, "id": 1, "amount": 1})
    kept_preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="kept.csv",
        data=_csv("REF-K", description="Biaya admin", amount="15000"), user=user,
    )
    kept_batch = await _commit(kept_preview["id"], user)
    kept = await server.db[LINES].find_one({"import_id": kept_batch["id"]}, {"_id": 0, "id": 1})
    await resolve_duplicate(
        server.db, kept["id"], action="keep", reason="dipertahankan operator",
        idempotency_key="keep-partial", user=user,
    )
    preview_a = await create_preview(server.db, cash_bank_id=bank["id"], filename="twin.csv", data=_csv("REF-A"), user=user)
    preview_b = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="other.csv",
        data=_csv("REF-O", description="Setoran tunai", amount="22000"), user=user,
    )
    a_entered = asyncio.Event()
    a_release = asyncio.Event()
    statement_svc._before_fenced_write = _pause_first("twin", a_entered, a_release, {"paused": False})
    b_entered = asyncio.Event()
    b_release = asyncio.Event()
    original = _pause_reassign_once(b_entered, b_release, {"paused": False})
    from motor.motor_asyncio import AsyncIOMotorCollection
    update_many = AsyncIOMotorCollection.update_many
    seen = []

    async def wrapped(self, *args, **kwargs):
        result = await update_many(self, *args, **kwargs)
        setting = (args[1] or {}).get("$set") or {} if len(args) > 1 else {}
        if self.name == LINES and setting.get("duplicate_status") == "pending_review":
            seen.append(result.matched_count)
        return result

    AsyncIOMotorCollection.update_many = wrapped
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task_a = asyncio.create_task(_commit(preview_a["id"], user))
    try:
        await asyncio.wait_for(a_entered.wait(), timeout=8)
        owner = (await _fence(bank["id"]))["holder"]
        await _expire_lease(bank["id"], owner)
        task_b = asyncio.create_task(_commit(preview_b["id"], user))
        await asyncio.wait_for(b_entered.wait(), timeout=8)
        held = await _fence(bank["id"])
        assert held["holder"] != owner
        assert (await _account(bank["id"])).get("import_epoch") == 1
        victim_mid = await server.db[LINES].find_one({"id": victim["id"]}, {"_id": 0, "duplicate_status": 1, "import_epoch": 1})
        assert victim_mid["duplicate_status"] == "clear"
        assert victim_mid["import_epoch"] == 1
        a_release.set()
        with pytest.raises(StatementConflictError, match="tidak lagi dimiliki"):
            await task_a
        assert seen == [0]
        b_release.set()
        statement_svc._reassign_stale_import_writers = original
        winner = await task_b
        assert winner["status"] == "complete"
    finally:
        AsyncIOMotorCollection.update_many = update_many
        statement_svc._reassign_stale_import_writers = original
        a_release.set()
        b_release.set()
    victim_after = await server.db[LINES].find_one({"id": victim["id"]}, {"_id": 0})
    kept_after = await server.db[LINES].find_one({"id": kept["id"]}, {"_id": 0})
    assert victim_after["duplicate_status"] == "clear"
    assert victim_after["amount"] == 750000.0
    assert kept_after["duplicate_status"] == "kept"
    assert kept_after["amount"] == 15000.0
    assert len(kept_after.get("resolutions") or []) == 1
    ok, _reason = line_matchable(victim_after, "IDR")
    assert ok
    retried = await _commit(preview_a["id"], user)
    assert retried["status"] == "complete"
    twins = await server.db[LINES].find(
        {"cash_bank_id": bank["id"], "description": "Transfer masuk"}, {"_id": 0, "duplicate_status": 1, "amount": 1},
    ).to_list(5)
    assert len(twins) == 2
    assert {row["duplicate_status"] for row in twins} == {"pending_review"}
    assert {row["amount"] for row in twins} == {750000.0}
    kept_final = await server.db[LINES].find_one({"id": kept["id"]}, {"_id": 0, "duplicate_status": 1, "resolutions": 1})
    assert kept_final["duplicate_status"] == "kept"
    assert len(kept_final.get("resolutions") or []) == 1
    assert (await _account(bank["id"]))["currency_code"] == "IDR"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals


async def test_finish_before_writer_adopt_stays_a_real_complete_batch():
    user = _user()
    bank = await _bank()
    preview_a = await create_preview(server.db, cash_bank_id=bank["id"], filename="fin-a.csv", data=_csv("REF-A"), user=user)
    preview_b = await create_preview(server.db, cash_bank_id=bank["id"], filename="fin-b.csv", data=_csv("REF-B"), user=user)
    a_entered = asyncio.Event()
    a_release = asyncio.Event()
    statement_svc._before_fenced_write = _pause_first("finish", a_entered, a_release, {"paused": False})
    b_entered = asyncio.Event()
    b_release = asyncio.Event()
    original = _pause_reassign_once(b_entered, b_release, {"paused": False})
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task_a = asyncio.create_task(_commit(preview_a["id"], user))
    try:
        await asyncio.wait_for(a_entered.wait(), timeout=8)
        owner = (await _fence(bank["id"]))["holder"]
        batch_id = (await _fence(bank["id"]))["batch_id"]
        await _expire_lease(bank["id"], owner)
        task_b = asyncio.create_task(_commit(preview_b["id"], user))
        await asyncio.wait_for(b_entered.wait(), timeout=8)
        a_release.set()
        finished = await task_a
        assert finished["status"] == "complete"
        assert finished["stored_count"] == 1
        b_release.set()
        statement_svc._reassign_stale_import_writers = original
        winner = await task_b
        assert winner["status"] == "complete"
    finally:
        statement_svc._reassign_stale_import_writers = original
        a_release.set()
        b_release.set()
    rows = await server.db[LINES].find(
        {"cash_bank_id": bank["id"]}, {"_id": 0, "amount": 1, "duplicate_status": 1, "import_id": 1},
    ).to_list(5)
    assert len(rows) == 2
    assert {row["amount"] for row in rows} == {750000.0}
    assert {row["duplicate_status"] for row in rows} == {"pending_review"}
    batches = await server.db[IMPORTS].find(
        {"cash_bank_id": bank["id"]}, {"_id": 0, "status": 1, "stored_count": 1},
    ).to_list(5)
    assert len(batches) == 2
    assert {batch["status"] for batch in batches} == {"complete"}
    assert {batch["stored_count"] for batch in batches} == {1}
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
    ready = await list_ready_lines(server.db, cash_bank_id=bank["id"], start="2026-09-01", end="2026-09-30")
    assert len(ready) == 2
    assert batch_id in {row["import_id"] for row in ready}


async def test_next_takeover_adopts_a_writer_the_dead_owner_did_not_move():
    user = _user()
    bank = await _bank()
    preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="two.csv", data=_csv_two(), user=user,
    )
    a_entered = asyncio.Event()
    a_release = asyncio.Event()
    state = {"paused": False}

    async def after_line(index):
        if index == 0 and not state["paused"]:
            state["paused"] = True
            a_entered.set()
            await a_release.wait()

    statement_svc._after_statement_line_insert = after_line
    b_entered = asyncio.Event()
    b_release = asyncio.Event()
    original = _pause_reassign_once(b_entered, b_release, {"paused": False})
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    task_a = asyncio.create_task(_commit(preview["id"], user))
    task_b = None
    try:
        await asyncio.wait_for(a_entered.wait(), timeout=8)
        owner_a = (await _fence(bank["id"]))["holder"]
        first = await server.db[LINES].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "id": 1, "amount": 1})
        assert first["amount"] == 100000.0
        await _expire_lease(bank["id"], owner_a)
        task_b = asyncio.create_task(_commit(preview["id"], user))
        await asyncio.wait_for(b_entered.wait(), timeout=8)
        fence_b = await _fence(bank["id"])
        assert fence_b["holder"] != owner_a
        batch = await server.db[IMPORTS].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "status": 1, "writer_attempt": 1})
        assert batch["status"] == "importing"
        assert batch["writer_attempt"] == owner_a
        await _expire_lease(bank["id"], fence_b["holder"])
        statement_svc._reassign_stale_import_writers = original
        resumed = await _commit(preview["id"], user)
        assert resumed["status"] == "complete"
        assert resumed["stored_count"] == 2
    finally:
        statement_svc._reassign_stale_import_writers = original
        a_release.set()
        b_release.set()
        if task_b is not None and not task_b.done():
            task_b.cancel()
        if not task_a.done():
            task_a.cancel()
    lines = await server.db[LINES].find(
        {"cash_bank_id": bank["id"], "sealed": {"$ne": False}}, {"_id": 0, "id": 1, "amount": 1, "duplicate_status": 1},
    ).to_list(5)
    assert sorted(row["amount"] for row in lines) == [50000.0, 100000.0]
    assert first["id"] in {row["id"] for row in lines}
    assert {row["duplicate_status"] for row in lines} == {"clear"}
    finished = await server.db[IMPORTS].find_one({"cash_bank_id": bank["id"]}, {"_id": 0, "status": 1})
    assert finished["status"] == "complete"
    assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"], "sealed": False}) == 0
    assert (await _account(bank["id"]))["currency_code"] == "IDR"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
