"""Payroll payment posting — Phase 2.

FINAL does not pay. Accrual stays a separate POST. Payment is its own
document: create, approve, then post Dr Utang Gaji / Cr BANK.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_payroll_payment_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.accounting_periods import close_period
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cash_bank
from services.cash_bank_mutasi import get_mutasi
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.hr_payroll import (
    ATTENDANCE,
    COMPONENTS,
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    STATUS_APPROVAL,
    STATUS_FINAL,
    STATUS_REVIEW,
    ensure_indexes,
)
from services.journal_core import ensure_indexes as ensure_journal_indexes
from services.payroll_accounting_settings import SETTINGS_KEY
from services.payroll_payment import PAYMENT_LINES, PAYMENTS, SOURCE_TYPE


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await ensure_indexes(server.db)
    await ensure_coa(server.db)
    await ensure_journal_indexes(server.db)
    await ensure_cash_bank(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES, PAYMENTS, PAYMENT_LINES):
        await server.db[coll].delete_many({})
    await server.db.journal_entries.delete_many({})
    await server.db.journal_lines.delete_many({})
    await server.db.accounting_periods.delete_many({})
    await server.db.cash_bank_accounts.delete_many({})
    await server.db.cash_bank_payments.delete_many({})
    await server.db.cash_bank_receipts.delete_many({})
    await server.db.cash_bank_transfers.delete_many({})
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    user = {
        "id": "admin-pyp",
        "email": "admin@eternalpackaging.com",
        "name": "Admin PYP",
        "role": "admin",
    }
    app.dependency_overrides[current_user] = lambda: user
    return user


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _setup_accounts():
    return {
        "payable": await _coa("21", "Utang Gaji", "OCLY"),
        "salary": await _coa("51", "Beban Gaji", "EXPS"),
        "bank": await _coa("11", "Bank Operasional", "BANK"),
    }


async def _bank(account: dict) -> dict:
    return await create_cash_bank(
        server.db,
        {"name": f"Bank {uuid.uuid4().hex[:4]}", "account_id": account["id"], "active": True},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _finalize(c, *, year: int, month: int, map_accounts=None):
    emp = (await c.post("/api/hr-payroll/employees", json={
        "name": "Andi Payroll",
        "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
        "role": "operator",
        "division": "operator",
        "salary_profile": {"base_salary": 1_000_000, "allowances": []},
    })).json()["employee"]
    created = await c.post("/api/hr-payroll/periods", json={
        "year": year,
        "month": month,
        "start_date": f"{year}-{month:02d}-01",
        "end_date": f"{year}-{month:02d}-28",
    })
    assert created.status_code == 200, created.text
    period = created.json()["period"]
    calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
    assert calc.status_code == 200, calc.text
    if map_accounts:
        saved = await c.put("/api/settings/payroll-accounting", json={
            "payroll_payable_account_id": map_accounts["payable"]["id"],
            "component_mappings": {
                "salary_profile:BASE": {
                    "treatment": "expense_debit",
                    "account_id": map_accounts["salary"]["id"],
                },
            },
        })
        assert saved.status_code == 200, saved.text
    for target in (STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL):
        moved = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": target},
        )
        assert moved.status_code == 200, moved.text
        period = moved.json()["period"]
    fresh = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"]
    return emp, fresh


async def _post_accrual(c, period_id: str):
    res = await c.post(f"/api/hr-payroll/periods/{period_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()


async def _payable_credit(period_id: str) -> float:
    journal = await server.db.journal_entries.find_one(
        {"source_type": "payroll_period", "source_id": period_id, "reversal_of": None},
        {"_id": 0, "id": 1},
    )
    assert journal
    lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(20)
    credits = [
        line["credit"]
        for line in lines
        if line.get("credit") and str(line.get("description") or "").endswith("Utang Gaji")
    ]
    assert len(credits) == 1
    return credits[0]


async def _payment_journals(payment_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": payment_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(10)


@pytest.mark.anyio
async def test_create_payroll_payment_succeeds():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        emp, period = await _finalize(c, year=2093, month=1, map_accounts=accounts)
        assert await server.db[PAYMENTS].count_documents({}) == 0
        await _post_accrual(c, period["id"])
        credit = await _payable_credit(period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-01-28"},
        )
        assert created.status_code == 200, created.text
        payment = created.json()["payment"]
        again = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-01-28"},
        )
        assert again.status_code == 409, again.text
    assert payment["status"] == "DRAFT"
    assert payment["payroll_period_id"] == period["id"]
    assert payment["cash_bank_account_id"] == bank["id"]
    assert payment["total_amount"] == credit == 1_000_000
    assert payment["payment_number"].startswith("PYB")
    assert payment["created_by"]["id"] == "admin-pyp"
    assert payment["created_at"]
    assert len(payment["lines"]) == 1
    assert payment["lines"][0]["employee_id"] == emp["id"]
    assert payment["lines"][0]["employee_name"] == "Andi Payroll"
    assert payment["lines"][0]["net_salary"] == 1_000_000
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 1
    assert await server.db[PAYMENT_LINES].count_documents({"payment_id": payment["id"]}) == 1
    assert await _payment_journals(payment["id"]) == []
    stored_period = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored_period["status"] == STATUS_FINAL
    assert await server.db.cash_bank_payments.count_documents({}) == 0


@pytest.mark.anyio
async def test_get_period_payment_is_read_only():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=9, map_accounts=accounts)
        empty = await c.get(f"/api/hr-payroll/periods/{period['id']}/payments")
        assert empty.status_code == 200, empty.text
        assert empty.json()["payment"] is None
        assert await server.db[PAYMENTS].count_documents({}) == 0
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-09-20"},
        )
        assert created.status_code == 200, created.text
        listed = await c.get(f"/api/hr-payroll/periods/{period['id']}/payments")
        assert listed.status_code == 200, listed.text
        assert listed.json()["payment"]["id"] == created.json()["payment"]["id"]
        assert listed.json()["payment"]["status"] == "DRAFT"
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 1


@pytest.mark.anyio
async def test_payment_without_accrual_journal_fails():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=2, map_accounts=accounts)
        journal = await server.db.journal_entries.find_one(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None},
            {"_id": 0, "id": 1},
        )
        assert journal
        await server.db.journal_lines.delete_many({"journal_id": journal["id"]})
        await server.db.journal_entries.delete_one({"id": journal["id"]})
        await server.db[PERIODS].update_one(
            {"id": period["id"]},
            {"$unset": {"accounting_journal_id": "", "accounting_posted_at": "", "accounting_posted_by": ""}},
        )
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-02-28"},
        )
        assert created.status_code == 409, created.text
        assert "akrual" in created.text.lower()
    assert await server.db[PAYMENTS].count_documents({}) == 0
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL


@pytest.mark.anyio
async def test_approve_payment_succeeds():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=3, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-03-28"},
        )
        assert created.status_code == 200, created.text
        payment_id = created.json()["payment"]["id"]
        approved = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert approved.status_code == 200, approved.text
        body = approved.json()["payment"]
        second = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert second.status_code == 200, second.text
    assert body["status"] == "APPROVED"
    assert body["approved_by"]["id"] == "admin-pyp"
    assert body["approved_at"]
    assert second.json()["payment"]["approved_at"] == body["approved_at"]
    assert await _payment_journals(payment_id) == []
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL


@pytest.mark.anyio
async def test_post_payment_creates_balanced_journal():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=4, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-04-20"},
        )
        payment = created.json()["payment"]
        too_early = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        assert too_early.status_code == 409, too_early.text
        approved = await c.post(f"/api/hr-payroll/payments/{payment['id']}/approve")
        assert approved.status_code == 200, approved.text
        posted = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        body = posted.json()
    assert body["status"] == "POSTED"
    assert body["payment_status"] == "PAID"
    assert body["journal_id"]
    rows = await _payment_journals(payment["id"])
    assert len(rows) == 1
    journal = rows[0]
    assert journal["id"] == body["journal_id"]
    assert journal["source_type"] == "payroll_payment"
    assert journal["source_id"] == payment["id"]
    assert journal["source_id"] != period["id"]
    assert journal["date"] == "2093-04-20"
    assert journal["status"] == "posted"
    lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(10)
    debit = sum(line["debit"] for line in lines)
    credit = sum(line["credit"] for line in lines)
    assert debit == credit == 1_000_000
    by_account = {line["account_id"]: line for line in lines}
    assert by_account[accounts["payable"]["id"]]["debit"] == 1_000_000
    assert by_account[accounts["bank"]["id"]]["credit"] == 1_000_000
    stored_payment = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    assert stored_payment["status"] == "PAID"
    assert stored_payment["accounting_journal_id"] == journal["id"]
    assert stored_payment["paid_by"]["id"] == "admin-pyp"
    assert stored_payment["paid_at"]
    stored_period = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored_period["status"] == STATUS_FINAL
    assert await server.db.cash_bank_payments.count_documents({}) == 0
    assert await server.db.cash_bank_receipts.count_documents({}) == 0
    assert await server.db.cash_bank_transfers.count_documents({}) == 0


@pytest.mark.anyio
async def test_retry_post_does_not_duplicate():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=5, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-05-20"},
        )
        payment_id = created.json()["payment"]["id"]
        await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        first = await c.post(f"/api/hr-payroll/payments/{payment_id}/post-accounting")
        assert first.status_code == 200, first.text
        stamped = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0, "paid_at": 1, "paid_by": 1})
        second = await c.post(f"/api/hr-payroll/payments/{payment_id}/post-accounting")
        assert second.status_code == 200, second.text
    assert first.json()["journal_id"] == second.json()["journal_id"]
    assert first.json()["journal_number"] == second.json()["journal_number"]
    assert "sudah ada" in second.json()["message"]
    assert len(await _payment_journals(payment_id)) == 1
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 1
    again = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0})
    assert again["status"] == "PAID"
    assert again["paid_at"] == stamped["paid_at"]
    assert again["accounting_journal_id"] == first.json()["journal_id"]


@pytest.mark.anyio
async def test_non_bank_account_is_rejected():
    accounts = await _setup_accounts()
    expense = await _coa("61", "Beban Lain", "EXPS")
    legacy_id = str(uuid.uuid4())
    await server.db.cash_bank_accounts.insert_one({
        "id": legacy_id,
        "name": "Kas Non Bank",
        "account_id": expense["id"],
        "active": True,
    })
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=6, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        rejected = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": legacy_id, "payment_date": "2093-06-20"},
        )
        assert rejected.status_code == 400, rejected.text
        assert "BANK" in rejected.text
    assert await server.db[PAYMENTS].count_documents({}) == 0
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0


@pytest.mark.anyio
async def test_cash_bank_mutasi_reads_payroll_payment():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=7, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-07-18"},
        )
        payment = created.json()["payment"]
        await c.post(f"/api/hr-payroll/payments/{payment['id']}/approve")
        posted = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
    mutasi = await get_mutasi(
        server.db, cash_bank_id=bank["id"], start_date="2093-07-01", end_date="2093-07-31"
    )
    matches = [row for row in mutasi["items"] if row["source_type"] == "payroll_payment"]
    assert len(matches) == 1
    row = matches[0]
    assert row["source_label"] == "Pembayaran Payroll"
    assert row["source_id"] == payment["id"]
    assert row["transaction_number"] == payment["payment_number"]
    assert row["credit"] == 1_000_000
    assert row["debit"] == 0
    assert mutasi["ending_balance"] == -1_000_000
    assert await server.db.cash_bank_payments.count_documents({}) == 0


@pytest.mark.anyio
async def test_reopen_after_posted_payment_fails():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=8, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-08-18"},
        )
        payment_id = created.json()["payment"]["id"]
        await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        posted = await c.post(f"/api/hr-payroll/payments/{payment_id}/post-accounting")
        assert posted.status_code == 200, posted.text
        blocked = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/reopen",
            json={"reason": "koreksi setelah bayar"},
        )
        assert blocked.status_code == 409, blocked.text
        await server.db.journal_entries.update_one(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None},
            {"$set": {"status": "reversed"}},
        )
        still_blocked = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/reopen",
            json={"reason": "akrual sudah tidak posted"},
        )
        assert still_blocked.status_code == 409, still_blocked.text
        assert "pembayaran payroll" in still_blocked.text.lower()
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1, "group_id": 1})
    assert stored["status"] == STATUS_FINAL
    assert await server.db[PERIODS].count_documents({"group_id": stored["group_id"]}) == 1
    assert len(await _payment_journals(payment_id)) == 1


@pytest.mark.anyio
async def test_approve_uses_its_own_permission():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=9, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-09-20"},
        )
        assert created.status_code == 200, created.text
        payment_id = created.json()["payment"]["id"]

        def _clerk(override):
            app.dependency_overrides[current_user] = lambda: {
                "id": "clerk-pyp",
                "email": "clerk-pyp@eternalpackaging.com",
                "name": "Clerk PYP",
                "role": "payroll_clerk",
                "permissions_override": override,
            }

        _clerk({"payroll_payment.post": True})
        denied = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert denied.status_code == 403, denied.text
        assert "payroll_payment.approve" in denied.text
        stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0, "status": 1})
        assert stored["status"] == "DRAFT"

        _clerk({"payroll_payment.approve": True})
        approved = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["payment"]["status"] == "APPROVED"
        denied_post = await c.post(f"/api/hr-payroll/payments/{payment_id}/post-accounting")
        assert denied_post.status_code == 403, denied_post.text
        assert "payroll_payment.post" in denied_post.text
    assert await _payment_journals(payment_id) == []


@pytest.mark.anyio
async def test_payment_unique_index_is_created_without_insert():
    from services.payroll_payment import ACTIVE_PERIOD_INDEX, ensure_indexes

    await server.db[PAYMENTS].drop()
    await ensure_indexes(server.db)
    info = await server.db[PAYMENTS].index_information()
    full_unique = [
        spec for spec in info.values()
        if list(spec.get("key") or []) == [("payroll_period_id", 1)]
        and spec.get("unique")
        and not spec.get("partialFilterExpression")
    ]
    partial = info.get(ACTIVE_PERIOD_INDEX)
    assert not full_unique
    assert partial["unique"] is True
    assert partial["partialFilterExpression"]["status"]["$in"] == ["DRAFT", "APPROVED", "PAID"]
    assert await server.db[PAYMENTS].count_documents({}) == 0


async def _posted_payment(c, *, year: int, month: int, accounts: dict, bank: dict, day: str = "18"):
    _emp, period = await _finalize(c, year=year, month=month, map_accounts=accounts)
    await _post_accrual(c, period["id"])
    payment_date = f"{year}-{month:02d}-{day}"
    created = await c.post(
        f"/api/hr-payroll/periods/{period['id']}/payments",
        json={"cash_bank_account_id": bank["id"], "payment_date": payment_date},
    )
    assert created.status_code == 200, created.text
    payment = created.json()["payment"]
    approved = await c.post(f"/api/hr-payroll/payments/{payment['id']}/approve")
    assert approved.status_code == 200, approved.text
    posted = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
    assert posted.status_code == 200, posted.text
    return period, payment


@pytest.mark.anyio
async def test_reverse_paid_payment_succeeds():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period, payment = await _posted_payment(c, year=2093, month=10, accounts=accounts, bank=bank)
        before = await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]})
        reversed_res = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "koreksi pembayaran"},
        )
        assert reversed_res.status_code == 200, reversed_res.text
        body = reversed_res.json()
    assert body["payment_status"] == "REVERSED"
    assert body["payment"]["reversal_reason"] == "koreksi pembayaran"
    assert body["payment"]["reversed_by"]["id"] == "admin-pyp"
    assert body["payment"]["reversed_at"]
    assert body["reversal_journal_id"]
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == before == 1
    original = (await _payment_journals(payment["id"]))[0]
    assert original["status"] == "reversed"
    reversals = await server.db.journal_entries.find(
        {"source_type": "payroll_payment_reversal", "source_id": payment["id"]},
        {"_id": 0},
    ).to_list(10)
    assert len(reversals) == 1
    assert reversals[0]["id"] == body["journal_id"]
    assert reversals[0]["reversal_of"] == original["id"]
    assert reversals[0]["date"] == "2093-10-18"
    lines = await server.db.journal_lines.find({"journal_id": reversals[0]["id"]}, {"_id": 0}).to_list(10)
    debit = sum(line["debit"] for line in lines)
    credit = sum(line["credit"] for line in lines)
    assert debit == credit == 1_000_000
    by_account = {line["account_id"]: line for line in lines}
    assert by_account[accounts["bank"]["id"]]["debit"] == 1_000_000
    assert by_account[accounts["payable"]["id"]]["credit"] == 1_000_000
    stored = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    assert stored["status"] == "REVERSED"
    assert stored["reversal_journal_id"] == reversals[0]["id"]
    assert await server.db.cash_bank_payments.count_documents({}) == 0


@pytest.mark.anyio
async def test_reverse_retry_returns_the_same_journal():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _period, payment = await _posted_payment(c, year=2093, month=11, accounts=accounts, bank=bank)
        first = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "koreksi pertama"},
        )
        assert first.status_code == 200, first.text
        stamped = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0, "reversed_at": 1})
        second = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "koreksi ulang"},
        )
        assert second.status_code == 200, second.text
    assert first.json()["journal_id"] == second.json()["journal_id"]
    assert "sudah ada" in second.json()["message"]
    assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment_reversal"}) == 1
    again = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    assert again["status"] == "REVERSED"
    assert again["reversed_at"] == stamped["reversed_at"]
    assert again["reversal_reason"] == "koreksi pertama"
    assert await server.db[PAYMENTS].count_documents({}) == 1


@pytest.mark.anyio
async def test_reverse_draft_payment_is_rejected():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=12, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-12-18"},
        )
        assert created.status_code == 200, created.text
        payment_id = created.json()["payment"]["id"]
        rejected = await c.post(
            f"/api/hr-payroll/payments/{payment_id}/reverse-accounting",
            json={"reason": "belum dibayar"},
        )
        assert rejected.status_code == 409, rejected.text
        assert "PAID" in rejected.text
    stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0, "status": 1, "reversal_journal_id": 1})
    assert stored["status"] == "DRAFT"
    assert stored.get("reversal_journal_id") in (None, "")
    assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment_reversal"}) == 0


@pytest.mark.anyio
async def test_reverse_closed_period_is_rejected():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _period, payment = await _posted_payment(c, year=2094, month=1, accounts=accounts, bank=bank)
        await close_period(
            server.db,
            "2094-01",
            user={"id": "admin-pyp", "name": "Admin", "role": "admin"},
            reason="tutup uji",
        )
        rejected = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "periode tertutup"},
        )
        assert rejected.status_code == 409, rejected.text
    stored = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "PAID"
    assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment_reversal"}) == 0
    original = (await _payment_journals(payment["id"]))[0]
    assert original["status"] == "posted"


@pytest.mark.anyio
async def test_cash_bank_mutasi_reads_payroll_payment_reversal():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _period, payment = await _posted_payment(c, year=2094, month=2, accounts=accounts, bank=bank, day="12")
        reversed_res = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "saldo bank kembali"},
        )
        assert reversed_res.status_code == 200, reversed_res.text
    mutasi = await get_mutasi(
        server.db, cash_bank_id=bank["id"], start_date="2094-02-01", end_date="2094-02-28"
    )
    by_type = {row["source_type"]: row for row in mutasi["items"]}
    assert by_type["payroll_payment"]["source_label"] == "Pembayaran Payroll"
    assert by_type["payroll_payment"]["credit"] == 1_000_000
    reversal = by_type["payroll_payment_reversal"]
    assert reversal["source_label"] == "Reversal Pembayaran Payroll"
    assert reversal["source_id"] == payment["id"]
    assert reversal["transaction_number"] == payment["payment_number"]
    assert reversal["debit"] == 1_000_000
    assert reversal["credit"] == 0
    assert mutasi["ending_balance"] == 0
    assert await server.db.cash_bank_payments.count_documents({}) == 0


@pytest.mark.anyio
async def test_permissions_are_catalog_only():
    keys = {key for _module, key, _label in PERMISSION_CATALOG}
    for perm in (
        "payroll_payment.view",
        "payroll_payment.create",
        "payroll_payment.approve",
        "payroll_payment.post",
        "payroll_payment.reverse",
        "payroll_payment.cancel",
    ):
        assert perm in keys
        assert perm in ALL_PERMISSIONS
        assert perm in ROLE_DEFAULT_PERMISSIONS["admin"]
        for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
            if role == "admin":
                continue
            assert perm not in perms


async def _assert_final(period_id: str):
    stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL


@pytest.mark.anyio
async def test_cancel_draft_and_approved_then_create_again():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2095, month=5, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        payload = {"cash_bank_account_id": bank["id"], "payment_date": "2095-05-18"}
        draft = await c.post(f"/api/hr-payroll/periods/{period['id']}/payments", json=payload)
        assert draft.status_code == 200, draft.text
        draft_id = draft.json()["payment"]["id"]
        cancelled = await c.post(
            f"/api/hr-payroll/payments/{draft_id}/cancel",
            json={"reason": "bank salah"},
        )
        assert cancelled.status_code == 200, cancelled.text
        approved = await c.post(f"/api/hr-payroll/periods/{period['id']}/payments", json=payload)
        assert approved.status_code == 200, approved.text
        approved_id = approved.json()["payment"]["id"]
        await c.post(f"/api/hr-payroll/payments/{approved_id}/approve")
        cancelled_approved = await c.post(
            f"/api/hr-payroll/payments/{approved_id}/cancel",
            json={"reason": "tanggal salah"},
        )
        assert cancelled_approved.status_code == 200, cancelled_approved.text
        again = await c.post(f"/api/hr-payroll/periods/{period['id']}/payments", json=payload)
        assert again.status_code == 200, again.text
        listed = await c.get(f"/api/hr-payroll/periods/{period['id']}/payments")
    assert cancelled.json()["payment"]["status"] == "CANCELLED"
    assert cancelled.json()["payment"]["cancellation_reason"] == "bank salah"
    assert cancelled.json()["payment"]["cancelled_by"]["id"] == "admin-pyp"
    assert cancelled.json()["payment"]["cancelled_at"]
    assert cancelled_approved.json()["payment"]["status"] == "CANCELLED"
    body = listed.json()
    assert body["payment"]["id"] == again.json()["payment"]["id"]
    assert body["payment"]["status"] == "DRAFT"
    statuses = {row["status"] for row in body["payments"]}
    assert statuses == {"CANCELLED", "DRAFT"}
    assert len(body["payments"]) == 3
    assert await server.db[PAYMENTS].count_documents({"id": draft_id}) == 1
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_cancel_paid_payment_is_rejected():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period, payment = await _posted_payment(c, year=2095, month=6, accounts=accounts, bank=bank)
        rejected = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/cancel",
            json={"reason": "sudah dibayar"},
        )
        assert rejected.status_code == 409, rejected.text
    stored = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    assert stored["status"] == "PAID"
    assert not stored.get("cancelled_at")
    assert len(await _payment_journals(payment["id"])) == 1
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_reverse_then_repay_creates_a_new_journal():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period, payment = await _posted_payment(c, year=2095, month=7, accounts=accounts, bank=bank)
        first_post = payment
        posted = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        reversed_res = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/reverse-accounting",
            json={"reason": "koreksi bank"},
        )
        assert reversed_res.status_code == 200, reversed_res.text
        assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 1
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2095-07-20"},
        )
        assert created.status_code == 200, created.text
        replacement = created.json()["payment"]
        await c.post(f"/api/hr-payroll/payments/{replacement['id']}/approve")
        reposted = await c.post(f"/api/hr-payroll/payments/{replacement['id']}/post-accounting")
        assert reposted.status_code == 200, reposted.text
        retry = await c.post(f"/api/hr-payroll/payments/{replacement['id']}/post-accounting")
        listed = await c.get(f"/api/hr-payroll/periods/{period['id']}/payments")
    assert replacement["id"] != first_post["id"]
    assert reposted.json()["journal_id"] != posted.json()["journal_id"]
    assert retry.json()["journal_id"] == reposted.json()["journal_id"]
    assert len(await _payment_journals(replacement["id"])) == 1
    assert (await _payment_journals(replacement["id"]))[0]["status"] == "posted"
    assert (await _payment_journals(first_post["id"]))[0]["status"] == "reversed"
    history = listed.json()
    assert history["payment"]["id"] == replacement["id"]
    assert history["payment"]["journal_number"] == reposted.json()["journal_number"]
    assert history["payment"]["journal_number"]
    assert {row["status"] for row in history["payments"]} == {"REVERSED", "PAID"}
    reloaded = await server.db[PAYMENTS].find_one({"id": first_post["id"]}, {"_id": 0, "status": 1})
    assert reloaded["status"] == "REVERSED"
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_concurrent_create_keeps_one_active_payment():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2095, month=8, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        payload = {"cash_bank_account_id": bank["id"], "payment_date": "2095-08-18"}
        url = f"/api/hr-payroll/periods/{period['id']}/payments"
        first, second = await asyncio.gather(c.post(url, json=payload), c.post(url, json=payload))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    active = await server.db[PAYMENTS].count_documents({
        "payroll_period_id": period["id"],
        "status": {"$in": ["DRAFT", "APPROVED", "PAID"]},
    })
    assert active == 1
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_line_insert_failure_cancels_without_deleting():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2095, month=9, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        with patch(
            "services.payroll_payment._insert_payment_lines",
            new=AsyncMock(side_effect=RuntimeError("disk")),
        ):
            failed = await c.post(
                f"/api/hr-payroll/periods/{period['id']}/payments",
                json={"cash_bank_account_id": bank["id"], "payment_date": "2095-09-18"},
            )
        assert failed.status_code == 409, failed.text
        stored = await server.db[PAYMENTS].find_one({"payroll_period_id": period["id"]}, {"_id": 0})
        assert stored["status"] == "CANCELLED"
        assert stored["cancellation_reason"] == "Detail pembayaran gagal disimpan."
        assert await server.db[PAYMENT_LINES].count_documents({"payment_id": stored["id"]}) == 0
        assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": stored["id"]}) == 0
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2095-09-19"},
        )
        assert created.status_code == 200, created.text
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 2
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_incomplete_payment_is_rejected_and_can_be_cancelled():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    broken_id = str(uuid.uuid4())
    async with _client() as c:
        _emp, period = await _finalize(c, year=2095, month=10, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        await server.db[PAYMENTS].insert_one({
            "id": broken_id,
            "payment_number": f"PYB-BROKEN-{uuid.uuid4().hex[:6]}",
            "payroll_period_id": period["id"],
            "payment_date": "2095-10-18",
            "cash_bank_account_id": bank["id"],
            "payable_account_id": accounts["payable"]["id"],
            "total_amount": 1_000_000,
            "status": "DRAFT",
            "accounting_journal_id": None,
            "created_at": "2095-10-18T00:00:00+00:00",
        })
        denied = await c.post(f"/api/hr-payroll/payments/{broken_id}/approve")
        assert denied.status_code == 409, denied.text
        await server.db[PAYMENT_LINES].insert_one({
            "id": str(uuid.uuid4()),
            "payment_id": broken_id,
            "employee_id": "emp-partial",
            "employee_name": "Sebagian",
            "net_salary": 1,
        })
        mismatched = await c.post(f"/api/hr-payroll/payments/{broken_id}/approve")
        assert mismatched.status_code == 409, mismatched.text
        await server.db[PAYMENT_LINES].update_one(
            {"payment_id": broken_id},
            {"$set": {"net_salary": 1_000_000, "employee_id": ""}},
        )
        nameless = await c.post(f"/api/hr-payroll/payments/{broken_id}/approve")
        assert nameless.status_code == 409, nameless.text
        await server.db[PAYMENT_LINES].delete_many({"payment_id": broken_id})
        await server.db[PAYMENTS].update_one({"id": broken_id}, {"$set": {"status": "APPROVED"}})
        denied_post = await c.post(f"/api/hr-payroll/payments/{broken_id}/post-accounting")
        assert denied_post.status_code == 409, denied_post.text
        recovered = await c.post(
            f"/api/hr-payroll/payments/{broken_id}/cancel",
            json={"reason": "detail hilang"},
        )
        assert recovered.status_code == 200, recovered.text
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2095-10-21"},
        )
        assert created.status_code == 200, created.text
    stored = await server.db[PAYMENTS].find_one({"id": broken_id}, {"_id": 0})
    assert stored["status"] == "CANCELLED"
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": broken_id}) == 0
    assert await server.db[PAYMENTS].count_documents({"id": broken_id}) == 1
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_post_closed_period_stays_approved():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2095, month=11, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2095-11-18"},
        )
        payment_id = created.json()["payment"]["id"]
        await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        await close_period(
            server.db,
            "2095-11",
            user={"id": "admin-pyp", "name": "Admin", "role": "admin"},
            reason="tutup uji",
        )
        rejected = await c.post(f"/api/hr-payroll/payments/{payment_id}/post-accounting")
        assert rejected.status_code == 409, rejected.text
    stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0, "status": 1})
    assert stored["status"] == "APPROVED"
    assert await _payment_journals(payment_id) == []
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_reload_returns_payment_journal_number():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period, payment = await _posted_payment(c, year=2095, month=12, accounts=accounts, bank=bank)
        posted = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        listed = await c.get(f"/api/hr-payroll/periods/{period['id']}/payments")
        single = await c.get(f"/api/hr-payroll/payments/{payment['id']}")
    assert listed.json()["payment"]["journal_number"] == posted.json()["journal_number"]
    assert listed.json()["payment"]["journal_number"]
    assert single.json()["payment"]["journal_number"] == posted.json()["journal_number"]
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_old_unique_index_migrates_without_deleting_payments():
    from services.payroll_payment import ACTIVE_PERIOD_INDEX, ensure_indexes

    await server.db[PAYMENTS].drop()
    await server.db[PAYMENTS].create_index("payroll_period_id", unique=True)
    period_id = "period-migrate"
    first_id = str(uuid.uuid4())
    await server.db[PAYMENTS].insert_one({
        "id": first_id,
        "payment_number": "PYB-MIG-1",
        "payroll_period_id": period_id,
        "status": "DRAFT",
    })
    before = await server.db[PAYMENTS].count_documents({})
    await ensure_indexes(server.db)
    await ensure_indexes(server.db)
    info = await server.db[PAYMENTS].index_information()
    full_unique = [
        name for name, spec in info.items()
        if list(spec.get("key") or []) == [("payroll_period_id", 1)]
        and spec.get("unique")
        and not spec.get("partialFilterExpression")
    ]
    assert not full_unique
    assert info[ACTIVE_PERIOD_INDEX]["unique"] is True
    assert await server.db[PAYMENTS].count_documents({}) == before
    stored = await server.db[PAYMENTS].find_one({"id": first_id}, {"_id": 0})
    assert stored["status"] == "DRAFT"
    assert stored["payroll_period_id"] == period_id
    with pytest.raises(DuplicateKeyError):
        await server.db[PAYMENTS].insert_one({
            "id": str(uuid.uuid4()),
            "payment_number": "PYB-MIG-2",
            "payroll_period_id": period_id,
            "status": "APPROVED",
        })
    await server.db[PAYMENTS].insert_one({
        "id": str(uuid.uuid4()),
        "payment_number": "PYB-MIG-3",
        "payroll_period_id": period_id,
        "status": "CANCELLED",
    })
    await server.db[PAYMENTS].insert_one({
        "id": str(uuid.uuid4()),
        "payment_number": "PYB-MIG-4",
        "payroll_period_id": period_id,
        "status": "REVERSED",
    })
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period_id}) == 3
    assert await server.db[PAYMENTS].count_documents({"id": first_id}) == 1


def _cross_process_payment_call(action, mongo_url, db_name, payment_id):
    """Run one payment action in a fresh process and event loop."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def go():
        from services.payroll_payment import approve_payroll_payment, cancel_payroll_payment
        from services.payroll_payment_posting import post_payroll_payment

        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        user = {"id": "racer", "name": "Racer", "role": "admin", "email": "racer@example.com"}
        try:
            if action == "cancel":
                await cancel_payroll_payment(db, payment_id, user, "race batal")
                return "cancel-ok"
            if action == "approve":
                await approve_payroll_payment(db, payment_id, user)
                return "approve-ok"
            await post_payroll_payment(db, payment_id, user)
            return "post-ok"
        except Exception as exc:
            return f"err:{exc}"
        finally:
            client.close()

    return asyncio.run(go())


def _run_cross_process(actions, payment_id):
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(actions)) as pool:
        return pool.starmap(
            _cross_process_payment_call,
            [(action, os.environ["MONGO_URL"], os.environ["DB_NAME"], payment_id) for action in actions],
        )


@pytest.mark.anyio
async def test_index_create_failure_keeps_the_old_constraint():
    from services.payroll_payment import ensure_indexes

    await server.db[PAYMENTS].drop()
    await server.db[PAYMENTS].create_index("payroll_period_id", unique=True)
    first_id = str(uuid.uuid4())
    await server.db[PAYMENTS].insert_one({
        "id": first_id,
        "payment_number": "PYB-KEEP-1",
        "payroll_period_id": "period-keep",
        "status": "DRAFT",
    })
    with patch("services.payroll_payment._create_active_period_index", side_effect=RuntimeError("index failed")):
        with pytest.raises(RuntimeError, match="index failed"):
            await ensure_indexes(server.db)
    info = await server.db[PAYMENTS].index_information()
    legacy = [
        name for name, spec in info.items()
        if list(spec.get("key") or []) == [("payroll_period_id", 1)]
        and spec.get("unique")
        and not spec.get("partialFilterExpression")
    ]
    assert legacy
    assert "uniq_active_payroll_period_payment" not in info
    assert await server.db[PAYMENTS].count_documents({"id": first_id}) == 1
    with pytest.raises(DuplicateKeyError):
        await server.db[PAYMENTS].insert_one({
            "id": str(uuid.uuid4()),
            "payment_number": "PYB-KEEP-2",
            "payroll_period_id": "period-keep",
            "status": "PAID",
        })
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": "period-keep"}) == 1


@pytest.mark.anyio
async def test_index_migration_stops_when_active_payments_conflict():
    from fastapi import HTTPException
    from services.payroll_payment import ensure_indexes, require_active_payment_index

    await server.db[PAYMENTS].drop()
    period_id = "period-conflict"
    for suffix in ("a", "b"):
        await server.db[PAYMENTS].insert_one({
            "id": str(uuid.uuid4()),
            "payment_number": f"PYB-CONFLICT-{suffix}",
            "payroll_period_id": period_id,
            "status": "DRAFT",
        })
    with pytest.raises(HTTPException) as raised:
        await ensure_indexes(server.db)
    assert raised.value.status_code == 409
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period_id}) == 2
    with pytest.raises(HTTPException) as missing:
        await require_active_payment_index(server.db)
    assert missing.value.status_code == 503
    with pytest.raises(HTTPException):
        await ensure_indexes(server.db)
    assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period_id}) == 2


@pytest.mark.anyio
async def test_cancel_and_post_race_across_processes():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2096, month=1, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2096-01-18"},
        )
        payment_id = created.json()["payment"]["id"]
        approved = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert approved.status_code == 200, approved.text
    results = _run_cross_process(["cancel", "post"], payment_id)
    stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0})
    journals = await _payment_journals(payment_id)
    posted = [row for row in journals if row["status"] == "posted"]
    assert stored["status"] in {"PAID", "CANCELLED"}
    if stored["status"] == "CANCELLED":
        assert posted == []
        assert not str(stored.get("accounting_journal_id") or "").strip()
    if posted:
        assert stored["status"] == "PAID"
        assert len(posted) == 1
    assert all(result.startswith(("cancel-ok", "post-ok", "err:")) for result in results)
    assert not any("Traceback" in result for result in results)
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_approve_and_cancel_race_across_processes():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2096, month=2, map_accounts=accounts)
        await _post_accrual(c, period["id"])
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2096-02-18"},
        )
        payment_id = created.json()["payment"]["id"]
    results = _run_cross_process(["approve", "cancel"], payment_id)
    stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0})
    assert stored["status"] in {"APPROVED", "CANCELLED"}
    assert await _payment_journals(payment_id) == []
    assert not str(stored.get("accounting_journal_id") or "").strip()
    assert all(result.startswith(("approve-ok", "cancel-ok", "err:")) for result in results)
    await _assert_final(period["id"])


@pytest.mark.anyio
async def test_approved_payment_with_journal_cannot_be_cancelled():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _period, payment = await _posted_payment(c, year=2096, month=3, accounts=accounts, bank=bank)
        await server.db[PAYMENTS].update_one(
            {"id": payment["id"]},
            {"$set": {"status": "APPROVED"}, "$unset": {"paid_at": "", "paid_by": ""}},
        )
        rejected = await c.post(
            f"/api/hr-payroll/payments/{payment['id']}/cancel",
            json={"reason": "sudah ada jurnal"},
        )
        assert rejected.status_code == 409, rejected.text
        repaired = await c.post(f"/api/hr-payroll/payments/{payment['id']}/post-accounting")
        assert repaired.status_code == 200, repaired.text
    stored = await server.db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    assert stored["status"] == "PAID"
    assert len(await _payment_journals(payment["id"])) == 1


@pytest.mark.anyio
async def test_payment_uses_accrual_snapshot_not_later_mapping_or_same_account_credits():
    accounts = await _setup_accounts()
    other_payable = await _coa("22", "Utang Lain", "OCLY")
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        emp = (await c.post("/api/hr-payroll/employees", json={
            "name": "Andi Payroll",
            "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
            "role": "operator",
            "division": "operator",
            "salary_profile": {"base_salary": 1_000_000, "allowances": []},
        })).json()["employee"]
        component = (await c.post("/api/hr-payroll/components", json={
            "code": "POTONG",
            "name": "Penyesuaian Utang Gaji",
            "kind": "deduction",
            "calc_mode": "fixed",
            "amount": 100_000,
            "recurring": True,
            "active": True,
        })).json()["component"]
        created = await c.post("/api/hr-payroll/periods", json={
            "year": 2093, "month": 12, "start_date": "2093-12-01", "end_date": "2093-12-28",
        })
        assert created.status_code == 200, created.text
        period = created.json()["period"]
        calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
        assert calc.status_code == 200, calc.text
        saved = await c.put("/api/settings/payroll-accounting", json={
            "payroll_payable_account_id": accounts["payable"]["id"],
            "component_mappings": {
                "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                f"component:{component['id']}": {"treatment": "liability_credit", "account_id": accounts["payable"]["id"]},
            },
        })
        assert saved.status_code == 200, saved.text
        for target in (STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL):
            moved = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": target})
            assert moved.status_code == 200, moved.text
        journal = await server.db.journal_entries.find_one(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None},
            {"_id": 0},
        )
        assert journal["payroll_payable"]["amount"] == 900_000
        assert journal["payroll_payable"]["account_id"] == accounts["payable"]["id"]
        lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(20)
        same_account_credit = sum(
            line["credit"] for line in lines if line["account_id"] == accounts["payable"]["id"]
        )
        assert same_account_credit == 1_000_000
        changed = await c.put("/api/settings/payroll-accounting", json={
            "payroll_payable_account_id": other_payable["id"],
            "component_mappings": {
                "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                f"component:{component['id']}": {"treatment": "liability_credit", "account_id": other_payable["id"]},
            },
        })
        assert changed.status_code == 200, changed.text
        created_payment = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-12-28"},
        )
        assert created_payment.status_code == 200, created_payment.text
        payment = created_payment.json()["payment"]
        assert payment["total_amount"] == 900_000
        assert payment["payable_account_id"] == accounts["payable"]["id"]
        assert payment["lines"][0]["employee_id"] == emp["id"]
        raw_after = await server.db.journal_entries.find_one({"id": journal["id"]}, {"_id": 0, "payroll_payable": 1})
        assert raw_after["payroll_payable"]["amount"] == 900_000
        assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0


@pytest.mark.anyio
async def test_legacy_journal_without_snapshot_stays_compatible_when_unambiguous():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=11, map_accounts=accounts)
        journal = await server.db.journal_entries.find_one(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None},
            {"_id": 0, "id": 1},
        )
        await server.db.journal_entries.update_one({"id": journal["id"]}, {"$unset": {"payroll_payable": ""}})
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2093-11-28"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["payment"]["total_amount"] == 1_000_000
        assert created.json()["payment"]["payable_account_id"] == accounts["payable"]["id"]
        raw = await server.db.journal_entries.find_one({"id": journal["id"]}, {"_id": 0, "payroll_payable": 1})
        assert raw.get("payroll_payable") in (None, {})
