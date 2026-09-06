"""Neraca Phase 1 — GET-only read model from journal_core."""
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
os.environ["DB_NAME"] = f"eternal_packaging_balance_sheet_test_{_worker}"
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

BS_PATH = "/api/ledger/balance-sheet"
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


async def _bs(client, as_of="2026-09-30", **extra):
    params = {"as_of_date": as_of}
    params.update({k: v for k, v in extra.items() if v is not None})
    return await client.get(BS_PATH, params=params)


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


def _all_rows(body):
    rows = []
    for key in ("assets", "liabilities", "equity"):
        rows.extend(body["groups"][key]["accounts"])
    return rows


def _row(body, account_id):
    return next(r for r in _all_rows(body) if r["account_id"] == account_id)


def _has_account(body, account_id):
    return any(r["account_id"] == account_id for r in _all_rows(body))


def _src_has_no_abs():
    src = Path(__file__).resolve().parents[1] / "services" / "balance_sheet.py"
    text = src.read_text(encoding="utf-8")
    assert "abs(" not in text
    assert "math.fabs" not in text


@pytest.mark.anyio
async def test_get_zero_write_twice():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank ZW", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        before = await _counts()
        r1 = await _bs(c)
        assert r1.status_code == 200, r1.text
        assert await _counts() == before
        r2 = await _bs(c)
        assert r2.status_code == 200
        assert await _counts() == before


@pytest.mark.anyio
async def test_as_of_date_required_and_valid():
    admin = _user()
    async with await client_for(admin) as c:
        missing = await c.get(BS_PATH)
        assert missing.status_code == 422
        bad = await _bs(c, as_of="not-a-date")
        assert bad.status_code == 400
        assert "tanggal" in bad.json()["detail"].lower()
        empty = await c.get(BS_PATH, params={"as_of_date": " "})
        assert empty.status_code in (400, 422)


@pytest.mark.anyio
async def test_future_journals_excluded():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Fut", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 5000, date="2026-09-05")
        await _post_receipt(c, bank["id"], income["id"], 9000, date="2026-10-02")
        body = (await _bs(c, as_of="2026-09-30")).json()
        assert _row(body, cash["id"])["amount"] == 5000.0
        assert body["summary"]["current_earnings"] == 5000.0


@pytest.mark.anyio
async def test_only_balance_sheet_coa_rows_income_excluded():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        cogs = await _coa(f"H{uuid.uuid4().hex[:6]}", "HPP", "COGS")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Beban", "EXPS")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        ap = await _coa(f"P{uuid.uuid4().hex[:6]}", "Utang", "APAY")
        body = (await _bs(c)).json()
        assert _has_account(body, cash["id"])
        assert _has_account(body, equity["id"])
        assert _has_account(body, ap["id"])
        assert not _has_account(body, income["id"])
        assert not _has_account(body, cogs["id"])
        assert not _has_account(body, expense["id"])
        for row in _all_rows(body):
            meta = ACCOUNT_TYPE_REPORTING_MAP[row["account_type"]]
            assert meta["statement"] == "BALANCE_SHEET"


@pytest.mark.anyio
async def test_signed_group_amounts_and_depr_contra():
    _src_has_no_abs()
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas Debit", "BANK")
        odd_asset = await _coa(f"B{uuid.uuid4().hex[:6]}", "Aset Kredit", "OCAS")
        fixed = await _coa(f"F{uuid.uuid4().hex[:6]}", "Aset Tetap", "FASS")
        depr = await _coa(f"D{uuid.uuid4().hex[:6]}", "Akumulasi Depr", "DEPR")
        ap = await _coa(f"P{uuid.uuid4().hex[:6]}", "Utang Kredit", "APAY")
        odd_liab = await _coa(f"L{uuid.uuid4().hex[:6]}", "Utang Debit", "OCLY")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal Kredit", "EQTY")
        odd_eq = await _coa(f"U{uuid.uuid4().hex[:6]}", "Ekuitas Debit", "EQTY")

        await _post_gj(c, cash["id"], equity["id"], 100)
        await _post_gj(c, equity["id"], odd_asset["id"], 15)
        await _post_gj(c, fixed["id"], equity["id"], 100)
        await _post_gj(c, equity["id"], depr["id"], 20)
        await _post_gj(c, cash["id"], ap["id"], 40)
        await _post_gj(c, odd_liab["id"], cash["id"], 8)
        await _post_gj(c, odd_eq["id"], cash["id"], 6)

        body = (await _bs(c)).json()
        assert _row(body, cash["id"])["amount"] == 126.0
        assert _row(body, odd_asset["id"])["amount"] == -15.0
        assert _row(body, fixed["id"])["amount"] == 100.0
        depr_row = _row(body, depr["id"])
        assert depr_row["account_type"] == "DEPR"
        assert depr_row["report_group"] == "asset"
        assert depr_row["normal_balance"] == "credit"
        assert depr_row["credit"] == 20.0
        assert depr_row["amount"] == -20.0
        assert _row(body, ap["id"])["amount"] == 40.0
        assert _row(body, odd_liab["id"])["amount"] == -8.0
        assert _row(body, equity["id"])["amount"] == 165.0
        assert _row(body, odd_eq["id"])["amount"] == -6.0
        assert body["groups"]["assets"]["total"] == 191.0
        assert body["summary"]["total_assets"] == 191.0
        assert body["summary"]["is_balanced"] is True


@pytest.mark.anyio
async def test_draft_excluded_and_reversal_reflected():
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
        empty = (await _bs(c)).json()
        assert _row(empty, cash["id"])["amount"] == 0
        assert empty["summary"]["current_earnings"] == 0
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0

        payment = await _post_payment(c, bank["id"], expense["id"], 30000, date="2026-09-06")
        before = (await _bs(c)).json()
        assert _row(before, cash["id"])["amount"] == -30000.0
        assert before["summary"]["current_earnings"] == -30000.0
        rev = await c.post(f"/api/cash-bank/payments/{payment['id']}/reverse")
        assert rev.status_code == 200
        after = (await _bs(c)).json()
        cash_row = _row(after, cash["id"])
        assert cash_row["debit"] == 30000.0
        assert cash_row["credit"] == 30000.0
        assert cash_row["amount"] == 0
        assert after["summary"]["current_earnings"] == 0
        assert after["summary"]["is_balanced"] is True


@pytest.mark.anyio
async def test_exact_account_isolation_same_name():
    admin = _user()
    async with await client_for(admin) as c:
        a1 = await _coa(f"X{uuid.uuid4().hex[:6]}", "Kas Operasional", "BANK")
        a2 = await _coa(f"Y{uuid.uuid4().hex[:6]}", "Kas Operasional", "BANK")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        await _post_gj(c, a1["id"], equity["id"], 8000, desc="Hanya akun 1")
        body = (await _bs(c)).json()
        r1 = _row(body, a1["id"])
        r2 = _row(body, a2["id"])
        assert r1["name"] == r2["name"] == "Kas Operasional"
        assert r1["account_id"] != r2["account_id"]
        assert r1["amount"] == 8000.0
        assert r2["amount"] == 0
        assert r1["drilldown"]["account_id"] == a1["id"]
        assert r2["drilldown"]["account_id"] == a2["id"]
        assert r1["drilldown"]["end_date"] == "2026-09-30"
        assert "start_date" not in r1["drilldown"]


@pytest.mark.anyio
async def test_current_earnings_derived_profit_and_loss():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan Jual", "REVE")
        cogs = await _coa(f"H{uuid.uuid4().hex[:6]}", "HPP Barang", "COGS")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Beban Operasi", "EXPS")
        oinc = await _coa(f"N{uuid.uuid4().hex[:6]}", "Pendapatan Bunga", "OINC")
        oexp = await _coa(f"X{uuid.uuid4().hex[:6]}", "Beban Lain", "OEXP")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        bank = await _bank(c, "Bank CE", cash["id"])

        await _post_receipt(c, bank["id"], income["id"], 100000)
        await _post_gj(c, cogs["id"], cash["id"], 40000, desc="HPP")
        await _post_payment(c, bank["id"], expense["id"], 15000)
        await _post_gj(c, cash["id"], oinc["id"], 3000, desc="Bunga")
        await _post_gj(c, oexp["id"], cash["id"], 2000, desc="Beban lain")
        await _post_ob(c, cash["id"], equity["id"], 10000, cutoff="2026-08-01")

        body = (await _bs(c)).json()
        assert body["current_earnings"]["label"] == "Laba/Rugi Berjalan"
        assert body["current_earnings"]["derived"] is True
        assert "account_id" not in body["current_earnings"]
        assert body["summary"]["current_earnings"] == 46000.0
        assert body["summary"]["equity_accounts_total"] == 10000.0
        assert body["summary"]["total_equity"] == 56000.0
        assert body["summary"]["total_assets"] == 56000.0
        assert body["summary"]["total_liabilities"] == 0
        assert body["summary"]["total_liabilities_and_equity"] == 56000.0
        assert body["summary"]["balance_difference"] == 0
        assert body["summary"]["is_balanced"] is True

        loss = await _post_gj(c, expense["id"], cash["id"], 70000, desc="Beban besar")
        assert loss
        after = (await _bs(c)).json()
        assert after["summary"]["current_earnings"] == -24000.0
        assert after["summary"]["total_equity"] == -14000.0
        assert after["summary"]["is_balanced"] is True

        names = {acc["name"] for acc in await server.db[COA_COLLECTION].find({}, {"_id": 0, "name": 1}).to_list(1000)}
        assert "Laba/Rugi Berjalan" not in names


@pytest.mark.anyio
async def test_existing_closing_journal_naturally_reflected():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Laba Ditahan", "EQTY")
        bank = await _bank(c, "Bank Close", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 25000)
        open_pl = (await _bs(c)).json()
        assert open_pl["summary"]["current_earnings"] == 25000.0
        assert open_pl["summary"]["equity_accounts_total"] == 0
        await _post_gj(c, income["id"], equity["id"], 25000, desc="Tutup buku")
        closed = (await _bs(c)).json()
        assert closed["summary"]["current_earnings"] == 0
        assert closed["summary"]["equity_accounts_total"] == 25000.0
        assert closed["summary"]["total_equity"] == 25000.0
        assert closed["summary"]["total_assets"] == 25000.0
        assert closed["summary"]["is_balanced"] is True


@pytest.mark.anyio
async def test_unbalanced_source_difference_visible():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        jid = str(uuid.uuid4())
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": jid,
            "number": "JE-UNBAL",
            "date": "2026-09-05",
            "source_type": "test_unbalanced",
            "source_id": jid,
            "description": "Intentional imbalance",
            "status": "posted",
        })
        await server.db[JOURNAL_LINES].insert_one({
            "id": str(uuid.uuid4()),
            "journal_id": jid,
            "account_id": cash["id"],
            "debit": 999.0,
            "credit": 0,
            "description": "Orphan debit",
        })
        body = (await _bs(c)).json()
        assert body["summary"]["total_assets"] == 999.0
        assert body["summary"]["total_liabilities_and_equity"] == 0
        assert body["summary"]["balance_difference"] == 999.0
        assert body["summary"]["is_balanced"] is False


@pytest.mark.anyio
async def test_search_rows_only_keeps_summary_and_earnings():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas Unik Alpha", "BANK")
        ap = await _coa(f"P{uuid.uuid4().hex[:6]}", "Utang Usaha", "APAY")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Filt", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 100000)
        await _post_gj(c, cash["id"], ap["id"], 5000, desc="Utang")
        full = (await _bs(c)).json()
        searched = (await _bs(c, q="Kas Unik Alpha")).json()
        assert searched["summary"] == full["summary"]
        assert searched["current_earnings"] == full["current_earnings"]
        assert searched["account_count"] == full["account_count"]
        assert searched["total"] == 1
        assert searched["groups"]["assets"]["accounts"][0]["account_id"] == cash["id"]
        assert searched["groups"]["assets"]["total"] == full["summary"]["total_assets"]
        assert searched["groups"]["liabilities"]["accounts"] == []
        assert searched["groups"]["liabilities"]["total"] == full["summary"]["total_liabilities"]
        assert searched["groups"]["equity"]["total"] == full["groups"]["equity"]["total"]

        by_label = (await _bs(c, q="Utang Usaha")).json()
        assert by_label["summary"] == full["summary"]
        assert by_label["total"] == 1
        assert by_label["groups"]["liabilities"]["accounts"][0]["account_id"] == ap["id"]

        by_code = (await _bs(c, q=cash["code"])).json()
        assert by_code["summary"] == full["summary"]
        assert by_code["groups"]["assets"]["accounts"][0]["account_id"] == cash["id"]


@pytest.mark.anyio
async def test_decimal_precision_safe():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Dec", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 10.10)
        await _post_receipt(c, bank["id"], income["id"], 20.20)
        body = (await _bs(c)).json()
        cash_row = _row(body, cash["id"])
        assert cash_row["amount"] == 30.30
        assert body["summary"]["current_earnings"] == 30.30
        assert body["summary"]["total_assets"] == 30.30
        expected = Decimal("10.10") + Decimal("20.20")
        assert Decimal(str(cash_row["amount"])) == expected
        assert Decimal(str(body["summary"]["balance_difference"])) == Decimal("0.00")


@pytest.mark.anyio
async def test_rbac_and_no_auto_grant():
    admin = _user()
    async with await client_for(admin) as c:
        await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")

    denied = _user("viewer", perms=["cash_bank.view", "trial_balance.view", "profit_loss.view", "general_ledger.view"])
    async with await client_for(denied) as c:
        bad = await _bs(c)
        assert bad.status_code == 403

    viewer = _user("viewer", perms=["balance_sheet.view", "nav.ledger", "nav.ledger.balance_sheet"])
    async with await client_for(viewer) as c:
        ok = await _bs(c)
        assert ok.status_code == 200

    assert "balance_sheet.view" in ALL_PERMISSIONS
    assert "nav.ledger.balance_sheet" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "balance_sheet.view" not in perms, role
        assert "nav.ledger.balance_sheet" not in perms, role


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
        assert {"REVE", "COGS", "EXPS", "OINC", "OEXP", "BANK", "DEPR", "EQTY"} <= codes

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
        bs = await _bs(c, as_of="2026-09-30")
        assert bs.status_code == 200
        assert await _counts() == before
        body = bs.json()
        assert _row(body, cash["id"])["amount"] == 1300.0
        assert not _has_account(body, income["id"])
        assert body["summary"]["current_earnings"] == 856.0
        assert body["summary"]["equity_accounts_total"] == 444.0
        assert body["summary"]["total_equity"] == 1300.0
        assert body["summary"]["is_balanced"] is True

        pl = await _pl(c, start="2026-08-01", end="2026-09-30")
        assert pl.status_code == 200
        assert pl.json()["totals"]["net_profit"] == 856.0

        tb = await _tb(c, start="2026-08-01", end="2026-09-30")
        assert tb.status_code == 200
        cash_row = next(r for r in tb.json()["items"] if r["account_id"] == cash["id"])
        gl = await _gl(c, cash["id"], start="2026-08-01", end="2026-09-30")
        assert gl.status_code == 200
        assert gl.json()["total_debit"] == cash_row["period_debit"]
        assert gl.json()["total_credit"] == cash_row["period_credit"]
        assert pay["id"]
