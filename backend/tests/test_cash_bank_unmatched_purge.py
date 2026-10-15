"""Hapus Belum Cocok — purge unmatched statement lines + same-file gap-fill."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
PURGE_DB = f"recon_purge_isolated_{_worker}_{uuid.uuid4().hex[:6]}"
os.environ["DB_NAME"] = PURGE_DB
_TEST_JWT = "test-secret-key-12345678901234567890"
if len(os.environ.get("JWT_SECRET") or "") < 16:
    os.environ["JWT_SECRET"] = _TEST_JWT
else:
    os.environ.setdefault("JWT_SECRET", _TEST_JWT)
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_pair_match import create_pair, create_pair_group
from services.cash_bank_reconciliation import RECON_MATCHES, RECONCILIATIONS, ensure_indexes as ensure_recon
from services.cash_bank_statement import IMPORT_FENCES, IMPORTS, LINES, PREVIEWS, ensure_indexes as ensure_statement
from services.cash_bank_statement_purge import PURGES, ensure_purge_indexes
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from tests.test_cash_bank_statement_match import (  # noqa: E402
    PERIOD,
    _import,
    _mapping,
    _receipt,
    _setup,
    _user,
    client_for,
)

pytestmark = pytest.mark.anyio

FILE_ABCD = (
    "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
    "2026-09-01,Mutasi A,100000,,REF-A,TX-A,\n"
    "2026-09-02,Mutasi B,200000,,REF-B,TX-B,\n"
    "2026-09-03,Mutasi C,300000,,REF-C,TX-C,\n"
    "2026-09-04,Mutasi D,400000,,REF-D,TX-D,\n"
)
AUTH_MAP = _mapping(bank_txn_id_authoritative=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = PURGE_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[PURGE_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    await ensure_statement(db)
    await ensure_purge_indexes(db)
    names = (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, RECONCILIATIONS, RECON_MATCHES,
        IMPORTS, LINES, PREVIEWS, PURGES, IMPORT_FENCES,
        "cash_bank_manual_statement_balances",
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


def _director(**kw):
    return _user(role="direktur_utama", **kw)


def _holding(**kw):
    return _user(role="holding_team", **kw)


async def _seed_abcd(c):
    bank, income = await _setup(c, currency="IDR")
    await _receipt(c, bank["id"], income["id"], 300000, date="2026-09-01", desc="Gabungan A+B")
    _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
    assert imported.status_code == 201, imported.text
    ws = await c.get(
        "/api/cash-bank/reconciliations/pairs/workspace",
        params={"cash_bank_id": bank["id"], **PERIOD},
    )
    assert ws.status_code == 200, ws.text
    body = ws.json()
    by_desc = {row["description"]: row for row in body["bank_lines"]}
    book = body["book_lines"][0]["journal_line_id"]
    return bank, book, by_desc


async def test_rbac_director_allowed_holding_allowed_others_denied():
    director = _director()
    holding = _holding()
    admin = _user(role="admin")
    operator = _user(role="operator", perms=["cash_bank.view", "cash_bank.reconcile"])
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        params = {"cash_bank_id": bank["id"], **PERIOD}
        prev = await c.get("/api/cash-bank/reconciliations/unmatched-purge/preview", params=params)
        assert prev.status_code == 200, prev.text
    async with await client_for(holding) as c:
        prev = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert prev.status_code == 200, prev.text
    async with await client_for(admin) as c:
        denied = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert denied.status_code == 403
    async with await client_for(operator) as c:
        denied = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert denied.status_code == 403


async def test_rbac_director_super_admin_preview_execute_data_effect():
    """Direktur + is_super_admin retains purge; admin SA without override denied."""
    from server import user_permissions

    purge_key = "cash_bank.reconcile.purge_unmatched"
    director_sa = _director()
    director_sa["email"] = "dir.sa.purge@example.com"
    admin_sa = _user(role="admin")
    admin_sa["email"] = "admin.sa.purge@example.com"
    holding = _holding()
    holding["email"] = "hold.purge@example.com"
    operator = _user(role="operator", perms=["cash_bank.view", "cash_bank.reconcile"])
    operator["email"] = "op.purge@example.com"

    await server.db.users.insert_one(
        {
            "id": director_sa["id"],
            "email": director_sa["email"],
            "role": "direktur_utama",
            "status": "active",
            "is_super_admin": True,
            "permissions_override": {},
        }
    )
    await server.db.users.insert_one(
        {
            "id": admin_sa["id"],
            "email": admin_sa["email"],
            "role": "admin",
            "status": "active",
            "is_super_admin": True,
            "permissions_override": {},
        }
    )
    await server.db.users.insert_one(
        {
            "id": holding["id"],
            "email": holding["email"],
            "role": "holding_team",
            "status": "active",
            "is_super_admin": False,
            "permissions_override": {},
        }
    )

    before_dir_sa = await user_permissions(director_sa)
    before_admin_sa = await user_permissions(admin_sa)
    before_holding = await user_permissions(holding)
    before_operator = await user_permissions(operator)
    assert purge_key in before_dir_sa
    assert purge_key not in before_admin_sa
    assert purge_key in before_holding
    assert purge_key not in before_operator

    async with await client_for(director_sa) as c:
        bank, _book, by_desc = await _seed_abcd(c)
        params = {"cash_bank_id": bank["id"], **PERIOD}
        me = await c.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert purge_key in me.json()["permissions"]
        prev = await c.get("/api/cash-bank/reconciliations/unmatched-purge/preview", params=params)
        assert prev.status_code == 200, prev.text
        assert prev.json()["eligible_count"] == 4
        lines_before = await server.db[LINES].count_documents({})
        journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
        key = f"purge-sa-{uuid.uuid4().hex}"
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": key,
            },
        )
        assert purged.status_code == 200, purged.text
        body = purged.json()
        assert body["status"] == "completed"
        assert body["deleted_count"] == 4
        assert await server.db[LINES].count_documents({}) == lines_before - 4
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
        for desc in ("Mutasi A", "Mutasi B", "Mutasi C", "Mutasi D"):
            assert await server.db[LINES].find_one({"id": by_desc[desc]["id"]}) is None

    after_dir_sa = await user_permissions(director_sa)
    after_admin_sa = await user_permissions(admin_sa)
    after_holding = await user_permissions(holding)
    after_operator = await user_permissions(operator)
    assert (purge_key in after_dir_sa) == (purge_key in before_dir_sa) == True
    assert (purge_key in after_admin_sa) == (purge_key in before_admin_sa) == False
    assert (purge_key in after_holding) == (purge_key in before_holding) == True
    assert (purge_key in after_operator) == (purge_key in before_operator) == False

    async with await client_for(admin_sa) as c:
        denied = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert denied.status_code == 403
        denied_x = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"admin-deny-{uuid.uuid4().hex}",
            },
        )
        assert denied_x.status_code == 403

    async with await client_for(holding) as c:
        # Fresh unmatched rows for holding preview on same bank period (empty after purge)
        prev_h = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert prev_h.status_code == 200, prev_h.text

    async with await client_for(operator) as c:
        denied_op = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert denied_op.status_code == 403


async def test_preview_zero_write_and_counts():
    director = _director()
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        before_lines = await server.db[LINES].count_documents({})
        before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
        prev = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert prev.status_code == 200, prev.text
        body = prev.json()
        assert body["zero_write"] is True
        assert body["eligible_count"] == 4
        assert body["protected_count"] == 0
        assert await server.db[LINES].count_documents({}) == before_lines
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals


async def test_purge_1to1_protected_unmatched_deleted_gap_fill():
    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-09-01", desc="Rcv A")
        await _receipt(c, bank["id"], income["id"], 200000, date="2026-09-02", desc="Rcv B")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        batch_id = imported.json()["id"]
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        by_desc = {row["description"]: row for row in body["bank_lines"]}
        books = {row["description"]: row["journal_line_id"] for row in body["book_lines"]}
        await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi A"]["id"],
            journal_line_id=books["Rcv A"],
            idempotency_key="pair-A",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi B"]["id"],
            journal_line_id=books["Rcv B"],
            idempotency_key="pair-B",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
        matches_before = await server.db[RECON_MATCHES].count_documents({"status": "active"})
        key = f"purge-{uuid.uuid4().hex}"
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": key,
            },
        )
        assert purged.status_code == 200, purged.text
        result = purged.json()
        assert result["deleted_count"] == 2
        assert result["status"] == "completed"
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi A"]["id"]})
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi B"]["id"]})
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi C"]["id"]}) is None
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi D"]["id"]}) is None
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
        assert await server.db[RECON_MATCHES].count_documents({"status": "active"}) == matches_before
        batch = await server.db[IMPORTS].find_one({"id": batch_id}, {"_id": 0})
        assert batch["allows_unmatched_gap_fill"] is True

        # Retry same key — idempotent
        again = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": key,
            },
        )
        assert again.status_code == 200
        assert again.json()["deleted_count"] == 2

        # Gap-fill reimport same file
        _p2, reimported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert reimported.status_code == 201, reimported.text
        body2 = reimported.json()
        assert body2.get("gap_fill") is True or body2.get("new_count", 0) >= 2
        assert body2["id"] == batch_id
        lines = await server.db[LINES].find(
            {"import_id": batch_id, "sealed": {"$ne": False}},
            {"_id": 0, "description": 1, "authoritative_txn_id": 1},
        ).to_list(20)
        descs = {row["description"] for row in lines}
        assert descs == {"Mutasi A", "Mutasi B", "Mutasi C", "Mutasi D"}
        assert await server.db[RECON_MATCHES].count_documents({"status": "active"}) == matches_before

        # Repeat reimport — no duplicates
        _p3, third = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert third.status_code == 201, third.text
        assert await server.db[LINES].count_documents({"import_id": batch_id, "sealed": {"$ne": False}}) == 4


async def test_purge_protects_pair_group_members():
    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 700000, date="2026-09-03", desc="Group CD")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        body = ws.json()
        by_desc = {row["description"]: row for row in body["bank_lines"]}
        book = body["book_lines"][0]["journal_line_id"]
        await create_pair_group(
            server.db,
            cash_bank_id=bank["id"],
            journal_line_id=book,
            statement_line_ids=[by_desc["Mutasi C"]["id"], by_desc["Mutasi D"]["id"]],
            idempotency_key="group-cd",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"pg-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["deleted_count"] == 2  # A,B only
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi C"]["id"]})
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi D"]["id"]})


async def test_active_claim_and_claim_slot_protected():
    director = _director()
    async with await client_for(director) as c:
        bank, _book, rows = await _seed_abcd(c)
        await server.db[LINES].update_one(
            {"id": rows["Mutasi C"]["id"]},
            {"$set": {"active_claim": "inflight-orphan", "claim_slot": "slot-1"}},
        )
        prev = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert prev.status_code == 200
        assert prev.json()["eligible_count"] == 3  # A,B,D — C protected
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"claim-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        assert await server.db[LINES].find_one({"id": rows["Mutasi C"]["id"]})


async def test_duplicate_review_protected():
    director = _director()
    async with await client_for(director) as c:
        bank, _book, rows = await _seed_abcd(c)
        await server.db[LINES].update_one(
            {"id": rows["Mutasi D"]["id"]},
            {"$set": {"duplicate_status": "pending_review"}},
        )
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"dup-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        assert await server.db[LINES].find_one({"id": rows["Mutasi D"]["id"]})


async def test_cross_account_and_period_isolation():
    director = _director()
    async with await client_for(director) as c:
        bank_a, _b, rows_a = await _seed_abcd(c)
        bank_b, income = await _setup(c, currency="IDR")
        await _receipt(c, bank_b["id"], income["id"], 100000)
        _p, imported = await _import(c, bank_b["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        lines_b_before = await server.db[LINES].count_documents({"cash_bank_id": bank_b["id"]})
        # Period that only includes A (2026-09-01)
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank_a["id"],
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "idempotency_key": f"period-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["deleted_count"] == 1
        assert await server.db[LINES].find_one({"id": rows_a["Mutasi A"]["id"]}) is None
        assert await server.db[LINES].find_one({"id": rows_a["Mutasi B"]["id"]})
        assert await server.db[LINES].count_documents({"cash_bank_id": bank_b["id"]}) == lines_b_before


async def test_import_fence_conflict():
    director = _director()
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        await server.db[IMPORT_FENCES].insert_one({
            "cash_bank_id": bank["id"],
            "holder": "other-import",
            "operation_id": "other-import",
            "batch_id": None,
            "file_hash": "busy",
            "lease_until": "2099-01-01T00:00:00+00:00",
            "at": "2099-01-01T00:00:00+00:00",
        })
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"fence-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 409


async def test_cancelled_history_survives_purge():
    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-09-01", desc="Rcv")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        by_desc = {row["description"]: row for row in ws.json()["bank_lines"]}
        book = ws.json()["book_lines"][0]["journal_line_id"]
        created = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi A"]["id"],
            journal_line_id=book,
            idempotency_key="hist-pair",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        cancel = await c.post(
            f"/api/cash-bank/reconciliations/pairs/{created['id']}/cancel",
            json={"idempotency_key": "cancel-hist", "reason": "salah cocok"},
        )
        assert cancel.status_code == 200, cancel.text
        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"hist-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        hist = await server.db[RECON_MATCHES].find_one({"id": created["id"]}, {"_id": 0})
        assert hist["status"] == "cancelled"
        assert hist.get("history_statement_line_id") == by_desc["Mutasi A"]["id"]


async def test_owner_view_still_readable_after_purge():
    director = _director()
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": PERIOD["start_date"],
                "end_date": PERIOD["end_date"],
                "idempotency_key": f"ov-{uuid.uuid4().hex}",
            },
        )
        ov = await c.get(
            "/api/cash-bank/reconciliation/owner-view",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert ov.status_code == 200, ov.text


async def test_match_vs_purge_race_guarded_delete_preserves_claim():
    """After classify snapshot, claim+match appear before delete — must not delete or orphan."""
    from unittest.mock import patch
    from services import cash_bank_statement_purge as purge_svc

    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-09-01", desc="Rcv A")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        by_desc = {row["description"]: row for row in ws.json()["bank_lines"]}
        book = ws.json()["book_lines"][0]["journal_line_id"]
        race_line = by_desc["Mutasi C"]["id"]
        claim_token = f"race-{uuid.uuid4().hex}"
        match_id = str(uuid.uuid4())
        injected = {"done": False}
        real_classify = purge_svc._classify_lines

        async def classify_then_claim(db, **kwargs):
            out = await real_classify(db, **kwargs)
            assert race_line in out["eligible_ids"]
            await db[LINES].update_one(
                {"id": race_line},
                {"$set": {"active_claim": claim_token, "claim_slot": "slot-race"}},
            )
            await db[RECON_MATCHES].insert_one({
                "id": match_id,
                "match_kind": "pair",
                "status": "active",
                "cash_bank_id": bank["id"],
                "statement_line_id": race_line,
                "journal_line_id": book,
                "idempotency_key": f"race-pair-{uuid.uuid4().hex}",
                "created_at": "2026-09-01T00:00:00+00:00",
            })
            injected["done"] = True
            return out

        with patch.object(purge_svc, "_classify_lines", side_effect=classify_then_claim):
            result = await purge_svc.execute_unmatched_purge(
                server.db,
                cash_bank_id=bank["id"],
                start_date=PERIOD["start_date"],
                end_date=PERIOD["end_date"],
                idempotency_key=f"race-{uuid.uuid4().hex}",
                user=director,
            )
        surviving = await server.db[LINES].find_one({"id": race_line}, {"_id": 0})
        assert injected["done"] is True
        assert surviving is not None, "claimed/matched line must survive purge race"
        assert surviving.get("active_claim") == claim_token
        match = await server.db[RECON_MATCHES].find_one({"id": match_id}, {"_id": 0})
        assert match is not None and match.get("status") == "active"
        assert match.get("statement_line_id") == race_line
        assert result["status"] == "completed"
        assert result["conflicted_count"] >= 1
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi A"]["id"]}) is None
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi B"]["id"]}) is None
        assert await server.db[LINES].find_one({"id": by_desc["Mutasi D"]["id"]}) is None

        # Atomic filter proof: claim-present row cannot be deleted by purge filter.
        claim_clause = purge_svc._claim_absent_clause()
        refused = await server.db[LINES].delete_one({
            "id": race_line,
            "cash_bank_id": bank["id"],
            "$and": claim_clause["$and"],
        })
        assert refused.deleted_count == 0
        assert await server.db[LINES].find_one({"id": race_line}) is not None


async def test_crash_retry_partial_idempotency_and_payload_conflict():
    """Crash after deletes before progress write: resume reconciles cumulative totals."""
    from services.cash_bank_statement_purge import (
        STATUS_PENDING,
        execute_unmatched_purge,
        PURGES,
        _payload_hash,
    )

    director = _director()
    async with await client_for(director) as c:
        bank, _book, rows = await _seed_abcd(c)
        key = f"crash-{uuid.uuid4().hex}"
        id_a, id_b = rows["Mutasi A"]["id"], rows["Mutasi B"]["id"]
        id_c, id_d = rows["Mutasi C"]["id"], rows["Mutasi D"]["id"]
        op_id = str(uuid.uuid4())
        # Snapshot persisted (pre-delete evidence); A/B deleted without progress write.
        await server.db[PURGES].insert_one({
            "id": op_id,
            "idempotency_key": key,
            "payload_hash": _payload_hash(bank["id"], PERIOD["start_date"], PERIOD["end_date"]),
            "cash_bank_id": bank["id"],
            "cash_bank_name": "Bank Uji",
            "start_date": PERIOD["start_date"],
            "end_date": PERIOD["end_date"],
            "status": STATUS_PENDING,
            "requested_at": "2026-09-01T00:00:00+00:00",
            "committed_at": None,
            "actor": {"id": director["id"]},
            "role": "direktur_utama",
            "eligible_snapshot_ids": [id_a, id_b, id_c, id_d],
            "eligible_count": 4,
            "deleted_count": 0,
            "protected_count": 0,
            "ambiguous_count": 0,
            "skipped_count": 0,
            "conflicted_count": 0,
            "deleted_line_ids": [],
            "gap_fill_import_ids": [],
            "snapshot_import_ids": [],
            "message": "",
        })
        await server.db[LINES].delete_one({"id": id_a})
        await server.db[LINES].delete_one({"id": id_b})
        await server.db[LINES].update_one(
            {"id": id_c},
            {"$set": {"active_claim": "keep-me"}},
        )

        resumed = await execute_unmatched_purge(
            server.db,
            cash_bank_id=bank["id"],
            start_date=PERIOD["start_date"],
            end_date=PERIOD["end_date"],
            idempotency_key=key,
            user=director,
        )
        assert resumed["status"] == "completed"
        assert resumed["id"] == op_id
        assert await server.db[LINES].find_one({"id": id_c})
        assert await server.db[LINES].find_one({"id": id_d}) is None
        audit = await server.db[PURGES].find_one({"id": op_id}, {"_id": 0})
        assert audit["status"] == "completed"
        assert audit.get("committed_at")
        # Cumulative: A+B reconciled + D deleted this wave.
        assert audit["deleted_count"] == 3
        assert set(audit["deleted_line_ids"]) == {id_a, id_b, id_d}

        conflict = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "idempotency_key": key,
            },
        )
        assert conflict.status_code == 409, conflict.text

        again = await execute_unmatched_purge(
            server.db,
            cash_bank_id=bank["id"],
            start_date=PERIOD["start_date"],
            end_date=PERIOD["end_date"],
            idempotency_key=key,
            user=director,
        )
        assert again["status"] == "completed"
        assert again["deleted_count"] == 3


async def test_rbac_execute_director_holding_allow_others_denied():
    director = _director()
    holding = _holding()
    admin = _user(role="admin")
    operator = _user(role="operator", perms=["cash_bank.view", "cash_bank.reconcile"])
    finance = _user(role="finance") if False else _user(
        role="operator", perms=["cash_bank.view", "cash_bank.reconcile", "cash_bank.post"]
    )
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        payload = {
            "cash_bank_id": bank["id"],
            "start_date": PERIOD["start_date"],
            "end_date": PERIOD["end_date"],
            "idempotency_key": f"rbac-dir-{uuid.uuid4().hex}",
        }
        ok = await c.post("/api/cash-bank/reconciliations/unmatched-purge", json=payload)
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "completed"
    # Re-seed for holding execute
    async with await client_for(director) as c:
        bank2, _b, _r = await _seed_abcd(c)
    async with await client_for(holding) as c:
        prev = await c.get(
            "/api/cash-bank/reconciliations/unmatched-purge/preview",
            params={"cash_bank_id": bank2["id"], **PERIOD},
        )
        assert prev.status_code == 200
        hold = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank2["id"],
                **PERIOD,
                "idempotency_key": f"rbac-hold-{uuid.uuid4().hex}",
            },
        )
        assert hold.status_code == 200, hold.text
    for denied_user in (admin, operator, finance):
        async with await client_for(denied_user) as c:
            d_prev = await c.get(
                "/api/cash-bank/reconciliations/unmatched-purge/preview",
                params={"cash_bank_id": bank2["id"], **PERIOD},
            )
            d_exec = await c.post(
                "/api/cash-bank/reconciliations/unmatched-purge",
                json={
                    "cash_bank_id": bank2["id"],
                    **PERIOD,
                    "idempotency_key": f"rbac-deny-{uuid.uuid4().hex}",
                },
            )
            assert d_prev.status_code == 403, (denied_user["role"], d_prev.status_code)
            assert d_exec.status_code == 403, (denied_user["role"], d_exec.status_code)


async def test_data_integrity_gap_fill_ids_and_failed_batch():
    """Journals/vouchers/GL untouched; A/B ids+claims stable; failed batch no silent gap-fill."""
    from services.general_ledger import get_general_ledger

    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-09-01", desc="Rcv A")
        await _receipt(c, bank["id"], income["id"], 200000, date="2026-09-02", desc="Rcv B")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        batch_id = imported.json()["id"]
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        by_desc = {row["description"]: row for row in ws.json()["bank_lines"]}
        books = {row["description"]: row["journal_line_id"] for row in ws.json()["book_lines"]}
        pair_a = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi A"]["id"],
            journal_line_id=books["Rcv A"],
            idempotency_key="int-A",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        pair_b = await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi B"]["id"],
            journal_line_id=books["Rcv B"],
            idempotency_key="int-B",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        id_a = by_desc["Mutasi A"]["id"]
        id_b = by_desc["Mutasi B"]["id"]
        claim_a = (await server.db[LINES].find_one({"id": id_a}))["active_claim"]
        claim_b = (await server.db[LINES].find_one({"id": id_b}))["active_claim"]
        auth_a = (await server.db[LINES].find_one({"id": id_a}))["authoritative_txn_id"]
        je_before = await server.db[JOURNAL_ENTRIES].find({}, {"_id": 0}).to_list(100)
        jl_before = await server.db[JOURNAL_LINES].find({}, {"_id": 0}).to_list(200)
        rc_before = await server.db[RECEIPTS].find({}, {"_id": 0}).to_list(100)
        pay_before = await server.db[PAYMENTS].count_documents({})
        matches_before = await server.db[RECON_MATCHES].find(
            {"status": "active"}, {"_id": 0}
        ).to_list(100)
        coa_id = bank["account_id"]
        gl_before = await get_general_ledger(
            server.db,
            start_date=PERIOD["start_date"],
            end_date=PERIOD["end_date"],
            account_id=coa_id,
        )

        purged = await c.post(
            "/api/cash-bank/reconciliations/unmatched-purge",
            json={
                "cash_bank_id": bank["id"],
                **PERIOD,
                "idempotency_key": f"int-{uuid.uuid4().hex}",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["deleted_count"] == 2

        line_a = await server.db[LINES].find_one({"id": id_a}, {"_id": 0})
        line_b = await server.db[LINES].find_one({"id": id_b}, {"_id": 0})
        assert line_a["id"] == id_a and line_a["active_claim"] == claim_a
        assert line_b["id"] == id_b and line_b["active_claim"] == claim_b
        assert line_a["authoritative_txn_id"] == auth_a
        assert await server.db[JOURNAL_ENTRIES].find({}, {"_id": 0}).to_list(100) == je_before
        assert await server.db[JOURNAL_LINES].find({}, {"_id": 0}).to_list(200) == jl_before
        assert await server.db[RECEIPTS].find({}, {"_id": 0}).to_list(100) == rc_before
        assert await server.db[PAYMENTS].count_documents({}) == pay_before
        assert await server.db[RECON_MATCHES].find(
            {"status": "active"}, {"_id": 0}
        ).to_list(100) == matches_before
        gl_after = await get_general_ledger(
            server.db,
            start_date=PERIOD["start_date"],
            end_date=PERIOD["end_date"],
            account_id=coa_id,
        )
        assert gl_after == gl_before

        _p2, reimp = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert reimp.status_code == 201, reimp.text
        assert reimp.json()["id"] == batch_id
        assert reimp.json().get("gap_fill") is True
        # A/B ids unchanged after gap-fill
        assert (await server.db[LINES].find_one({"id": id_a}))["active_claim"] == claim_a
        assert (await server.db[LINES].find_one({"id": id_b}))["active_claim"] == claim_b
        assert await server.db[LINES].count_documents({"import_id": batch_id}) == 4
        # Authoritative uniqueness: one row per TX-*
        auths = await server.db[LINES].find(
            {"cash_bank_id": bank["id"]},
            {"_id": 0, "authoritative_txn_id": 1, "bank_txn_id": 1},
        ).to_list(20)
        txn_ids = [a.get("bank_txn_id") for a in auths]
        assert sorted(txn_ids) == ["TX-A", "TX-B", "TX-C", "TX-D"]
        assert len(set(txn_ids)) == 4

        _p3, third = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert third.status_code == 201
        assert await server.db[LINES].count_documents({"import_id": batch_id}) == 4

        # Failed batch for a different file must not silently create rows via gap-fill path.
        other = (
            "tanggal,uraian,masuk,keluar,referensi,id,saldo\n"
            "2026-09-05,Mutasi E,50000,,REF-E,TX-E,\n"
        )
        _p4, failed_imp = await _import(c, bank["id"], other, AUTH_MAP)
        # Force failed status on that batch
        if failed_imp.status_code == 201:
            fid = failed_imp.json()["id"]
            await server.db[IMPORTS].update_one({"id": fid}, {"$set": {"status": "failed"}})
            # delete its lines so reimport would want to insert
            await server.db[LINES].delete_many({"import_id": fid})
            _p_bad, bad = await _import(c, bank["id"], other, AUTH_MAP)
            assert bad.status_code == 409, bad.text
            assert await server.db[LINES].count_documents({"import_id": fid}) == 0

        # COMPLETE without gap-fill flag → idempotent short-circuit (no new inserts).
        await server.db[IMPORTS].update_one(
            {"id": batch_id},
            {"$unset": {"allows_unmatched_gap_fill": ""}},
        )
        await server.db[LINES].delete_many(
            {"import_id": batch_id, "description": {"$in": ["Mutasi C", "Mutasi D"]}}
        )
        _p_blocked, blocked = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert blocked.status_code == 201, blocked.text
        body = blocked.json()
        assert body.get("gap_fill") is not True
        assert await server.db[LINES].count_documents({"import_id": batch_id}) == 2

        assert pair_a["id"] and pair_b["id"]


async def test_wib_period_boundary_inclusive_and_malformed():
    """Date-only inclusive + aware UTC→WIB; missing/malformed excluded or ambiguous."""
    from datetime import datetime, timezone
    from services.cash_bank_statement_purge import (
        _classify_lines,
        _line_period_date,
        execute_unmatched_purge,
    )

    director = _director()
    async with await client_for(director) as c:
        bank, _income = await _setup(c, currency="IDR")
        bank_id = bank["id"]
        # 2026-08-31 17:00 UTC == 2026-09-01 00:00 WIB
        utc_sep1 = datetime(2026, 8, 31, 17, 0, 0, tzinfo=timezone.utc)
        # 2026-09-30 17:00 UTC == 2026-10-01 00:00 WIB → outside Sep period
        utc_after_end = datetime(2026, 9, 30, 17, 0, 0, tzinfo=timezone.utc)
        assert _line_period_date({"date": utc_sep1}) == "2026-09-01"
        assert _line_period_date({"date": utc_after_end}) == "2026-10-01"

        base = {
            "cash_bank_id": bank_id,
            "import_id": "imp-wib",
            "direction": "in",
            "amount": 1000,
            "duplicate_status": "clear",
            "identity_guarantee": "authoritative",
            "sealed": True,
            "active_claim": None,
            "claim_slot": None,
            "description": "wib",
        }
        await server.db[LINES].insert_many([
            {**base, "id": "wib-start", "date": "2026-09-01", "bank_txn_id": "W1"},
            {**base, "id": "wib-end", "date": "2026-09-30", "bank_txn_id": "W2"},
            {**base, "id": "wib-utc-start", "date": utc_sep1, "bank_txn_id": "W3"},
            {**base, "id": "wib-utc-out", "date": utc_after_end, "bank_txn_id": "W4"},
            {**base, "id": "wib-outside", "date": "2026-08-31", "bank_txn_id": "W5"},
            {**base, "id": "wib-missing", "date": "", "bank_txn_id": "W6"},
            {**base, "id": "wib-bad", "date": "bukan-tanggal", "bank_txn_id": "W7"},
            {**base, "id": "wib-iso", "date": "2026-09-15T10:00:00+00:00", "bank_txn_id": "W8"},
        ])
        classified = await _classify_lines(
            server.db, cash_bank_id=bank_id, start="2026-09-01", end="2026-09-30",
        )
        eligible = set(classified["eligible_ids"])
        assert "wib-start" in eligible
        assert "wib-end" in eligible
        assert "wib-utc-start" in eligible
        assert "wib-iso" in eligible
        assert "wib-utc-out" not in eligible
        assert "wib-outside" not in eligible
        assert "wib-missing" not in eligible
        ambiguous_ids = {r["id"] for r in classified["ambiguous"]}
        assert "wib-bad" in ambiguous_ids

        purged = await execute_unmatched_purge(
            server.db,
            cash_bank_id=bank_id,
            start_date="2026-09-01",
            end_date="2026-09-30",
            idempotency_key=f"wib-{uuid.uuid4().hex}",
            user=director,
        )
        assert purged["status"] == "completed"
        assert await server.db[LINES].find_one({"id": "wib-start"}) is None
        assert await server.db[LINES].find_one({"id": "wib-end"}) is None
        assert await server.db[LINES].find_one({"id": "wib-utc-start"}) is None
        assert await server.db[LINES].find_one({"id": "wib-utc-out"}) is not None
        assert await server.db[LINES].find_one({"id": "wib-outside"}) is not None
        assert await server.db[LINES].find_one({"id": "wib-bad"}) is not None


async def test_importing_and_failed_batch_no_unauthorized_gap_fill():
    """IMPORTING/FAILED batches must not gap-fill; COMPLETE+marker may."""
    director = _director()
    async with await client_for(director) as c:
        bank, income = await _setup(c, currency="IDR")
        await _receipt(c, bank["id"], income["id"], 100000, date="2026-09-01", desc="Rcv A")
        _p, imported = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert imported.status_code == 201, imported.text
        batch_id = imported.json()["id"]
        ws = await c.get(
            "/api/cash-bank/reconciliations/pairs/workspace",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        by_desc = {row["description"]: row for row in ws.json()["bank_lines"]}
        book = ws.json()["book_lines"][0]["journal_line_id"]
        await create_pair(
            server.db,
            cash_bank_id=bank["id"],
            statement_line_id=by_desc["Mutasi A"]["id"],
            journal_line_id=book,
            idempotency_key="imp-batch-A",
            start=PERIOD["start_date"],
            end=PERIOD["end_date"],
            user=director,
        )
        id_a = by_desc["Mutasi A"]["id"]
        claim_a = (await server.db[LINES].find_one({"id": id_a}))["active_claim"]

        # IMPORTING: no gap-fill even if marker wrongly set.
        await server.db[IMPORTS].update_one(
            {"id": batch_id},
            {"$set": {
                "status": "importing",
                "allows_unmatched_gap_fill": True,
            }},
        )
        await server.db[LINES].delete_many(
            {"import_id": batch_id, "description": {"$in": ["Mutasi C", "Mutasi D"]}}
        )
        _p_imp, importing_try = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        # Continue-importing path may 201 or 409 depending on writer — must not
        # invent a second batch or duplicate A.
        assert await server.db[IMPORTS].count_documents(
            {"cash_bank_id": bank["id"], "file_sha256": imported.json()["file_sha256"]}
        ) == 1
        assert (await server.db[LINES].find_one({"id": id_a}))["active_claim"] == claim_a
        assert importing_try.json().get("gap_fill") is not True

        # FAILED: unauthorized gap-fill blocked.
        await server.db[IMPORTS].update_one(
            {"id": batch_id},
            {"$set": {"status": "failed", "allows_unmatched_gap_fill": True}},
        )
        _p_fail, failed_try = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert failed_try.status_code == 409, failed_try.text
        line_a_failed = await server.db[LINES].find_one({"id": id_a})
        assert line_a_failed["active_claim"] == claim_a

        # COMPLETE + marker: gap-fill restores C/D only.
        await server.db[IMPORTS].update_one(
            {"id": batch_id},
            {"$set": {"status": "complete", "allows_unmatched_gap_fill": True}},
        )
        _p_ok, ok = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert ok.status_code == 201, ok.text
        assert ok.json().get("gap_fill") is True
        assert ok.json()["id"] == batch_id
        assert await server.db[LINES].count_documents({"import_id": batch_id}) == 4
        assert (await server.db[LINES].find_one({"id": id_a}))["active_claim"] == claim_a

        _p_rep, rep = await _import(c, bank["id"], FILE_ABCD, AUTH_MAP)
        assert rep.status_code == 201
        assert await server.db[LINES].count_documents({"import_id": batch_id}) == 4


async def test_failed_purge_same_key_resumes_fence_stays_pending():
    """FAILED resumes; fence busy keeps PENDING (not terminal CONFLICT)."""
    from services.cash_bank_statement_purge import (
        STATUS_FAILED,
        STATUS_PENDING,
        PURGES,
        PurgeConflictError,
        execute_unmatched_purge,
        _payload_hash,
    )

    director = _director()
    async with await client_for(director) as c:
        bank, _book, rows = await _seed_abcd(c)
        key = f"fail-{uuid.uuid4().hex}"
        ids = [rows[k]["id"] for k in ("Mutasi A", "Mutasi B", "Mutasi C", "Mutasi D")]
        op_id = str(uuid.uuid4())
        await server.db[PURGES].insert_one({
            "id": op_id,
            "idempotency_key": key,
            "payload_hash": _payload_hash(bank["id"], PERIOD["start_date"], PERIOD["end_date"]),
            "cash_bank_id": bank["id"],
            "cash_bank_name": "Bank Uji",
            "start_date": PERIOD["start_date"],
            "end_date": PERIOD["end_date"],
            "status": STATUS_FAILED,
            "requested_at": "2026-09-01T00:00:00+00:00",
            "eligible_snapshot_ids": ids,
            "eligible_count": 4,
            "deleted_count": 1,
            "deleted_line_ids": [ids[0]],
            "protected_count": 0,
            "ambiguous_count": 0,
            "skipped_count": 0,
            "conflicted_count": 0,
            "gap_fill_import_ids": [],
            "message": "simulated crash",
        })
        await server.db[LINES].delete_one({"id": ids[0]})

        resumed = await execute_unmatched_purge(
            server.db,
            cash_bank_id=bank["id"],
            start_date=PERIOD["start_date"],
            end_date=PERIOD["end_date"],
            idempotency_key=key,
            user=director,
        )
        assert resumed["status"] == "completed"
        assert resumed["deleted_count"] == 4
        assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 0

        # Fresh op + busy fence → 409 and stays PENDING.
        bank2, _b2, _r2 = await _seed_abcd(c)
        await server.db[IMPORT_FENCES].insert_one({
            "cash_bank_id": bank2["id"],
            "holder": "other-import",
            "operation_id": "other-import",
            "batch_id": None,
            "file_hash": "busy",
            "lease_until": "2099-01-01T00:00:00+00:00",
            "at": "2099-01-01T00:00:00+00:00",
        })
        key2 = f"fence2-{uuid.uuid4().hex}"
        with pytest.raises(PurgeConflictError):
            await execute_unmatched_purge(
                server.db,
                cash_bank_id=bank2["id"],
                start_date=PERIOD["start_date"],
                end_date=PERIOD["end_date"],
                idempotency_key=key2,
                user=director,
            )
        # Op may or may not exist yet — if created, must remain PENDING not CONFLICT.
        op2 = await server.db[PURGES].find_one(
            {"cash_bank_id": bank2["id"], "idempotency_key": key2},
            {"_id": 0, "status": 1},
        )
        if op2:
            assert op2["status"] == STATUS_PENDING


async def test_duplicate_same_key_concurrent_no_double_delete():
    """Two same-key callers share one cumulative completed result."""
    import asyncio
    from services.cash_bank_statement_purge import execute_unmatched_purge

    director = _director()
    async with await client_for(director) as c:
        bank, _book, _rows = await _seed_abcd(c)
        key = f"dupkey-{uuid.uuid4().hex}"
        results = await asyncio.gather(
            execute_unmatched_purge(
                server.db,
                cash_bank_id=bank["id"],
                start_date=PERIOD["start_date"],
                end_date=PERIOD["end_date"],
                idempotency_key=key,
                user=director,
            ),
            execute_unmatched_purge(
                server.db,
                cash_bank_id=bank["id"],
                start_date=PERIOD["start_date"],
                end_date=PERIOD["end_date"],
                idempotency_key=key,
                user=director,
            ),
            return_exceptions=True,
        )
        oks = [r for r in results if isinstance(r, dict)]
        errs = [r for r in results if isinstance(r, Exception)]
        # One may hit fence conflict while the other runs — both outcomes safe.
        assert oks, results
        for body in oks:
            assert body["status"] == "completed"
            assert body["deleted_count"] == 4
            assert body["idempotency_key"] == key
        assert await server.db[LINES].count_documents({"cash_bank_id": bank["id"]}) == 0
        assert await server.db[PURGES].count_documents(
            {"cash_bank_id": bank["id"], "idempotency_key": key}
        ) == 1
        del errs  # fence conflict on loser is acceptable
