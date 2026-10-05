"""Payroll accrual journal posting.

APPROVAL → FINAL posts one accrual. Manual POST remains for a legacy FINAL
period that has no journal.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

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


async def _to_approval(c, *, year: int, month: int, map_accounts=None):
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
    for target in (STATUS_REVIEW, STATUS_APPROVAL):
        moved = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": target},
        )
        assert moved.status_code == 200, moved.text
        period = moved.json()["period"]
    return emp, period


async def _journals_for(period_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": period_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(10)


@pytest.mark.anyio
async def test_finalize_posts_one_balanced_accrual():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=1, map_accounts=accounts)
    assert period["status"] == STATUS_FINAL
    assert period.get("accounting_journal_id")
    rows = await _journals_for(period["id"])
    assert len(rows) == 1
    journal = rows[0]
    assert journal["date"] == period["end_date"]
    assert journal["status"] == "posted"
    assert journal["source_type"] == "payroll_period"
    assert journal["payroll_payable"]["account_id"] == accounts["payable"]["id"]
    assert journal["payroll_payable"]["amount"] == 1_000_000
    assert journal["payroll_payable"]["line_code"] == "PAYROLL_PAYABLE"
    lines = await server.db.journal_lines.find({"journal_id": journal["id"]}, {"_id": 0}).to_list(20)
    assert sum(line["debit"] for line in lines) == sum(line["credit"] for line in lines) == 1_000_000
    assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment"}) == 0
    assert await server.db.journal_entries.count_documents({
        "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
    }) == 0


@pytest.mark.anyio
async def test_readiness_is_true_before_post_and_clerk_cannot_post():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=8, map_accounts=accounts)
        ready = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
        assert ready.status_code == 200, ready.text
        assert ready.json()["readiness"]["accounting_ready"] is True
        before = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "accounting_journal_id": 1})
        assert before.get("accounting_journal_id")
        app.dependency_overrides[current_user] = lambda: {
            "id": "clerk-pjp",
            "email": "clerk-pjp@eternalpackaging.com",
            "name": "Clerk PJP",
            "role": "payroll_clerk",
            "permissions_override": {"payroll.view": True},
        }
        denied = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert denied.status_code == 403, denied.text
        assert "payroll_accounting.post" in denied.text
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": period["id"]}) == 1
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1, "accounting_journal_id": 1})
    assert stored["status"] == STATUS_FINAL
    assert stored.get("accounting_journal_id") == before["accounting_journal_id"]


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
async def test_reload_reads_journal_number_from_stored_id():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=9, map_accounts=accounts)
        posted_ref = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-journal")
        assert posted_ref.status_code == 200, posted_ref.text
        assert posted_ref.json()["journal_number"]
        posted = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        second = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert second.status_code == 200, second.text
        assert second.json()["journal_id"] == posted.json()["journal_id"]
        reloaded = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-journal")
        assert reloaded.status_code == 200, reloaded.text
    body = reloaded.json()
    assert body["journal_id"] == posted.json()["journal_id"]
    assert body["journal_number"] == posted.json()["journal_number"]
    assert body["journal_number"]
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": period["id"]}) == 1


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
        _emp, period = await _to_approval(c, year=2091, month=4, map_accounts=None)
        blocked = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_FINAL},
        )
        assert blocked.status_code == 409, blocked.text
        assert "Gaji Pokok" in blocked.text
        await server.db[PERIODS].update_one({"id": period["id"]}, {"$set": {"status": STATUS_FINAL, "frozen": True}})
        res = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert res.status_code == 409, res.text
        assert "Mapping COA" in res.text
    assert await server.db.journal_entries.count_documents({}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    assert stored["status"] == STATUS_FINAL
    assert stored.get("accounting_journal_id") in (None, "")
    kasbon = await server.db.hr_kasbon_schedules.find_one({"payments.period_id": period["id"]})
    assert kasbon is None


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

    async def unbalanced(db, period_id, for_finalization=False):
        data = await original(db, period_id, for_finalization=for_finalization)
        data["accounting_ready"] = True
        data["reasons"] = []
        data["planned_lines"] = [
            {
                "account_id": accounts["salary"]["id"],
                "debit": 100,
                "credit": 0,
                "name": "Gaji Pokok",
                "code": "BASE",
                "source_keys": ["salary_profile:BASE"],
            },
            {
                "account_id": accounts["payable"]["id"],
                "debit": 0,
                "credit": 40,
                "name": "Utang Gaji",
                "code": "PAYROLL_PAYABLE",
                "source_keys": ["payroll_payable"],
            },
        ]
        return data

    posting.evaluate_payroll_accounting_readiness = unbalanced
    try:
        async with _client() as c:
            _emp, period = await _to_approval(c, year=2091, month=5, map_accounts=accounts)
            res = await c.post(
                f"/api/hr-payroll/periods/{period['id']}/transition",
                json={"to_status": STATUS_FINAL},
            )
            assert res.status_code == 409, res.text
            assert "tidak seimbang" in res.text
    finally:
        posting.evaluate_payroll_accounting_readiness = original
    assert await server.db.journal_entries.count_documents({}) == 0
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_APPROVAL


@pytest.mark.anyio
async def test_locked_accounting_period_is_rejected():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _to_approval(c, year=2091, month=6, map_accounts=accounts)
        await close_period(server.db, "2091-06", user={"id": "admin-pjp", "name": "Admin", "role": "admin"}, reason="tutup uji")
        blocked = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_FINAL},
        )
        assert blocked.status_code == 409, blocked.text
        assert "2091-06" in blocked.text or "ditutup" in blocked.text.lower()
        stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
        assert stored["status"] == STATUS_APPROVAL
        assert stored.get("accounting_journal_id") in (None, "")

        _emp2, legacy = await _to_approval(c, year=2091, month=11, map_accounts=accounts)
        await server.db[PERIODS].update_one({"id": legacy["id"]}, {"$set": {"status": STATUS_FINAL, "frozen": True}})
        await close_period(server.db, "2091-11", user={"id": "admin-pjp", "name": "Admin", "role": "admin"}, reason="tutup uji")
        res = await c.post(f"/api/hr-payroll/periods/{legacy['id']}/post-accounting")
        assert res.status_code == 409, res.text
        assert "2091-11" in res.text or "ditutup" in res.text.lower()
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE}) == 0
    legacy_stored = await server.db[PERIODS].find_one({"id": legacy["id"]}, {"_id": 0})
    assert legacy_stored["status"] == STATUS_FINAL
    assert legacy_stored.get("accounting_journal_id") in (None, "")


@pytest.mark.anyio
async def test_reopen_posted_period_is_rejected():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _finalize(c, year=2091, month=7, map_accounts=accounts)
        posted = await c.post(f"/api/hr-payroll/periods/{period['id']}/post-accounting")
        assert posted.status_code == 200, posted.text
        await server.db[PERIODS].update_one({"id": period["id"]}, {"$unset": {"accounting_journal_id": ""}})
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


@pytest.mark.anyio
async def test_finalize_recovers_boundary_failures_without_duplicates():
    import services.hr_payroll as payroll
    import services.payroll_journal_posting as posting

    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _to_approval(c, year=2091, month=10, map_accounts=accounts)
        period_id = period["id"]

        async def fail_once(module, name):
            original = getattr(module, name)
            state = {"n": 0}

            async def wrapped(*args, **kwargs):
                state["n"] += 1
                if state["n"] == 1:
                    raise HTTPException(500, f"{name} gagal")
                return await original(*args, **kwargs)

            setattr(module, name, wrapped)
            return original

        originals = {
            "post": (posting, "post_balanced_journal", await fail_once(posting, "post_balanced_journal")),
        }
        try:
            failed = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert failed.status_code == 500, failed.text
        finally:
            setattr(posting, "post_balanced_journal", originals["post"][2])
        stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1})
        assert stored["status"] == STATUS_APPROVAL
        assert await _journals_for(period_id) == []

        slip_original = await fail_once(payroll, "generate_payslip_for_line")
        try:
            failed = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert failed.status_code == 500, failed.text
        finally:
            payroll.generate_payslip_for_line = slip_original
        assert (await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL
        assert len(await _journals_for(period_id)) == 1

        kasbon_original = await fail_once(payroll, "_post_kasbon_payments")
        try:
            failed = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert failed.status_code == 500, failed.text
        finally:
            payroll._post_kasbon_payments = kasbon_original
        assert (await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL
        assert len(await _journals_for(period_id)) == 1

        commit_original = await fail_once(payroll, "_commit_final_status")
        try:
            failed = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert failed.status_code == 500, failed.text
        finally:
            payroll._commit_final_status = commit_original
        assert (await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL
        assert len(await _journals_for(period_id)) == 1

        done = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
        assert done.status_code == 200, done.text
        assert done.json()["period"]["status"] == STATUS_FINAL
        rows = await _journals_for(period_id)
        assert len(rows) == 1
        assert rows[0]["date"] == "2091-10-28"
        assert rows[0]["payroll_payable"]["amount"] == 1_000_000
        again = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
        assert again.status_code == 200, again.text
        assert len(await _journals_for(period_id)) == 1
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment"}) == 0

        await server.db.journal_entries.update_one({"id": rows[0]["id"]}, {"$unset": {"payroll_payable": ""}})
        repost = await c.post(f"/api/hr-payroll/periods/{period_id}/post-accounting")
        assert repost.status_code == 200, repost.text
        raw = await server.db.journal_entries.find_one({"id": rows[0]["id"]}, {"_id": 0, "payroll_payable": 1})
        assert raw.get("payroll_payable") in (None, {})

        await server.db[PERIODS].update_one({"id": period_id}, {"$set": {"status": STATUS_APPROVAL, "frozen": False}})
        recovered = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
        assert recovered.status_code == 200, recovered.text
        filled = await server.db.journal_entries.find_one({"id": rows[0]["id"]}, {"_id": 0, "payroll_payable": 1})
        assert filled["payroll_payable"]["amount"] == 1_000_000
        assert filled["payroll_payable"]["account_id"] == accounts["payable"]["id"]
        assert len(await _journals_for(period_id)) == 1


@pytest.mark.anyio
async def test_concurrent_finalize_creates_one_journal():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        _emp, period = await _to_approval(c, year=2091, month=12, map_accounts=accounts)
        first, second = await asyncio.gather(
            c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL}),
            c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL}),
        )
    assert 200 in (first.status_code, second.status_code)
    stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == STATUS_FINAL
    assert len(await _journals_for(period["id"])) == 1


@pytest.mark.anyio
async def test_primary_journal_index_rejects_a_second_insert():
    """Same-process proof of uniq_primary_source_journal. Not a second OS process."""
    source_id = str(uuid.uuid4())
    base = {
        "date": "2091-01-28",
        "source_type": SOURCE_TYPE,
        "source_id": source_id,
        "description": "Payroll index",
        "status": "posted",
        "reversal_of": None,
    }
    await server.db.journal_entries.insert_one({**base, "id": str(uuid.uuid4()), "number": f"JRN-{uuid.uuid4().hex[:8]}"})
    with pytest.raises(DuplicateKeyError):
        await server.db.journal_entries.insert_one({**base, "id": str(uuid.uuid4()), "number": f"JRN-{uuid.uuid4().hex[:8]}"})
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": source_id}) == 1


@pytest.mark.anyio
async def test_stale_stamp_and_historical_snapshot_do_not_overwrite():
    from services.payroll_journal_posting import _stamp_period_metadata, fill_missing_payable_snapshot
    from services.payroll_payment import payable_credit_from_accrual

    period_id = "period-stamp-guard"
    journal_id = "journal-stamp-guard"
    await server.db[PERIODS].insert_one({"id": period_id, "status": STATUS_FINAL})
    await server.db.journal_entries.insert_one({
        "id": journal_id,
        "source_type": SOURCE_TYPE,
        "source_id": period_id,
        "status": "posted",
        "reversal_of": None,
        "date": "2091-01-28",
    })
    await server.db.journal_lines.insert_one({
        "id": "line-stamp-guard",
        "journal_id": journal_id,
        "account_id": "acc-utang-asal",
        "debit": 0,
        "credit": 500,
        "description": "Payroll uji — Utang Gaji",
    })
    await _stamp_period_metadata(
        server.db, period_id,
        {"id": journal_id, "posted_at": "2026-01-01T00:00:00+00:00"},
        {"id": "user-a", "name": "Ani", "role": "admin"},
    )
    await _stamp_period_metadata(
        server.db, period_id,
        {"id": "journal-lain", "posted_at": "2026-02-02T00:00:00+00:00"},
        {"id": "user-b", "name": "Budi", "role": "admin"},
    )
    stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
    assert stored["accounting_journal_id"] == journal_id
    assert stored["accounting_posted_at"] == "2026-01-01T00:00:00+00:00"
    assert stored["accounting_posted_by"]["id"] == "user-a"

    await fill_missing_payable_snapshot(server.db, period_id)
    raw = await server.db.journal_entries.find_one({"id": journal_id}, {"_id": 0})
    assert raw.get("payroll_payable") in (None, {})
    payable_credit_from_accrual({
        **raw,
        "lines": [{
            "account_id": "acc-utang-asal",
            "debit": 0,
            "credit": 500,
            "description": "Payroll uji — Utang Gaji",
        }],
    })
    raw = await server.db.journal_entries.find_one({"id": journal_id}, {"_id": 0, "payroll_payable": 1})
    assert raw.get("payroll_payable") in (None, {})

    await server.db[PERIODS].update_one({"id": period_id}, {"$set": {"status": STATUS_APPROVAL}})
    await fill_missing_payable_snapshot(server.db, period_id)
    filled = await server.db.journal_entries.find_one({"id": journal_id}, {"_id": 0, "payroll_payable": 1})
    assert filled["payroll_payable"]["account_id"] == "acc-utang-asal"
    assert filled["payroll_payable"]["amount"] == 500
    await fill_missing_payable_snapshot(server.db, period_id)
    again = await server.db.journal_entries.find_one({"id": journal_id}, {"_id": 0, "payroll_payable": 1})
    assert again["payroll_payable"] == filled["payroll_payable"]
