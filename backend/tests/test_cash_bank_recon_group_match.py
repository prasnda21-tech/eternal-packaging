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
# Preview HMAC requires JWT_SECRET length >= 16 (cash_bank_statement._preview_secret).
# CI job env historically sets JWT_SECRET=ci-only-secret (14 chars); setdefault would
# keep that short value and every HTTP import seed fails with "Kunci pratinjau…".
_TEST_JWT_SECRET = "test-secret-key-12345678901234567890"
if len(os.environ.get("JWT_SECRET") or "") < 16:
    os.environ["JWT_SECRET"] = _TEST_JWT_SECRET
else:
    os.environ.setdefault("JWT_SECRET", _TEST_JWT_SECRET)
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
import services.cash_bank_pair_match as pair_svc
from services.cash_bank_pair_match import (
    PairConflictError,
    ProcessInterrupt,
    cancel_pair,
    cancel_pair_group,
    create_pair,
    create_pair_group,
    pair_workspace,
)
from services.cash_bank_owner_recon import STATUS_REVIEW_CANON, STATUS_SESUAI, get_owner_recon_view
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    ClaimFeatureNotReady,
    ensure_indexes as ensure_recon,
)
from services.cash_bank_statement import (
    IMPORTS,
    LINES,
    PREVIEWS,
    StatementConflictError as StatementPreviewConflictError,
    _preview_secret,
    ensure_indexes as ensure_statement,
)
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


async def test_preview_secret_rejects_short_jwt_key():
    """CI-length secrets must still fail closed; do not weaken production guard."""
    previous = os.environ.get("JWT_SECRET")
    try:
        os.environ["JWT_SECRET"] = "ci-only-secret"  # 14 chars — same as old CI value
        with pytest.raises(StatementPreviewConflictError, match="Kunci pratinjau tidak tersedia"):
            _preview_secret()
    finally:
        if previous is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = previous


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
    """Same key + same payload: one persisted group; both callers converge.

    A late loser may briefly see claim busy after the winner adopts
    active_claim → group_id; create_pair_group must replay the stored group
    rather than surfacing a false foreign conflict.
    """
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        key = "g-race-1"
        started = 0
        original_before = pair_svc._before_pair_claim

        async def stagger_second_claimer():
            nonlocal started
            started += 1
            if started > 1:
                # Let the first caller finish adopt before the second claims.
                await asyncio.sleep(0.05)
            return await original_before()

        pair_svc._before_pair_claim = stagger_second_claimer
        try:
            async def attempt():
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

            left, right = await asyncio.gather(attempt(), attempt())
        finally:
            pair_svc._before_pair_claim = original_before

        outcomes = [left, right]
        winners = [
            item for item in outcomes
            if isinstance(item, dict) and item.get("status") == "active"
        ]
        conflicts = [item for item in outcomes if isinstance(item, PairConflictError)]
        assert len(winners) + len(conflicts) == 2
        assert len(winners) >= 1
        group_id = winners[0]["id"]
        assert all(item["id"] == group_id for item in winners)
        assert winners[0].get("members_complete") is True
        assert winners[0].get("active_member_count") == 3
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active"}
        ) == 1
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group_member", "group_id": group_id, "status": "active"}
        ) == 3
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row and row.get("active_claim") == group_id

        # Same-key retry returns the original group (conflicts must converge).
        replay = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key=key,
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert replay["id"] == group_id
        assert replay["status"] == "active"
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


_HOOKS = {
    "after_claim": pair_svc._after_statement_claim,
    "after_pair": pair_svc._after_pair_insert,
    "after_member": pair_svc._after_group_member_insert,
    "after_member_cancel": pair_svc._after_group_member_cancel,
    "after_cancel": pair_svc._after_pair_cancel_stored,
}


def _restore_hooks():
    pair_svc._after_statement_claim = _HOOKS["after_claim"]
    pair_svc._after_pair_insert = _HOOKS["after_pair"]
    pair_svc._after_group_member_insert = _HOOKS["after_member"]
    pair_svc._after_group_member_cancel = _HOOKS["after_member_cancel"]
    pair_svc._after_pair_cancel_stored = _HOOKS["after_cancel"]


async def test_hard_interrupt_after_bank_claims_before_group_orphan_until_same_key_retry():
    """ProcessInterrupt after CAS claims: orphan active_claim, no Exception cleanup."""
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)

        async def hard_stop():
            raise ProcessInterrupt("kill setelah klaim bank")

        pair_svc._after_statement_claim = hard_stop
        try:
            with pytest.raises(ProcessInterrupt):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="group-orphan",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            assert await server.db[RECON_MATCHES].count_documents(
                {"match_kind": "pair_group"}
            ) == 0
            for line_id in bank_ids:
                row = await server.db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "active_claim": 1}
                )
                assert row.get("active_claim") == "group-orphan"

            ws = await pair_workspace(
                server.db,
                cash_bank_id=bank["id"],
                start="2026-09-01",
                end="2026-09-30",
            )
            by_id = {row["id"]: row for row in ws["bank_lines"]}
            for line_id in bank_ids:
                row = by_id[line_id]
                assert row["active_claim"] == "group-orphan"
                assert row["claim_in_flight"] is True
                assert row["matchable"] is False
                assert row.get("claim_id") in (None, "")
            assert ws["unmatched_bank_count"] == 0

            with pytest.raises(PairConflictError):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="group-other",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            for line_id in bank_ids:
                row = await server.db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "active_claim": 1}
                )
                assert row.get("active_claim") == "group-orphan"
        finally:
            _restore_hooks()

        restored = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-orphan",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert restored["status"] == "active"
        assert restored["members_complete"] is True
        assert restored["claim_active"] is True
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active"}
        ) == 1
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group_member", "status": "active"}
        ) == 3


async def test_hard_interrupt_after_group_header_incomplete_members_not_fully_matched():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        seen = {"n": 0}

        async def stop_after_one_member():
            seen["n"] += 1
            if seen["n"] >= 1:
                raise ProcessInterrupt("kill setelah 1 member")

        pair_svc._after_group_member_insert = stop_after_one_member
        try:
            with pytest.raises(ProcessInterrupt):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="group-partial",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            group = await server.db[RECON_MATCHES].find_one(
                {"match_kind": "pair_group", "idempotency_key": "group-partial"},
                {"_id": 0},
            )
            assert group and group["status"] == "active"
            assert group.get("journal_line_id") == book_line
            member_n = await server.db[RECON_MATCHES].count_documents(
                {"match_kind": "pair_group_member", "group_id": group["id"], "status": "active"}
            )
            assert member_n == 1
            for line_id in bank_ids:
                row = await server.db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "active_claim": 1}
                )
                assert row.get("active_claim") == "group-partial"

            public = await pair_workspace(
                server.db,
                cash_bank_id=bank["id"],
                start="2026-09-01",
                end="2026-09-30",
            )
            matched = [m for m in public["matched_pairs"] if m["id"] == group["id"]][0]
            assert matched["status"] == "active"
            assert matched["members_complete"] is False
            assert matched["claim_active"] is False
            assert matched["active_member_count"] == 1

            owner = await get_owner_recon_view(
                server.db,
                cash_bank_id=bank["id"],
                start_date="2026-09-01",
                end_date="2026-09-30",
            )
            group_rows = [r for r in owner["items"] if str(r.get("id") or "").startswith("group:")]
            assert len(group_rows) == 1
            assert group_rows[0]["status"] == STATUS_REVIEW_CANON
            assert group_rows[0]["status"] != STATUS_SESUAI
        finally:
            _restore_hooks()

        # Same payload with a new create key still repairs incomplete members.
        healed = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-partial-other",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert healed["id"] == group["id"]
        assert healed["members_complete"] is True
        assert healed["claim_active"] is True
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group_member", "group_id": group["id"], "status": "active"}
        ) == 3
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row.get("active_claim") == group["id"]
        owner2 = await get_owner_recon_view(
            server.db,
            cash_bank_id=bank["id"],
            start_date="2026-09-01",
            end_date="2026-09-30",
        )
        row = next(r for r in owner2["items"] if str(r.get("id") or "").startswith("group:"))
        assert row["status"] == STATUS_SESUAI


async def test_hard_interrupt_mid_cancel_keeps_claims_until_retry():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-cancel-mid",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        group_id = created["id"]
        seen = {"n": 0}

        async def stop_after_one_cancel():
            seen["n"] += 1
            if seen["n"] >= 1:
                raise ProcessInterrupt("kill mid cancel")

        pair_svc._after_group_member_cancel = stop_after_one_cancel
        try:
            with pytest.raises(ProcessInterrupt):
                await cancel_pair_group(
                    server.db,
                    group_id,
                    idempotency_key="cancel-mid-1",
                    reason="batal sebagian",
                    user=admin,
                )
            header = await server.db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
            assert header["status"] == "active"
            assert header.get("journal_line_id") == book_line
            active_members = await server.db[RECON_MATCHES].count_documents(
                {"match_kind": "pair_group_member", "group_id": group_id, "status": "active"}
            )
            cancelled_members = await server.db[RECON_MATCHES].count_documents(
                {"match_kind": "pair_group_member", "group_id": group_id, "status": "cancelled"}
            )
            assert active_members == 2
            assert cancelled_members == 1
            for line_id in bank_ids:
                row = await server.db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "active_claim": 1}
                )
                assert row.get("active_claim") in (group_id, "group-cancel-mid")

            # Same payload create finds the still-active header; must not open a
            # second group and must not resurrect the cancelled member.
            stolen = await create_pair_group(
                server.db,
                cash_bank_id=bank["id"],
                journal_line_id=book_line,
                statement_line_ids=bank_ids,
                idempotency_key="steal-during-cancel",
                start="2026-09-01",
                end="2026-09-30",
                user=admin,
            )
            assert stolen["id"] == group_id
            assert stolen["members_complete"] is False
            assert await server.db[RECON_MATCHES].count_documents(
                {"match_kind": "pair_group", "status": "active"}
            ) == 1
            assert await server.db[RECON_MATCHES].count_documents(
                {
                    "match_kind": "pair_group_member",
                    "group_id": group_id,
                    "status": "cancelled",
                }
            ) == 1
        finally:
            _restore_hooks()

        finished = await cancel_pair_group(
            server.db,
            group_id,
            idempotency_key="cancel-mid-1",
            reason="batal sebagian",
            user=admin,
        )
        assert finished["status"] == "cancelled"
        assert finished.get("history_journal_line_id") == book_line
        stored = await server.db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
        assert stored["status"] == "cancelled"
        assert stored.get("journal_line_id") in (None, "")
        assert stored.get("history_journal_line_id") == book_line
        for line_id in bank_ids:
            row = await server.db[LINES].find_one(
                {"id": line_id}, {"_id": 0, "active_claim": 1}
            )
            assert row.get("active_claim") in (None, "")


async def test_concurrent_create_vs_cancel_one_effective_no_double_match():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-vs-cancel",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )

        async def do_cancel():
            try:
                return await cancel_pair_group(
                    server.db,
                    created["id"],
                    idempotency_key="cancel-race",
                    reason="race cancel",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        async def do_create():
            try:
                return await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="create-race",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            except PairConflictError as exc:
                return exc

        left, right = await asyncio.gather(do_cancel(), do_create())
        outcomes = [left, right]
        active = await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active", "journal_line_id": book_line}
        )
        # At most one active group may own the journal line.
        assert active <= 1
        assert await server.db[RECON_MATCHES].count_documents(
            {"journal_line_id": book_line}
        ) <= 1
        cancelled = await server.db[RECON_MATCHES].find_one(
            {"id": created["id"]}, {"_id": 0, "status": 1, "history_journal_line_id": 1}
        )
        if cancelled and cancelled.get("status") == "cancelled":
            assert cancelled.get("history_journal_line_id") == book_line
        # One of cancel success / create conflict / create after cancel is acceptable;
        # double-active is not.
        assert not (
            isinstance(left, dict)
            and left.get("status") == "active"
            and isinstance(right, dict)
            and right.get("status") == "active"
            and left.get("id") != right.get("id")
        )
        del outcomes


async def test_same_idempotency_key_different_payload_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        first = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-payload",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert first["status"] == "active"
        # Different member set (drop one id) with same key must conflict.
        with pytest.raises(PairConflictError):
            await create_pair_group(
                server.db,
                cash_bank_id=bank["id"],
                journal_line_id=book_line,
                statement_line_ids=bank_ids[:2],
                idempotency_key="group-payload",
                start="2026-09-01",
                end="2026-09-30",
                user=admin,
            )
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "idempotency_key": "group-payload"}
        ) == 1
        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active"}
        ) == 1


async def test_workspace_inflight_claim_not_counted_as_unmatched():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)

        async def hard_stop():
            raise ProcessInterrupt("inflight workspace")

        pair_svc._after_statement_claim = hard_stop
        try:
            with pytest.raises(ProcessInterrupt):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="inflight-ws",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
            ws = await c.get(
                "/api/cash-bank/reconciliations/pairs/workspace",
                params={"cash_bank_id": bank["id"], **PERIOD},
            )
            assert ws.status_code == 200, ws.text
            body = ws.json()
            assert body["unmatched_bank_count"] == 0
            held = [row for row in body["bank_lines"] if row["id"] in bank_ids]
            assert len(held) == 3
            assert all(row["claim_in_flight"] for row in held)
            assert all(row["active_claim"] == "inflight-ws" for row in held)
            assert all(not row["matchable"] for row in held)
        finally:
            _restore_hooks()


async def test_recover_unadopted_pair_via_create_same_payload():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 1000000, desc="Terima 1jt")
        _preview, imported = await _import(
            c,
            bank["id"],
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-01,Mutasi 1jt,1000000,,REF-P1,TX-P1,\n",
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        bank_id = body["bank_lines"][0]["id"]
        book_line = body["book_lines"][0]["journal_line_id"]
        journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
        receipts_before = await server.db[RECEIPTS].count_documents({})

        created = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=bank_id,
            journal_line_id=book_line,
            idempotency_key="pair-unadopted",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        await server.db[LINES].update_one(
            {"id": bank_id},
            {"$set": {"active_claim": "pair-unadopted"}},
        )
        line = await server.db[LINES].find_one({"id": bank_id}, {"_id": 0, "active_claim": 1})
        assert line.get("active_claim") == "pair-unadopted"

        healed = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=bank_id,
            journal_line_id=book_line,
            idempotency_key="pair-unadopted-new-key",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert healed["id"] == created["id"]
        assert healed["claim_active"] is True
        line2 = await server.db[LINES].find_one({"id": bank_id}, {"_id": 0, "active_claim": 1})
        assert line2.get("active_claim") == created["id"]
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
        assert await server.db[RECEIPTS].count_documents({}) == receipts_before


async def test_recover_unadopted_group_via_create_same_payload():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-unadopted",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        for line_id in bank_ids:
            await server.db[LINES].update_one(
                {"id": line_id},
                {"$set": {"active_claim": "group-unadopted"}},
            )
        healed = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-unadopted-new",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        assert healed["id"] == created["id"]
        assert healed["members_complete"] is True
        assert healed["claim_active"] is True
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row.get("active_claim") == created["id"]


async def test_cancelled_group_leftover_claim_cleared_with_new_cancel_key():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-cancel-left",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        group_id = created["id"]
        # Simulate crashed cancel: header cancelled, claims still held.
        await server.db[RECON_MATCHES].update_one(
            {"id": group_id},
            {
                "$set": {
                    "status": "cancelled",
                    "history_journal_line_id": book_line,
                    "history_statement_line_ids": bank_ids,
                    "cancel_reason": "partial crash",
                    "review_state": "riwayat",
                },
                "$unset": {"journal_line_id": ""},
            },
        )
        for member in await server.db[RECON_MATCHES].find(
            {"match_kind": "pair_group_member", "group_id": group_id},
            {"_id": 0},
        ).to_list(20):
            sid = member.get("statement_line_id")
            await server.db[RECON_MATCHES].update_one(
                {"id": member["id"]},
                {
                    "$set": {
                        "status": "cancelled",
                        "history_statement_line_id": sid,
                    },
                    "$unset": {"statement_line_id": ""},
                },
            )
        for line_id in bank_ids:
            await server.db[LINES].update_one(
                {"id": line_id},
                {"$set": {"active_claim": group_id}},
            )

        foreign_id = bank_ids[0]
        # Plant a foreign token on a different line shape: keep one line import-hold.
        hold = f"import-hold:batch-x:{bank_ids[1]}"
        await server.db[LINES].update_one(
            {"id": bank_ids[1]},
            {"$set": {"active_claim": hold}},
        )
        # Foreign create-key on another account line stays put — use bank_ids[2] as owned leftover.
        await server.db[LINES].update_one(
            {"id": bank_ids[2]},
            {"$set": {"active_claim": "someone-else"}},
        )
        await server.db[LINES].update_one(
            {"id": foreign_id},
            {"$set": {"active_claim": group_id}},
        )

        finished = await cancel_pair_group(
            server.db,
            group_id,
            idempotency_key="cancel-left-NEW",
            reason="ulangi batal setelah crash",
            user=admin,
        )
        assert finished["status"] == "cancelled"
        assert finished.get("history_journal_line_id") == book_line
        row0 = await server.db[LINES].find_one({"id": foreign_id}, {"_id": 0, "active_claim": 1})
        assert row0.get("active_claim") in (None, "")
        row1 = await server.db[LINES].find_one({"id": bank_ids[1]}, {"_id": 0, "active_claim": 1})
        assert row1.get("active_claim") == hold
        row2 = await server.db[LINES].find_one({"id": bank_ids[2]}, {"_id": 0, "active_claim": 1})
        assert row2.get("active_claim") == "someone-else"


async def test_cancelled_pair_leftover_claim_cleared_with_new_cancel_key():
    admin = _user()
    async with await client_for(admin) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 500000, desc="Terima 500")
        _preview, imported = await _import(
            c,
            bank["id"],
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-02,Mutasi 500,500000,,REF-PC,TX-PC,\n",
        )
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        bank_id = body["bank_lines"][0]["id"]
        book_line = body["book_lines"][0]["journal_line_id"]
        created = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=bank_id,
            journal_line_id=book_line,
            idempotency_key="pair-cancel-left",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        await server.db[RECON_MATCHES].update_one(
            {"id": created["id"]},
            {
                "$set": {
                    "status": "cancelled",
                    "history_journal_line_id": book_line,
                    "history_statement_line_id": bank_id,
                    "cancel_reason": "crash",
                    "review_state": "riwayat",
                },
                "$unset": {"journal_line_id": "", "statement_line_id": ""},
            },
        )
        await server.db[LINES].update_one(
            {"id": bank_id},
            {"$set": {"active_claim": created["id"]}},
        )
        finished = await cancel_pair(
            server.db,
            created["id"],
            idempotency_key="cancel-pair-NEW",
            reason="ulangi batal pair",
            user=admin,
        )
        assert finished["status"] == "cancelled"
        line = await server.db[LINES].find_one({"id": bank_id}, {"_id": 0, "active_claim": 1})
        assert line.get("active_claim") in (None, "")


async def test_ensure_stops_when_group_cancelled_no_member_resurrection():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        created = await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book_line,
            statement_line_ids=bank_ids,
            idempotency_key="group-no-resurrect",
            start="2026-09-01",
            end="2026-09-30",
            user=admin,
        )
        group_id = created["id"]
        members = await server.db[RECON_MATCHES].find(
            {"match_kind": "pair_group_member", "group_id": group_id},
            {"_id": 0},
        ).to_list(20)
        # Cancel one member + header while leaving snapshots intact.
        victim = members[0]
        sid = victim["statement_line_id"]
        await server.db[RECON_MATCHES].update_one(
            {"id": victim["id"]},
            {
                "$set": {
                    "status": "cancelled",
                    "history_statement_line_id": sid,
                },
                "$unset": {"statement_line_id": ""},
            },
        )
        await server.db[RECON_MATCHES].update_one(
            {"id": group_id},
            {
                "$set": {
                    "status": "cancelled",
                    "history_journal_line_id": book_line,
                    "history_statement_line_ids": bank_ids,
                },
                "$unset": {"journal_line_id": ""},
            },
        )
        before = await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group_member", "group_id": group_id}
        )
        # Repair path must not resurrect cancelled member or re-activate header.
        await pair_svc._ensure_group_members(
            server.db,
            {
                "id": group_id,
                "status": "active",  # stale caller view
                "statement_line_ids": bank_ids,
                "member_snapshots": created.get("members") or [],
            },
        )
        # Use real snapshots from stored header
        header = await server.db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
        await pair_svc._ensure_group_members(
            server.db,
            {**header, "status": "active"},
        )
        after = await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group_member", "group_id": group_id}
        )
        assert after == before
        active_members = await server.db[RECON_MATCHES].count_documents(
            {
                "match_kind": "pair_group_member",
                "group_id": group_id,
                "status": "active",
                "statement_line_id": sid,
            }
        )
        assert active_members == 0
        header2 = await server.db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
        assert header2["status"] == "cancelled"


async def test_concurrent_cancel_vs_member_repair():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)
        seen = {"n": 0}

        async def stop_after_one_member():
            seen["n"] += 1
            if seen["n"] >= 1:
                raise ProcessInterrupt("partial for concurrency")

        pair_svc._after_group_member_insert = stop_after_one_member
        try:
            with pytest.raises(ProcessInterrupt):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="group-race-repair",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
        finally:
            _restore_hooks()

        group = await server.db[RECON_MATCHES].find_one(
            {"match_kind": "pair_group", "idempotency_key": "group-race-repair"},
            {"_id": 0},
        )
        assert group and group["status"] == "active"

        async def repair():
            return await create_pair_group(
                server.db,
                cash_bank_id=bank["id"],
                journal_line_id=book_line,
                statement_line_ids=bank_ids,
                idempotency_key="group-race-repair-b",
                start="2026-09-01",
                end="2026-09-30",
                user=admin,
            )

        async def cancel():
            return await cancel_pair_group(
                server.db,
                group["id"],
                idempotency_key="cancel-race-repair",
                reason="batal saat repair",
                user=admin,
            )

        results = await asyncio.gather(repair(), cancel(), return_exceptions=True)
        assert not any(isinstance(r, BaseException) and not isinstance(r, Exception) for r in results)
        # One effective outcome: cancelled or fully active — never double-active groups.
        active_groups = await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "status": "active", "journal_line_id": book_line}
        )
        cancelled = await server.db[RECON_MATCHES].find_one({"id": group["id"]}, {"_id": 0})
        assert active_groups in (0, 1)
        if cancelled.get("status") == "cancelled":
            assert active_groups == 0
            # Cancel replay scrubs members inserted by a racing repair after the
            # first cancel loop (recovery C). New cancel-key is allowed.
            await cancel_pair_group(
                server.db,
                group["id"],
                idempotency_key="cancel-race-repair-scrub",
                reason="bersihkan sisa member race",
                user=admin,
            )
            assert await server.db[RECON_MATCHES].count_documents(
                {
                    "match_kind": "pair_group_member",
                    "group_id": group["id"],
                    "status": "active",
                    "statement_line_id": {"$type": "string"},
                }
            ) == 0
        else:
            public = next(r for r in results if isinstance(r, dict) and r.get("status") == "active")
            assert public.get("members_complete") is True


async def test_true_orphan_remains_blocked_without_auto_release():
    admin = _user()
    async with await client_for(admin) as c:
        bank, book_line, bank_ids = await _three_bank_one_journal(c)

        async def hard_stop():
            raise ProcessInterrupt("true orphan")

        pair_svc._after_statement_claim = hard_stop
        try:
            with pytest.raises(ProcessInterrupt):
                await create_pair_group(
                    server.db,
                    cash_bank_id=bank["id"],
                    journal_line_id=book_line,
                    statement_line_ids=bank_ids,
                    idempotency_key="orphan-stay",
                    start="2026-09-01",
                    end="2026-09-30",
                    user=admin,
                )
        finally:
            _restore_hooks()

        assert await server.db[RECON_MATCHES].count_documents(
            {"match_kind": "pair_group", "idempotency_key": "orphan-stay"}
        ) == 0
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row.get("active_claim") == "orphan-stay"

        # Cancel paths do not apply (no match id). Different create key stays blocked.
        with pytest.raises(PairConflictError):
            await create_pair_group(
                server.db,
                cash_bank_id=bank["id"],
                journal_line_id=book_line,
                statement_line_ids=bank_ids,
                idempotency_key="orphan-other",
                start="2026-09-01",
                end="2026-09-30",
                user=admin,
            )
        for line_id in bank_ids:
            row = await server.db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
            assert row.get("active_claim") == "orphan-stay"

        ws = await pair_workspace(
            server.db,
            cash_bank_id=bank["id"],
            start="2026-09-01",
            end="2026-09-30",
        )
        assert ws["unmatched_bank_count"] == 0
        held = [row for row in ws["bank_lines"] if row["id"] in bank_ids]
        assert all(row["claim_in_flight"] for row in held)
