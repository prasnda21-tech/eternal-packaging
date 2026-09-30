"""Payroll payment posting — Phase 2.

FINAL does not pay. Accrual stays a separate POST. Payment is its own
document: create, approve, then post Dr Utang Gaji / Cr BANK.
"""
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
os.environ["DB_NAME"] = f"eternal_packaging_payroll_payment_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
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
    for target in (STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL):
        moved = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": target},
        )
        assert moved.status_code == 200, moved.text
        period = moved.json()["period"]
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
async def test_payment_without_accrual_journal_fails():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2093, month=2, map_accounts=accounts)
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
    from services.payroll_payment import ensure_indexes

    await server.db[PAYMENTS].drop()
    await ensure_indexes(server.db)
    info = await server.db[PAYMENTS].index_information()
    unique_period = [
        spec for spec in info.values()
        if list(spec.get("key") or []) == [("payroll_period_id", 1)] and spec.get("unique")
    ]
    assert unique_period
    assert await server.db[PAYMENTS].count_documents({}) == 0


@pytest.mark.anyio
async def test_permissions_are_catalog_only():
    keys = {key for _module, key, _label in PERMISSION_CATALOG}
    for perm in (
        "payroll_payment.view",
        "payroll_payment.create",
        "payroll_payment.approve",
        "payroll_payment.post",
        "payroll_payment.reverse",
    ):
        assert perm in keys
        assert perm in ALL_PERMISSIONS
        assert perm in ROLE_DEFAULT_PERMISSIONS["admin"]
        for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
            if role == "admin":
                continue
            assert perm not in perms
