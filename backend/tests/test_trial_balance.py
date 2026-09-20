"""Neraca Saldo Phase 1 — read model from journal_core."""
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
os.environ["DB_NAME"] = f"eternal_packaging_trial_balance_test_{_worker}"
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
from services.financial_statement_common import (
    ACCOUNT_READ_BATCH_SIZE,
    JOURNAL_ID_BATCH_SIZE,
    parse_financial_date,
    FinancialStatementDateError,
)
from services.opening_balance import COLLECTION as OB_COLLECTION, ensure_indexes as ensure_ob
from services import financial_statement_common as fsc

pytestmark = pytest.mark.anyio

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
        "settings": await db["app_settings"].count_documents({}),
        "periods": await db["accounting_periods"].count_documents({}),
    }


def _assert_balanced(totals):
    assert totals["opening_debit"] == totals["opening_credit"]
    assert totals["period_debit"] == totals["period_credit"]
    assert totals["ending_debit"] == totals["ending_credit"]
    assert totals["opening_diff"] == 0
    assert totals["period_diff"] == 0
    assert totals["ending_diff"] == 0


def _row(body, account_id):
    return next(r for r in body["items"] if r["account_id"] == account_id)


@pytest.mark.anyio
async def test_get_zero_write():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank ZW", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000)
        before = await _counts()
        r = await _tb(c)
        assert r.status_code == 200, r.text
        assert await _counts() == before
        r2 = await _tb(c)
        assert r2.status_code == 200
        assert await _counts() == before


@pytest.mark.anyio
async def test_invalid_date_range_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bad = await _tb(c, start="2026-09-30", end="2026-09-01")
        assert bad.status_code == 400
        assert "tanggal" in bad.json()["detail"].lower()


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

        tb = await _tb(c, start="2026-08-01", end="2026-09-30")
        assert tb.status_code == 200
        body = tb.json()
        cash_row = _row(body, cash["id"])
        assert cash_row["opening_debit"] == 0
        assert cash_row["opening_credit"] == 0
        assert cash_row["period_debit"] == 0
        assert cash_row["period_credit"] == 0
        assert cash_row["ending_debit"] == 0
        assert cash_row["ending_credit"] == 0
        _assert_balanced(body["totals"])
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


@pytest.mark.anyio
async def test_opening_period_ending_and_sources():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas Utama", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        bank = await _bank(c, "Bank A", cash["id"])

        await _post_ob(c, cash["id"], equity["id"], 40000, cutoff="2026-08-31", desc="Saldo Awal Kas")
        await _post_receipt(c, bank["id"], income["id"], 50000, date="2026-08-31", desc="Opening seed")
        await _post_receipt(c, bank["id"], income["id"], 100000, date="2026-09-05", desc="Terima periode")
        await _post_payment(c, bank["id"], expense["id"], 30000, date="2026-09-06", desc="Bayar periode")
        await _post_gj(c, expense["id"], cash["id"], 5000, date="2026-09-08", desc="JU kredit kas")

        tb = await _tb(c)
        assert tb.status_code == 200, tb.text
        body = tb.json()
        cash_row = _row(body, cash["id"])
        assert cash_row["opening_debit"] == 90000.0
        assert cash_row["opening_credit"] == 0
        assert cash_row["period_debit"] == 100000.0
        assert cash_row["period_credit"] == 35000.0
        assert cash_row["ending_debit"] == 155000.0
        assert cash_row["ending_credit"] == 0

        income_row = _row(body, income["id"])
        assert income_row["opening_credit"] == 50000.0
        assert income_row["period_credit"] == 100000.0
        assert income_row["ending_credit"] == 150000.0

        expense_row = _row(body, expense["id"])
        assert expense_row["period_debit"] == 35000.0
        assert expense_row["ending_debit"] == 35000.0

        equity_row = _row(body, equity["id"])
        assert equity_row["opening_credit"] == 40000.0
        assert equity_row["period_debit"] == 0
        assert equity_row["ending_credit"] == 40000.0

        _assert_balanced(body["totals"])
        assert body["account_count"] == 4
        assert body["total"] == 4

        gl = (await _gl(c, cash["id"])).json()
        assert gl["opening_balance"] == 90000.0
        assert gl["total_debit"] == cash_row["period_debit"]
        assert gl["total_credit"] == cash_row["period_credit"]
        assert gl["ending_balance"] == 155000.0


@pytest.mark.anyio
async def test_reversal_reflected_as_opposite_movement():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        bank = await _bank(c, "Bank Rev", cash["id"])
        payment = await _post_payment(c, bank["id"], expense["id"], 30000, date="2026-09-06")
        gj = await _post_gj(c, expense["id"], cash["id"], 4000, date="2026-09-08", desc="JU reverse me")

        before = (await _tb(c)).json()
        cash_before = _row(before, cash["id"])
        assert cash_before["period_credit"] == 34000.0
        assert cash_before["ending_credit"] == 34000.0

        rev_pay = await c.post(f"/api/cash-bank/payments/{payment['id']}/reverse")
        assert rev_pay.status_code == 200
        rev_gj = await c.post(f"/api/ledger/general-journals/{gj['id']}/reverse")
        assert rev_gj.status_code == 200

        after = (await _tb(c)).json()
        cash_after = _row(after, cash["id"])
        assert cash_after["period_debit"] == 34000.0
        assert cash_after["period_credit"] == 34000.0
        assert cash_after["ending_debit"] == 0
        assert cash_after["ending_credit"] == 0
        _assert_balanced(after["totals"])


@pytest.mark.anyio
async def test_exact_account_isolation_same_name():
    admin = _user()
    async with await client_for(admin) as c:
        a1 = await _coa(f"X{uuid.uuid4().hex[:6]}", "Biaya Kantor", "EXPS")
        a2 = await _coa(f"Y{uuid.uuid4().hex[:6]}", "Biaya Kantor", "EXPS")
        cash = await _coa(f"C{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        await _post_gj(c, a1["id"], cash["id"], 8000, desc="Hanya akun 1")
        tb = (await _tb(c)).json()
        r1 = _row(tb, a1["id"])
        r2 = _row(tb, a2["id"])
        assert r1["name"] == r2["name"] == "Biaya Kantor"
        assert r1["account_id"] != r2["account_id"]
        assert r1["period_debit"] == 8000.0
        assert r2["period_debit"] == 0
        assert r2["ending_debit"] == 0
        _assert_balanced(tb["totals"])


@pytest.mark.anyio
async def test_deterministic_ordering():
    admin = _user()
    async with await client_for(admin) as c:
        z = await _coa(f"Z{uuid.uuid4().hex[:6]}", "Zulu", "EXPS")
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Alpha", "BANK")
        m = await _coa(f"M{uuid.uuid4().hex[:6]}", "Mike", "REVE")
        tb = (await _tb(c)).json()
        codes = [r["code"] for r in tb["items"]]
        assert codes == sorted(codes)
        ids_by_code = {}
        for row in tb["items"]:
            ids_by_code.setdefault(row["code"], []).append(row["account_id"])
        for ids in ids_by_code.values():
            assert ids == sorted(ids)
        assert {r["account_id"] for r in tb["items"]} == {z["id"], a["id"], m["id"]}


@pytest.mark.anyio
async def test_search_rows_only_keeps_global_totals():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas Utama Unik", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        expense = await _coa(f"E{uuid.uuid4().hex[:6]}", "Biaya ATK", "EXPS")
        bank = await _bank(c, "Bank Filt", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 100000)
        await _post_gj(c, expense["id"], cash["id"], 5000, desc="Gamma Jurnal")

        full = (await _tb(c)).json()
        searched = (await _tb(c, q="Kas Utama Unik")).json()
        assert searched["totals"] == full["totals"]
        assert searched["account_count"] == full["account_count"]
        assert searched["total"] == 1
        assert searched["items"][0]["account_id"] == cash["id"]
        _assert_balanced(searched["totals"])

        by_type = (await _tb(c, q="EXPS")).json()
        assert by_type["totals"] == full["totals"]
        assert by_type["total"] == 1
        assert by_type["items"][0]["account_id"] == expense["id"]

        by_code = (await _tb(c, q=cash["code"])).json()
        assert by_code["totals"] == full["totals"]
        assert by_code["total"] == 1
        assert by_code["items"][0]["account_id"] == cash["id"]


@pytest.mark.anyio
async def test_decimal_precision_safe():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Dec", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 10.10)
        await _post_receipt(c, bank["id"], income["id"], 20.20)
        tb = (await _tb(c)).json()
        cash_row = _row(tb, cash["id"])
        assert cash_row["period_debit"] == 30.30
        assert cash_row["ending_debit"] == 30.30
        assert tb["totals"]["period_debit"] == 30.30
        assert tb["totals"]["period_credit"] == 30.30
        expected = Decimal("10.10") + Decimal("20.20")
        assert Decimal(str(cash_row["period_debit"])) == expected
        _assert_balanced(tb["totals"])


@pytest.mark.anyio
async def test_rbac_and_no_auto_grant():
    admin = _user()
    async with await client_for(admin) as c:
        await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")

    denied = _user("viewer", perms=["cash_bank.view", "general_ledger.view"])
    async with await client_for(denied) as c:
        bad = await _tb(c)
        assert bad.status_code == 403

    viewer = _user("viewer", perms=["trial_balance.view", "nav.ledger", "nav.ledger.trial_balance"])
    async with await client_for(viewer) as c:
        ok = await _tb(c)
        assert ok.status_code == 200

    assert "trial_balance.view" in ALL_PERMISSIONS
    assert "nav.ledger.trial_balance" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "trial_balance.view" not in perms, role
        assert "nav.ledger.trial_balance" not in perms, role


@pytest.mark.anyio
async def test_buku_besar_and_voucher_regression():
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
        listed_gj = await c.get("/api/ledger/general-journals")
        assert listed_gj.status_code == 200
        assert any(i["id"] == gj["id"] and i["status"] == "posted" for i in listed_gj.json()["items"])
        listed_ob = await c.get("/api/ledger/opening-balances")
        assert listed_ob.status_code == 200
        assert any(i["id"] == ob["id"] and i["status"] == "posted" for i in listed_ob.json()["items"])

        before = await _counts()
        tb = await _tb(c, start="2026-08-01", end="2026-09-30")
        assert tb.status_code == 200
        assert await _counts() == before
        cash_row = _row(tb.json(), cash["id"])
        assert cash_row["period_debit"] == 1555.0  # 444 SA + 1111 receipt
        assert cash_row["period_credit"] == 255.0  # 222 pay + 33 JU
        gl = await _gl(c, cash["id"], start="2026-08-01", end="2026-09-30")
        assert gl.status_code == 200
        assert gl.json()["total_debit"] == cash_row["period_debit"]
        assert gl.json()["total_credit"] == cash_row["period_credit"]
        assert pay["id"]
        _assert_balanced(tb.json()["totals"])


def _statement_sources():
    root = Path(__file__).resolve().parents[1] / "services"
    return {
        name: (root / name).read_text(encoding="utf-8")
        for name in (
            "trial_balance.py",
            "profit_loss.py",
            "balance_sheet.py",
            "financial_statement_common.py",
        )
    }


@pytest.mark.anyio
async def test_no_fixed_read_caps_in_statement_sources():
    sources = _statement_sources()
    for name, text in sources.items():
        assert "to_list(100000)" not in text, name
        assert "to_list(500000)" not in text, name
        assert "to_list(5000)" not in text, name
    for name in ("trial_balance.py", "profit_loss.py", "balance_sheet.py"):
        assert "list_accounts" not in sources[name]


@pytest.mark.anyio
async def test_strict_financial_date_parser():
    assert parse_financial_date(
        "2026-09-30", empty_message="empty", invalid_message="invalid"
    ) == "2026-09-30"
    assert parse_financial_date(
        "2024-02-29", empty_message="empty", invalid_message="invalid"
    ) == "2024-02-29"
    for raw in (
        "2026-02-29",
        "2026-02-31",
        "2026-13-01",
        "2026-00-10",
        "2026-9-1",
        "abc",
        "",
        "2026-09-30T00:00:00",
        "2026-09-30 00:00:00",
    ):
        with pytest.raises(FinancialStatementDateError):
            parse_financial_date(raw, empty_message="empty", invalid_message="invalid")
    assert ACCOUNT_READ_BATCH_SIZE >= 1
    assert JOURNAL_ID_BATCH_SIZE >= 1


@pytest.mark.anyio
async def test_strict_dates_accepted_and_invalid_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        ok = await _tb(c, start="2024-02-29", end="2026-09-30")
        assert ok.status_code == 200, ok.text
        assert ok.json()["start_date"] == "2024-02-29"
        assert ok.json()["end_date"] == "2026-09-30"

        bad_start = await _tb(c, start="2026-02-31", end="2026-09-30")
        assert bad_start.status_code == 400
        assert "tanggal" in bad_start.json()["detail"].lower()

        bad_end = await _tb(c, start="2026-09-01", end="2026-02-29")
        assert bad_end.status_code == 400
        assert "tanggal" in bad_end.json()["detail"].lower()

        unpadded = await _tb(c, start="2026-9-1", end="2026-09-30")
        assert unpadded.status_code == 400


@pytest.mark.anyio
async def test_zero_balance_coa_and_account_batches_included(monkeypatch):
    monkeypatch.setattr(fsc, "ACCOUNT_READ_BATCH_SIZE", 2)
    admin = _user()
    async with await client_for(admin) as c:
        accounts = []
        for prefix, name, typ in (
            ("A", "Kas", "BANK"),
            ("B", "Piutang", "AREC"),
            ("C", "Persediaan", "INTR"),
            ("D", "Modal", "EQTY"),
            ("E", "Pendapatan", "REVE"),
        ):
            accounts.append(await _coa(f"{prefix}{uuid.uuid4().hex[:6]}", name, typ))
        tb = await _tb(c)
        assert tb.status_code == 200, tb.text
        body = tb.json()
        ids = {row["account_id"] for row in body["items"]}
        assert {acc["id"] for acc in accounts} <= ids
        assert body["account_count"] == 5
        unused = next(row for row in body["items"] if row["account_id"] == accounts[1]["id"])
        assert unused["opening_debit"] == 0
        assert unused["period_debit"] == 0
        assert unused["ending_debit"] == 0
        _assert_balanced(body["totals"])


@pytest.mark.anyio
async def test_multi_batch_journals_all_included(monkeypatch):
    monkeypatch.setattr(fsc, "JOURNAL_ID_BATCH_SIZE", 2)
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        income = await _coa(f"I{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        bank = await _bank(c, "Bank Batch", cash["id"])
        await _post_receipt(c, bank["id"], income["id"], 1000, date="2026-09-01")
        await _post_receipt(c, bank["id"], income["id"], 2000, date="2026-09-10")
        await _post_receipt(c, bank["id"], income["id"], 4000, date="2026-09-30")
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 3
        body = (await _tb(c)).json()
        cash_row = _row(body, cash["id"])
        assert cash_row["period_debit"] == 7000.0
        assert cash_row["ending_debit"] == 7000.0
        assert body["totals"]["period_debit"] == 7000.0
        _assert_balanced(body["totals"])


@pytest.mark.anyio
async def test_missing_status_journal_still_included():
    admin = _user()
    async with await client_for(admin) as c:
        cash = await _coa(f"A{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        equity = await _coa(f"Q{uuid.uuid4().hex[:6]}", "Modal", "EQTY")
        jid = str(uuid.uuid4())
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": jid,
            "number": "JE-NOSTATUS",
            "date": "2026-09-05",
            "source_type": "legacy_no_status",
            "source_id": jid,
            "description": "Historical row without status",
        })
        await server.db[JOURNAL_LINES].insert_many([
            {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": cash["id"], "debit": 50.0, "credit": 0},
            {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": equity["id"], "debit": 0, "credit": 50.0},
        ])
        body = (await _tb(c)).json()
        assert _row(body, cash["id"])["period_debit"] == 50.0
        _assert_balanced(body["totals"])
