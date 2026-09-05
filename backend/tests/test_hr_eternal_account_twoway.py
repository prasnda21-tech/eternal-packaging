"""Two-way HR ↔ Eternal account: explicit Buat Akun Eternal."""
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
from services.hr_payroll import EMPLOYEES, PERM_EMP_MANAGE, PERM_EMP_VIEW, PERM_PAY_VIEW, ensure_indexes

MARKER = "PREVIEW-EMPLOYEE-TWOWAY"
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


def _admin(**kw):
    uid = kw.get("id") or f"tw-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "TW Admin",
        "role": "admin",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {},
        "is_super_admin": True,
    }


def _hr_only_actor(**kw):
    uid = kw.get("id") or f"tw-hr-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "HR Only",
        "role": "helper",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {
            PERM_EMP_VIEW: True,
            PERM_EMP_MANAGE: True,
            PERM_PAY_VIEW: True,
            "users.manage": False,
        },
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean():
    await server.db[EMPLOYEES].delete_many({
        "$or": [
            {"name": {"$regex": f"^{MARKER}"}},
            {"email": {"$regex": "preview-employee-twoway"}},
            {"employee_number": {"$regex": "^TW-"}},
        ]
    })
    await server.db.users.delete_many({"email": {"$regex": "preview-employee-twoway"}})


@pytest.mark.anyio
async def test_hr_create_without_account_does_not_create_user():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin1@example.com"))
    users_before = await server.db.users.count_documents({})
    async with await client_for(admin) as c:
        try:
            res = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-HROnly",
                "employee_number": "TW-HR-001",
                "division": "gudang",
                "create_account": False,
                "salary_profile": {"base_salary": 1000, "allowances": []},
            })
            assert res.status_code == 200, res.text
            emp = res.json()["employee"]
            assert emp["id"]
            assert not emp.get("user_id")
            assert await server.db.users.count_documents({}) == users_before
            assert await server.db.users.count_documents({"email": {"$regex": "preview-employee-twoway-hronly"}}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"id": emp["id"]}) if res.status_code == 200 else None


@pytest.mark.anyio
async def test_hr_create_with_account_links_exact_user_and_settings_visible():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin2@example.com"))
    email = "preview-employee-twoway-linked@example.com"
    role_perms_before = await server.db.role_permissions.find({}, {"_id": 0}).to_list(500)
    periods_before = await server.db.hr_payroll_periods.count_documents({})
    lines_before = await server.db.hr_payroll_lines.count_documents({})
    async with await client_for(admin) as c:
        try:
            res = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-LinkedHR",
                "employee_number": "TW-HR-002",
                "division": "produksi",
                "employment_status": "kontrak",
                "salary_profile": {"base_salary": 2500, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-LinkedUser",
                    "email": email,
                    "role": "operator",
                    "password": "SecurePass99!",
                },
            })
            assert res.status_code == 200, res.text
            emp = res.json()["employee"]
            assert emp["user_id"]
            assert emp["has_hr_profile"] is True
            assert emp["name"] == f"{MARKER}-LinkedUser"  # enriched from users
            assert emp["role"] == "operator"
            assert emp["division"] == "produksi"  # HR field not invented from role
            assert emp["salary_profile"]["base_salary"] == 2500

            user = await server.db.users.find_one({"id": emp["user_id"]}, {"_id": 0, "password_hash": 0})
            assert user["email"] == email
            assert user["role"] == "operator"
            assert "password" not in emp and "password_hash" not in emp

            settings_list = await c.get("/api/admin/users")
            assert settings_list.status_code == 200
            assert any(u["id"] == emp["user_id"] for u in settings_list.json()["users"])

            dir_rows = [r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r.get("user_id") == emp["user_id"]]
            assert len(dir_rows) == 1
            assert dir_rows[0]["id"] == emp["id"]

            assert await server.db.role_permissions.find({}, {"_id": 0}).to_list(500) == role_perms_before
            assert await server.db.hr_payroll_periods.count_documents({}) == periods_before
            assert await server.db.hr_payroll_lines.count_documents({}) == lines_before
        finally:
            app.dependency_overrides.pop(current_user, None)
            if res.status_code == 200:
                uid = emp.get("user_id")
                await server.db[EMPLOYEES].delete_many({"id": emp["id"]})
                if uid:
                    await server.db.users.delete_many({"id": uid})


@pytest.mark.anyio
async def test_existing_hr_create_eternal_account_no_second_employee():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin3@example.com"))
    async with await client_for(admin) as c:
        try:
            emp = (await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-Legacy",
                "employee_number": "TW-HR-003",
                "create_account": False,
                "salary_profile": {"base_salary": 500, "allowances": []},
            })).json()["employee"]
            hr_count = await server.db[EMPLOYEES].count_documents({"employee_number": "TW-HR-003"})
            assert hr_count == 1 and not emp.get("user_id")

            linked = await c.post(f"/api/hr-payroll/employees/{emp['id']}/eternal-account", json={
                "name": f"{MARKER}-LegacyUser",
                "email": "preview-employee-twoway-legacy@example.com",
                "role": "helper",
                "password": "SecurePass99!",
            })
            assert linked.status_code == 200, linked.text
            row = linked.json()["employee"]
            assert row["id"] == emp["id"]
            assert row["user_id"]
            assert await server.db[EMPLOYEES].count_documents({"employee_number": "TW-HR-003"}) == 1

            again = await c.post(f"/api/hr-payroll/employees/{emp['id']}/eternal-account", json={
                "name": "X", "email": "preview-employee-twoway-legacy2@example.com",
                "role": "helper", "password": "SecurePass99!",
            })
            assert again.status_code == 409
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-legacy2@example.com"}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"employee_number": "TW-HR-003"})
            await server.db.users.delete_many({"email": {"$regex": "preview-employee-twoway-legacy"}})


@pytest.mark.anyio
async def test_duplicate_email_rejected_no_autolink_no_partial():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin4@example.com"))
    await _upsert_user({
        "id": str(uuid.uuid4()),
        "email": "preview-employee-twoway-taken@example.com",
        "name": "Taken",
        "role": "crm",
        "employee_id": "EMP-TAKE",
        "status": "active",
        "password_hash": "x",
        "permissions_override": {},
    })
    async with await client_for(admin) as c:
        try:
            hr_before = await server.db[EMPLOYEES].count_documents({})
            users_before = await server.db.users.count_documents({})
            res = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-Dup",
                "employee_number": "TW-HR-004",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "Dup",
                    "email": "preview-employee-twoway-taken@example.com",
                    "role": "operator",
                    "password": "SecurePass99!",
                },
            })
            assert res.status_code == 409
            assert "Email sudah digunakan" in res.text
            assert await server.db[EMPLOYEES].count_documents({}) == hr_before
            assert await server.db.users.count_documents({}) == users_before
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db.users.delete_many({"email": "preview-employee-twoway-taken@example.com"})


@pytest.mark.anyio
async def test_invalid_role_and_missing_password_rejected():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin5@example.com"))
    async with await client_for(admin) as c:
        try:
            bad_role = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-BadRole",
                "employee_number": "TW-HR-005",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "Bad", "email": "preview-employee-twoway-badrole@example.com",
                    "role": "not_a_real_role_xyz", "password": "SecurePass99!",
                },
            })
            assert bad_role.status_code == 400
            no_pw = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-NoPw",
                "employee_number": "TW-HR-006",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "NoPw", "email": "preview-employee-twoway-nopw@example.com",
                    "role": "operator",
                },
            })
            assert no_pw.status_code == 400
            assert await server.db.users.count_documents({"email": {"$regex": "preview-employee-twoway-(badrole|nopw)"}}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_hr_manage_without_users_manage_cannot_create_account_but_can_hr_only():
    await _clean()
    actor = await _upsert_user(_hr_only_actor(email="preview-employee-twoway-hrmgr@example.com"))
    async with await client_for(actor) as c:
        try:
            ok = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-PermHR",
                "employee_number": "TW-HR-007",
                "create_account": False,
                "salary_profile": {"base_salary": 1, "allowances": []},
            })
            assert ok.status_code == 200, ok.text
            denied = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-PermAcc",
                "employee_number": "TW-HR-008",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "X", "email": "preview-employee-twoway-denied@example.com",
                    "role": "helper", "password": "SecurePass99!",
                },
            })
            assert denied.status_code == 403
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-denied@example.com"}) == 0
            emp_id = ok.json()["employee"]["id"]
            denied2 = await c.post(f"/api/hr-payroll/employees/{emp_id}/eternal-account", json={
                "name": "X", "email": "preview-employee-twoway-denied2@example.com",
                "role": "helper", "password": "SecurePass99!",
            })
            assert denied2.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"employee_number": {"$in": ["TW-HR-007", "TW-HR-008"]}})


@pytest.mark.anyio
async def test_settings_role_update_reflects_and_delete_hr_keeps_user():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin6@example.com"))
    async with await client_for(admin) as c:
        try:
            emp = (await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-KeepUser",
                "employee_number": "TW-HR-009",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-KeepUser",
                    "email": "preview-employee-twoway-keep@example.com",
                    "role": "operator",
                    "password": "SecurePass99!",
                },
            })).json()["employee"]
            uid = emp["user_id"]
            await c.put(f"/api/admin/users/{uid}", json={"role": "helper", "name": f"{MARKER}-Renamed"})
            rows = [r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r.get("user_id") == uid]
            assert len(rows) == 1
            assert rows[0]["role"] == "helper"
            assert rows[0]["name"] == f"{MARKER}-Renamed"

            # No delete HR endpoint — remove exact HR profile fixture only.
            await server.db[EMPLOYEES].delete_many({"id": emp["id"]})
            assert await server.db.users.count_documents({"id": uid}) == 1
            virt = [r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r.get("user_id") == uid]
            assert len(virt) == 1 and virt[0]["has_hr_profile"] is False and virt[0]["id"] is None

            await c.post(f"/api/admin/users/{uid}/deactivate")
            # Deactivate must not recreate/delete HR
            assert await server.db[EMPLOYEES].count_documents({"user_id": uid}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"employee_number": "TW-HR-009"})
            await server.db.users.delete_many({"email": "preview-employee-twoway-keep@example.com"})


@pytest.mark.anyio
async def test_same_name_accounts_remain_distinct():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin7@example.com"))
    async with await client_for(admin) as c:
        try:
            a = (await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-Same",
                "employee_number": "TW-HR-010",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-Same",
                    "email": "preview-employee-twoway-same1@example.com",
                    "role": "operator", "password": "SecurePass99!",
                },
            })).json()["employee"]
            b = (await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-Same",
                "employee_number": "TW-HR-011",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-Same",
                    "email": "preview-employee-twoway-same2@example.com",
                    "role": "crm", "password": "SecurePass99!",
                },
            })).json()["employee"]
            assert a["user_id"] != b["user_id"]
            assert a["id"] != b["id"]
            rows = (await c.get("/api/hr-payroll/employees", params={"q": MARKER + "-Same"})).json()["employees"]
            keys = {r["directory_key"] for r in rows if r.get("user_id") in (a["user_id"], b["user_id"])}
            assert len(keys) == 2
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"employee_number": {"$in": ["TW-HR-010", "TW-HR-011"]}})
            await server.db.users.delete_many({"email": {"$regex": "preview-employee-twoway-same"}})


def _users_manage_only_actor(**kw):
    uid = kw.get("id") or f"tw-um-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "Users Manage Only",
        "role": "helper",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {
            "users.manage": True,
            "users.view": True,
            PERM_EMP_VIEW: False,
            PERM_EMP_MANAGE: False,
            PERM_PAY_VIEW: False,
        },
    }


def _both_perms_actor(**kw):
    uid = kw.get("id") or f"tw-both-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "Both Perms",
        "role": "helper",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {
            PERM_EMP_VIEW: True,
            PERM_EMP_MANAGE: True,
            PERM_PAY_VIEW: True,
            "users.manage": True,
            "users.view": True,
        },
    }


@pytest.mark.anyio
async def test_permission_matrix_hr_only_users_only_both_and_admin():
    """A/B/C/D permission combinations for HR create vs Eternal account."""
    await _clean()
    role_perms_before = await server.db.role_permissions.find({}, {"_id": 0}).to_list(500)
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin-perm@example.com"))
    hr_only = await _upsert_user(_hr_only_actor(email="preview-employee-twoway-hr-only@example.com"))
    users_only = await _upsert_user(_users_manage_only_actor(email="preview-employee-twoway-users-only@example.com"))
    both = await _upsert_user(_both_perms_actor(email="preview-employee-twoway-both@example.com"))
    created = {"emp": [], "users": []}

    # A: hr.employee.manage only
    async with await client_for(hr_only) as c:
        try:
            ok = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-A-HR",
                "employee_number": "TW-PERM-A",
                "create_account": False,
                "salary_profile": {"base_salary": 1, "allowances": []},
            })
            assert ok.status_code == 200, ok.text
            created["emp"].append(ok.json()["employee"]["id"])
            deny_create = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-A-Acc",
                "employee_number": "TW-PERM-A2",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "A", "email": "preview-employee-twoway-a-denied@example.com",
                    "role": "helper", "password": "SecurePass99!",
                },
            })
            assert deny_create.status_code == 403
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-a-denied@example.com"}) == 0
            deny_link = await c.post(
                f"/api/hr-payroll/employees/{ok.json()['employee']['id']}/eternal-account",
                json={
                    "name": "A", "email": "preview-employee-twoway-a-denied2@example.com",
                    "role": "helper", "password": "SecurePass99!",
                },
            )
            assert deny_link.status_code == 403
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-a-denied2@example.com"}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)

    # B: users.manage only — cannot create/link via HR endpoints
    async with await client_for(users_only) as c:
        try:
            deny_hr = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-B-HR",
                "employee_number": "TW-PERM-B",
                "create_account": False,
                "salary_profile": {"base_salary": 1, "allowances": []},
            })
            assert deny_hr.status_code == 403
            deny_acc = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-B-Acc",
                "employee_number": "TW-PERM-B2",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": "B", "email": "preview-employee-twoway-b-denied@example.com",
                    "role": "helper", "password": "SecurePass99!",
                },
            })
            assert deny_acc.status_code == 403
            # existing HR from A — still cannot link without hr.employee.manage
            deny_link = await c.post(
                f"/api/hr-payroll/employees/{created['emp'][0]}/eternal-account",
                json={
                    "name": "B", "email": "preview-employee-twoway-b-denied2@example.com",
                    "role": "helper", "password": "SecurePass99!",
                },
            )
            assert deny_link.status_code == 403
            assert await server.db.users.count_documents({"email": {"$regex": "preview-employee-twoway-b-denied"}}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)

    # C: both permissions
    async with await client_for(both) as c:
        try:
            ok = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-C-Both",
                "employee_number": "TW-PERM-C",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-C-Both",
                    "email": "preview-employee-twoway-c-both@example.com",
                    "role": "operator",
                    "password": "SecurePass99!",
                },
            })
            assert ok.status_code == 200, ok.text
            emp = ok.json()["employee"]
            created["emp"].append(emp["id"])
            created["users"].append(emp["user_id"])
            assert emp["user_id"]
        finally:
            app.dependency_overrides.pop(current_user, None)

    # D: admin ALL_PERMISSIONS / super admin
    async with await client_for(admin) as c:
        try:
            ok = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-D-Admin",
                "employee_number": "TW-PERM-D",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-D-Admin",
                    "email": "preview-employee-twoway-d-admin@example.com",
                    "role": "crm",
                    "password": "SecurePass99!",
                },
            })
            assert ok.status_code == 200, ok.text
            emp = ok.json()["employee"]
            created["emp"].append(emp["id"])
            created["users"].append(emp["user_id"])
        finally:
            app.dependency_overrides.pop(current_user, None)

    assert await server.db.role_permissions.find({}, {"_id": 0}).to_list(500) == role_perms_before
    await server.db[EMPLOYEES].delete_many({"id": {"$in": created["emp"]}})
    await server.db.users.delete_many({"id": {"$in": created["users"]}})
    await server.db.users.delete_many({"email": {"$regex": "preview-employee-twoway-(hr-only|users-only|both|admin-perm|a-denied|b-denied|c-both|d-admin)"}})


@pytest.mark.anyio
async def test_compensating_rollback_on_hr_failure_removes_exact_user_and_audit():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin-rb@example.com"))
    async with await client_for(admin) as c:
        try:
            first = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-RB-First",
                "employee_number": "TW-RB-001",
                "create_account": False,
                "salary_profile": {"base_salary": 1, "allowances": []},
            })
            assert first.status_code == 200
            emp1 = first.json()["employee"]
            users_before = await server.db.users.count_documents({"email": "preview-employee-twoway-rb@example.com"})
            assert users_before == 0

            # Same employee_number → HR insert fails after user create → compensate
            conflict = await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-RB-Second",
                "employee_number": "TW-RB-001",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-RB-Second",
                    "email": "preview-employee-twoway-rb@example.com",
                    "role": "helper",
                    "password": "SecurePass99!",
                },
            })
            assert conflict.status_code == 409
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-rb@example.com"}) == 0
            assert await server.db.audit_logs.count_documents({
                "action": "user.create",
                "target_email": "preview-employee-twoway-rb@example.com",
            }) == 0
            assert await server.db.audit_logs.count_documents({
                "action": "user.create",
                "after.id": {"$exists": True},
                "after.email": "preview-employee-twoway-rb@example.com",
            }) == 0
            assert await server.db[EMPLOYEES].count_documents({"employee_number": "TW-RB-001"}) == 1
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"id": emp1["id"]})
            await server.db.users.delete_many({"email": "preview-employee-twoway-rb@example.com"})


@pytest.mark.anyio
async def test_retry_and_double_submit_do_not_duplicate():
    await _clean()
    admin = await _upsert_user(_admin(email="preview-employee-twoway-admin-retry@example.com"))
    async with await client_for(admin) as c:
        try:
            payload = {
                "name": f"{MARKER}-Retry",
                "employee_number": "TW-RETRY-001",
                "salary_profile": {"base_salary": 1, "allowances": []},
                "create_account": {
                    "name": f"{MARKER}-Retry",
                    "email": "preview-employee-twoway-retry@example.com",
                    "role": "operator",
                    "password": "SecurePass99!",
                },
            }
            first = await c.post("/api/hr-payroll/employees", json=payload)
            assert first.status_code == 200, first.text
            emp = first.json()["employee"]
            uid = emp["user_id"]

            # Client retry after success: same email → 409, no second user
            second = await c.post("/api/hr-payroll/employees", json=payload)
            assert second.status_code == 409
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-retry@example.com"}) == 1
            assert await server.db[EMPLOYEES].count_documents({"employee_number": "TW-RETRY-001"}) == 1

            # Existing HR already linked → second eternal-account rejected
            again = await c.post(f"/api/hr-payroll/employees/{emp['id']}/eternal-account", json={
                "name": "X",
                "email": "preview-employee-twoway-retry2@example.com",
                "role": "helper",
                "password": "SecurePass99!",
            })
            assert again.status_code == 409
            assert await server.db.users.count_documents({"email": "preview-employee-twoway-retry2@example.com"}) == 0
            assert (await server.db[EMPLOYEES].find_one({"id": emp["id"]}))["user_id"] == uid
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"employee_number": "TW-RETRY-001"})
            await server.db.users.delete_many({"email": {"$regex": "preview-employee-twoway-retry"}})
