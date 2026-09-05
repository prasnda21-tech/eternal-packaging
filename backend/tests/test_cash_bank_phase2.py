"""Kas & Bank Phase 2 — Mutasi + Rekonsiliasi safety tests."""
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
os.environ["DB_NAME"] = f"eternal_packaging_cash_bank_p2_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    ensure_indexes as ensure_recon,
)
from services.cash_bank_vouchers import (
    PAYMENTS,
    RECEIPTS,
    TRANSFERS,
    ensure_indexes as ensure_vouchers,
)
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal

pytestmark = pytest.mark.anyio


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
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    for coll in (
        CB_COLLECTION,
        COA_COLLECTION,
        RECEIPTS,
        PAYMENTS,
        TRANSFERS,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        RECONCILIATIONS,
        RECON_MATCHES,
    ):
        await db[coll].delete_many({})
    yield
    for coll in (
        CB_COLLECTION,
        COA_COLLECTION,
        RECEIPTS,
        PAYMENTS,
        TRANSFERS,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        RECONCILIATIONS,
        RECON_MATCHES,
    ):
        await db[coll].delete_many({})


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        if role == "admin":
            override = {}
        elif perms is not None:
            override = {p: True for p in perms}
        else:
            override = {}
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role if role != "viewer" else "operator",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name, account_type="BANK"):
    return await create_account(
        server.db, {"code": code, "name": name, "account_type": account_type}, user=_user()
    )


async def _bank(client, name, account_id):
    r = await client.post("/api/cash-bank/accounts", json={
        "name": name,
        "account_id": account_id,
        "active": True,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _post_receipt(client, cash_bank_id, counter_id, amount, date="2026-09-05", desc=""):
    d = await client.post("/api/cash-bank/receipts", json={
        "date": date,
        "cash_bank_id": cash_bank_id,
        "counter_account_id": counter_id,
        "amount": amount,
        "description": desc or f"Receipt {amount}",
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/cash-bank/receipts/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


async def _post_payment(client, cash_bank_id, counter_id, amount, date="2026-09-05", desc=""):
    d = await client.post("/api/cash-bank/payments", json={
        "date": date,
        "cash_bank_id": cash_bank_id,
        "counter_account_id": counter_id,
        "amount": amount,
        "description": desc or f"Payment {amount}",
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/cash-bank/payments/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


async def _post_transfer(client, src, dest, amount, date="2026-09-05"):
    d = await client.post("/api/cash-bank/transfers", json={
        "date": date,
        "source_cash_bank_id": src,
        "dest_cash_bank_id": dest,
        "amount": amount,
        "description": f"Transfer {amount}",
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/cash-bank/transfers/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


@pytest.mark.anyio
async def test_mutasi_01_draft_excluded():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"M{uuid.uuid4().hex[:6]}", "Kas Mutasi")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan")
        bank = await _bank(c, "Bank Mutasi", cash["id"])
        draft = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": bank["id"],
            "counter_account_id": income["id"],
            "amount": 50000,
            "description": "Draft only",
        })
        assert draft.status_code == 201
        mut = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        assert mut.status_code == 200
        body = mut.json()
        assert body["total"] == 0
        assert body["opening_balance"] == 0
        assert body["ending_balance"] == 0


@pytest.mark.anyio
async def test_mutasi_02_10_receipt_payment_transfer_reversal_balances():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas A")
        cash_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas B")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya")
        bank_a = await _bank(c, "Bank A", cash["id"])
        bank_b = await _bank(c, "Bank B", cash_b["id"])

        # Opening before period: receipt on Aug 31
        await _post_receipt(c, bank_a["id"], income["id"], 50000, date="2026-08-31", desc="Opening seed")

        receipt = await _post_receipt(c, bank_a["id"], income["id"], 100000, date="2026-09-05")
        payment = await _post_payment(c, bank_a["id"], expense["id"], 30000, date="2026-09-06")
        transfer = await _post_transfer(c, bank_a["id"], bank_b["id"], 20000, date="2026-09-07")

        mut = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank_a["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        assert mut.status_code == 200
        body = mut.json()
        assert body["opening_balance"] == 50000.0
        assert body["total_debit"] == 100000.0
        assert body["total_credit"] == 50000.0  # 30k payment + 20k transfer out
        assert body["ending_balance"] == 100000.0  # 50 + 100 - 30 - 20
        assert abs(body["ending_balance"] - (body["opening_balance"] + body["total_debit"] - body["total_credit"])) < 0.001

        by_src = {r["source_type"]: r for r in body["items"]}
        assert by_src["cash_bank_receipt"]["debit"] == 100000.0
        assert by_src["cash_bank_receipt"]["credit"] == 0
        assert by_src["cash_bank_payment"]["credit"] == 30000.0
        assert by_src["cash_bank_transfer"]["credit"] == 20000.0
        assert by_src["cash_bank_receipt"]["source_id"] == receipt["id"]
        assert by_src["cash_bank_payment"]["source_id"] == payment["id"]
        assert by_src["cash_bank_transfer"]["source_id"] == transfer["id"]

        # Dest bank sees transfer debit
        mut_b = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank_b["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        assert mut_b.status_code == 200
        b_items = mut_b.json()["items"]
        assert len(b_items) == 1
        assert b_items[0]["debit"] == 20000.0
        assert b_items[0]["credit"] == 0

        # Exact bank only — bank_b mutasi must not include A's receipt
        assert all(i["source_id"] != receipt["id"] for i in b_items)

        # Reversal of payment restores running balance
        rev = await c.post(f"/api/cash-bank/payments/{payment['id']}/reverse")
        assert rev.status_code == 200
        mut2 = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank_a["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        body2 = mut2.json()
        # Extra debit 30k from reversal → ending 130000
        assert body2["ending_balance"] == 130000.0
        rev_rows = [i for i in body2["items"] if i["source_type"] == "cash_bank_reversal"]
        assert len(rev_rows) == 1
        assert rev_rows[0]["debit"] == 30000.0
        # Original payment credit still present
        assert any(
            i["source_type"] == "cash_bank_payment" and i["credit"] == 30000.0
            for i in body2["items"]
        )


@pytest.mark.anyio
async def test_mutasi_get_zero_write():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"Z{uuid.uuid4().hex[:6]}", "Kas Z")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Inc")
        bank = await _bank(c, "Bank Z", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        before_l = await server.db[JOURNAL_LINES].count_documents({})
        before_r = await server.db[RECONCILIATIONS].count_documents({})
        r = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        assert r.status_code == 200
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j
        assert await server.db[JOURNAL_LINES].count_documents({}) == before_l
        assert await server.db[RECONCILIATIONS].count_documents({}) == before_r


@pytest.mark.anyio
async def test_recon_11_22_draft_match_finalize_immutable_no_journal():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"R{uuid.uuid4().hex[:6]}", "Kas Recon")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Inc")
        bank = await _bank(c, "Bank Recon", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 100000, date="2026-09-05")
        await _post_payment(
            c, bank["id"],
            (await _coa(f"E{uuid.uuid4().hex[:6]}", "Exp"))["id"],
            30000, date="2026-09-06",
        )

        j_before = await server.db[JOURNAL_ENTRIES].count_documents({})
        l_before = await server.db[JOURNAL_LINES].count_documents({})
        vouchers_before = await server.db[RECEIPTS].count_documents({})

        # Workspace GET does not auto-create
        ws = await c.get("/api/cash-bank/reconciliations/workspace", params={
            "cash_bank_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": "70000",
        })
        assert ws.status_code == 200
        assert await server.db[RECONCILIATIONS].count_documents({}) == 0
        assert ws.json()["book_balance"] == 70000.0
        assert ws.json()["difference"] == 0.0
        assert ws.json()["total_movements"] == 2

        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": 65000,
            "notes": "preview draft",
        })
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["status"] == "draft"
        assert draft["book_balance"] == 70000.0
        assert draft["statement_balance"] == 65000.0
        assert draft["difference"] == -5000.0

        line_ids = [m["journal_line_id"] for m in ws.json()["movements"]]
        matched = await c.put(
            f"/api/cash-bank/reconciliations/{draft['id']}/matches",
            json={"journal_line_ids": line_ids},
        )
        assert matched.status_code == 200, matched.text
        assert matched.json()["matched_count"] == 2

        # Edit draft statement
        patched = await c.patch(f"/api/cash-bank/reconciliations/{draft['id']}", json={
            "statement_balance": 70000,
            "notes": "updated",
        })
        assert patched.status_code == 200
        assert patched.json()["difference"] == 0.0
        assert patched.json()["notes"] == "updated"

        fin = await c.post(f"/api/cash-bank/reconciliations/{draft['id']}/finalize")
        assert fin.status_code == 200, fin.text
        assert fin.json()["status"] == "finalized"

        # Finalize does NOT create journal / alter vouchers
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == j_before
        assert await server.db[JOURNAL_LINES].count_documents({}) == l_before
        assert await server.db[RECEIPTS].count_documents({}) == vouchers_before

        # Immutable
        bad_patch = await c.patch(f"/api/cash-bank/reconciliations/{draft['id']}", json={
            "statement_balance": 1,
        })
        assert bad_patch.status_code == 409
        bad_del = await c.delete(f"/api/cash-bank/reconciliations/{draft['id']}")
        assert bad_del.status_code == 409
        bad_match = await c.put(
            f"/api/cash-bank/reconciliations/{draft['id']}/matches",
            json={"journal_line_ids": []},
        )
        assert bad_match.status_code == 409

        # Idempotent finalize retry
        fin2 = await c.post(f"/api/cash-bank/reconciliations/{draft['id']}/finalize")
        assert fin2.status_code == 200
        assert fin2.json()["status"] == "finalized"

        # Movement cannot be finalized into second reconciliation
        other = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": 70000,
        })
        assert other.status_code == 201
        conflict = await c.put(
            f"/api/cash-bank/reconciliations/{other.json()['id']}/matches",
            json={"journal_line_ids": [line_ids[0]]},
        )
        assert conflict.status_code == 409

        # GET detail zero-write
        before_m = await server.db[RECON_MATCHES].count_documents({})
        detail = await c.get(f"/api/cash-bank/reconciliations/{draft['id']}")
        assert detail.status_code == 200
        assert await server.db[RECON_MATCHES].count_documents({}) == before_m


@pytest.mark.anyio
async def test_recon_concurrent_finalize_safe():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"C{uuid.uuid4().hex[:6]}", "Kas Conc")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Inc")
        bank = await _bank(c, "Bank Conc", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": 1000,
        })
        rid = created.json()["id"]

        async def _finalize():
            async with await client_for(admin) as c2:
                return await c2.post(f"/api/cash-bank/reconciliations/{rid}/finalize")

        results = await asyncio.gather(_finalize(), _finalize(), _finalize())
        assert all(r.status_code == 200 for r in results)
        assert all(r.json()["status"] == "finalized" for r in results)
        assert await server.db[RECONCILIATIONS].count_documents({"id": rid, "status": "finalized"}) == 1


@pytest.mark.anyio
async def test_recon_unauthorized_rejected():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"U{uuid.uuid4().hex[:6]}", "Kas Unauth")
        bank = await _bank(c, "Bank Unauth", cash["id"])
        bank_id = bank["id"]

    viewer = _user("viewer", perms=["cash_bank.view", "nav.cash_bank"])
    async with await client_for(viewer) as c:
        ok_get = await c.get("/api/cash-bank/reconciliations")
        assert ok_get.status_code == 200
        bad = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank_id,
            "reconciliation_date": "2026-09-10",
            "statement_balance": 0,
        })
        assert bad.status_code == 403


@pytest.mark.anyio
async def test_recon_delete_draft():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash = await _coa(f"D{uuid.uuid4().hex[:6]}", "Kas Del")
        bank = await _bank(c, "Bank Del", cash["id"])
        created = await c.post("/api/cash-bank/reconciliations", json={
            "cash_bank_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": 0,
        })
        rid = created.json()["id"]
        deleted = await c.delete(f"/api/cash-bank/reconciliations/{rid}")
        assert deleted.status_code == 200
        assert await server.db[RECONCILIATIONS].count_documents({"id": rid}) == 0
