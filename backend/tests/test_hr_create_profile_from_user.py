"""Targeted backend tests for creating HR profile from existing user (Buat Profil HR)."""
import asyncio
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

MARKER = "TEST-CREATE-HR-PROFILE"
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
    uid = kw.get("id") or f"admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "Admin User",
        "role": "admin",
        "employee_id": f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": {},
        "is_super_admin": True,
    }


def _view_only_user(**kw):
    uid = kw.get("id") or f"viewer-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": "Viewer",
        "role": "operator",
        "status": "active",
        "user_id": uid,
        "permissions_override": {
            PERM_EMP_VIEW: True,
            PERM_EMP_MANAGE: False,
        },
        "is_super_admin": False,
    }


async def test_virtual_user_create_persistent_hr_profile():
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    user_id = f"usr-{uuid.uuid4().hex[:8]}"
    emp_num = f"EMP-{uuid.uuid4().hex[:8]}"

    await server.db.users.insert_one({
        "id": user_id,
        "name": "Budi Santoso",
        "email": f"budi-{user_id}@example.com",
        "role": "operator",
        "status": "active",
        "phone": "08123456789",
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r_list_before = await client.get("/api/hr-payroll/employees")
        assert r_list_before.status_code == 200
        rows_before = [x for x in r_list_before.json()["employees"] if x.get("user_id") == user_id]
        assert len(rows_before) == 1
        assert rows_before[0]["has_hr_profile"] is False
        assert rows_before[0]["id"] is None

        create_payload = {
            "user_id": user_id,
            "employee_number": emp_num,
            "division": "Finishing",
            "employment_status": "tetap",
            "join_date": "2026-01-15",
            "salary_profile": {"base_salary": 4500000},
        }
        res = await client.post("/api/hr-payroll/employees", json=create_payload)
        assert res.status_code == 200, res.text
        created = res.json()["employee"]
        assert created["id"] is not None
        assert created["id"] != "null"
        assert created["user_id"] == user_id
        assert created["has_hr_profile"] is True
        assert created["employee_number"] == emp_num
        assert created["name"] == "Budi Santoso"
        assert created["email"] == f"budi-{user_id}@example.com"

        r_list_after = await client.get("/api/hr-payroll/employees")
        assert r_list_after.status_code == 200
        rows_after = [x for x in r_list_after.json()["employees"] if x.get("user_id") == user_id]
        assert len(rows_after) == 1
        assert rows_after[0]["has_hr_profile"] is True
        assert rows_after[0]["id"] == created["id"]

    app.dependency_overrides.clear()


async def test_duplicate_create_for_same_user_rejected_409():
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    user_id = f"usr-{uuid.uuid4().hex[:8]}"

    await server.db.users.insert_one({
        "id": user_id,
        "name": "Siti Aminah",
        "email": f"siti-{user_id}@example.com",
        "role": "helper",
        "status": "active",
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        p1 = {
            "user_id": user_id,
            "employee_number": f"EMP-{uuid.uuid4().hex[:8]}",
            "division": "Produksi",
        }
        r1 = await client.post("/api/hr-payroll/employees", json=p1)
        assert r1.status_code == 200

        p2 = {
            "user_id": user_id,
            "employee_number": f"EMP-{uuid.uuid4().hex[:8]}",
            "division": "Gudang",
        }
        r2 = await client.post("/api/hr-payroll/employees", json=p2)
        assert r2.status_code == 409
        assert "Profil HR untuk user ini sudah ada" in r2.json()["detail"]

        cnt = await server.db[EMPLOYEES].count_documents({"user_id": user_id})
        assert cnt == 1

    app.dependency_overrides.clear()


async def test_same_name_different_user_does_not_conflict_or_cross_link():
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    u1 = f"usr-{uuid.uuid4().hex[:8]}"
    u2 = f"usr-{uuid.uuid4().hex[:8]}"

    await server.db.users.insert_many([
        {"id": u1, "name": "Ahmad", "email": f"ahmad1-{u1}@example.com", "role": "operator", "status": "active"},
        {"id": u2, "name": "Ahmad", "email": f"ahmad2-{u2}@example.com", "role": "helper", "status": "active"},
    ])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/hr-payroll/employees", json={
            "user_id": u1,
            "employee_number": f"EMP-{uuid.uuid4().hex[:8]}",
        })
        assert r1.status_code == 200
        assert r1.json()["employee"]["user_id"] == u1

        r2 = await client.post("/api/hr-payroll/employees", json={
            "user_id": u2,
            "employee_number": f"EMP-{uuid.uuid4().hex[:8]}",
        })
        assert r2.status_code == 200
        assert r2.json()["employee"]["user_id"] == u2

        emp1 = await server.db[EMPLOYEES].find_one({"user_id": u1})
        emp2 = await server.db[EMPLOYEES].find_one({"user_id": u2})
        assert emp1["id"] != emp2["id"]

    app.dependency_overrides.clear()


async def test_historical_employee_without_user_id_remains_readable():
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    hist_id = f"hist-{uuid.uuid4().hex[:8]}"
    hist_emp_num = f"HIST-{uuid.uuid4().hex[:8]}"

    await server.db[EMPLOYEES].insert_one({
        "id": hist_id,
        "employee_number": hist_emp_num,
        "name": "Historical Staff",
        "user_id": None,
        "role": "operator",
        "division": "Cetak",
        "active": True,
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/hr-payroll/employees/{hist_id}")
        assert r.status_code == 200
        data = r.json()["employee"]
        assert data["id"] == hist_id
        assert data["has_hr_profile"] is True
        assert data["user_id"] is None

    app.dependency_overrides.clear()


async def test_get_employees_is_pure_read_no_side_effects():
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor

    count_before = await server.db[EMPLOYEES].count_documents({})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/hr-payroll/employees")
        assert res.status_code == 200

    count_after = await server.db[EMPLOYEES].count_documents({})
    assert count_before == count_after

    app.dependency_overrides.clear()


async def test_unauthorized_user_cannot_create_hr_profile():
    actor = _view_only_user()
    app.dependency_overrides[current_user] = lambda: actor
    user_id = f"usr-{uuid.uuid4().hex[:8]}"

    await server.db.users.insert_one({
        "id": user_id,
        "name": "Target User",
        "email": f"target-{user_id}@example.com",
        "role": "operator",
        "status": "active",
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/hr-payroll/employees", json={
            "user_id": user_id,
            "employee_number": f"EMP-{uuid.uuid4().hex[:8]}",
        })
        assert res.status_code == 403

    app.dependency_overrides.clear()


async def test_unknown_user_id_rejected_404_no_record_created():
    """Requirement 1: Non-existent user_id must be rejected with 404 and create ZERO records."""
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    non_existent_uid = f"nonexistent-{uuid.uuid4().hex[:12]}"
    emp_num = f"EMP-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/hr-payroll/employees", json={
            "user_id": non_existent_uid,
            "employee_number": emp_num,
            "name": "Ghost User",
            "division": "IT",
        })
        assert res.status_code == 404
        assert "User yang ditautkan tidak ditemukan" in res.json()["detail"]

        # Confirm ZERO records created
        assert await server.db[EMPLOYEES].count_documents({"user_id": non_existent_uid}) == 0
        assert await server.db[EMPLOYEES].count_documents({"employee_number": emp_num}) == 0

    app.dependency_overrides.clear()


async def test_authoritative_user_identity_prevents_client_spoofing():
    """Requirement 2: Identity fields come from canonical user; client cannot spoof name/email/role."""
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    user_id = f"usr-{uuid.uuid4().hex[:8]}"
    emp_num = f"EMP-{uuid.uuid4().hex[:8]}"

    # Canonical User A in database
    await server.db.users.insert_one({
        "id": user_id,
        "name": "Canonical User A",
        "email": "canonical-a@example.com",
        "role": "helper",
        "status": "active",
        "phone": "08111222333",
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Attacker tries to create profile for User A but spoof name/email as User B / Super Admin
        spoofed_payload = {
            "user_id": user_id,
            "employee_number": emp_num,
            "name": "Spoofed User B",
            "email": "attacker@evil.com",
            "role": "super_admin",
            "division": "Logistik",
            "employment_status": "kontrak",
        }
        res = await client.post("/api/hr-payroll/employees", json=spoofed_payload)
        assert res.status_code == 200
        created = res.json()["employee"]

        # Response identity is authoritative to User A
        assert created["user_id"] == user_id
        assert created["name"] == "Canonical User A"
        assert created["email"] == "canonical-a@example.com"
        assert created["role"] == "helper"

        # Stored DB record is also authoritative to User A
        stored = await server.db[EMPLOYEES].find_one({"user_id": user_id})
        assert stored is not None
        assert stored["name"] == "Canonical User A"
        assert stored["email"] == "canonical-a@example.com"
        assert stored["role"] == "helper"
        # Genuine HR-only fields from payload are preserved
        assert stored["employee_number"] == emp_num
        assert stored["division"] == "Logistik"
        assert stored["employment_status"] == "kontrak"

    app.dependency_overrides.clear()


async def test_concurrent_same_user_create_requests_race_safety():
    """Requirement 3: Near-simultaneous create requests for SAME user_id result in exactly 1 profile."""
    actor = _admin()
    app.dependency_overrides[current_user] = lambda: actor
    user_id = f"usr-{uuid.uuid4().hex[:8]}"

    await server.db.users.insert_one({
        "id": user_id,
        "name": "Concurrency User",
        "email": f"concurrent-{user_id}@example.com",
        "role": "operator",
        "status": "active",
    })

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        p1 = {
            "user_id": user_id,
            "employee_number": f"EMP-C1-{uuid.uuid4().hex[:6]}",
            "division": "Div 1",
        }
        p2 = {
            "user_id": user_id,
            "employee_number": f"EMP-C2-{uuid.uuid4().hex[:6]}",
            "division": "Div 2",
        }

        # Send both requests simultaneously
        res1, res2 = await asyncio.gather(
            client.post("/api/hr-payroll/employees", json=p1),
            client.post("/api/hr-payroll/employees", json=p2),
        )

        codes = sorted([res1.status_code, res2.status_code])
        # Exactly one should succeed (200), and the other should be rejected with 409
        assert codes == [200, 409], f"Expected [200, 409], got: {codes}"
        assert res1.status_code != 500 and res2.status_code != 500

        conflict_res = res1 if res1.status_code == 409 else res2
        detail = conflict_res.json().get("detail", "")
        assert "Profil HR untuk user ini sudah ada" in detail or "sudah dipakai" in detail

        # Verify exactly ONE persistent hr_employee in DB
        total_created = await server.db[EMPLOYEES].count_documents({"user_id": user_id})
        assert total_created == 1

    app.dependency_overrides.clear()
