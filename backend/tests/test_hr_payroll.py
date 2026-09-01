"""HR & Payroll — workflow, Kasbon, snapshots, permissions, PDF."""
import asyncio
import os
import sys
import uuid
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
            assert reopened.status_code == 200
            new_p = reopened.json()["period"]
            assert new_p["status"] == STATUS_DRAFT
            assert new_p["revision"] == 2
            assert new_p["parent_period_id"] == period["id"]
            old = (await c.get(f"/api/hr-payroll/periods/{period['id']}")).json()["period"]
            assert old["status"] == STATUS_FINAL
            # Adjustment only in Draft of new revision
            await c.post(f"/api/hr-payroll/periods/{new_p['id']}/calculate")
            new_line = (await c.get(f"/api/hr-payroll/periods/{new_p['id']}/lines")).json()["lines"][0]
            adj = await c.post(
                f"/api/hr-payroll/periods/{new_p['id']}/lines/{new_line['id']}/adjust",
                json={"kind": "earning", "amount": 50000, "reason": "koreksi manual"},
            )
            assert adj.status_code == 200
            assert any(a.get("reason") == "koreksi manual" for a in adj.json()["line"]["adjustments"])
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
        finally:
            app.dependency_overrides.pop(current_user, None)
