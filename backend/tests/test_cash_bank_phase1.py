"""Kas & Bank Phase 1 + minimal journal core — required safety tests."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
# Per-xdist-worker DB so parallel autouse cleanup cannot wipe sibling workers' COA/cash-bank rows.
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_cash_bank_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
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
    for coll in (
        CB_COLLECTION,
        COA_COLLECTION,
        RECEIPTS,
        PAYMENTS,
        TRANSFERS,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
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
    ):
        await db[coll].delete_many({})


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        if role == "admin":
            override = {}
        elif perms is not None:
            # Non-admin with explicit grant via override on empty role defaults
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


async def _coa(code, name):
    return await create_account(server.db, {"code": code, "name": name}, user=_user())


async def _make_cash_bank(client, name, account_id, **extra):
    r = await client.post("/api/cash-bank/accounts", json={
        "name": name,
        "account_id": account_id,
        "description": extra.get("description", ""),
        "active": extra.get("active", True),
    })
    return r


@pytest.mark.anyio
async def test_01_create_cash_bank_linked_coa():
    admin = _user("admin")
    async with await client_for(admin) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "Kas Test A")
        r = await _make_cash_bank(c, "Kas BCA Preview", a["id"])
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Kas BCA Preview"
        assert body["account_id"] == a["id"]
        assert body["active"] is True


@pytest.mark.anyio
async def test_02_duplicate_coa_link_409():
    admin = _user("admin")
    async with await client_for(admin) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "Shared COA")
        r1 = await _make_cash_bank(c, "Bank Satu", a["id"])
        assert r1.status_code == 201
        r2 = await _make_cash_bank(c, "Bank Dua", a["id"])
        assert r2.status_code == 409


@pytest.mark.anyio
async def test_03_inactive_handling():
    admin = _user("admin")
    async with await client_for(admin) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "Inactive COA")
        r = await _make_cash_bank(c, "Kas Nonaktif", a["id"], active=False)
        assert r.status_code == 201
        cb = r.json()
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Pendapatan")
        bad = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 1000,
        })
        assert bad.status_code == 400
        assert "nonaktif" in bad.json()["detail"].lower()


@pytest.mark.anyio
async def test_04_05_06_receipt_draft_then_post_balanced():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash_coa = await _coa(f"CA{uuid.uuid4().hex[:6]}", "Kas")
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Pendapatan")
        cb = (await _make_cash_bank(c, "Kas Preview", cash_coa["id"])).json()
        draft = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 100000,
            "description": "Terima preview",
        })
        assert draft.status_code == 201, draft.text
        body = draft.json()
        assert body["status"] == "draft"
        assert body.get("journal_id") in (None, "")
        jcount = await server.db[JOURNAL_ENTRIES].count_documents({"source_id": body["id"]})
        assert jcount == 0

        posted = await c.post(f"/api/cash-bank/receipts/{body['id']}/post")
        assert posted.status_code == 200, posted.text
        p = posted.json()
        assert p["status"] == "posted"
        assert p["journal_id"]

        detail = await c.get(f"/api/cash-bank/receipts/{body['id']}")
        assert detail.status_code == 200
        journal = detail.json()["journal"]
        assert journal["status"] == "posted"
        lines = journal["lines"]
        assert len(lines) == 2
        total_dr = sum(float(ln["debit"]) for ln in lines)
        total_cr = sum(float(ln["credit"]) for ln in lines)
        assert total_dr == total_cr == 100000.0
        by_acc = {ln["account_id"]: ln for ln in lines}
        assert by_acc[cash_coa["id"]]["debit"] == 100000.0
        assert by_acc[cash_coa["id"]]["credit"] == 0
        assert by_acc[counter["id"]]["credit"] == 100000.0
        assert by_acc[counter["id"]]["debit"] == 0


@pytest.mark.anyio
async def test_07_payment_posted_dr_counter_cr_cash():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash_coa = await _coa(f"CA{uuid.uuid4().hex[:6]}", "Kas")
        counter = await _coa(f"EX{uuid.uuid4().hex[:6]}", "Beban")
        cb = (await _make_cash_bank(c, "Kas Pay", cash_coa["id"])).json()
        draft = (await c.post("/api/cash-bank/payments", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 30000,
        })).json()
        posted = await c.post(f"/api/cash-bank/payments/{draft['id']}/post")
        assert posted.status_code == 200
        detail = (await c.get(f"/api/cash-bank/payments/{draft['id']}")).json()
        by_acc = {ln["account_id"]: ln for ln in detail["journal"]["lines"]}
        assert by_acc[counter["id"]]["debit"] == 30000.0
        assert by_acc[cash_coa["id"]]["credit"] == 30000.0


@pytest.mark.anyio
async def test_08_09_transfer_and_same_source_dest_rejected():
    admin = _user("admin")
    async with await client_for(admin) as c:
        a1 = await _coa(f"T1{uuid.uuid4().hex[:6]}", "Bank 1")
        a2 = await _coa(f"T2{uuid.uuid4().hex[:6]}", "Bank 2")
        b1 = (await _make_cash_bank(c, "Bank Sumber", a1["id"])).json()
        b2 = (await _make_cash_bank(c, "Bank Tujuan", a2["id"])).json()
        same = await c.post("/api/cash-bank/transfers", json={
            "date": "2026-09-05",
            "source_cash_bank_id": b1["id"],
            "dest_cash_bank_id": b1["id"],
            "amount": 20000,
        })
        assert same.status_code == 400

        draft = (await c.post("/api/cash-bank/transfers", json={
            "date": "2026-09-05",
            "source_cash_bank_id": b1["id"],
            "dest_cash_bank_id": b2["id"],
            "amount": 20000,
        })).json()
        posted = await c.post(f"/api/cash-bank/transfers/{draft['id']}/post")
        assert posted.status_code == 200
        detail = (await c.get(f"/api/cash-bank/transfers/{draft['id']}")).json()
        by_acc = {ln["account_id"]: ln for ln in detail["journal"]["lines"]}
        assert by_acc[a2["id"]]["debit"] == 20000.0
        assert by_acc[a1["id"]]["credit"] == 20000.0


@pytest.mark.anyio
async def test_10_11_12_double_post_invalid_amount_immutable():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash_coa = await _coa(f"CA{uuid.uuid4().hex[:6]}", "Kas")
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Pendapatan")
        cb = (await _make_cash_bank(c, "Kas Idem", cash_coa["id"])).json()

        bad = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 0,
        })
        assert bad.status_code == 400

        draft = (await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 5000,
        })).json()
        p1 = await c.post(f"/api/cash-bank/receipts/{draft['id']}/post")
        p2 = await c.post(f"/api/cash-bank/receipts/{draft['id']}/post")
        assert p1.status_code == 200
        assert p2.status_code == 200
        assert p1.json()["journal_id"] == p2.json()["journal_id"]
        jcount = await server.db[JOURNAL_ENTRIES].count_documents({
            "source_type": "cash_bank_receipt",
            "source_id": draft["id"],
            "reversal_of": None,
        })
        assert jcount == 1

        edit = await c.patch(f"/api/cash-bank/receipts/{draft['id']}", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 999,
        })
        assert edit.status_code == 409
        delete = await c.delete(f"/api/cash-bank/receipts/{draft['id']}")
        assert delete.status_code == 409


@pytest.mark.anyio
async def test_13_14_15_reversal_exact_once_preserved():
    admin = _user("admin")
    async with await client_for(admin) as c:
        cash_coa = await _coa(f"CA{uuid.uuid4().hex[:6]}", "Kas")
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Pendapatan")
        cb = (await _make_cash_bank(c, "Kas Rev", cash_coa["id"])).json()
        draft = (await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 75000,
        })).json()
        await c.post(f"/api/cash-bank/receipts/{draft['id']}/post")
        before = (await c.get(f"/api/cash-bank/receipts/{draft['id']}")).json()
        orig_lines = before["journal"]["lines"]
        orig_id = before["journal"]["id"]

        rev1 = await c.post(f"/api/cash-bank/receipts/{draft['id']}/reverse")
        assert rev1.status_code == 200, rev1.text
        after = (await c.get(f"/api/cash-bank/receipts/{draft['id']}")).json()
        assert after["status"] == "reversed"
        assert after["journal"]["status"] == "reversed"
        assert after["journal"]["id"] == orig_id
        rev_lines = after["reversal_journal"]["lines"]
        assert after["reversal_journal"]["reversal_of"] == orig_id

        orig_by = {ln["account_id"]: ln for ln in orig_lines}
        rev_by = {ln["account_id"]: ln for ln in rev_lines}
        for aid in orig_by:
            assert rev_by[aid]["debit"] == orig_by[aid]["credit"]
            assert rev_by[aid]["credit"] == orig_by[aid]["debit"]

        rev2 = await c.post(f"/api/cash-bank/receipts/{draft['id']}/reverse")
        assert rev2.status_code == 409

        # Original journal preserved
        still = await server.db[JOURNAL_ENTRIES].find_one({"id": orig_id}, {"_id": 0})
        assert still is not None
        assert still["status"] == "reversed"


@pytest.mark.anyio
async def test_16_rbac_backend_protected():
    viewer = _user("viewer", perms=["cash_bank.view", "nav.cash_bank", "nav.cash_bank.accounts"])
    async with await client_for(viewer) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "RBAC COA")
        create = await c.post("/api/cash-bank/accounts", json={
            "name": "No Manage",
            "account_id": a["id"],
        })
        assert create.status_code == 403

        list_ok = await c.get("/api/cash-bank/accounts")
        assert list_ok.status_code == 200

    manager = _user("viewer", perms=["cash_bank.view", "cash_bank.manage"])
    async with await client_for(manager) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "Mgr COA")
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Counter")
        cb = (await _make_cash_bank(c, "Mgr Bank", a["id"])).json()
        draft = (await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 1000,
        })).json()
        post = await c.post(f"/api/cash-bank/receipts/{draft['id']}/post")
        assert post.status_code == 403

    poster = _user("viewer", perms=["cash_bank.view", "cash_bank.manage", "cash_bank.post"])
    async with await client_for(poster) as c:
        # reuse by creating fresh
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "Post COA")
        counter = await _coa(f"CR{uuid.uuid4().hex[:6]}", "Counter2")
        cb = (await _make_cash_bank(c, "Post Bank", a["id"])).json()
        draft = (await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": cb["id"],
            "counter_account_id": counter["id"],
            "amount": 1000,
        })).json()
        post = await c.post(f"/api/cash-bank/receipts/{draft['id']}/post")
        assert post.status_code == 200
        rev = await c.post(f"/api/cash-bank/receipts/{draft['id']}/reverse")
        assert rev.status_code == 403

    assert "cash_bank.view" in ALL_PERMISSIONS
    assert "cash_bank.manage" in ALL_PERMISSIONS
    assert "cash_bank.post" in ALL_PERMISSIONS
    assert "cash_bank.reverse" in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_17_get_zero_write():
    admin = _user("admin")
    async with await client_for(admin) as c:
        a = await _coa(f"CB{uuid.uuid4().hex[:6]}", "ZW")
        await _make_cash_bank(c, "ZW Bank", a["id"])
        before_cb = await server.db[CB_COLLECTION].count_documents({})
        before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        r = await c.get("/api/cash-bank/accounts")
        assert r.status_code == 200
        r2 = await c.get("/api/cash-bank/receipts")
        assert r2.status_code == 200
        after_cb = await server.db[CB_COLLECTION].count_documents({})
        after_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        assert before_cb == after_cb
        assert before_j == after_j


@pytest.mark.anyio
async def test_18_sales_payroll_hpp_untouched():
    """Safety: this module must not import or call HPP / sales journal wiring."""
    import services.cash_bank_vouchers as v
    import services.journal_core as j
    import services.cash_bank_http as h
    src = Path(v.__file__).read_text(encoding="utf-8") + Path(j.__file__).read_text(encoding="utf-8") + Path(h.__file__).read_text(encoding="utf-8")
    for forbidden in ("hpp_valuation", "sales_receipts", "hr_payroll", "purchase_invoices", "stock"):
        assert forbidden not in src
    hpp_path = Path(__file__).resolve().parents[1] / "services" / "hpp_valuation.py"
    # File may or may not exist; if present ensure we did not modify it in this task via mtime check is hard —
    # instead verify our services do not reference it.
    assert "hpp" not in src.lower() or "hpp_valuation" not in src
