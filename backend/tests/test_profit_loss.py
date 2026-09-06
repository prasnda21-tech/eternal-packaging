"""Laba/Rugi Phase 1 — GET-only read model from journal_core."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_profit_loss_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.account_type_reporting import ACCOUNT_TYPE_REPORTING_MAP
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

PL_PATH = "/api/ledger/profit-loss"
TB_PATH = "/api/ledger/trial-balance"
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


def _gj_lines(a, b, amount=10000):
    return [
        {"account_id": a, "debit": amount, "credit": 0, "description": "Dr GJ"},
        {"account_id": b, "debit": 0, "credit": amount, "description": "Cr GJ"},
    ]


async def _post_gj(client, debit_id, credit_id, amount=10000, date="2026-09-05", desc="JU"):
    d = await client.post("/api/ledger/general-journals", json={
        "date": date,
        "description": desc,
        "lines": _gj_lines(debit_id, credit_id, amount),
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


async def _pl(client, start="2026-09-01", end="2026-09-30", **extra):
    params = {"start_date": start, "end_date": end}
    params.update({k: v for k, v in extra.items() if v is not None})
    return await client.get(PL_PATH, params=params)


async def _tb(client, start="2026-09-01", end="2026-09-30", **extra):
    params = {"start_date": start, "end_date": end}
    params.update({k: v for k, v in extra.items() if v is not None})
    return await client.get(TB_PATH, params=params)


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


def _row(body, account_id):
    return next(r for r in body["items"] if r["account_id"] == account_id)


def _has_account(body, account_id):
    return any(r["account_id"] == account_id for r in body["items"])


@pytest.mark.anyio
async def test_get_zero_write():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank ZW", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        before = await _counts()
        r = await _pl(c)
        assert r.status_code == 200, r.text
        assert await _counts() == before


@pytest.mark.anyio
async def test_invalid_date_range_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bad = await _pl(c, start="2026-09-30", end="2026-09-01")
        assert bad.status_code == 400
        assert "tanggal" in bad.json()["detail"].lower()


@pytest.mark.anyio
async def test_only_income_statement_accounts_included():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        ar = await _coa(f"R{uuid.uuid4().hex[:6]}", "Piutang", "AREC")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        cogs = await _coa(f"H{uuid.uuid4().hex[:6]}", "HPP", "COGS")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Beban", "EXPS")
        oinc = await _coa(f"N{uuid.uuid4().hex[:6]}", "Bunga", "OINC")
        oexp = await _coa(f"X{uuid.uuid4().hex[:6]}", "Rugi Lain", "OEXP")
        body = (await _pl(c)).json()
        ids = {r["account_id"] for r in body["items"]}
        assert income["id"] in ids
        assert cogs["id"] in ids
        assert expense["id"] in ids
        assert oinc["id"] in ids
        assert oexp["id"] in ids
        assert cash["id"] not in ids
        assert ar["id"] not in ids
        assert equity["id"] not in ids
        for row in body["items"]:
            meta = ACCOUNT_TYPE_REPORTING_MAP[row["account_type"]]
            assert meta["statement"] == "INCOME_STATEMENT"
            assert row["report_group"] == meta["report_group"]
            assert row["normal_balance"] == meta["normal_balance"]


@pytest.mark.anyio
async def test_group_presentation_and_formulas():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan Jual", "REVE")
        cogs = await _coa(f"H{uuid.uuid4().hex[:6]}", "HPP Barang", "COGS")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Beban Operasi", "EXPS")
        oinc = await _coa(f"N{uuid.uuid4().hex[:6]}", "Pendapatan Bunga", "OINC")
        oexp = await _coa(f"X{uuid.uuid4().hex[:6]}", "Beban Lain", "OEXP")
        bank = await _bank(c, "Bank PL", cash["id"])

        await _post_receipt(c, bank["id"], income["id"], 100000)
        await _post_gj(c, cogs["id"], cash["id"], 40000, desc="HPP")
        await _post_payment(c, bank["id"], expense["id"], 15000)
        await _post_gj(c, cash["id"], oinc["id"], 3000, desc="Bunga")
        await _post_gj(c, oexp["id"], cash["id"], 2000, desc="Beban lain")

        body = (await _pl(c)).json()
        rev = _row(body, income["id"])
        assert rev["debit"] == 0
        assert rev["credit"] == 100000.0
        assert rev["amount"] == 100000.0
        assert rev["report_group"] == "revenue"
        assert rev["normal_balance"] == "credit"

        cogs_row = _row(body, cogs["id"])
        assert cogs_row["debit"] == 40000.0
        assert cogs_row["credit"] == 0
        assert cogs_row["amount"] == 40000.0
        assert cogs_row["report_group"] == "cogs"
        assert cogs_row["normal_balance"] == "debit"

        exp = _row(body, expense["id"])
        assert exp["debit"] == 15000.0
        assert exp["amount"] == 15000.0
        assert exp["report_group"] == "expense"

        oi = _row(body, oinc["id"])
        assert oi["credit"] == 3000.0
        assert oi["amount"] == 3000.0
        assert oi["report_group"] == "other_income"

        oe = _row(body, oexp["id"])
        assert oe["debit"] == 2000.0
        assert oe["amount"] == 2000.0
        assert oe["report_group"] == "other_expense"

        t = body["totals"]
        assert t["revenue_total"] == 100000.0
        assert t["cogs_total"] == 40000.0
        assert t["gross_profit"] == 60000.0
        assert t["expense_total"] == 15000.0
        assert t["operating_profit"] == 45000.0
        assert t["other_income_total"] == 3000.0
        assert t["other_expense_total"] == 2000.0
        assert t["net_profit"] == 46000.0
        assert body["labels"]["net_profit"] == "LABA BERSIH"
        assert "tax" not in str(body).lower() or "pajak" not in str(body["labels"]).lower()


@pytest.mark.anyio
async def test_negative_and_contra_amounts_preserved():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Beban", "EXPS")
        await _post_gj(c, income["id"], cash["id"], 7000, desc="Koreksi pendapatan")
        await _post_gj(c, cash["id"], expense["id"], 2500, desc="Koreksi beban")
        body = (await _pl(c)).json()
        rev = _row(body, income["id"])
        assert rev["debit"] == 7000.0
        assert rev["credit"] == 0
        assert rev["amount"] == -7000.0
        exp = _row(body, expense["id"])
        assert exp["debit"] == 0
        assert exp["credit"] == 2500.0
        assert exp["amount"] == -2500.0
        assert body["totals"]["revenue_total"] == -7000.0
        assert body["totals"]["expense_total"] == -2500.0
        assert body["totals"]["gross_profit"] == -7000.0
        assert body["totals"]["operating_profit"] == -4500.0
        assert body["totals"]["net_profit"] == -4500.0


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
        body = (await _pl(c)).json()
        assert _row(body, income["id"])["amount"] == 0
        assert _row(body, expense["id"])["amount"] == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


@pytest.mark.anyio
async def test_reversal_reflected_as_opposite_effect():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Rev", cash["id"])
        payment = await _post_payment(c, bank["id"], expense["id"], 30000, date="2026-09-06")
        before = (await _pl(c)).json()
        assert _row(before, expense["id"])["amount"] == 30000.0
        rev = await c.post(f"/api/cash-bank/payments/{payment['id']}/reverse")
        assert rev.status_code == 200
        after = (await _pl(c)).json()
        exp = _row(after, expense["id"])
        assert exp["debit"] == 30000.0
        assert exp["credit"] == 30000.0
        assert exp["amount"] == 0
        assert after["totals"]["expense_total"] == 0
        assert after["totals"]["net_profit"] == 0


@pytest.mark.anyio
async def test_no_opening_balance_carry_forward():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        bank = await _bank(c, "Bank OB", cash["id"])
        await _post_ob(c, cash["id"], equity["id"], 40000, cutoff="2026-08-31")
        await _post_receipt(c, bank["id"], income["id"], 50000, date="2026-08-31")
        await _post_receipt(c, bank["id"], income["id"], 10000, date="2026-09-05")
        body = (await _pl(c)).json()
        rev = _row(body, income["id"])
        assert rev["credit"] == 10000.0
        assert rev["amount"] == 10000.0
        assert body["totals"]["revenue_total"] == 10000.0


@pytest.mark.anyio
async def test_exact_account_isolation_same_name():
    admin = _user()
    async with await client_for(admin) as c:
        a1 = await _coa(f"X{uuid.uuid4().hex[:6]}", "Pendapatan Jasa", "REVE")
        a2 = await _coa(f"Y{uuid.uuid4().hex[:6]}", "Pendapatan Jasa", "REVE")
        cash = await _coa(f"C{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        await _post_gj(c, cash["id"], a1["id"], 8000, desc="Hanya akun 1")
        body = (await _pl(c)).json()
        r1 = _row(body, a1["id"])
        r2 = _row(body, a2["id"])
        assert r1["name"] == r2["name"] == "Pendapatan Jasa"
        assert r1["account_id"] != r2["account_id"]
        assert r1["amount"] == 8000.0
        assert r2["amount"] == 0
        assert r1["drilldown"]["account_id"] == a1["id"]
        assert r2["drilldown"]["account_id"] == a2["id"]


@pytest.mark.anyio
async def test_search_rows_only_keeps_report_totals():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan Unik Alpha", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya ATK", "EXPS")
        bank = await _bank(c, "Bank Filt", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 100000)
        await _post_gj(c, expense["id"], cash["id"], 5000, desc="Beban")
        full = (await _pl(c)).json()
        searched = (await _pl(c, q="Pendapatan Unik Alpha")).json()
        assert searched["totals"] == full["totals"]
        assert searched["account_count"] == full["account_count"]
        assert searched["total"] == 1
        assert searched["items"][0]["account_id"] == income["id"]
        assert searched["groups"][0]["total"] == full["totals"]["revenue_total"]
        assert searched["groups"][0]["items"][0]["account_id"] == income["id"]
        assert searched["groups"][2]["items"] == []
        assert searched["groups"][2]["total"] == full["totals"]["expense_total"]

        by_label = (await _pl(c, q="Beban")).json()
        assert by_label["totals"] == full["totals"]
        assert by_label["total"] == 1
        assert by_label["items"][0]["account_id"] == expense["id"]

        by_code = (await _pl(c, q=income["code"])).json()
        assert by_code["totals"] == full["totals"]
        assert by_code["items"][0]["account_id"] == income["id"]


@pytest.mark.anyio
async def test_decimal_precision_safe():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Dec", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 10.10)
        await _post_receipt(c, bank["id"], income["id"], 20.20)
        body = (await _pl(c)).json()
        rev = _row(body, income["id"])
        assert rev["credit"] == 30.30
        assert rev["amount"] == 30.30
        assert body["totals"]["revenue_total"] == 30.30
        expected = Decimal("10.10") + Decimal("20.20")
        assert Decimal(str(rev["amount"])) == expected


@pytest.mark.anyio
async def test_rbac_and_no_auto_grant():
    admin = _user()
    async with await client_for(admin) as c:
        await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")

    denied = _user("viewer", perms=["cash_bank.view", "trial_balance.view", "general_ledger.view"])
    async with await client_for(denied) as c:
        bad = await _pl(c)
        assert bad.status_code == 403

    viewer = _user("viewer", perms=["profit_loss.view", "nav.ledger", "nav.ledger.profit_loss"])
    async with await client_for(viewer) as c:
        ok = await _pl(c)
        assert ok.status_code == 200

    assert "profit_loss.view" in ALL_PERMISSIONS
    assert "nav.ledger.profit_loss" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "profit_loss.view" not in perms, role
        assert "nav.ledger.profit_loss" not in perms, role


@pytest.mark.anyio
async def test_regression_reporting_map_and_ledgers():
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

        types = await c.get("/api/ledger/account-types")
        assert types.status_code == 200
        codes = {i["code"] for i in types.json()["items"]}
        assert {"REVE", "COGS", "EXPS", "OINC", "OEXP", "BANK"} <= codes

        listed_rcpt = await c.get("/api/cash-bank/receipts")
        assert listed_rcpt.status_code == 200
        assert any(i["id"] == rcpt["id"] and i["status"] == "posted" for i in listed_rcpt.json()["items"])
        listed_gj = await c.get("/api/ledger/general-journals")
        assert listed_gj.status_code == 200
        assert any(i["id"] == gj["id"] and i["status"] == "posted" for i in listed_gj.json()["items"])
        listed_ob = await c.get("/api/ledger/opening-balances")
        assert listed_ob.status_code == 200
        assert any(i["id"] == ob["id"] and i["status"] == "posted" for i in listed_ob.json()["items"])

        before = await _counts()
        pl = await _pl(c, start="2026-08-01", end="2026-09-30")
        assert pl.status_code == 200
        assert await _counts() == before
        body = pl.json()
        assert _row(body, income["id"])["amount"] == 1111.0
        assert _row(body, expense["id"])["amount"] == 255.0
        assert body["totals"]["net_profit"] == 856.0

        tb = await _tb(c, start="2026-08-01", end="2026-09-30")
        assert tb.status_code == 200
        cash_row = next(r for r in tb.json()["items"] if r["account_id"] == cash["id"])
        gl = await _gl(c, cash["id"], start="2026-08-01", end="2026-09-30")
        assert gl.status_code == 200
        assert gl.json()["total_debit"] == cash_row["period_debit"]
        assert gl.json()["total_credit"] == cash_row["period_credit"]
        assert pay["id"]
        assert _has_account(body, income["id"])
        assert not _has_account(body, cash["id"])
