"""Payroll accounting readiness preview — Phase 0, zero journals."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.hr_payroll import (
    ATTENDANCE,
    COMPONENTS,
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    STATUS_APPROVAL,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_REVIEW,
    ensure_indexes,
    to_money,
)
from services.payroll_accounting_settings import SETTINGS_KEY


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
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES):
        await server.db[coll].delete_many({})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _admin():
    user = {
        "id": "admin-par",
        "email": "admin@eternalpackaging.com",
        "name": "Admin PAR",
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


async def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _setup_accounts():
    return {
        "payable": await _coa("21", "Utang Gaji", "OCLY"),
        "salary": await _coa("51", "Beban Gaji", "EXPS"),
        "ot": await _coa("51", "Beban Lembur", "EXPS"),
        "allowance": await _coa("51", "Beban Tunjangan", "EXPS"),
        "att": await _coa("51", "Pengurang Gaji", "EXPS"),
        "kasbon": await _coa("11", "Piutang Kasbon", "OCAS"),
        "comp": await _coa("51", "Beban Komponen", "EXPS"),
    }


async def _seed_employee_and_period(c, *, year=2097, month=3, allowance=None, extra_components=None):
    emp = (await c.post("/api/hr-payroll/employees", json={
        "name": "Andi Payroll",
        "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
        "role": "operator",
        "division": "operator",
        "salary_profile": {
            "base_salary": 1_000_000,
            "allowances": allowance or [],
        },
    })).json()["employee"]
    for component in extra_components or []:
        created = await c.post("/api/hr-payroll/components", json=component)
        assert created.status_code == 200, created.text
    period = (await c.post("/api/hr-payroll/periods", json={
        "year": year, "month": month, "start_date": f"{year}-{month:02d}-01", "end_date": f"{year}-{month:02d}-28",
    })).json()["period"]
    return emp, period


async def _to_status(c, period_id: str, target: str):
    res = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": target})
    assert res.status_code == 200, res.text
    return res.json()["period"]


@pytest.mark.anyio
async def test_non_final_not_ready_and_get_zero_write():
    _admin()
    before_j = await server.db.journal_entries.count_documents({})
    before_l = await server.db.journal_lines.count_documents({})
    try:
        async with await _client() as c:
            emp, period = await _seed_employee_and_period(c)
            calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert calc.status_code == 200, calc.text
            draft = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert draft.status_code == 200, draft.text
            assert draft.json()["readiness"]["accounting_ready"] is False
            assert "payroll_not_final" in draft.json()["readiness"]["reasons"]
            assert draft.json()["readiness"]["creates_journal"] is False
            assert "accounting_journal_id" not in draft.json()["readiness"]

            await _to_status(c, period["id"], STATUS_REVIEW)
            review = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert review.json()["readiness"]["accounting_ready"] is False
            assert "payroll_not_final" in review.json()["readiness"]["reasons"]

            await _to_status(c, period["id"], STATUS_APPROVAL)
            approval = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert approval.json()["readiness"]["accounting_ready"] is False
            assert "payroll_not_final" in approval.json()["readiness"]["reasons"]

            settings_before = await server.db.app_settings.find_one({"key": SETTINGS_KEY})
            snap_before = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"].get("final_snapshot")
            await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert await server.db.app_settings.find_one({"key": SETTINGS_KEY}) == settings_before
            snap_after = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"].get("final_snapshot")
            assert snap_after == snap_before
        assert await server.db.journal_entries.count_documents({}) == before_j
        assert await server.db.journal_lines.count_documents({}) == before_l
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_final_unmapped_and_mapped_balance_no_plug():
    accounts = await _setup_accounts()
    _admin()
    before_j = await server.db.journal_entries.count_documents({})
    try:
        async with await _client() as c:
            emp, period = await _seed_employee_and_period(
                c,
                allowance=[{"code": "MAKAN", "name": "Uang Makan", "amount": 100000}],
                extra_components=[{
                    "code": "TETAP_X", "name": "Tunjangan Tetap", "kind": "earning",
                    "calc_mode": "fixed", "amount": 50000, "recurring": True, "active": True,
                }],
            )
            await c.put(f"/api/hr-payroll/periods/{period['id']}/attendance/{emp['id']}", json={
                "work_days": 20, "late_minutes": 10, "alpha_days": 0,
                "overtime_weekday_hours": 2, "overtime_weekend_hours": 0, "overtime_holiday_hours": 0,
            })
            await c.put("/api/hr-payroll/settings", json={
                "attendance": {"late_grace_minutes": 0, "late_rate_per_minute": 1000, "late_cap": 0, "alpha_rate_per_day": 0, "unpaid_leave_rate_per_day": 0, "unpaid_sick_rate_per_day": 0},
                "overtime": {"weekday_multiplier": 1.5, "weekend_multiplier": 2, "holiday_multiplier": 3, "min_hours": 1, "hourly_base_divisor": 25},
            })
            calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert calc.status_code == 200, calc.text
            await _to_status(c, period["id"], STATUS_REVIEW)
            await _to_status(c, period["id"], STATUS_APPROVAL)
            await _to_status(c, period["id"], STATUS_FINAL)

            missing = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            body = missing.json()["readiness"]
            assert body["status"] == STATUS_FINAL
            assert body["accounting_ready"] is False
            assert "payable_account_missing" in body["reasons"]
            assert any(row["reason"] == "source_mapping_missing" for row in body["unmapped_sources"])
            assert body["summary"]["gross"] == body["summary"]["net"] + body["summary"]["deductions"]

            line = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"][0]
            components = {row["code"]: row for row in line["earnings"] if row.get("source") == "component"}
            assert "TETAP_X" in components
            component_id = components["TETAP_X"]["component_id"]

            save = await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": accounts["payable"]["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                    "salary_profile:MAKAN": {"treatment": "expense_debit", "account_id": accounts["allowance"]["id"]},
                    "overtime:OT_WEEKDAY": {"treatment": "expense_debit", "account_id": accounts["ot"]["id"]},
                    "attendance:ATT_LATE": {"treatment": "expense_credit", "account_id": accounts["att"]["id"]},
                    f"component:{component_id}": {"treatment": "expense_debit", "account_id": accounts["comp"]["id"]},
                },
            })
            assert save.status_code == 200, save.text

            ready = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            r = ready.json()["readiness"]
            assert r["unmapped_sources"] == []
            assert r["accounting_ready"] is True
            assert r["summary"]["difference"] == 0
            assert r["summary"]["total_debit"] == r["summary"]["total_credit"]
            assert r["summary"]["gross"] == line["gross"]
            assert r["summary"]["net"] == line["net"]
            assert r["summary"]["deductions"] == line["total_deductions"]
            assert to_money(r["summary"]["total_debit"]) == to_money(r["summary"]["gross"])
            assert to_money(r["summary"]["total_credit"]) == to_money(line["total_deductions"]) + to_money(line["net"])
            keys = [key for row in r["planned_lines"] for key in row["source_keys"]]
            assert "salary_profile:BASE" in keys
            assert "payroll_payable" in keys
            assert all(not (row["debit"] == 0 and row["credit"] == 0) for row in r["planned_lines"])
            assert r["financial_source"] == "hr_payroll_lines"
            assert r["creates_journal"] is False
        assert await server.db.journal_entries.count_documents({}) == before_j
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_superseded_revision_not_ready():
    accounts = await _setup_accounts()
    _admin()
    try:
        async with await _client() as c:
            emp, period = await _seed_employee_and_period(c, year=2096, month=4)
            await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            await _to_status(c, period["id"], STATUS_REVIEW)
            await _to_status(c, period["id"], STATUS_APPROVAL)
            await _to_status(c, period["id"], STATUS_FINAL)
            await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": accounts["payable"]["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                },
            })
            first = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert first.json()["readiness"]["accounting_ready"] is True
            assert first.json()["readiness"]["is_latest_revision"] is True

            reopened = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "koreksi"})
            assert reopened.status_code == 200, reopened.text
            new_period = reopened.json()["period"]
            assert new_period["id"] != period["id"]
            assert new_period["revision"] == 2

            old = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert old.json()["readiness"]["accounting_ready"] is False
            assert old.json()["readiness"]["is_latest_revision"] is False
            assert "superseded_revision" in old.json()["readiness"]["reasons"]

            await c.post(f"/api/hr-payroll/periods/{new_period['id']}/calculate")
            await _to_status(c, new_period["id"], STATUS_REVIEW)
            await _to_status(c, new_period["id"], STATUS_APPROVAL)
            await _to_status(c, new_period["id"], STATUS_FINAL)
            latest = await c.get(f"/api/hr-payroll/periods/{new_period['id']}/accounting-readiness")
            assert latest.json()["readiness"]["is_latest_revision"] is True
            assert latest.json()["readiness"]["accounting_ready"] is True
            still_old = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert still_old.json()["readiness"]["accounting_ready"] is False
            assert still_old.json()["readiness"]["period_id"] == period["id"]
            assert latest.json()["readiness"]["period_id"] == new_period["id"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_adjustment_blocks_new_adjustment_maps():
    accounts = await _setup_accounts()
    _admin()
    try:
        async with await _client() as c:
            emp, period = await _seed_employee_and_period(c, year=2095, month=5)
            await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            line = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"][0]
            missing = await c.post(
                f"/api/hr-payroll/periods/{period['id']}/lines/{line['id']}/adjust",
                json={"amount": 25000, "reason": "bonus adhoc", "kind": "earning"},
            )
            assert missing.status_code == 400

            typed = await c.post(
                f"/api/hr-payroll/periods/{period['id']}/lines/{line['id']}/adjust",
                json={
                    "amount": 25000,
                    "reason": "bonus adhoc",
                    "kind": "earning",
                    "accounting_treatment": "expense_debit",
                    "accounting_account_id": accounts["salary"]["id"],
                },
            )
            assert typed.status_code == 200, typed.text
            adj_row = next(r for r in typed.json()["line"]["earnings"] if r.get("source") == "adjustment")
            assert adj_row["accounting_treatment"] == "expense_debit"
            assert adj_row["accounting_account_id"] == accounts["salary"]["id"]
            assert adj_row["name"] == "bonus adhoc"

            # Legacy row injected without metadata — readable, not ready.
            await server.db[LINES].update_one(
                {"id": line["id"]},
                {"$push": {
                    "earnings": {
                        "code": "ADJUSTMENT",
                        "name": "legacy reason",
                        "amount": 10000,
                        "source": "adjustment",
                        "adjustment_id": str(uuid.uuid4()),
                    }
                }},
            )
            # Recompute stored totals to include the legacy amount.
            fresh = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0})
            gross = sum(to_money(r.get("amount")) for r in fresh["earnings"])
            deductions = sum(to_money(r.get("amount")) for r in fresh["deductions"])
            await server.db[LINES].update_one({"id": line["id"]}, {"$set": {
                "gross": float(gross), "total_deductions": float(deductions), "net": float(gross - deductions),
            }})

            await _to_status(c, period["id"], STATUS_REVIEW)
            await _to_status(c, period["id"], STATUS_APPROVAL)
            await _to_status(c, period["id"], STATUS_FINAL)
            await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": accounts["payable"]["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                },
            })
            ready = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            r = ready.json()["readiness"]
            assert r["accounting_ready"] is False
            assert any(row["reason"] == "unsupported_adjustment" for row in r["unmapped_sources"])
            assert not any(row.get("label") == "legacy reason" and row.get("source_key") == "adjustment" for row in r["unmapped_sources"])
            get_again = await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")
            assert get_again.status_code == 200
            legacy = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0})
            assert any(r.get("name") == "legacy reason" and not r.get("accounting_treatment") for r in legacy["earnings"])
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_zero_lines_omitted_and_no_journal_core_post():
    accounts = await _setup_accounts()
    _admin()
    before_j = await server.db.journal_entries.count_documents({})
    try:
        async with await _client() as c:
            _emp, period = await _seed_employee_and_period(c, year=2094, month=6)
            await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            await _to_status(c, period["id"], STATUS_REVIEW)
            await _to_status(c, period["id"], STATUS_APPROVAL)
            await _to_status(c, period["id"], STATUS_FINAL)
            await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": accounts["payable"]["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["salary"]["id"]},
                },
            })
            r = (await c.get(f"/api/hr-payroll/periods/{period['id']}/accounting-readiness")).json()["readiness"]
            assert r["accounting_ready"] is True
            assert all((row["debit"] or 0) + (row["credit"] or 0) > 0 for row in r["planned_lines"])
            src = Path(__file__).resolve().parents[1] / "services"
            callers = []
            for path in src.glob("payroll_accounting*.py"):
                text = path.read_text(encoding="utf-8")
                assert "post_balanced_journal" not in text
                callers.append(path.name)
            assert callers
        assert await server.db.journal_entries.count_documents({}) == before_j
        cash = await server.db.journal_entries.count_documents({
            "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
        })
        after_cash = await server.db.journal_entries.count_documents({
            "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
        })
        assert cash == after_cash
    finally:
        app.dependency_overrides.pop(current_user, None)
