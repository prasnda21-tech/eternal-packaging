"""Buku Besar per akun Phase 1 — read model from journal_core."""
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
os.environ["DB_NAME"] = f"eternal_packaging_general_ledger_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_vouchers import (
    PAYMENTS,
    RECEIPTS,
    TRANSFERS,
    ensure_indexes as ensure_vouchers,
)
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.general_journal import COLLECTION as GJ_COLLECTION, ensure_indexes as ensure_gj
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.opening_balance import COLLECTION as OB_COLLECTION, ensure_indexes as ensure_ob

pytestmark = pytest.mark.anyio

GL_PATH = "/api/ledger/general-ledger"


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
    await ensure_gj(db)
    await ensure_ob(db)
    colls = (
        CB_COLLECTION,
        COA_COLLECTION,
        RECEIPTS,
        PAYMENTS,
        TRANSFERS,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        GJ_COLLECTION,
        OB_COLLECTION,
    )
    for coll in colls:
        await db[coll].delete_many({})
    yield
    for coll in colls:
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


async def _coa(code, name, account_type="EXPS"):
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


def _gj_lines(a, b, amount=10000):
    return [
        {"account_id": a, "debit": amount, "credit": 0, "description": "Dr GJ"},
        {"account_id": b, "debit": 0, "credit": amount, "description": "Cr GJ"},
    ]


async def _post_gj(client, a_id, b_id, amount=10000, date="2026-09-05", desc="JU"):
    d = await client.post("/api/ledger/general-journals", json={
        "date": date,
        "description": desc,
        "lines": _gj_lines(a_id, b_id, amount),
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/ledger/general-journals/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


async def _post_ob(client, a_id, b_id, amount=10000, cutoff="2026-08-31", desc="SA"):
    d = await client.post("/api/ledger/opening-balances", json={
        "cutoff_date": cutoff,
        "description": desc,
        "lines": [
            {"account_id": a_id, "debit": amount, "credit": 0, "description": "Dr SA"},
            {"account_id": b_id, "debit": 0, "credit": amount, "description": "Cr SA"},
        ],
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/ledger/opening-balances/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


async def _gl(client, account_id, start="2026-09-01", end="2026-09-30", **extra):
    params = {"account_id": account_id, "start_date": start, "end_date": end}
    params.update({k: v for k, v in extra.items() if v is not None})
    return await client.get(GL_PATH, params=params)


async def _counts():
    db = server.db
    return {
        "journals": await db[JOURNAL_ENTRIES].count_documents({}),
        "lines": await db[JOURNAL_LINES].count_documents({}),
        "receipts": await db[RECEIPTS].count_documents({}),
        "payments": await db[PAYMENTS].count_documents({}),
        "transfers": await db[TRANSFERS].count_documents({}),
        "gj": await db[GJ_COLLECTION].count_documents({}),
        "ob": await db[OB_COLLECTION].count_documents({}),
        "coa": await db[COA_COLLECTION].count_documents({}),
        "cb": await db[CB_COLLECTION].count_documents({}),
    }


def _assert_invariant(body):
    assert abs(
        body["ending_balance"] - (body["opening_balance"] + body["total_debit"] - body["total_credit"])
    ) < 0.001


@pytest.mark.anyio
async def test_get_zero_write():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank ZW", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        before = await _counts()
        r = await _gl(c, cash["id"])
        assert r.status_code == 200, r.text
        assert await _counts() == before


@pytest.mark.anyio
async def test_invalid_account_and_date_range_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        missing = await _gl(c, str(uuid.uuid4()))
        assert missing.status_code == 400
        assert "tidak ditemukan" in missing.json()["detail"].lower()

        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        bad_range = await _gl(c, cash["id"], start="2026-09-30", end="2026-09-01")
        assert bad_range.status_code == 400
        assert "tanggal" in bad_range.json()["detail"].lower()


@pytest.mark.anyio
async def test_draft_excluded():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Draft", cash["id"])
        draft_rcpt = await c.post("/api/cash-bank/receipts", json={
            "date": "2026-09-05",
            "cash_bank_id": bank["id"],
            "counter_account_id": income["id"],
            "amount": 50000,
            "description": "Draft receipt",
        })
        assert draft_rcpt.status_code == 201
        draft_gj = await c.post("/api/ledger/general-journals", json={
            "date": "2026-09-05",
            "description": "Draft JU",
            "lines": _gj_lines(expense["id"], cash["id"], 7000),
        })
        assert draft_gj.status_code == 201
        draft_ob = await c.post("/api/ledger/opening-balances", json={
            "cutoff_date": "2026-08-31",
            "description": "Draft SA",
            "lines": _gj_lines(cash["id"], expense["id"], 3000),
        })
        assert draft_ob.status_code == 201

        gl = await _gl(c, cash["id"], start="2026-08-01", end="2026-09-30")
        assert gl.status_code == 200
        body = gl.json()
        assert body["total"] == 0
        assert body["opening_balance"] == 0
        assert body["ending_balance"] == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


@pytest.mark.anyio
async def test_opening_period_running_invariant_and_sources():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas Utama", "BANK")
        cash_b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas Tujuan", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        bank_a = await _bank(c, "Bank A", cash["id"])
        bank_b = await _bank(c, "Bank B", cash_b["id"])

        ob = await _post_ob(c, cash["id"], equity["id"], 40000, cutoff="2026-08-31", desc="Saldo Awal Kas")
        await _post_receipt(c, bank_a["id"], income["id"], 50000, date="2026-08-31", desc="Opening seed")

        receipt = await _post_receipt(c, bank_a["id"], income["id"], 100000, date="2026-09-05", desc="Terima periode")
        payment = await _post_payment(c, bank_a["id"], expense["id"], 30000, date="2026-09-06", desc="Bayar periode")
        transfer = await _post_transfer(c, bank_a["id"], bank_b["id"], 20000, date="2026-09-07")
        gj = await _post_gj(c, expense["id"], cash["id"], 5000, date="2026-09-08", desc="JU kredit kas")

        gl = await _gl(c, cash["id"])
        assert gl.status_code == 200, gl.text
        body = gl.json()
        assert body["opening_balance"] == 90000.0  # 40k SA + 50k seed
        assert body["total_debit"] == 100000.0
        assert body["total_credit"] == 55000.0  # 30k pay + 20k xfer + 5k JU
        assert body["ending_balance"] == 135000.0
        _assert_invariant(body)

        by_src = {}
        for row in body["items"]:
            by_src.setdefault(row["source_type"], []).append(row)

        assert by_src["cash_bank_receipt"][0]["debit"] == 100000.0
        assert by_src["cash_bank_receipt"][0]["source_id"] == receipt["id"]
        assert by_src["cash_bank_payment"][0]["credit"] == 30000.0
        assert by_src["cash_bank_payment"][0]["source_id"] == payment["id"]
        assert by_src["cash_bank_transfer"][0]["credit"] == 20000.0
        assert by_src["cash_bank_transfer"][0]["source_id"] == transfer["id"]
        assert by_src["general_journal"][0]["credit"] == 5000.0
        assert by_src["general_journal"][0]["source_id"] == gj["id"]
        assert "opening_balance" not in by_src  # SA is before start_date

        gl_ob = await _gl(c, cash["id"], start="2026-08-01", end="2026-08-31")
        ob_body = gl_ob.json()
        ob_types = {r["source_type"] for r in ob_body["items"]}
        assert "opening_balance" in ob_types
        sa_row = next(r for r in ob_body["items"] if r["source_type"] == "opening_balance")
        assert sa_row["source_id"] == ob["id"]
        assert sa_row["debit"] == 40000.0

        dest = await _gl(c, cash_b["id"])
        dest_items = dest.json()["items"]
        assert len(dest_items) == 1
        assert dest_items[0]["debit"] == 20000.0
        assert dest_items[0]["source_id"] == transfer["id"]
        assert dest_items[0]["source_type"] == "cash_bank_transfer"

        running = body["opening_balance"]
        prev_key = ("", "", "")
        for row in body["items"]:
            running = running + row["debit"] - row["credit"]
            assert abs(row["running_balance"] - running) < 0.001
            key = (row["date"], row["journal_number"], row["journal_line_id"])
            assert key >= prev_key
            prev_key = key
            assert row["journal_id"]
            assert row["journal_line_id"]
            assert row["journal_number"]
            assert row["source_type"]
            assert row["source_id"]
            assert "description" in row
            assert "line_description" in row
            assert row["status"] in ("posted", "reversed")

        income_gl = await _gl(c, income["id"], start="2026-08-01", end="2026-09-30")
        income_rows = income_gl.json()["items"]
        assert all(r["source_id"] != payment["id"] for r in income_rows)
        assert all(r["source_type"] == "cash_bank_receipt" for r in income_rows)
        assert {round(r["credit"], 2) for r in income_rows} == {50000.0, 100000.0}


@pytest.mark.anyio
async def test_exact_account_isolation_same_name():
    admin = _user()
    async with await client_for(admin) as c:
        a1 = await _coa(f"X{uuid.uuid4().hex[:6]}", "Biaya Kantor", "EXPS")
        a2 = await _coa(f"Y{uuid.uuid4().hex[:6]}", "Biaya Kantor", "EXPS")
        cash = await _coa(f"C{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        await _post_gj(c, a1["id"], cash["id"], 8000, desc="Hanya akun 1")
        gl1 = await _gl(c, a1["id"])
        gl2 = await _gl(c, a2["id"])
        assert gl1.json()["total"] == 1
        assert gl1.json()["items"][0]["debit"] == 8000.0
        assert gl2.json()["total"] == 0
        assert gl2.json()["ending_balance"] == 0


@pytest.mark.anyio
async def test_reversal_reflected_as_opposite_movement():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Rev", cash["id"])
        payment = await _post_payment(c, bank["id"], expense["id"], 30000, date="2026-09-06")
        gj = await _post_gj(c, expense["id"], cash["id"], 4000, date="2026-09-08", desc="JU reverse me")

        before = await _gl(c, cash["id"])
        assert before.json()["ending_balance"] == -34000.0

        rev_pay = await c.post(f"/api/cash-bank/payments/{payment['id']}/reverse")
        assert rev_pay.status_code == 200
        rev_gj = await c.post(f"/api/ledger/general-journals/{gj['id']}/reverse")
        assert rev_gj.status_code == 200

        after = await _gl(c, cash["id"])
        body = after.json()
        assert body["ending_balance"] == 0.0
        _assert_invariant(body)
        srcs = {r["source_type"] for r in body["items"]}
        assert "cash_bank_reversal" in srcs
        assert "general_journal_reversal" in srcs
        pay_rev = next(r for r in body["items"] if r["source_type"] == "cash_bank_reversal")
        assert pay_rev["debit"] == 30000.0
        assert pay_rev["source_id"] == payment["id"]
        assert any(r["source_type"] == "cash_bank_payment" and r["credit"] == 30000.0 for r in body["items"])
        gj_rev = next(r for r in body["items"] if r["source_type"] == "general_journal_reversal")
        assert gj_rev["debit"] == 4000.0
        assert gj_rev["source_id"] == gj["id"]


@pytest.mark.anyio
async def test_source_filter_and_search_do_not_change_summary():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Filt", cash["id"])
        rcpt = await _post_receipt(c, bank["id"], income["id"], 100000, desc="Alpha Terima Unik")
        await _post_payment(c, bank["id"], expense["id"], 25000, desc="Beta Bayar")
        await _post_gj(c, expense["id"], cash["id"], 5000, desc="Gamma Jurnal")

        full = (await _gl(c, cash["id"])).json()
        filtered = (await _gl(c, cash["id"], source_type="cash_bank_receipt")).json()
        assert filtered["opening_balance"] == full["opening_balance"]
        assert filtered["total_debit"] == full["total_debit"]
        assert filtered["total_credit"] == full["total_credit"]
        assert filtered["ending_balance"] == full["ending_balance"]
        assert filtered["period_count"] == full["period_count"]
        assert filtered["total"] == 1
        assert filtered["items"][0]["source_type"] == "cash_bank_receipt"
        assert filtered["items"][0]["source_id"] == rcpt["id"]

        searched = (await _gl(c, cash["id"], q="Alpha Terima")).json()
        assert searched["total_debit"] == full["total_debit"]
        assert searched["total_credit"] == full["total_credit"]
        assert searched["ending_balance"] == full["ending_balance"]
        assert searched["total"] == 1
        blob = " ".join([
            searched["items"][0]["journal_number"] or "",
            searched["items"][0]["description"] or "",
            searched["items"][0]["line_description"] or "",
        ])
        assert "Alpha Terima" in blob

        needle_number = full["items"][0]["journal_number"]
        by_number = (await _gl(c, cash["id"], q=needle_number)).json()
        assert by_number["ending_balance"] == full["ending_balance"]
        assert by_number["total"] >= 1
        assert all(r["journal_number"] == needle_number for r in by_number["items"])


@pytest.mark.anyio
async def test_exact_source_linkage_and_safe_paths():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Link", cash["id"])
        rcpt = await _post_receipt(c, bank["id"], income["id"], 1000)
        pay = await _post_payment(c, bank["id"], expense["id"], 400)
        gj = await _post_gj(c, expense["id"], cash["id"], 100, desc="JU path")
        gl = (await _gl(c, cash["id"])).json()
        by_src = {r["source_type"]: r for r in gl["items"]}
        assert by_src["cash_bank_receipt"]["source_id"] == rcpt["id"]
        assert by_src["cash_bank_receipt"]["source_path"] == "/kas-bank/penerimaan"
        assert by_src["cash_bank_payment"]["source_id"] == pay["id"]
        assert by_src["cash_bank_payment"]["source_path"] == "/kas-bank/pembayaran"
        assert by_src["general_journal"]["source_id"] == gj["id"]
        assert by_src["general_journal"]["source_path"] is None


@pytest.mark.anyio
async def test_orphan_line_excluded_and_income_not_mixed():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Orph", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 2000)
        await server.db[JOURNAL_LINES].insert_one({
            "id": str(uuid.uuid4()),
            "journal_id": str(uuid.uuid4()),
            "account_id": cash["id"],
            "debit": 999999,
            "credit": 0,
            "description": "orphan",
        })
        gl = (await _gl(c, cash["id"])).json()
        assert gl["total_debit"] == 2000.0
        assert all(r["debit"] != 999999 for r in gl["items"])


@pytest.mark.anyio
async def test_rbac_and_no_auto_grant():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        account_id = cash["id"]

    denied = _user("viewer", perms=["cash_bank.view", "general_journal.view"])
    async with await client_for(denied) as c:
        bad = await _gl(c, account_id)
        assert bad.status_code == 403

    viewer = _user("viewer", perms=["general_ledger.view", "nav.ledger", "nav.ledger.general_ledger"])
    async with await client_for(viewer) as c:
        ok = await _gl(c, account_id)
        assert ok.status_code == 200

    assert "general_ledger.view" in ALL_PERMISSIONS
    assert "nav.ledger.general_ledger" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "general_ledger.view" not in perms, role
        assert "nav.ledger.general_ledger" not in perms, role


@pytest.mark.anyio
async def test_kas_bank_jurnal_umum_saldo_awal_regression():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        bank = await _bank(c, "Bank Reg", cash["id"])
        rcpt = await _post_receipt(c, bank["id"], income["id"], 1111)
        pay = await _post_payment(c, bank["id"], expense["id"], 222)
        gj = await _post_gj(c, expense["id"], cash["id"], 33, desc="Reg JU")
        ob = await _post_ob(c, cash["id"], equity["id"], 444, cutoff="2026-08-01")

        listed_rcpt = await c.get("/api/cash-bank/receipts")
        assert listed_rcpt.status_code == 200
        assert any(i["id"] == rcpt["id"] and i["status"] == "posted" for i in listed_rcpt.json()["items"])
        listed_pay = await c.get("/api/cash-bank/payments")
        assert listed_pay.status_code == 200
        assert any(i["id"] == pay["id"] for i in listed_pay.json()["items"])
        listed_gj = await c.get("/api/ledger/general-journals")
        assert listed_gj.status_code == 200
        assert any(i["id"] == gj["id"] and i["status"] == "posted" for i in listed_gj.json()["items"])
        listed_ob = await c.get("/api/ledger/opening-balances")
        assert listed_ob.status_code == 200
        assert any(i["id"] == ob["id"] and i["status"] == "posted" for i in listed_ob.json()["items"])

        mut = await c.get("/api/cash-bank/mutasi", params={
            "cash_bank_id": bank["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })
        assert mut.status_code == 200
        assert mut.json()["total_debit"] == 1111.0

        before = await _counts()
        gl = await _gl(c, cash["id"], start="2026-08-01", end="2026-09-30")
        assert gl.status_code == 200
        assert await _counts() == before
        types = {r["source_type"] for r in gl.json()["items"]}
        assert "cash_bank_receipt" in types
        assert "cash_bank_payment" in types
        assert "general_journal" in types
        assert "opening_balance" in types
