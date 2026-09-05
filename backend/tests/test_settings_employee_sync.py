"""Settings Karyawan → Daftar Karyawan directory union (read-only, no hr auto-create)."""
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
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.hr_payroll import EMPLOYEES, ensure_indexes, list_employees

MARKER = "PREVIEW-EMPLOYEE-SYNC"
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
    uid = kw.get("id") or f"sync-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or "Sync Admin",
        "role": "admin",
        "employee_id": kw.get("employee_id") or f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {},
        "is_super_admin": True,
    }


def _viewer(**kw):
    uid = kw.get("id") or f"sync-view-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "Viewer",
        "role": "helper",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {},
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean_fixture():
    await server.db[EMPLOYEES].delete_many({
        "$or": [
            {"name": {"$regex": f"^{MARKER}"}},
            {"email": {"$regex": "preview-employee-sync"}},
        ]
    })
    await server.db.users.delete_many({"email": {"$regex": "preview-employee-sync"}})


@pytest.mark.anyio
async def test_settings_create_shows_in_directory_without_hr_employee():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-a", email="preview-employee-sync-admin-a@example.com"))
    # Snapshot only Settings-fixture-linked HR rows (avoid flaky global counts under xdist).
    fixture_hr_before = await server.db[EMPLOYEES].count_documents({
        "$or": [
            {"name": {"$regex": f"^{MARKER}"}},
            {"email": {"$regex": "preview-employee-sync"}},
        ]
    })

    async with await client_for(admin) as c:
        try:
            created = await c.post("/api/admin/users", json={
                "name": f"{MARKER}-A",
                "email": "preview-employee-sync-a@example.com",
                "role": "operator",
                "password": "Eternal123!",
            })
            assert created.status_code == 200, created.text
            user = created.json()["user"]
            uid = user["id"]

            assert await server.db[EMPLOYEES].count_documents({
                "$or": [
                    {"name": {"$regex": f"^{MARKER}"}},
                    {"email": {"$regex": "preview-employee-sync"}},
                ]
            }) == fixture_hr_before
            assert await server.db[EMPLOYEES].count_documents({"user_id": uid}) == 0
            # Payroll eligibility / side effects must not attach to Settings-only users.
            assert await server.db.hr_payroll_lines.count_documents({
                "$or": [
                    {"employee.user_id": uid},
                    {"employee_snapshot.user_id": uid},
                    {"user_id": uid},
                ]
            }) == 0
            assert await server.db.hr_attendance_entries.count_documents({
                "$or": [{"user_id": uid}, {"employee.user_id": uid}]
            }) == 0
            assert await server.db.hr_kasbon_schedules.count_documents({
                "$or": [{"user_id": uid}, {"employee.user_id": uid}]
            }) == 0

            listed = await c.get("/api/hr-payroll/employees")
            assert listed.status_code == 200
            matches = [r for r in listed.json()["employees"] if r.get("user_id") == uid]
            assert len(matches) == 1
            row = matches[0]
            assert row["name"] == f"{MARKER}-A"
            assert row["role"] == "operator"
            assert row["role_label"] in ("Operator", "operator")
            assert row["has_hr_profile"] is False
            assert row["id"] is None
            assert row["directory_key"] == f"user:{uid}"
            assert row["directory_source"] == "settings_user"
            assert row.get("salary_profile") in (None, {})
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_update_name_and_role_reflect_without_hr_write_or_duplicate():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-b", email="preview-employee-sync-admin-b@example.com"))
    async with await client_for(admin) as c:
        try:
            user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-B",
                "email": "preview-employee-sync-b@example.com",
                "role": "operator",
                "password": "Eternal123!",
            })).json()["user"]
            hr_before = await server.db[EMPLOYEES].count_documents({})

            await c.put(f"/api/admin/users/{user['id']}", json={
                "name": f"{MARKER}-B-Renamed",
                "role": "helper",
            })
            assert await server.db[EMPLOYEES].count_documents({}) == hr_before

            rows = [r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r.get("user_id") == user["id"]]
            assert len(rows) == 1
            assert rows[0]["name"] == f"{MARKER}-B-Renamed"
            assert rows[0]["role"] == "helper"
            assert rows[0]["has_hr_profile"] is False
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_same_name_users_remain_distinct_by_user_id():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-c", email="preview-employee-sync-admin-c@example.com"))
    async with await client_for(admin) as c:
        try:
            shared = f"{MARKER}-SameName"
            u1 = (await c.post("/api/admin/users", json={
                "name": shared, "email": "preview-employee-sync-same-1@example.com",
                "role": "operator", "password": "Eternal123!",
            })).json()["user"]
            u2 = (await c.post("/api/admin/users", json={
                "name": shared, "email": "preview-employee-sync-same-2@example.com",
                "role": "helper", "password": "Eternal123!",
            })).json()["user"]
            rows = (await c.get("/api/hr-payroll/employees")).json()["employees"]
            m1 = [r for r in rows if r.get("user_id") == u1["id"]]
            m2 = [r for r in rows if r.get("user_id") == u2["id"]]
            assert len(m1) == 1 and len(m2) == 1
            assert m1[0]["directory_key"] != m2[0]["directory_key"]
            assert m1[0]["role"] == "operator" and m2[0]["role"] == "helper"
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_inactive_user_directory_status():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-d", email="preview-employee-sync-admin-d@example.com"))
    async with await client_for(admin) as c:
        try:
            user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-Inactive",
                "email": "preview-employee-sync-inactive@example.com",
                "role": "crm",
                "password": "Eternal123!",
            })).json()["user"]
            assert (await c.post(f"/api/admin/users/{user['id']}/deactivate")).status_code == 200
            assert await server.db[EMPLOYEES].count_documents({"user_id": user["id"]}) == 0

            all_rows = (await c.get("/api/hr-payroll/employees")).json()["employees"]
            matches = [r for r in all_rows if r.get("user_id") == user["id"]]
            assert len(matches) == 1
            assert matches[0]["active"] is False
            assert matches[0]["account_status"] == "inactive"

            active_only = (await c.get("/api/hr-payroll/employees", params={"include_inactive": "false"})).json()["employees"]
            assert not any(r.get("user_id") == user["id"] for r in active_only)
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_linked_hr_profile_enriches_single_row_not_duplicate():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-link", email="preview-employee-sync-admin-link@example.com"))
    async with await client_for(admin) as c:
        try:
            user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-Linked",
                "email": "preview-employee-sync-linked@example.com",
                "role": "operator",
                "password": "Eternal123!",
            })).json()["user"]
            created = await c.post("/api/hr-payroll/employees", json={
                "name": "SHOULD_BE_OVERLAYED",
                "employee_number": f"NIK-{user['id'][:8]}",
                "user_id": user["id"],
                "role": "stale",
                "division": "produksi",
                "salary_profile": {"base_salary": 1000, "allowances": []},
            })
            assert created.status_code == 200, created.text
            emp_id = created.json()["employee"]["id"]

            rows = [r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r.get("user_id") == user["id"]]
            assert len(rows) == 1
            assert rows[0]["id"] == emp_id
            assert rows[0]["has_hr_profile"] is True
            assert rows[0]["name"] == f"{MARKER}-Linked"
            assert rows[0]["role"] == "operator"
            assert rows[0]["division"] == "produksi"
            assert rows[0]["salary_profile"]["base_salary"] == 1000
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_employee_without_user_relation_remains_readable():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-e", email="preview-employee-sync-admin-e@example.com"))
    legacy_id = str(uuid.uuid4())
    await server.db[EMPLOYEES].insert_one({
        "id": legacy_id,
        "employee_number": f"LEGACY-{MARKER[:6]}",
        "name": f"{MARKER}-Legacy",
        "user_id": None,
        "role": "Warehouse Lead",
        "division": "gudang",
        "active": True,
        "salary_profile": {"base_salary": 0, "allowances": [], "effective_from": ""},
        "employment_history": [],
        "audit_events": [],
    })
    async with await client_for(admin) as c:
        try:
            legacy = next(r for r in (await c.get("/api/hr-payroll/employees")).json()["employees"] if r["id"] == legacy_id)
            assert legacy["name"] == f"{MARKER}-Legacy"
            assert legacy["role"] == "Warehouse Lead"
            assert legacy["has_hr_profile"] is True
            assert legacy["directory_source"] == "hr_legacy"
            assert not legacy.get("user_id")
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"id": legacy_id})


@pytest.mark.anyio
async def test_get_directory_is_zero_write():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-f", email="preview-employee-sync-admin-f@example.com"))
    async with await client_for(admin) as c:
        try:
            user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-ZW",
                "email": "preview-employee-sync-zw@example.com",
                "role": "designer",
                "password": "Eternal123!",
            })).json()["user"]
            before_hr = await server.db[EMPLOYEES].find({}, {"_id": 0}).to_list(5000)
            before_user = await server.db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
            before_rp = await server.db.role_permissions.find({}, {"_id": 0}).to_list(500)

            assert (await c.get("/api/hr-payroll/employees")).status_code == 200
            await list_employees(server.db)

            after_hr = await server.db[EMPLOYEES].find({}, {"_id": 0}).to_list(5000)
            after_user = await server.db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
            after_rp = await server.db.role_permissions.find({}, {"_id": 0}).to_list(500)
            assert before_hr == after_hr
            assert before_user == after_user
            assert before_rp == after_rp
            assert await server.db[EMPLOYEES].count_documents({"user_id": user["id"]}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unauthorized_employee_list_denied_and_nav_defaults_untouched():
    await _clean_fixture()
    viewer = await _upsert_user(_viewer())
    async with await client_for(viewer) as c:
        try:
            assert (await c.get("/api/hr-payroll/employees")).status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    for role in ("helper", "operator", "screener", "designer", "driver", "crm"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "hr.employee.view" not in defaults
        assert "nav.hr_payroll.employees" not in defaults
    assert "hr.employee.view" in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_employee_number_index_unchanged_from_main_contract():
    """Task must not rewrite employee_number unique index semantics in code."""
    import inspect
    from services import hr_payroll as hr_mod

    src = inspect.getsource(hr_mod.ensure_indexes)
    assert 'create_index("employee_number", unique=True)' in src
    assert "employee_number_nonempty_unique" not in src
    assert "drop_index" not in src
    assert "_safe_unique_index" not in src

    await ensure_indexes(server.db)
    info = await server.db[EMPLOYEES].index_information()
    assert "employee_number_1" in info
    assert info["employee_number_1"].get("unique") is True


@pytest.mark.anyio
async def test_union_pagination_search_filter_on_final_directory():
    """Pagination/search/filter must run on FINAL union (not HR-first then append users)."""
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-page", email="preview-employee-sync-admin-page@example.com"))
    created_users = []
    created_hr = []
    async with await client_for(admin) as c:
        try:
            for i in range(6):
                u = (await c.post("/api/admin/users", json={
                    "name": f"{MARKER}-PageVirt-{i:02d}",
                    "email": f"preview-employee-sync-page-v{i}@example.com",
                    "role": "operator" if i % 2 == 0 else "helper",
                    "password": "Eternal123!",
                })).json()["user"]
                created_users.append(u["id"])

            linked_user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-PageLinked",
                "email": "preview-employee-sync-page-linked@example.com",
                "role": "crm",
                "password": "Eternal123!",
            })).json()["user"]
            created_users.append(linked_user["id"])
            linked_emp = (await c.post("/api/hr-payroll/employees", json={
                "name": "overlay",
                "employee_number": f"NIK-PAGE-{linked_user['id'][:8]}",
                "user_id": linked_user["id"],
                "division": "gudang",
                "salary_profile": {"base_salary": 1, "allowances": []},
            })).json()["employee"]
            created_hr.append(linked_emp["id"])

            legacy_id = str(uuid.uuid4())
            created_hr.append(legacy_id)
            await server.db[EMPLOYEES].insert_one({
                "id": legacy_id,
                "employee_number": f"LEGACY-PAGE-{MARKER[:4]}",
                "name": f"{MARKER}-PageLegacy",
                "user_id": None,
                "role": "Warehouse",
                "division": "gudang",
                "active": True,
                "salary_profile": {"base_salary": 0, "allowances": [], "effective_from": ""},
                "employment_history": [],
                "audit_events": [],
            })

            before_hr = await server.db[EMPLOYEES].find({}, {"_id": 0}).to_list(5000)

            full = await c.get("/api/hr-payroll/employees", params={"q": MARKER + "-Page"})
            assert full.status_code == 200
            full_body = full.json()
            assert full_body["count"] == len(full_body["employees"])
            assert full_body["count"] >= 8

            linked_rows = [r for r in full_body["employees"] if r.get("user_id") == linked_user["id"]]
            assert len(linked_rows) == 1 and linked_rows[0]["id"] == linked_emp["id"]
            assert linked_rows[0]["has_hr_profile"] is True

            virt = [r for r in full_body["employees"] if r.get("has_hr_profile") is False]
            assert len(virt) >= 6
            legacy_rows = [r for r in full_body["employees"] if r.get("id") == legacy_id]
            assert len(legacy_rows) == 1 and legacy_rows[0]["directory_source"] == "hr_legacy"

            p1 = (await c.get("/api/hr-payroll/employees", params={"q": MARKER + "-Page", "page": 1, "page_size": 3})).json()
            p2 = (await c.get("/api/hr-payroll/employees", params={"q": MARKER + "-Page", "page": 2, "page_size": 3})).json()
            assert p1["count"] == full_body["count"]
            assert p2["count"] == full_body["count"]
            assert p1["page"] == 1 and p2["page"] == 2
            assert len(p1["employees"]) == 3
            keys1 = {r["directory_key"] for r in p1["employees"]}
            keys2 = {r["directory_key"] for r in p2["employees"]}
            assert keys1.isdisjoint(keys2)
            all_page_keys = set(keys1) | set(keys2)
            page_count = p1["page_count"]
            for extra in range(3, page_count + 1):
                body = (await c.get("/api/hr-payroll/employees", params={"q": MARKER + "-Page", "page": extra, "page_size": 3})).json()
                all_page_keys |= {r["directory_key"] for r in body["employees"]}
            assert any(k.startswith("user:") for k in all_page_keys)
            assert any(r.get("id") == legacy_id for r in full_body["employees"])

            by_role = (await c.get("/api/hr-payroll/employees", params={"q": "helper"})).json()["employees"]
            assert any(r.get("user_id") in created_users and r.get("role") == "helper" for r in by_role)

            inactive_user = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-PageInactive",
                "email": "preview-employee-sync-page-inactive@example.com",
                "role": "designer",
                "password": "Eternal123!",
            })).json()["user"]
            created_users.append(inactive_user["id"])
            await c.post(f"/api/admin/users/{inactive_user['id']}/deactivate")
            active_only = (await c.get("/api/hr-payroll/employees", params={
                "q": MARKER + "-PageInactive", "include_inactive": "false",
            })).json()["employees"]
            assert not any(r.get("user_id") == inactive_user["id"] for r in active_only)
            with_inactive = (await c.get("/api/hr-payroll/employees", params={
                "q": MARKER + "-PageInactive", "include_inactive": "true",
            })).json()["employees"]
            assert any(r.get("user_id") == inactive_user["id"] and r.get("active") is False for r in with_inactive)

            same = [r for r in full_body["employees"] if (r.get("name") or "").startswith(f"{MARKER}-PageVirt")]
            keys = [r["directory_key"] for r in same]
            assert len(keys) == len(set(keys))

            after_hr = await server.db[EMPLOYEES].find({}, {"_id": 0}).to_list(5000)
            assert before_hr == after_hr
        finally:
            app.dependency_overrides.pop(current_user, None)
            await server.db[EMPLOYEES].delete_many({"id": {"$in": created_hr}})
            await server.db.users.delete_many({"id": {"$in": created_users}})


@pytest.mark.anyio
async def test_virtual_user_excluded_from_hr_only_ops_and_payroll_calc():
    await _clean_fixture()
    admin = await _upsert_user(_admin(id="sync-admin-pay", email="preview-employee-sync-admin-pay@example.com"))
    period = None
    async with await client_for(admin) as c:
        try:
            virtual = (await c.post("/api/admin/users", json={
                "name": f"{MARKER}-NoPayroll",
                "email": "preview-employee-sync-nopay@example.com",
                "role": "operator",
                "password": "Eternal123!",
            })).json()["user"]
            uid = virtual["id"]

            dir_rows = (await c.get("/api/hr-payroll/employees")).json()["employees"]
            assert any(r.get("user_id") == uid and r.get("has_hr_profile") is False for r in dir_rows)

            hr_only = (await c.get("/api/hr-payroll/employees", params={"hr_profile_only": "true"})).json()["employees"]
            assert not any(r.get("user_id") == uid for r in hr_only)
            assert all(r.get("id") and r.get("has_hr_profile") is True for r in hr_only)

            assert (await c.get("/api/hr-payroll/employees/null")).status_code == 404
            assert (await c.get(f"/api/hr-payroll/employees/user:{uid}")).status_code == 404
            assert (await c.put("/api/hr-payroll/employees/null", json={"division": "x"})).status_code == 404

            hr_emp = (await c.post("/api/hr-payroll/employees", json={
                "name": f"{MARKER}-PayHR",
                "employee_number": f"NIK-PAY-{uid[:8]}",
                "division": "produksi",
                "salary_profile": {"base_salary": 1000000, "allowances": []},
            })).json()["employee"]
            period = (await c.post("/api/hr-payroll/periods", json={
                "year": 2099, "month": 1, "start_date": "2099-01-01", "end_date": "2099-01-31",
            })).json()["period"]
            calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
            assert calc.status_code == 200, calc.text
            lines = (await c.get(f"/api/hr-payroll/periods/{period['id']}/lines")).json()["lines"]
            line_emp_ids = {str(l.get("employee_id")) for l in lines}
            assert hr_emp["id"] in line_emp_ids
            assert uid not in line_emp_ids
            assert "null" not in line_emp_ids
            assert not any((l.get("employee_snapshot") or {}).get("user_id") == uid for l in lines)

            att = await c.put(
                f"/api/hr-payroll/periods/{period['id']}/attendance/null",
                json={"work_days": 1},
            )
            assert att.status_code == 404
            assert await server.db[EMPLOYEES].count_documents({"user_id": uid}) == 0
        finally:
            app.dependency_overrides.pop(current_user, None)
            if period:
                await server.db.hr_payroll_lines.delete_many({"period_id": period["id"]})
                await server.db.hr_payroll_periods.delete_many({"id": period["id"]})
            await server.db[EMPLOYEES].delete_many({"name": {"$regex": f"^{MARKER}-Pay"}})
            await server.db.users.delete_many({"email": "preview-employee-sync-nopay@example.com"})
