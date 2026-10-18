"""Focused tests for CRM Daily Reports — RBAC, validation, same-day edit, no side effects."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_crm_daily")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx

from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions
from server import app, current_user
from services.crm_daily_reports import COLLECTION, ensure_indexes, today_wib_date
from services.sales_customers import COLLECTION as CUSTOMERS, ensure_sales_customer_indexes
from services.tanggal_pengiriman import WIB


def _user(role, *, uid=None, name=None, email=None, override=None):
    slug = str(role)
    ident = uid or f"{slug}-{uuid.uuid4().hex[:6]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": email or f"{ident}@eternal.test",
        "name": name or slug.upper(),
        "role": slug,
        "permissions_override": override or {},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


async def _upsert_users(db, users):
    for user in users:
        await db.users.delete_many({"email": user["email"]})
        await db.users.insert_one({
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "status": "active",
            "permissions_override": user.get("permissions_override") or {},
        })


async def _prepare(db, users):
    await ensure_indexes(db)
    await ensure_sales_customer_indexes(db)
    await db[COLLECTION].delete_many({})
    await db.audit_logs.delete_many({"action": {"$regex": r"^crm\.daily_report\."}})
    await _upsert_users(db, users)


async def _seed_customer(db, *, name="Kopi Repeat", phone="081234567890"):
    suffix = uuid.uuid4().hex[:6]
    customer = {
        "id": f"cust-{suffix}",
        "name": f"{name} {suffix}",
        "normalized_name": f"{name.lower()} {suffix}",
        "phone": phone,
        "pic": "PIC",
        "city": "Sleman",
        "address": "Jl. 1",
        "email": "",
        "notes": "",
        "is_active": True,
    }
    await db[CUSTOMERS].delete_many({"id": customer["id"]})
    await db[CUSTOMERS].insert_one(customer)
    return customer


def _override(user):
    async def _dep():
        return user
    return _dep


async def _client(user):
    app.dependency_overrides[current_user] = _override(user)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _new_payload(**overrides):
    base = {
        "report_date": today_wib_date(),
        "status_customer": "New",
        "outlet_name": "",
        "whatsapp": "081298765432",
        "sales_label": "AYUB",
        "status_order": "ORDER",
        "has_complaint": False,
        "has_issue": False,
        "notes": "",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_crm_role_defaults_include_create_view_edit_own():
    perms = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert "crm.daily_report.create" in perms
    assert "crm.daily_report.view_own" in perms
    assert "crm.daily_report.edit_own" in perms
    assert "crm.daily_report.view_all" not in perms


@pytest.mark.anyio
async def test_holding_default_view_all_no_edit():
    perms = set(ROLE_DEFAULT_PERMISSIONS["holding_team"])
    assert "crm.daily_report.view_all" in perms
    assert "crm.daily_report.edit_own" not in perms
    assert "crm.daily_report.create" not in perms


@pytest.mark.anyio
async def test_new_wa_only_ok_and_without_wa_rejected():
    import server
    crm = _user("crm", name="Tina")
    await _prepare(server.db, [crm])
    customers_before = await server.db[CUSTOMERS].count_documents({})
    orders_before = await server.db.orders.count_documents({})

    async with await _client(crm) as client:
        ok = await client.post("/api/crm-daily-reports", json=_new_payload(outlet_name="", whatsapp="081211122233"))
        assert ok.status_code == 200, ok.text
        body = ok.json()["report"]
        assert body["status_customer"] == "New"
        assert body["whatsapp"]
        assert body["outlet_name"] == ""
        assert body["created_by_id"] == crm["id"]

        bad = await client.post("/api/crm-daily-reports", json=_new_payload(whatsapp=""))
        assert bad.status_code == 400

    assert await server.db[CUSTOMERS].count_documents({}) == customers_before
    assert await server.db.orders.count_documents({}) == orders_before


@pytest.mark.anyio
async def test_repeat_requires_valid_customer():
    import server
    crm = _user("crm")
    await _prepare(server.db, [crm])
    cust = await _seed_customer(server.db)

    async with await _client(crm) as client:
        ok = await client.post("/api/crm-daily-reports", json={
            "report_date": today_wib_date(),
            "status_customer": "Repeat",
            "customer_id": cust["id"],
            "sales_label": "EUIS",
            "status_order": "ORDER",
            "has_complaint": False,
            "has_issue": False,
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["report"]["customer_id"] == cust["id"]
        assert ok.json()["report"]["outlet_name"] == cust["name"]
        assert ok.json()["report"]["whatsapp"] == cust["phone"]

        bad = await client.post("/api/crm-daily-reports", json={
            "report_date": today_wib_date(),
            "status_customer": "Repeat",
            "customer_id": "missing",
            "sales_label": "EUIS",
            "status_order": "ORDER",
            "has_complaint": False,
            "has_issue": False,
        })
        assert bad.status_code == 400


@pytest.mark.anyio
async def test_tidak_order_and_complaint_issue_validation():
    import server
    crm = _user("crm")
    await _prepare(server.db, [crm])

    async with await _client(crm) as client:
        no_reason = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="TIDAK ORDER", no_order_reason="",
        ))
        assert no_reason.status_code == 400

        complaint = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_complaint=True, complaint_description="",
        ))
        assert complaint.status_code == 400

        issue = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_issue=True, issue_description="",
        ))
        assert issue.status_code == 400

        ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="TIDAK ORDER",
            no_order_reason="Budget belum cocok",
            has_complaint=True,
            complaint_description="Cetakan kurang rapi",
            has_issue=True,
            issue_description="Harga kompetitor lebih murah",
        ))
        assert ok.status_code == 200, ok.text
        report = ok.json()["report"]
        assert report["no_order_reason"]
        assert report["has_complaint"] is True
        assert report["has_issue"] is True

        # ORDER clears no_order_reason
        cleared = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="ORDER",
            no_order_reason="should clear",
            whatsapp="081233344455",
        ))
        assert cleared.status_code == 200
        assert cleared.json()["report"]["no_order_reason"] == ""


@pytest.mark.anyio
async def test_edit_own_same_day_and_reject_other_day_and_other_crm():
    import server
    crm_a = _user("crm", name="Tina", uid="crm-a")
    crm_b = _user("crm", name="Riris", uid="crm-b")
    await _prepare(server.db, [crm_a, crm_b])

    async with await _client(crm_a) as client:
        created = await client.post("/api/crm-daily-reports", json=_new_payload(whatsapp="081200011122"))
        assert created.status_code == 200
        rid = created.json()["report"]["id"]

        upd = await client.patch(f"/api/crm-daily-reports/{rid}", json={
            **_new_payload(whatsapp="081200011122", notes="updated same day"),
        })
        assert upd.status_code == 200, upd.text
        assert upd.json()["report"]["notes"] == "updated same day"
        audits = await server.db.audit_logs.count_documents({"action": "crm.daily_report.update", "after.id": rid})
        assert audits >= 1

    async with await _client(crm_b) as client:
        steal = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(whatsapp="081200011122"))
        assert steal.status_code == 403
        peek = await client.get(f"/api/crm-daily-reports/{rid}")
        assert peek.status_code == 403

    # Simulate yesterday report then reject edit
    yesterday = (datetime.now(timezone.utc).astimezone(WIB).date() - timedelta(days=1)).isoformat()
    await server.db[COLLECTION].update_one({"id": rid}, {"$set": {"report_date": yesterday}})
    async with await _client(crm_a) as client:
        late = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(whatsapp="081200011122"))
        assert late.status_code == 403


@pytest.mark.anyio
async def test_holding_view_all_cannot_edit_other_roles_denied():
    import server
    crm = _user("crm", uid="crm-view")
    holding = _user("holding_team", uid="hold-1")
    helper = _user("helper", uid="help-1")
    await _prepare(server.db, [crm, holding, helper])

    async with await _client(crm) as client:
        created = await client.post("/api/crm-daily-reports", json=_new_payload(whatsapp="081255566677"))
        rid = created.json()["report"]["id"]

    async with await _client(holding) as client:
        listing = await client.get("/api/crm-daily-reports")
        assert listing.status_code == 200
        assert any(r["id"] == rid for r in listing.json()["items"])
        detail = await client.get(f"/api/crm-daily-reports/{rid}")
        assert detail.status_code == 200
        edit = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(whatsapp="081255566677"))
        assert edit.status_code == 403

    async with await _client(helper) as client:
        denied = await client.get("/api/crm-daily-reports")
        assert denied.status_code == 403
        create = await client.post("/api/crm-daily-reports", json=_new_payload())
        assert create.status_code == 403


@pytest.mark.anyio
async def test_list_scope_filter_pagination_and_forge_creator():
    import server
    crm = _user("crm", uid="crm-list")
    other = _user("crm", uid="crm-other")
    await _prepare(server.db, [crm, other])

    async with await _client(other) as client:
        await client.post("/api/crm-daily-reports", json=_new_payload(whatsapp="081211100001", sales_label="OTHER"))

    async with await _client(crm) as client:
        for i in range(3):
            await client.post("/api/crm-daily-reports", json=_new_payload(
                whatsapp=f"08121110001{i}",
                status_order="ORDER" if i % 2 == 0 else "TIDAK ORDER",
                no_order_reason="" if i % 2 == 0 else "alasan",
                has_issue=(i == 1),
                issue_description="kendala stok" if i == 1 else "",
            ))
        listing = await client.get("/api/crm-daily-reports", params={"page": 1, "page_size": 2})
        assert listing.status_code == 200
        body = listing.json()
        assert body["page_size"] == 2
        assert all(r["created_by_id"] == crm["id"] for r in body["items"])
        assert body["summary"]["total"] == 3

        forged = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081299988877",
            created_by_id=other["id"],
        ))
        assert forged.status_code == 403


@pytest.mark.anyio
async def test_direktur_effective_has_view_all():
    # direktur_utama uses full access set at runtime
    from permissions import director_full_permission_set
    assert "crm.daily_report.view_all" in director_full_permission_set()
