"""HR & Payroll — workflow, Kasbon, snapshots, permissions, PDF."""
import asyncio
import multiprocessing
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user
from services.accounting_periods import close_period
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa_indexes
from services.journal_core import ensure_indexes as ensure_journal_indexes
from services.payroll_accounting_settings import source_key_for_row
from services.hr_payroll import (
    ATTENDANCE,
    COMPONENTS,
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    PERM_ADJUST,
    PERM_EMP_MANAGE,
    PERM_EMP_VIEW,
    PERM_PAY_APPROVE,
    PERM_PAY_CALC,
    PERM_PAY_FINAL,
    PERM_PAY_REOPEN,
    PERM_PAY_REVIEW,
    PERM_PAY_SETTINGS,
    PERM_PAY_VIEW,
    PERM_SLIP_ALL,
    PERM_SLIP_OWN,
    STATUS_APPROVAL,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_REVIEW,
    ensure_indexes,
    to_money,
)

NEW_PERMS = (
    PERM_EMP_VIEW, PERM_EMP_MANAGE, PERM_PAY_VIEW, PERM_PAY_CALC, PERM_PAY_REVIEW,
    PERM_PAY_APPROVE, PERM_PAY_FINAL, PERM_PAY_REOPEN, PERM_PAY_SETTINGS,
    PERM_SLIP_OWN, PERM_SLIP_ALL, PERM_ADJUST,
    "nav.hr_payroll", "nav.hr_payroll.employees", "nav.hr_payroll.employment",
    "nav.hr_payroll.payroll", "nav.hr_payroll.payslips", "nav.hr_payroll.components",
    "nav.hr_payroll.history",
)

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
async def indexes(bind_motor):
    await ensure_indexes(server.db)
    await ensure_coa_indexes(server.db)


def _user(role="admin", **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    if "permissions_override" in kw:
        override = kw["permissions_override"]
    elif role == "admin":
        override = {}
    else:
        override = {
            PERM_EMP_VIEW: True,
            PERM_PAY_VIEW: True,
            PERM_SLIP_OWN: True,
        }
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "employee_id": kw.get("employee_id") or f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean():
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES):
        await server.db[coll].delete_many({})
    await server.db.employee_requests.delete_many({"request_number": {"$regex": "^PKY-(TEST|HRTEST)-"}})
    await server.db.files.delete_many({"kind": "hr_payroll"})


@pytest.mark.anyio
async def test_permission_catalog_and_unknown_rejected():
    keys = {k for _m, k, _l in PERMISSION_CATALOG}
    for key in NEW_PERMS:
        assert key in keys
        assert key in ALL_PERMISSIONS
    assert "payroll.hack" not in ALL_PERMISSIONS
    assert "not.a.real.permission" not in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_existing_roles_do_not_auto_gain_hr_payroll():
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        for key in NEW_PERMS:
            assert key not in defaults, f"{role} {key}"
            assert key not in derived, f"{role} {key}"
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    for key in NEW_PERMS:
        assert key in admin


@pytest.mark.anyio
async def test_employee_crud_auth_and_user_link():
    await _clean()
    admin = await _upsert_user(_user("admin", id="hr-admin-1", email="hr-admin-1@example.com"))
    op = await _upsert_user(_user("operator", id="hr-op-1", email="hr-op-1@example.com", permissions_override={}))
    async with await client_for(op) as c:
        try:
            deny = await c.get("/api/hr-payroll/employees")
            assert deny.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(admin) as c:
        try:
            created = await c.post("/api/hr-payroll/employees", json={
                "name": "Budi Operator",
                "employee_number": "NIK-1001",
                "user_id": op["id"],
                "role": "operator",
                "division": "operator",
                "salary_profile": {"base_salary": 5000000, "allowances": [{"code": "MAKAN", "name": "Uang Makan", "amount": 300000}]},
                "bank_name": "BCA",
                "bank_account": "123",
                "bank_account_name": "Budi",
            })
            assert created.status_code == 200, created.text
            emp = created.json()["employee"]
            assert emp["user_id"] == op["id"]
            assert emp["email"] == op["email"]
            assert to_money(emp["salary_profile"]["base_salary"]) == to_money(5000000)
            listed = await c.get("/api/hr-payroll/employees")
            assert listed.status_code == 200
            assert any(r["id"] == emp["id"] for r in listed.json()["employees"])
            # GET remains pure read — no mutation of users
            user_doc = await server.db.users.find_one({"id": op["id"]})
            assert "password_hash" not in (await c.get(f"/api/hr-payroll/employees/{emp['id']}")).json()["employee"]
            assert user_doc.get("email") == op["email"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_components_fixed_percentage_attendance_ot_kasbon_and_workflow():
    await _clean()
    admin = await _upsert_user(_user("admin", id="hr-admin-2", email="hr-admin-2@example.com"))
    stranger = await _upsert_user(_user("helper", id="hr-help-2", email="hr-help-2@example.com", permissions_override={
        PERM_EMP_VIEW: True, PERM_PAY_VIEW: True, PERM_SLIP_OWN: True,
    }))
    async with await client_for(admin) as c:
        try:
            emp = (await c.post("/api/hr-payroll/employees", json={
                "name": "Siti",
                "employee_number": "NIK-2002",
                "user_id": stranger["id"],
                "role": "helper",
                "division": "helper",
                "salary_profile": {"base_salary": 4000000, "allowances": []},
            })).json()["employee"]
            await c.post("/api/hr-payroll/components", json={
                "code": "TETAP", "name": "Tunjangan Tetap", "kind": "earning",
                "calc_mode": "fixed", "amount": 200000, "recurring": True, "active": True,
            })
            await c.post("/api/hr-payroll/components", json={
                "code": "PCT", "name": "Insentif %", "kind": "earning",
                "calc_mode": "percentage", "amount": 10, "percentage_of": "base",
                "recurring": True, "active": True,
            })
            await c.put("/api/hr-payroll/settings", json={
                "attendance": {
                    "late_grace_minutes": 5,
                    "late_rate_per_minute": 1000,
                    "late_cap": 50000,
                    "alpha_rate_per_day": 150000,
                    "unpaid_leave_rate_per_day": 100000,
                    "unpaid_sick_rate_per_day": 80000,
                },
                "overtime": {
                    "weekday_multiplier": 1.5,
                    "weekend_multiplier": 2,
                    "holiday_multiplier": 3,
                    "min_hours": 1,
                    "hourly_base_divisor": 25,
                },
                "kasbon": {
                    "default_mode": "installment_fixed",
                    "default_installment_amount": 500000,
                    "default_periods": 4,
                    "max_deduction_ratio": 0.5,
                },
            })
            # Approved kasbon linked by employee.id (user id on request)
            kas_suffix = uuid.uuid4().hex[:8].upper()
            req_id = str(uuid.uuid4())
            await server.db.employee_requests.insert_one({
                "id": req_id,
                "request_number": f"PKY-HRTEST-{kas_suffix}-A",
                "type_code": "KASBON",
                "status": "DISETUJUI",
                "employee": {"id": stranger["id"], "name": "Siti", "employee_id": stranger["employee_id"]},
                "values": {"nominal": 1000000},
                "type_snapshot": {"category": "Keuangan"},
                "submitted_at": "2026-09-01T00:00:00",
            })
            # Unapproved / rejected must not create deductible schedules via ensure endpoint semantics
            bad_id = str(uuid.uuid4())
            await server.db.employee_requests.insert_one({
                "id": bad_id,
                "request_number": f"PKY-HRTEST-{kas_suffix}-B",
                "type_code": "KASBON",
                "status": "DIAJUKAN",
                "employee": {"id": stranger["id"], "name": "Siti"},
                "values": {"nominal": 999999},
                "type_snapshot": {"category": "Keuangan"},
            })
            rej_id = str(uuid.uuid4())
            await server.db.employee_requests.insert_one({
                "id": rej_id,
                "request_number": f"PKY-HRTEST-{kas_suffix}-C",
                "type_code": "KASBON",
                "status": "DITOLAK",
                "employee": {"id": stranger["id"], "name": "Siti"},
                "values": {"nominal": 888888},
                "type_snapshot": {"category": "Keuangan"},
            })
            period = (await c.post("/api/hr-payroll/periods", json={
                "year": 2026, "month": 9, "start_date": "2026-09-01", "end_date": "2026-09-30",
            })).json()["period"]
            assert period["status"] == STATUS_DRAFT
            await c.put(f"/api/hr-payroll/periods/{period['id']}/attendance/{emp['id']}", json={
                "work_days": 22,
                "late_minutes": 20,
                "alpha_days": 1,
                "unpaid_leave_days": 1,
                "overtime_weekday_hours": 2,
                "overtime_weekend_hours": 0,
                "overtime_holiday_hours": 0,
            })
            calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert calc.status_code == 200, calc.text
            lines = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"]
            assert len(lines) == 1
            line = lines[0]
            codes = {e["code"] for e in line["earnings"]}
            assert "BASE" in codes and "TETAP" in codes and "PCT" in codes
            assert any(e["code"].startswith("OT_") for e in line["earnings"])
            assert any(d["source"] == "attendance" for d in line["deductions"])
            assert any(d["source"] == "kasbon" for d in line["deductions"])
            snap_ids = {s.get("request_id") for s in (line.get("kasbon_snapshot") or [])}
            assert req_id in snap_ids
            assert bad_id not in snap_ids
            assert rej_id not in snap_ids
            # Duplicate calc must not double kasbon for same period
            await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            line2 = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"][0]
            kasbon_rows = [d for d in line2["deductions"] if d.get("source") == "kasbon"]
            assert len(kasbon_rows) == 1
            await ensure_journal_indexes(server.db)
            salary = await create_account(
                server.db,
                {"code": f"51{uuid.uuid4().int % 10**6:06d}", "name": "Beban Gaji", "account_type": "EXPS"},
                user=admin,
            )
            payable = await create_account(
                server.db,
                {"code": f"21{uuid.uuid4().int % 10**6:06d}", "name": "Utang Gaji", "account_type": "OCLY"},
                user=admin,
            )
            mappings = {}
            for row in list(line2.get("earnings") or []) + list(line2.get("deductions") or []):
                key = source_key_for_row(row)
                if not key or to_money(row.get("amount")) <= 0:
                    continue
                treatment = "expense_debit" if row in (line2.get("earnings") or []) else "expense_credit"
                mappings[key] = {"treatment": treatment, "account_id": salary["id"]}
            mapped = await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": mappings,
            })
            assert mapped.status_code == 200, mapped.text
            # Skip transitions blocked
            skip = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_APPROVAL})
            assert skip.status_code == 409
            skip_final = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
            assert skip_final.status_code == 409
            # Unauthorized reviewer
            async with await client_for(stranger) as cs:
                try:
                    unauth = await cs.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_REVIEW})
                    assert unauth.status_code == 403
                finally:
                    app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides[current_user] = lambda: admin
            r1 = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_REVIEW, "note": "ok"})
            assert r1.status_code == 200
            assert r1.json()["period"]["status"] == STATUS_REVIEW
            # Review cannot jump to Final
            bad = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
            assert bad.status_code == 409
            r2 = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_APPROVAL})
            assert r2.status_code == 200
            # Double approval / concurrent finalize protection
            f1, f2 = await asyncio.gather(
                c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL}),
                c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL}),
            )
            codes = sorted([f1.status_code, f2.status_code])
            assert 200 in codes
            assert codes[1] in (200, 409)
            period_final = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"]
            assert period_final["status"] == STATUS_FINAL
            assert period_final.get("frozen") is True
            assert period_final.get("accounting_journal_id")
            journals = await server.db.journal_entries.count_documents({
                "source_type": "payroll_period", "source_id": period["id"], "reversal_of": None,
            })
            assert journals == 1
            assert await server.db.hr_payroll_payments.count_documents({"payroll_period_id": period["id"]}) == 0
            # Final edit/calc blocked
            blocked = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert blocked.status_code == 409
            # Snapshot immutable vs settings edit
            snap_base = line2["employee_snapshot"]["salary_profile"]["base_salary"]
            await c.put(f"/api/hr-payroll/employees/{emp['id']}", json={"salary_profile": {"base_salary": 1, "allowances": []}})
            hist_line = (await c.get(f"/api/hr-payroll/lines/{line2['id']}")).json()["line"]
            assert hist_line["employee_snapshot"]["salary_profile"]["base_salary"] == snap_base
            # PDF idempotent
            pdf1 = await c.get(f"/api/hr-payroll/periods/{period['id']}/lines/{line2['id']}/payslip")
            assert pdf1.status_code == 200
            assert pdf1.content[:4] == b"%PDF"
            pdf2 = await c.get(f"/api/hr-payroll/periods/{period['id']}/lines/{line2['id']}/payslip")
            assert pdf2.status_code == 200
            latest = (await c.get(f"/api/hr-payroll/lines/{line2['id']}")).json()["line"]
            assert latest["payslip"]["file_id"]
            # Kasbon outstanding reduced after final
            sched = (await c.get("/api/hr-payroll/kasbon-schedules", params={"employee_id": emp["id"]})).json()["schedules"]
            assert sched and sched[0]["outstanding"] < sched[0]["original_amount"]
            assert to_money(sched[0]["outstanding"]) == to_money(sched[0]["original_amount"]) - to_money(500000)
            posted_payments = [row for row in sched[0]["payments"] if row.get("period_id") == period["id"] and row.get("status") == "posted"]
            assert len(posted_payments) == 1
            # Own slip vs other
            async with await client_for(stranger) as cs:
                try:
                    own = await cs.get(f"/api/hr-payroll/periods/{period['id']}/lines/{line2['id']}/payslip")
                    assert own.status_code == 200
                finally:
                    app.dependency_overrides.pop(current_user, None)
            other = await _upsert_user(_user("operator", id="hr-op-other", email="hr-op-other@example.com", permissions_override={
                PERM_SLIP_OWN: True,
            }))
            async with await client_for(other) as co:
                try:
                    deny = await co.get(f"/api/hr-payroll/periods/{period['id']}/lines/{line2['id']}/payslip")
                    assert deny.status_code == 403
                finally:
                    app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides[current_user] = lambda: admin
            # Reopen without permission
            async with await client_for(stranger) as cs:
                try:
                    no_reopen = await cs.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "x"})
                    assert no_reopen.status_code == 403
                finally:
                    app.dependency_overrides.pop(current_user, None)
            app.dependency_overrides[current_user] = lambda: admin
            reopened = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "koreksi OT"})
            assert reopened.status_code == 409
            assert "jurnal" in reopened.text.lower()
            old = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"]
            assert old["status"] == STATUS_FINAL
            history = await c.get("/api/hr-payroll/history")
            assert history.status_code == 200
            assert history.json()["count"] >= 1
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unauthorized_approver_finalizer_and_concurrent_period():
    await _clean()
    admin = await _upsert_user(_user("admin", id="hr-admin-3", email="hr-admin-3@example.com"))
    reviewer = await _upsert_user(_user("crm", id="hr-rev-3", email="hr-rev-3@example.com", permissions_override={
        PERM_PAY_VIEW: True, PERM_PAY_REVIEW: True, PERM_PAY_CALC: True, PERM_EMP_MANAGE: True, PERM_EMP_VIEW: True,
    }))
    async with await client_for(admin) as c:
        try:
            await c.post("/api/hr-payroll/employees", json={
                "name": "Ani", "employee_number": "NIK-3003", "role": "crm", "division": "crm",
                "salary_profile": {"base_salary": 3500000, "allowances": []},
            })
            p = (await c.post("/api/hr-payroll/periods", json={
                "year": 2026, "month": 10, "start_date": "2026-10-01", "end_date": "2026-10-31",
            })).json()["period"]
            await c.post(f"/api/hr-payroll/periods/{p['id']}/calculate")
            # concurrent create same month blocked
            dup = await c.post("/api/hr-payroll/periods", json={
                "year": 2026, "month": 10, "start_date": "2026-10-01", "end_date": "2026-10-31",
            })
            assert dup.status_code == 409
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(reviewer) as c:
        try:
            ok = await c.post(f"/api/hr-payroll/periods/{p['id']}/transition", json={"to_status": STATUS_REVIEW})
            assert ok.status_code == 200
            deny_approve = await c.post(f"/api/hr-payroll/periods/{p['id']}/transition", json={"to_status": STATUS_APPROVAL})
            assert deny_approve.status_code == 403
            deny_final = await c.post(f"/api/hr-payroll/periods/{p['id']}/transition", json={"to_status": STATUS_FINAL})
            assert deny_final.status_code == 403
            assert "payroll.finalize" in deny_final.text
        finally:
            app.dependency_overrides.pop(current_user, None)
    approver = await _upsert_user(_user("admin", id="hr-appr-3", email="hr-appr-3@example.com"))
    final_only = await _upsert_user(_user("crm", id="hr-fin-3", email="hr-fin-3@example.com", permissions_override={
        PERM_PAY_VIEW: True,
        PERM_PAY_FINAL: True,
        "payroll_accounting.post": False,
    }))
    async with await client_for(approver) as c:
        try:
            approved = await c.post(f"/api/hr-payroll/periods/{p['id']}/transition", json={"to_status": STATUS_APPROVAL})
            assert approved.status_code == 200, approved.text
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(final_only) as c:
        try:
            denied = await c.post(f"/api/hr-payroll/periods/{p['id']}/transition", json={"to_status": STATUS_FINAL})
            assert denied.status_code == 403, denied.text
            assert "payroll_accounting.post" in denied.text
        finally:
            app.dependency_overrides.pop(current_user, None)
    stored = await server.db[PERIODS].find_one({"id": p["id"]}, {"_id": 0, "status": 1, "accounting_journal_id": 1})
    assert stored["status"] == STATUS_APPROVAL
    assert stored.get("accounting_journal_id") in (None, "")
    assert await server.db.journal_entries.count_documents({"source_id": p["id"]}) == 0


@pytest.mark.anyio
async def test_legacy_final_without_journal_reopen_restores_kasbon_once():
    await _clean()
    import services.hr_payroll as payroll

    admin = await _upsert_user(_user("admin", id="hr-admin-4", email="hr-admin-4@example.com"))
    async with await client_for(admin) as c:
        try:
            emp = (await c.post("/api/hr-payroll/employees", json={
                "name": "Dewi",
                "employee_number": "NIK-4004",
                "role": "helper",
                "division": "helper",
                "salary_profile": {"base_salary": 4000000, "allowances": []},
            })).json()["employee"]
            await c.put("/api/hr-payroll/settings", json={
                "kasbon": {
                    "default_mode": "installment_fixed",
                    "default_installment_amount": 500000,
                    "default_periods": 4,
                    "max_deduction_ratio": 0.5,
                },
            })
            req_id = str(uuid.uuid4())
            await server.db.employee_requests.insert_one({
                "id": req_id,
                "request_number": "PKY-HRTEST-RESTORE-A",
                "type_code": "KASBON",
                "status": "DISETUJUI",
                "employee": {"id": emp["id"], "name": "Dewi"},
                "values": {"nominal": 1000000},
                "type_snapshot": {"category": "Keuangan"},
                "submitted_at": "2026-08-01T00:00:00",
            })
            period = (await c.post("/api/hr-payroll/periods", json={
                "year": 2026, "month": 8, "start_date": "2026-08-01", "end_date": "2026-08-31",
            })).json()["period"]
            calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert calc.status_code == 200, calc.text
            await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_REVIEW})
            await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_APPROVAL})
            await payroll._post_kasbon_payments(server.db, period["id"])
            await server.db[PERIODS].update_one(
                {"id": period["id"]},
                {"$set": {"status": STATUS_FINAL, "frozen": True}},
            )
            schedule = await server.db[KASBON].find_one({"employee_id": emp["id"]}, {"_id": 0})
            assert to_money(schedule["outstanding"]) == to_money(500000)
            assert schedule["payments"][0]["status"] == "posted"

            await asyncio.gather(
                payroll._restore_posted_kasbon(server.db, period["id"]),
                payroll._restore_posted_kasbon(server.db, period["id"]),
            )
            restored = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
            assert to_money(restored["outstanding"]) == to_money(1000000)
            assert to_money(restored["paid_total"]) == to_money(0)
            assert restored["payments"][0]["status"] == "restored"
            assert restored["payments"][0]["period_id"] == period["id"]

            original_insert = payroll._insert_reopened_period

            async def fail_insert(db, doc):
                payroll._insert_reopened_period = original_insert
                raise HTTPException(500, "insert revisi gagal")

            payroll._insert_reopened_period = fail_insert
            try:
                failed = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "koreksi bulan sama"})
                assert failed.status_code == 500, failed.text
            finally:
                payroll._insert_reopened_period = original_insert
            after_fail = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
            assert to_money(after_fail["outstanding"]) == to_money(1000000)
            assert await server.db[PERIODS].count_documents({"group_id": period["group_id"], "revision": 2}) == 0

            opened = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "koreksi bulan sama"})
            assert opened.status_code == 200, opened.text
            new_p = opened.json()["period"]
            again = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "koreksi bulan sama"})
            assert again.status_code == 409, again.text
            assert to_money((await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0}))["outstanding"]) == to_money(1000000)

            await c.post(f"/api/hr-payroll/periods/{new_p['id']}/calculate")
            new_line = (await c.get(f"/api/hr-payroll/periods/{new_p['id']}/lines")).json()["lines"][0]
            kasbon_rows = [row for row in new_line["deductions"] if row.get("source") == "kasbon"]
            assert len(kasbon_rows) == 1
            assert to_money(kasbon_rows[0]["amount"]) == to_money(500000)
            fresh = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
            assert to_money(fresh["outstanding"]) == to_money(1000000)
            assert sum(1 for row in fresh["payments"] if row.get("status") == "posted") == 0
            assert sum(1 for row in fresh["payments"] if row.get("status") == "pending" and row.get("period_id") == new_p["id"]) == 1
            assert sum(1 for row in fresh["payments"] if row.get("status") == "restored" and row.get("period_id") == period["id"]) == 1
            expense = await create_account(
                server.db,
                {"code": f"51{uuid.uuid4().int % 10**6:06d}", "name": "Beban Penyesuaian", "account_type": "EXPS"},
                user=admin,
            )
            adj = await c.post(
                f"/api/hr-payroll/periods/{new_p['id']}/lines/{new_line['id']}/adjust",
                json={
                    "kind": "earning",
                    "amount": 50000,
                    "reason": "koreksi manual",
                    "accounting_treatment": "expense_debit",
                    "accounting_account_id": expense["id"],
                },
            )
            assert adj.status_code == 200, adj.text
            assert await server.db.journal_entries.count_documents({"source_id": period["id"]}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_stale_slip_and_line_writes_do_not_replace_winner():
    await _clean()
    from services.hr_payroll import _mark_open_lines_final, _return_existing_payslip

    winner = {
        "id": "line-winner",
        "period_id": "period-winner",
        "employee_id": "emp-winner",
        "revision": 1,
        "net": 100,
        "status": STATUS_FINAL,
        "finalized_at": "T1",
        "payslip": {
            "generated": True,
            "file_id": "file-winner",
            "net": 100,
            "journal_id": "journal-winner",
        },
    }
    await server.db[LINES].insert_one(dict(winner))
    with pytest.raises(HTTPException):
        await _return_existing_payslip(
            server.db, winner, {"net": 100, "journal_id": "journal-lama"},
        )
    stored = await server.db[LINES].find_one({"id": "line-winner"}, {"_id": 0})
    assert stored["payslip"]["file_id"] == "file-winner"
    assert stored["payslip"]["journal_id"] == "journal-winner"
    assert stored["payslip"]["net"] == 100
    assert stored["finalized_at"] == "T1"

    partial = {
        "id": "line-partial",
        "period_id": "period-winner",
        "employee_id": "emp-partial",
        "revision": 1,
        "net": 100,
        "status": STATUS_DRAFT,
        "payslip": {"generated": True, "file_id": "file-partial", "net": 100},
    }
    await server.db[LINES].insert_one(partial)
    await _return_existing_payslip(
        server.db, partial, {"net": 100, "journal_id": "journal-winner"},
    )
    with pytest.raises(HTTPException):
        await _return_existing_payslip(
            server.db,
            await server.db[LINES].find_one({"id": "line-partial"}, {"_id": 0}),
            {"net": 100, "journal_id": "journal-lama"},
        )
    partial_stored = await server.db[LINES].find_one({"id": "line-partial"}, {"_id": 0})
    assert partial_stored["payslip"]["journal_id"] == "journal-winner"
    assert partial_stored["payslip"]["file_id"] == "file-partial"
    assert partial_stored["payslip"]["net"] == 100

    await _mark_open_lines_final(server.db, "period-winner", "T2")
    still = await server.db[LINES].find_one({"id": "line-winner"}, {"_id": 0, "finalized_at": 1, "status": 1})
    opened = await server.db[LINES].find_one({"id": "line-partial"}, {"_id": 0, "finalized_at": 1, "status": 1})
    assert still["finalized_at"] == "T1"
    assert still["status"] == STATUS_FINAL
    assert opened["status"] == STATUS_FINAL
    assert opened["finalized_at"] == "T2"
    await _mark_open_lines_final(server.db, "period-winner", "T3")
    opened = await server.db[LINES].find_one({"id": "line-partial"}, {"_id": 0, "finalized_at": 1})
    assert opened["finalized_at"] == "T2"


def _future_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _cross_process_payroll_call(action, mongo_url, db_name, period_id):
    """One payroll action in a fresh process. Its asyncio lock is not the parent's."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def go():
        from services.hr_payroll import reopen_period, transition_period

        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        user = {"id": "racer-hr", "name": "Racer", "role": "admin", "email": "racer-hr@example.com"}
        try:
            if action == "finalize":
                await transition_period(db, period_id, "FINAL", "lintas proses", user)
                return "ok"
            await reopen_period(db, period_id, "lintas proses", user)
            return "ok"
        except Exception as exc:
            return f"err:{type(exc).__name__}:{exc}"
        finally:
            client.close()

    return asyncio.run(go())


def _run_payroll_processes(action, period_id, count=2):
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(count) as pool:
        return pool.starmap(
            _cross_process_payroll_call,
            [(action, os.environ["MONGO_URL"], os.environ["DB_NAME"], period_id) for _ in range(count)],
        )


async def _mapped_approval(c, admin, *, year, month, nominal=1_000_000, installment=500_000):
    await ensure_journal_indexes(server.db)
    emp = (await c.post("/api/hr-payroll/employees", json={
        "name": "Wati",
        "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
        "role": "helper",
        "division": "helper",
        "salary_profile": {"base_salary": nominal, "allowances": []},
    })).json()["employee"]
    await c.put("/api/hr-payroll/settings", json={
        "kasbon": {
            "default_mode": "installment_fixed",
            "default_installment_amount": installment,
            "default_periods": 4,
            "max_deduction_ratio": 0.5,
        },
    })
    await server.db.employee_requests.insert_one({
        "id": str(uuid.uuid4()),
        "request_number": f"PKY-HRTEST-{uuid.uuid4().hex[:8]}",
        "type_code": "KASBON",
        "status": "DISETUJUI",
        "employee": {"id": emp["id"], "name": "Wati"},
        "values": {"nominal": nominal},
        "type_snapshot": {"category": "Keuangan"},
        "submitted_at": f"{year}-{month:02d}-01T00:00:00",
    })
    period = (await c.post("/api/hr-payroll/periods", json={
        "year": year,
        "month": month,
        "start_date": f"{year}-{month:02d}-01",
        "end_date": f"{year}-{month:02d}-28",
    })).json()["period"]
    calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
    assert calc.status_code == 200, calc.text
    line = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"][0]
    salary = await create_account(
        server.db,
        {"code": f"51{uuid.uuid4().int % 10**6:06d}", "name": "Beban Gaji", "account_type": "EXPS"},
        user=admin,
    )
    payable = await create_account(
        server.db,
        {"code": f"21{uuid.uuid4().int % 10**6:06d}", "name": "Utang Gaji", "account_type": "OCLY"},
        user=admin,
    )
    mappings = {}
    for row in list(line.get("earnings") or []) + list(line.get("deductions") or []):
        key = source_key_for_row(row)
        if not key or to_money(row.get("amount")) <= 0:
            continue
        treatment = "expense_debit" if row in (line.get("earnings") or []) else "expense_credit"
        mappings[key] = {"treatment": treatment, "account_id": salary["id"]}
    mapped = await c.put("/api/settings/payroll-accounting", json={
        "payroll_payable_account_id": payable["id"],
        "component_mappings": mappings,
    })
    assert mapped.status_code == 200, mapped.text
    for target in (STATUS_REVIEW, STATUS_APPROVAL):
        moved = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": target})
        assert moved.status_code == 200, moved.text
        period = moved.json()["period"]
    return emp, period, line, {"salary": salary, "payable": payable}


@pytest.mark.anyio
async def test_incomplete_finalization_rejects_inputs_and_keeps_original_journal():
    await _clean()
    import services.hr_payroll as payroll
    import services.payroll_journal_posting as posting

    admin = await _upsert_user(_user("admin", id="hr-admin-guard", email="hr-admin-guard@example.com"))
    async with await client_for(admin) as c:
        try:
            _emp, period, line, accounts = await _mapped_approval(c, admin, year=2094, month=3)
            period_id = period["id"]
            original_commit = payroll._commit_final_status

            async def fail_commit(db, period_id, token, updates):
                payroll._commit_final_status = original_commit
                raise HTTPException(500, "commit gagal")

            payroll._commit_final_status = fail_commit
            try:
                failed = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
                assert failed.status_code == 500, failed.text
            finally:
                payroll._commit_final_status = original_commit

            stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
            assert stored["status"] == STATUS_APPROVAL
            journals = await server.db.journal_entries.find(
                {"source_type": "payroll_period", "source_id": period_id, "reversal_of": None},
                {"_id": 0},
            ).to_list(5)
            assert len(journals) == 1
            journal = journals[0]
            assert journal["payroll_payable"]["account_id"] == accounts["payable"]["id"]
            original_amount = to_money(journal["payroll_payable"]["amount"])
            assert original_amount == to_money(line["net"])
            slip = (await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "payslip": 1}))["payslip"]
            assert slip["generated"] is True
            assert slip["journal_id"] == journal["id"]
            assert to_money(slip["net"]) == to_money(line["net"])
            file_id = slip["file_id"]
            assert await server.db.files.count_documents({"line_id": line["id"], "kind": "hr_payroll", "is_deleted": False}) == 1

            calc = await c.post(f"/api/hr-payroll/periods/{period_id}/calculate")
            assert calc.status_code == 409
            attendance = await c.put(
                f"/api/hr-payroll/periods/{period_id}/attendance/{_emp['id']}",
                json={"work_days": 10, "late_minutes": 0, "alpha_days": 0, "unpaid_leave_days": 0,
                      "overtime_weekday_hours": 0, "overtime_weekend_hours": 0, "overtime_holiday_hours": 0},
            )
            assert attendance.status_code == 409
            expense = await create_account(
                server.db,
                {"code": f"51{uuid.uuid4().int % 10**6:06d}", "name": "Beban Koreksi", "account_type": "EXPS"},
                user=admin,
            )
            adjusted = await c.post(
                f"/api/hr-payroll/periods/{period_id}/lines/{line['id']}/adjust",
                json={
                    "kind": "earning",
                    "amount": 1000,
                    "reason": "ubah setelah jurnal",
                    "accounting_treatment": "expense_debit",
                    "accounting_account_id": expense["id"],
                },
            )
            assert adjusted.status_code == 409
            backward = await c.post(
                f"/api/hr-payroll/periods/{period_id}/transition",
                json={"to_status": STATUS_REVIEW},
            )
            assert backward.status_code == 409
            assert "jurnal akrual" in backward.text.lower()
            reopened = await c.post(f"/api/hr-payroll/periods/{period_id}/reopen", json={"reason": "terlalu cepat"})
            assert reopened.status_code == 409
            assert (await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL

            await server.db[LINES].update_one({"id": line["id"]}, {"$set": {"net": 1}})
            divergent = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert divergent.status_code == 409, divergent.text
            assert "utang gaji" in divergent.text.lower()
            still = await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "payslip": 1, "net": 1})
            assert still["payslip"]["file_id"] == file_id
            assert await server.db.files.count_documents({"line_id": line["id"], "kind": "hr_payroll"}) == 1
            assert (await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL
            await server.db[LINES].update_one({"id": line["id"]}, {"$set": {"net": float(original_amount)}})

            other_payable = await create_account(
                server.db,
                {"code": f"21{uuid.uuid4().int % 10**6:06d}", "name": "Utang Lain", "account_type": "OCLY"},
                user=admin,
            )
            mappings = {}
            for row in list(line.get("earnings") or []) + list(line.get("deductions") or []):
                key = source_key_for_row(row)
                if not key or to_money(row.get("amount")) <= 0:
                    continue
                treatment = "expense_debit" if row in (line.get("earnings") or []) else "expense_credit"
                mappings[key] = {"treatment": treatment, "account_id": accounts["salary"]["id"]}
            remapped = await c.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": other_payable["id"],
                "component_mappings": mappings,
            })
            assert remapped.status_code == 200, remapped.text
            await close_period(server.db, "2094-03", user=admin, reason="kunci setelah jurnal ada")
            calls = {"n": 0}
            original_post = posting.post_balanced_journal

            async def forbidden_post(*args, **kwargs):
                calls["n"] += 1
                raise AssertionError("jurnal asal tidak boleh diposting ulang")

            posting.post_balanced_journal = forbidden_post
            try:
                done = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
                assert done.status_code == 200, done.text
            finally:
                posting.post_balanced_journal = original_post
            assert calls["n"] == 0
            finished = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
            assert finished["status"] == STATUS_FINAL
            assert finished.get("workflow_claim") in (None, "")
            rows = await server.db.journal_entries.find(
                {"source_type": "payroll_period", "source_id": period_id, "reversal_of": None},
                {"_id": 0},
            ).to_list(5)
            assert len(rows) == 1
            assert rows[0]["id"] == journal["id"]
            assert rows[0]["payroll_payable"]["account_id"] == accounts["payable"]["id"]
            assert to_money(rows[0]["payroll_payable"]["amount"]) == original_amount
            final_slip = (await server.db[LINES].find_one({"id": line["id"]}, {"_id": 0, "payslip": 1}))["payslip"]
            assert final_slip["file_id"] == file_id
            assert final_slip["journal_id"] == journal["id"]
            assert await server.db.files.count_documents({"line_id": line["id"], "kind": "hr_payroll"}) == 1
            schedule = await server.db[KASBON].find_one({"employee_id": _emp["id"]}, {"_id": 0})
            posted = [row for row in schedule["payments"] if row.get("period_id") == period_id and row.get("status") == "posted"]
            assert len(posted) == 1
            assert to_money(schedule["outstanding"]) == to_money(1_000_000) - to_money(posted[0]["amount"])
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_workflow_claim_lease_takeover_and_stale_worker():
    await _clean()
    import services.hr_payroll as payroll

    admin = await _upsert_user(_user("admin", id="hr-admin-claim", email="hr-admin-claim@example.com"))
    async with await client_for(admin) as c:
        try:
            _emp, period, _line, _accounts = await _mapped_approval(c, admin, year=2094, month=4)
            period_id = period["id"]
            stale_token = str(uuid.uuid4())
            await server.db[PERIODS].update_one(
                {"id": period_id},
                {"$set": {
                    "workflow_claim": stale_token,
                    "workflow_op": payroll.WORKFLOW_POST,
                    "workflow_claim_expires_at": _future_iso(600),
                }},
            )
            blocked = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert blocked.status_code == 409, blocked.text
            assert await server.db.journal_entries.count_documents({"source_id": period_id}) == 0
            held = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "workflow_claim": 1, "status": 1})
            assert held["status"] == STATUS_APPROVAL
            assert held["workflow_claim"] == stale_token

            await server.db[PERIODS].update_one(
                {"id": period_id},
                {"$set": {
                    "workflow_claim": stale_token,
                    "workflow_op": payroll.WORKFLOW_FINALIZE,
                    "workflow_claim_expires_at": _future_iso(600),
                }},
            )
            recovered = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": STATUS_FINAL})
            assert recovered.status_code == 200, recovered.text
            finished = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
            assert finished["status"] == STATUS_FINAL
            assert finished.get("workflow_claim") in (None, "")
            assert await server.db.journal_entries.count_documents({
                "source_type": "payroll_period", "source_id": period_id, "reversal_of": None,
            }) == 1

            _emp2, period2, line2, _accounts2 = await _mapped_approval(c, admin, year=2094, month=5)
            claimed = await payroll.claim_workflow(server.db, period2["id"], payroll.WORKFLOW_FINALIZE, STATUS_APPROVAL)
            token_a = claimed["workflow_claim"]
            from services.payroll_journal_posting import post_payroll_journal
            await post_payroll_journal(
                server.db, period2["id"], admin, for_finalization=True, workflow_token=token_a,
            )
            stolen = await payroll.claim_workflow(server.db, period2["id"], payroll.WORKFLOW_FINALIZE, STATUS_APPROVAL)
            token_b = stolen["workflow_claim"]
            assert token_b != token_a
            before = await server.db[KASBON].find_one({"employee_id": _emp2["id"]}, {"_id": 0})
            before_slip = await server.db[LINES].find_one({"id": line2["id"]}, {"_id": 0, "payslip": 1, "status": 1})
            with pytest.raises(HTTPException) as lost:
                await payroll._apply_final_effects(server.db, period2["id"], "worker lama", admin, token_a)
            assert lost.value.status_code == 409
            after = await server.db[KASBON].find_one({"id": before["id"]}, {"_id": 0})
            after_slip = await server.db[LINES].find_one({"id": line2["id"]}, {"_id": 0, "payslip": 1, "status": 1})
            assert after["payments"] == before["payments"]
            assert after["outstanding"] == before["outstanding"]
            assert after["paid_total"] == before["paid_total"]
            assert after_slip.get("payslip") == before_slip.get("payslip")
            assert (await server.db[PERIODS].find_one({"id": period2["id"]}, {"_id": 0, "status": 1}))["status"] == STATUS_APPROVAL
            await payroll._apply_final_effects(server.db, period2["id"], "worker baru", admin, token_b)
            done = await server.db[PERIODS].find_one({"id": period2["id"]}, {"_id": 0, "status": 1, "workflow_claim": 1})
            assert done["status"] == STATUS_FINAL
            assert done.get("workflow_claim") in (None, "")
            schedule = await server.db[KASBON].find_one({"id": before["id"]}, {"_id": 0})
            assert sum(1 for row in schedule["payments"] if row.get("period_id") == period2["id"] and row.get("status") == "posted") == 1
            assert (await server.db[LINES].find_one({"id": line2["id"]}, {"_id": 0, "payslip": 1}))["payslip"]["generated"] is True
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_kasbon_schedule_update_is_conditional_and_retries():
    await _clean()
    import services.hr_payroll as payroll

    period_id = "period-kasbon-atomic"
    first = {
        "id": "sched-a",
        "request_id": "req-sched-a",
        "employee_id": "emp-a",
        "outstanding": 1000.0,
        "paid_total": 0.0,
        "active": True,
        "payments": [
            {"period_id": period_id, "status": "pending", "amount": 400.0},
            {"period_id": "other-period", "status": "pending", "amount": 50.0},
        ],
    }
    second = {
        "id": "sched-b",
        "request_id": "req-sched-b",
        "employee_id": "emp-b",
        "outstanding": 800.0,
        "paid_total": 100.0,
        "active": True,
        "payments": [{"period_id": period_id, "status": "pending", "amount": 250.0}],
    }
    restored = {
        "id": "sched-c",
        "request_id": "req-sched-c",
        "employee_id": "emp-c",
        "outstanding": 1000.0,
        "paid_total": 0.0,
        "active": True,
        "payments": [{"period_id": period_id, "status": "restored", "amount": 400.0}],
    }
    await server.db[KASBON].insert_many([first, second, restored])
    await asyncio.gather(
        payroll._post_one_kasbon_schedule(server.db, first, period_id),
        payroll._post_one_kasbon_schedule(server.db, first, period_id),
    )
    saved = await server.db[KASBON].find_one({"id": "sched-a"}, {"_id": 0})
    assert to_money(saved["outstanding"]) == to_money(600)
    assert to_money(saved["paid_total"]) == to_money(400)
    statuses = {row["period_id"]: row["status"] for row in saved["payments"]}
    assert statuses[period_id] == "posted"
    assert statuses["other-period"] == "pending"
    await payroll._post_one_kasbon_schedule(server.db, saved, period_id)
    again = await server.db[KASBON].find_one({"id": "sched-a"}, {"_id": 0})
    assert to_money(again["outstanding"]) == to_money(600)
    assert to_money(again["paid_total"]) == to_money(400)

    untouched = await server.db[KASBON].find_one({"id": "sched-c"}, {"_id": 0})
    await payroll._post_one_kasbon_schedule(server.db, untouched, period_id)
    untouched = await server.db[KASBON].find_one({"id": "sched-c"}, {"_id": 0})
    assert untouched["payments"][0]["status"] == "restored"
    assert to_money(untouched["outstanding"]) == to_money(1000)

    retry_period = "period-kasbon-retry"
    await server.db[KASBON].insert_many([
        {
            "id": "sched-d",
            "request_id": "req-sched-d",
            "employee_id": "emp-d",
            "outstanding": 900.0,
            "paid_total": 0.0,
            "active": True,
            "payments": [{"period_id": retry_period, "status": "pending", "amount": 300.0}],
        },
        {
            "id": "sched-e",
            "request_id": "req-sched-e",
            "employee_id": "emp-e",
            "outstanding": 700.0,
            "paid_total": 20.0,
            "active": True,
            "payments": [{"period_id": retry_period, "status": "pending", "amount": 125.0}],
        },
    ])
    original = payroll._post_one_kasbon_schedule

    async def fail_second(db, schedule, target_period):
        if schedule["id"] == "sched-e":
            raise HTTPException(500, "jadwal kedua gagal")
        await original(db, schedule, target_period)

    payroll._post_one_kasbon_schedule = fail_second
    try:
        ordered = await server.db[KASBON].find({"payments.period_id": retry_period}, {"_id": 0}).sort("id", 1).to_list(10)
        with pytest.raises(HTTPException):
            for schedule in ordered:
                await payroll._post_one_kasbon_schedule(server.db, schedule, retry_period)
    finally:
        payroll._post_one_kasbon_schedule = original
    mid_d = await server.db[KASBON].find_one({"id": "sched-d"}, {"_id": 0})
    mid_e = await server.db[KASBON].find_one({"id": "sched-e"}, {"_id": 0})
    assert mid_d["payments"][0]["status"] == "posted"
    assert to_money(mid_d["outstanding"]) == to_money(600)
    assert to_money(mid_d["paid_total"]) == to_money(300)
    assert mid_e["payments"][0]["status"] == "pending"
    assert to_money(mid_e["outstanding"]) == to_money(700)
    await payroll._post_kasbon_payments(server.db, retry_period)
    done_e = await server.db[KASBON].find_one({"id": "sched-e"}, {"_id": 0})
    assert done_e["payments"][0]["status"] == "posted"
    assert to_money(done_e["outstanding"]) == to_money(575)
    assert to_money(done_e["paid_total"]) == to_money(145)
    done_d = await server.db[KASBON].find_one({"id": "sched-d"}, {"_id": 0})
    assert to_money(done_d["outstanding"]) == to_money(600)
    assert to_money(done_d["paid_total"]) == to_money(300)
    done_b = await server.db[KASBON].find_one({"id": "sched-b"}, {"_id": 0})
    assert done_b["payments"][0]["status"] == "pending"
    assert to_money(done_b["outstanding"]) == to_money(800)


@pytest.mark.anyio
async def test_two_processes_finalize_and_reopen_once():
    await _clean()
    import services.hr_payroll as payroll

    admin = await _upsert_user(_user("admin", id="hr-admin-race", email="hr-admin-race@example.com"))
    async with await client_for(admin) as c:
        try:
            emp, period, _line, _accounts = await _mapped_approval(c, admin, year=2094, month=6)
            results = _run_payroll_processes("finalize", period["id"])
            assert any(result == "ok" for result in results), results
            stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
            assert stored["status"] == STATUS_FINAL
            assert stored.get("workflow_claim") in (None, "")
            assert await server.db.journal_entries.count_documents({
                "source_type": "payroll_period", "source_id": period["id"], "reversal_of": None, "status": "posted",
            }) == 1
            schedule = await server.db[KASBON].find_one({"employee_id": emp["id"]}, {"_id": 0})
            posted = [row for row in schedule["payments"] if row.get("period_id") == period["id"] and row.get("status") == "posted"]
            assert len(posted) == 1
            assert to_money(schedule["outstanding"]) == to_money(schedule["original_amount"]) - to_money(posted[0]["amount"])
            assert to_money(schedule["paid_total"]) == to_money(posted[0]["amount"])
            line = await server.db[LINES].find_one({"period_id": period["id"]}, {"_id": 0, "id": 1, "payslip": 1})
            assert line["payslip"]["generated"] is True
            assert line["payslip"]["journal_id"]
            assert await server.db.files.count_documents({"line_id": line["id"], "kind": "hr_payroll"}) == 1

            legacy_emp, legacy, _legacy_line, _legacy_accounts = await _mapped_approval(c, admin, year=2094, month=7)
            await payroll._post_kasbon_payments(server.db, legacy["id"])
            await server.db.journal_entries.delete_many({"source_id": legacy["id"]})
            await server.db.journal_lines.delete_many({"journal_id": {"$in": []}})
            await server.db[PERIODS].update_one(
                {"id": legacy["id"]},
                {"$set": {"status": STATUS_FINAL, "frozen": True}, "$unset": {
                    "accounting_journal_id": "", "workflow_claim": "", "workflow_op": "", "workflow_claim_expires_at": "",
                }},
            )
            await server.db[ATTENDANCE].insert_one({
                "id": str(uuid.uuid4()),
                "period_id": legacy["id"],
                "employee_id": legacy_emp["id"],
                "work_days": 20,
                "created_at": "2094-07-01T00:00:00+00:00",
            })
            before = await server.db[KASBON].find_one({"employee_id": legacy_emp["id"]}, {"_id": 0})
            assert to_money(before["outstanding"]) < to_money(before["original_amount"])
            reduced_by = to_money(before["original_amount"]) - to_money(before["outstanding"])
            reopen_results = _run_payroll_processes("reopen", legacy["id"])
            assert any(result == "ok" for result in reopen_results), reopen_results
            revisions = await server.db[PERIODS].count_documents({"group_id": legacy["group_id"], "revision": 2})
            assert revisions == 1
            opened = await server.db[PERIODS].find_one({"group_id": legacy["group_id"], "revision": 2}, {"_id": 0, "id": 1})
            assert await server.db[ATTENDANCE].count_documents({"period_id": opened["id"]}) == 1
            assert await server.db[ATTENDANCE].count_documents({"period_id": legacy["id"]}) == 1
            restored = await server.db[KASBON].find_one({"id": before["id"]}, {"_id": 0})
            assert to_money(restored["outstanding"]) == to_money(before["original_amount"])
            assert to_money(restored["paid_total"]) == to_money(before["paid_total"]) - reduced_by
            assert sum(1 for row in restored["payments"] if row.get("period_id") == legacy["id"] and row.get("status") == "restored") == 1
            assert sum(1 for row in restored["payments"] if row.get("status") == "posted") == 0
            repeated = await c.post(f"/api/hr-payroll/periods/{legacy['id']}/reopen", json={"reason": "ulang"})
            assert repeated.status_code == 409, repeated.text
            assert await server.db[PERIODS].count_documents({"group_id": legacy["group_id"], "revision": 2}) == 1
            repeated_kasbon = await server.db[KASBON].find_one({"id": before["id"]}, {"_id": 0})
            assert to_money(repeated_kasbon["outstanding"]) == to_money(restored["outstanding"])

            token_a_doc = await payroll.claim_workflow(server.db, legacy["id"], payroll.WORKFLOW_REOPEN, STATUS_FINAL)
            token_b_doc = await payroll.claim_workflow(server.db, legacy["id"], payroll.WORKFLOW_REOPEN, STATUS_FINAL)
            assert token_a_doc["workflow_claim"] != token_b_doc["workflow_claim"]
            outstanding_now = to_money(repeated_kasbon["outstanding"])
            with pytest.raises(HTTPException) as stale:
                await payroll._reopen_effects(
                    server.db, await payroll.get_period(server.db, legacy["id"]), "worker lama", admin,
                    token_a_doc["workflow_claim"], 3,
                )
            assert stale.value.status_code == 409
            assert await server.db[PERIODS].count_documents({"group_id": legacy["group_id"], "revision": 3}) == 0
            assert to_money((await server.db[KASBON].find_one({"id": before["id"]}, {"_id": 0}))["outstanding"]) == outstanding_now
            await payroll.release_workflow_claim(server.db, legacy["id"], token_b_doc["workflow_claim"])
        finally:
            app.dependency_overrides.pop(current_user, None)
