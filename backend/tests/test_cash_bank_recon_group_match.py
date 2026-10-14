"""Focused tests for Accurate-style pair_group (many bank lines → one journal)."""
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
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
GROUP_DB = f"recon_group_isolated_{_worker}_{uuid.uuid4().hex[:6]}"
os.environ["DB_NAME"] = GROUP_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_pair_match import (
    PairConflictError,
    create_pair,
    create_pair_group,
)
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    ClaimFeatureNotReady,
    ensure_indexes as ensure_recon,
)
from services.cash_bank_statement import IMPORTS, LINES, PREVIEWS, ensure_indexes as ensure_statement
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal

# Reuse proven helpers from the 1:1 suite (same process DB binding via fixtures below).
from tests.test_cash_bank_statement_match import (  # noqa: E402
    _import,
    _receipt,
    _setup,
    _user,
)

pytestmark = pytest.mark.anyio

PERIOD = {"start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = GROUP_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[GROUP_DB]


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


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _three_bank_one_journal(c, amounts=(200000, 500000, 300000)):
    bank, income = await _setup(c, currency="IDR")
    total = sum(amounts)
    await _receipt(c, bank["id"], income["id"], total, desc="Penerimaan gabungan")
    rows = ["tanggal,uraian,masuk,keluar,referensi,id,saldo"]
    for index, amount in enumerate(amounts, start=1):
        rows.append(f"2026-09-0{index},Mutasi {index},{amount},,REF-G{index},TX-G{index},")
    _preview, imported = await _import(c, bank["id"], "\n".join(rows) + "\n")
    assert imported.status_code == 201, imported.text
    ws = await c.get(
        "/api/cash-bank/reconciliations/pairs/workspace",
        params={"cash_bank_id": bank["id"], **PERIOD},
    )
    assert ws.status_code == 200, ws.text
    body = ws.json()
    bank_ids = [row["id"] for row in body["bank_lines"]]
    book_line = body["book_lines"][0]["journal_line_id"]
    return bank, book_line, bank_ids


async def test_group_200_500_300_equals_one_million():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        created = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": bank_ids,
                "idempotency_key": "group-ok",
                **PERIOD,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["match_kind"] == "pair_group"
        assert body["status"] == "active"
        assert body["member_count"] == 3
        assert float(body["amount"]) == 1000000.0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert ws.status_code == 200
        assert ws.json()["unmatched_bank_count"] == 0
        assert ws.json()["unmatched_book_count"] == 0
        kinds = {row["match_kind"] for row in ws.json()["matched_pairs"]}
        assert "pair_group" in kinds


async def test_group_amount_mismatch_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        bad = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": bank_ids[:2],
                "idempotency_key": "group-bad-sum",
                **PERIOD,
            },
        )
        assert bad.status_code == 409
        assert await server.db[RECON_MATCHES].count_documents({"match_kind": "pair_group"}) == 0


async def test_group_duplicate_bank_member_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        dup = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": [bank_ids[0], bank_ids[0], bank_ids[1]],
                "idempotency_key": "group-dup",
                **PERIOD,
            },
        )
        assert dup.status_code == 400


async def test_group_rejects_bank_already_paired_1to1():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 200000, desc="A")
        await _receipt(c, bank["id"], income["id"], 800000, desc="B")
        _preview, imported = await _import(
            c,
            bank["id"],
            (
                "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
                "2026-09-01,Mutasi A,200000,,REF-A,TX-A,\n"
                "2026-09-02,Mutasi B,500000,,REF-B,TX-B,\n"
                "2026-09-03,Mutasi C,300000,,REF-C,TX-C,\n"
            ),
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        bank_by_amount = {float(row["amount"]): row["id"] for row in body["bank_lines"]}
        book_by_amount = {float(row["absolute_amount"]): row["journal_line_id"] for row in body["book_lines"]}
        paired = await c.post(
            "/api/cash-bank/reconciliations/pairs",
            json={
                "cash_bank_id": bank["id"],
                "statement_line_id": bank_by_amount[200000.0],
                "journal_line_id": book_by_amount[200000.0],
                "idempotency_key": "pair-first",
                **PERIOD,
            },
        )
        assert paired.status_code == 201, paired.text
        group = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_by_amount[800000.0],
                "statement_line_ids": [
                    bank_by_amount[200000.0],
                    bank_by_amount[500000.0],
                    bank_by_amount[300000.0],
                ],
                "idempotency_key": "group-after-pair",
                **PERIOD,
            },
        )
        assert group.status_code == 409


async def test_group_rejects_journal_already_paired_1to1():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 1000000, desc="Satu juta")
        _preview, imported = await _import(
            c,
            bank["id"],
            (
                "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
                "2026-09-01,Mutasi full,1000000,,REF-F,TX-F,\n"
                "2026-09-02,Mutasi 200,200000,,REF-2,TX-2,\n"
                "2026-09-03,Mutasi 800,800000,,REF-8,TX-8,\n"
            ),
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        full_bank = next(row for row in body["bank_lines"] if float(row["amount"]) == 1000000.0)
        book = body["book_lines"][0]["journal_line_id"]
        parts = [row["id"] for row in body["bank_lines"] if float(row["amount"]) != 1000000.0]
        paired = await c.post(
            "/api/cash-bank/reconciliations/pairs",
            json={
                "cash_bank_id": bank["id"],
                "statement_line_id": full_bank["id"],
                "journal_line_id": book,
                "idempotency_key": "pair-journal",
                **PERIOD,
            },
        )
        assert paired.status_code == 201, paired.text
        group = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book,
                "statement_line_ids": parts,
                "idempotency_key": "group-journal-taken",
                **PERIOD,
            },
        )
        assert group.status_code == 409


async def test_group_concurrent_same_payload_one_effective():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)

        async def attempt(key):
            try:
                return await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key=key,
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        left, right = await asyncio.gather(attempt("g-race-1"), attempt("g-race-1"))
        winners = [item for item in (left, right) if isinstance(item, dict) and item.get("status") == "active"]
        assert len(winners) == 2
        assert winners[0]["id"] == winners[1]["id"]
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active"}
        ) == 1


async def test_two_groups_contend_for_bank_line():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 700000, desc="J1")
        await _receipt(c, bank["id"], income["id"], 800000, desc="J2")
        _preview, imported = await _import(
            c,
            bank["id"],
            (
                "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
                "2026-09-01,Shared,200000,,REF-S,TX-S,\n"
                "2026-09-02,A1,500000,,REF-A1,TX-A1,\n"
                "2026-09-03,B1,600000,,REF-B1,TX-B1,\n"
            ),
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        by_amt = {float(row["amount"]): row["id"] for row in body["bank_lines"]}
        books = sorted(body["book_lines"], key=lambda row: float(row["absolute_amount"]))
        j700 = books[0]["journal_line_id"]
        j800 = books[1]["journal_line_id"]

        async def attempt(journal_id, ids, key):
            try:
                return await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=journal_id,
                    statement_line_ids=ids,
                    idempotency_key=key,
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        left, right = await asyncio.gather(
            attempt(j700, [by_amt[200000.0], by_amt[500000.0]], "g-left"),
            attempt(j800, [by_amt[200000.0], by_amt[600000.0]], "g-right"),
        )
        outcomes = [left, right]
        assert sum(isinstance(item, PairConflictError) for item in outcomes) == 1
        assert sum(isinstance(item, dict) and item.get("status") == "active" for item in outcomes) == 1


async def test_group_vs_pair_contend_for_journal():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 1000000, desc="Target")
        _preview, imported = await _import(
            c,
            bank["id"],
            (
                "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
                "2026-09-01,Full,1000000,,REF-F,TX-F,\n"
                "2026-09-02,P1,200000,,REF-1,TX-1,\n"
                "2026-09-03,P2,800000,,REF-2,TX-2,\n"
            ),
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        book = body["book_lines"][0]["journal_line_id"]
        by_amt = {float(row["amount"]): row["id"] for row in body["bank_lines"]}

        async def pair_attempt():
            try:
                return await create_pair(
                    server.db,
                    cash_bank_id=bank["id"],
                    statement_line_id=by_amt[1000000.0],
                    journal_line_id=book,
                    idempotency_key="pair-vs-group",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        async def group_attempt():
            try:
                return await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book,
                    statement_line_ids=[by_amt[200000.0], by_amt[800000.0]],
                    idempotency_key="group-vs-pair",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        left, right = await asyncio.gather(pair_attempt(), group_attempt())
        outcomes = [left, right]
        assert sum(isinstance(item, PairConflictError) for item in outcomes) == 1
        assert sum(isinstance(item, dict) and item.get("status") == "active" for item in outcomes) == 1
        assert await server.db[RECON_MATCHES].count_documents({"journal_line_id": book}) == 1


async def test_group_retry_idempotent_no_duplicate():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        first = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": bank_ids,
                "idempotency_key": "group-retry",
                **PERIOD,
            },
        )
        second = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": bank_ids,
                "idempotency_key": "group-retry",
                **PERIOD,
            },
        )
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "idempotency_key": "group-retry"}
        ) == 1


async def test_cancel_group_releases_claims_and_keeps_history():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": book_line,
                "statement_line_ids": bank_ids,
                "idempotency_key": "group-cancel",
                **PERIOD,
            },
        )
        assert created.status_code == 201, created.text
        group_id = created.json()["id"]
        journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        cancelled = await c.post(
            f"/api/cash-bank/reconciliations/pair-groups/{group_id}/cancel",
            json={"idempotency_key": "cancel-g1", "reason": "salah pilih grup"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        retry = await c.post(
            f"/api/cash-bank/reconciliations/pair-groups/{group_id}/cancel",
            json={"idempotency_key": "cancel-g1", "reason": "salah pilih grup"},
        )
        assert retry.status_code == 200
        assert retry.json()["id"] == group_id
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row.get("active_claim") in (None, "")
        stored = await server.db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
        assert stored["status"] == "cancelled"
        assert stored.get("history_journal_line_id") == book_line
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
        detail = await c.get(f"/api/cash-bank/reconciliations/pair-groups/{group_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "cancelled"


async def test_group_fail_closed_without_claim_feature(monkeypatch):
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)

        async def blocked(_db):
            raise ClaimFeatureNotReady("index klaim belum siap")

        monkeypatch.setattr(
            "services.cash_bank_pair_match.require_claim_feature",
            blocked,
        )
        with pytest.raises(PairConflictError):
            await create_pair_group(
                server.db,
                cash_bank_id=bank["id"],
                journal_line_id=book_line,
                statement_line_ids=bank_ids,
                idempotency_key="group-closed",
                start="2026-09-01",
                end="2026-09-30",
                user=admin,
            )


async def test_existing_pair_still_readable_with_groups():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 750000, desc="Pair")
        await _receipt(c, bank["id"], income["id"], 1000000, desc="Group")
        _preview, imported = await _import(
            c,
            bank["id"],
            (
                "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
                "2026-09-05,Pair bank,750000,,REF-P,TX-P,\n"
                "2026-09-01,G1,200000,,REF-1,TX-1,\n"
                "2026-09-02,G2,500000,,REF-2,TX-2,\n"
                "2026-09-03,G3,300000,,REF-3,TX-3,\n"
            ),
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        pair_bank = next(row for row in body["bank_lines"] if float(row["amount"]) == 750000.0)
        pair_book = next(row for row in body["book_lines"] if float(row["absolute_amount"]) == 750000.0)
        group_book = next(row for row in body["book_lines"] if float(row["absolute_amount"]) == 1000000.0)
        group_banks = [row["id"] for row in body["bank_lines"] if float(row["amount"]) != 750000.0]
        pair = await c.post(
            "/api/cash-bank/reconciliations/pairs",
            json={
                "cash_bank_id": bank["id"],
                "statement_line_id": pair_bank["id"],
                "journal_line_id": pair_book["journal_line_id"],
                "idempotency_key": "keep-pair",
                **PERIOD,
            },
        )
        assert pair.status_code == 201, pair.text
        group = await c.post(
            "/api/cash-bank/reconciliations/pair-groups",
            json={
                "cash_bank_id": bank["id"],
                "journal_line_id": group_book["journal_line_id"],
                "statement_line_ids": group_banks,
                "idempotency_key": "keep-group",
                **PERIOD,
            },
        )
        assert group.status_code == 201, group.text
        ws2 = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        kinds = {row["match_kind"] for row in ws2.json()["matched_pairs"]}
        assert kinds == {"pair", "pair_group"}
        assert ws2.json()["unmatched_bank_count"] == 0
