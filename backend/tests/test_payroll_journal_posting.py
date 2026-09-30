"""Payroll accrual journal posting — Phase 1.

Finalizing payroll must not create a journal. Posting is an explicit POST.
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
os.environ["DB_NAME"] = f"eternal_packaging_payroll_journal_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_period_indexes
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
from services.payroll_journal_posting import (
    SOURCE_TYPE,
    UNBALANCED_MESSAGE,
    assert_balanced_journal_lines,
)


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
    await ensure_period_indexes(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES):
        await server.db[coll].delete_many({})
    await server.db.journal_entries.delete_many({})
    await server.db.journal_lines.delete_many({})
    await server.db.accounting_periods.delete_many({})
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    user = {
        "id": "admin-pjp",
        "email": "admin@eternalpackaging.com",
        "name": "Admin PJP",
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
    }


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


async def _journals_for(period_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": period_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(10)


@pytest.mark.anyio
async def test_final_without_post_creates_no_journal():
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=1, map_accounts=await _setup_accounts())
    assert period["status"] == STATUS_FINAL
    assert period.get("accounting_journal_id") in (None, "")
    assert await server.db.journal_entries.count_documents({}) == 0
    assert await server.db.journal_lines.count_documents({}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL


@pytest.mark.anyio
async def test_post_ready_period_creates_one_journal():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=2, map_accounts=accounts)
        res = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "POSTED"
        assert body["journal_id"]
        assert body["journal_number"]
    rows = await _journals_for(period["id"])
    assert len(rows) == 1
    journal = rows[0]
    assert journal["id"] == body["journal_id"]
    assert journal["source_type"] == "payroll_period"
    assert journal["source_id"] == period["id"]
    assert journal["source_id"] != period["group_id"]
    assert journal["source_id"] != period["period_number"]
    assert journal["date"] == period["end_date"]
    assert journal["status"] == "posted"
    assert journal["description"].startswith("Payroll ")
    lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(20)
    debit = sum(line["debit"] for line in lines)
    credit = sum(line["credit"] for line in lines)
    assert debit == credit
    assert debit == 1_000_000
    assert {line["account_id"] for line in lines} == {accounts["salary"]["id"], accounts["payable"]["id"]}
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    assert stored["status"] == STATUS_FINAL
    assert stored["accounting_journal_id"] == journal["id"]
    assert stored["accounting_posted_at"]
    assert stored["accounting_posted_by"]["id"] == "admin-pjp"
    assert await server.db.journal_entries.count_documents({
        "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
    }) == 0


@pytest.mark.anyio
async def test_second_post_returns_the_same_journal():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=3, map_accounts=accounts)
        first = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert first.status_code == 200, first.text
        stamped = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "accounting_posted_at": 1})
        second = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert second.status_code == 200, second.text
    assert first.json()["journal_id"] == second.json()["journal_id"]
    assert first.json()["journal_number"] == second.json()["journal_number"]
    assert second.json()["status"] == "POSTED"
    assert "sudah ada" in second.json()["message"]
    assert len(await _journals_for(period["id"])) == 1
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 1
    again = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    assert again["status"] == STATUS_FINAL
    assert again["accounting_posted_at"] == stamped["accounting_posted_at"]
    assert again["accounting_journal_id"] == first.json()["journal_id"]


@pytest.mark.anyio
async def test_incomplete_mapping_is_rejected():
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=4, map_accounts=None)
        res = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert res.status_code == 409, res.text
        assert "Mapping COA" in res.text
    assert await server.db.journal_entries.count_documents({}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    assert stored["status"] == STATUS_FINAL
    assert stored.get("accounting_journal_id") in (None, "")


@pytest.mark.anyio
async def test_unbalanced_lines_are_rejected():
    accounts = await _setup_accounts()
    _admin()
    with pytest.raises(Exception) as rejected:
        assert_balanced_journal_lines([
            {"account_id": accounts["salary"]["id"], "debit": 100, "credit": 0},
            {"account_id": accounts["payable"]["id"], "debit": 0, "credit": 40},
        ])
    assert UNBALANCED_MESSAGE in str(rejected.value.detail if hasattr(rejected.value, "detail") else rejected.value)

    import services.payroll_journal_posting as posting

    original = posting.evaluate_payroll_accounting_readiness

    async def unbalanced(db, period_id):
        data = await original(db, period_id)
        data["accounting_ready"] = True
        data["reasons"] = []
        data["planned_lines"] = [
            {
                "account_id": accounts["salary"]["id"],
                "debit": 100,
                "credit": 0,
                "name": "Gaji Pokok",
                "code": "BASE",
            },
            {
                "account_id": accounts["payable"]["id"],
                "debit": 0,
                "credit": 40,
                "name": "Utang Gaji",
                "code": "PAYROLL_PAYABLE",
            },
        ]
        return data

    posting.evaluate_payroll_accounting_readiness = unbalanced
    try:
        async with _client() as c:
            _emp, period = await _finalize(c, year=2091, month=5, map_accounts=accounts)
            res = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
            assert res.status_code == 409, res.text
            assert "tidak seimbang" in res.text
    finally:
        posting.evaluate_payroll_accounting_readiness = original
    assert await server.db.journal_entries.count_documents({}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL


@pytest.mark.anyio
async def test_locked_accounting_period_is_rejected():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=6, map_accounts=accounts)
        await close_period(server.db, "2091-06", user={"id": "admin-pjp", "name": "Admin", "role": "admin"}, reason="tutup uji")
        res = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert res.status_code == 409, res.text
        assert "2091-06" in res.text or "ditutup" in res.text.lower()
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    assert stored["status"] == STATUS_FINAL
    assert stored.get("accounting_journal_id") in (None, "")


@pytest.mark.anyio
async def test_reopen_posted_period_is_rejected():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=7, map_accounts=accounts)
        posted = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        reopen = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/reopen",
            json={"reason": "koreksi setelah jurnal"},
        )
        assert reopen.status_code == 409, reopen.text
        assert "jurnal" in reopen.text.lower()
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1, "group_id": 1})
    assert stored["status"] == STATUS_FINAL
    assert await server.db[PERIODS].count_documents({"group_id": stored["group_id"]}) == 1
    assert len(await _journals_for(period["id"])) == 1
