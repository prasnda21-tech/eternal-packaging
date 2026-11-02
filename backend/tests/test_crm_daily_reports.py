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


SALES_AYUB_ID = "sales-ayub-test"
SALES_EUIS_ID = "sales-euis-test"
SALES_AYUB_LABEL = "Sholahudin Al - Ayubi"
SALES_EUIS_LABEL = "Euis Risnawati"


async def _seed_sales_people(db):
    """Canonical sales_eternal users + sales_user_mapping for CRM sales picker tests."""
    await db.users.delete_many({"id": {"$in": [SALES_AYUB_ID, SALES_EUIS_ID]}})
    await db.users.insert_many([
        {
            "id": SALES_AYUB_ID,
            "email": "ayub@eternal.com",
            "name": "Ayub",
            "employee_id": "EMP-0049",
            "role": "sales_eternal",
            "status": "active",
            "permissions_override": {},
        },
        {
            "id": SALES_EUIS_ID,
            "email": "euis@eternal.com",
            "name": "Euis",
            "employee_id": "EMP-0052",
            "role": "sales_eternal",
            "status": "active",
            "permissions_override": {},
        },
    ])
    await db.app_settings.delete_many({"key": "sales_user_mapping"})
    await db.app_settings.insert_one({
        "key": "sales_user_mapping",
        "entries": [
            {"label": SALES_AYUB_LABEL, "user_id": SALES_AYUB_ID},
            {"label": SALES_EUIS_LABEL, "user_id": SALES_EUIS_ID},
        ],
    })
    return SALES_AYUB_ID, SALES_EUIS_ID


async def _prepare(db, users):
    await ensure_indexes(db)
    await ensure_sales_customer_indexes(db)
    await db[COLLECTION].delete_many({})
    await db.orders.delete_many({})
    await db.audit_logs.delete_many({"action": {"$regex": r"^crm\.daily_report\."}})
    await _upsert_users(db, users)
    await _seed_sales_people(db)


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
        "sales_label": SALES_AYUB_LABEL,
        "sales_user_id": SALES_AYUB_ID,
        "sales_source": "existing",
        "status_order": "ORDER",
        "has_complaint": False,
        "has_issue": False,
        "notes": "",
    }
    base.update(overrides)
    return base


async def _seed_order(db, *, po_number, customer_id="", customer="", sales="", sales_user_id=None):
    doc = {
        "id": f"ord-{uuid.uuid4().hex[:8]}",
        "po_number": po_number,
        "customer_id": customer_id or None,
        "customer": customer,
        "sales": sales,
        "sales_user_id": sales_user_id,
        "order_date": today_wib_date(),
        "cancelled": False,
        "created_at": today_wib_date(),
    }
    await db.orders.insert_one(doc)
    return doc


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
            "sales_label": SALES_EUIS_LABEL,
            "sales_user_id": SALES_EUIS_ID,
            "sales_source": "existing",
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
            "sales_label": SALES_EUIS_LABEL,
            "sales_user_id": SALES_EUIS_ID,
            "sales_source": "existing",
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

        lainnya_empty = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="TIDAK ORDER", no_order_reason="Lainnya",
        ))
        assert lainnya_empty.status_code == 400

        complaint = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_complaint=True, complaint_description="",
        ))
        assert complaint.status_code == 400

        complaint_no_cat = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_complaint=True,
            complaint_description="Cetakan kurang rapi",
            complaint_category="",
        ))
        assert complaint_no_cat.status_code == 400

        issue = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_issue=True, issue_description="",
        ))
        assert issue.status_code == 400

        ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            has_complaint=True,
            complaint_category="CUP CACAT",
            complaint_description="Cetakan kurang rapi",
            has_issue=True,
            issue_description="Harga kompetitor lebih murah",
        ))
        assert ok.status_code == 200, ok.text
        report = ok.json()["report"]
        assert report["no_order_reason"] == "Harga terlalu mahal"
        assert report["has_complaint"] is True
        assert report["complaint_category"] == "CUP CACAT"
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
async def test_excel_status_reasons_complaint_and_summary():
    import server
    from services.crm_daily_reports import (
        COMPLAINT_CATEGORY_OPTIONS,
        NO_ORDER_REASON_PRESETS,
        STATUS_ORDER_OPTIONS,
    )

    crm = _user("crm", uid="crm-excel")
    await _prepare(server.db, [crm])

    assert list(STATUS_ORDER_OPTIONS) == [
        "ORDER", "TIDAK ORDER", "PENGAJUAN PO", "PENGAJUAN DITOLAK", "FOLLOW UP",
    ]
    assert len(NO_ORDER_REASON_PRESETS) == 6
    assert "Lainnya" in COMPLAINT_CATEGORY_OPTIONS

    async with await _client(crm) as client:
        meta = await client.get("/api/crm-daily-reports/meta")
        assert meta.status_code == 200
        assert meta.json()["status_order_options"] == list(STATUS_ORDER_OPTIONS)
        assert meta.json()["no_order_reason_presets"] == list(NO_ORDER_REASON_PRESETS)

        bad_status = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="MAYBE", whatsapp="081200000001",
        ))
        assert bad_status.status_code == 400

        for reason in NO_ORDER_REASON_PRESETS:
            resp = await client.post("/api/crm-daily-reports", json=_new_payload(
                status_order="TIDAK ORDER",
                no_order_reason=reason,
                whatsapp=f"0812{abs(hash(reason)) % 10**8:08d}",
            ))
            assert resp.status_code == 200, resp.text
            assert resp.json()["report"]["no_order_reason"] == reason

        lainnya_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_order="TIDAK ORDER",
            no_order_reason="Budget khusus cabang",
            whatsapp="081200000099",
        ))
        assert lainnya_ok.status_code == 200
        assert lainnya_ok.json()["report"]["no_order_reason"] == "Budget khusus cabang"

        for status in ("PENGAJUAN PO", "PENGAJUAN DITOLAK", "FOLLOW UP"):
            resp = await client.post("/api/crm-daily-reports", json=_new_payload(
                status_order=status,
                whatsapp=f"08121{abs(hash(status)) % 10**7:07d}",
            ))
            assert resp.status_code == 200, resp.text
            assert resp.json()["report"]["status_order"] == status
            assert resp.json()["report"]["no_order_reason"] == ""

        complaint_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            has_complaint=True,
            complaint_category="SALAH DESIGN",
            complaint_description="Logo terbalik",
            whatsapp="081200000088",
        ))
        assert complaint_ok.status_code == 200
        assert complaint_ok.json()["report"]["complaint_category"] == "SALAH DESIGN"

        # Legacy row without category remains readable / editable same-day.
        legacy_id = "legacy-complaint-1"
        await server.db[COLLECTION].insert_one({
            "id": legacy_id,
            "report_date": today_wib_date(),
            "created_by_id": crm["id"],
            "created_by_name": crm["name"],
            "status_customer": "New",
            "customer_id": None,
            "outlet_name": "",
            "whatsapp": "081200000077",
            "whatsapp_norm": "6281200000077",
            "order_id": None,
            "po_number": "",
            "sales_label": "Sales Legacy Free Text",
            "sales_user_id": None,
            "sales_source": "legacy",
            "status_order": "ORDER",
            "no_order_reason": "",
            "has_complaint": True,
            "complaint_description": "Legacy tanpa kategori",
            "has_issue": False,
            "issue_description": "",
            "notes": "",
            "created_at": "2026-04-16T01:00:00+00:00",
            "updated_at": "2026-04-16T01:00:00+00:00",
        })
        detail = await client.get(f"/api/crm-daily-reports/{legacy_id}")
        assert detail.status_code == 200
        assert detail.json()["report"]["has_complaint"] is True
        assert detail.json()["report"]["complaint_category"] == ""

        legacy_edit = await client.patch(f"/api/crm-daily-reports/{legacy_id}", json={
            "whatsapp": "081200000077",
            "status_customer": "New",
            "status_order": "ORDER",
            "sales_label": "Sales Legacy Free Text",
            "sales_user_id": None,
            "sales_source": "legacy",
            "has_complaint": True,
            "complaint_description": "Legacy tanpa kategori (edit)",
            "has_issue": False,
            "notes": "still legacy",
            "report_date": today_wib_date(),
        })
        assert legacy_edit.status_code == 200, legacy_edit.text
        assert legacy_edit.json()["report"]["complaint_category"] == ""
        assert legacy_edit.json()["report"]["sales_source"] in ("legacy", "manual")
        assert legacy_edit.json()["report"]["sales_user_id"] in (None, "")

        listing = await client.get("/api/crm-daily-reports")
        assert listing.status_code == 200
        summary = listing.json()["summary"]
        assert summary["pengajuan_po_count"] == 1
        assert summary["pengajuan_ditolak_count"] == 1
        assert summary["follow_up_count"] == 1
        # Extra statuses must not inflate ORDER / TIDAK ORDER.
        assert summary["order_count"] >= 1
        assert summary["no_order_count"] == len(NO_ORDER_REASON_PRESETS) + 1

        filtered = await client.get("/api/crm-daily-reports", params={"status_order": "FOLLOW UP"})
        assert filtered.status_code == 200
        assert filtered.json()["summary"]["total"] == 1
        assert all(r["status_order"] == "FOLLOW UP" for r in filtered.json()["items"])


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
        await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081211100001",
            sales_label=SALES_EUIS_LABEL,
            sales_user_id=SALES_EUIS_ID,
        ))

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


@pytest.mark.anyio
async def test_sales_po_picker_autofill_manual_and_validation():
    import server

    crm = _user("crm", uid="crm-sales-po")
    helper = _user("helper", uid="help-sales-po")
    await _prepare(server.db, [crm, helper])
    cust = await _seed_customer(server.db, name="Outlet PO", phone="081277700001")
    other = await _seed_customer(server.db, name="Outlet Lain", phone="081277700002")

    order_with_sales = await _seed_order(
        server.db,
        po_number="PO/CRM/SALES/001",
        customer_id=cust["id"],
        customer=cust["name"],
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )
    order_no_sales = await _seed_order(
        server.db,
        po_number="PO/CRM/NOSALES/002",
        customer_id=cust["id"],
        customer=cust["name"],
        sales="",
        sales_user_id=None,
    )
    order_euis = await _seed_order(
        server.db,
        po_number="PO/CRM/EUIS/003",
        customer_id=cust["id"],
        customer=cust["name"],
        sales=SALES_EUIS_LABEL,
        sales_user_id=SALES_EUIS_ID,
    )

    async with await _client(crm) as client:
        sales_list = await client.get("/api/crm-daily-reports/sales")
        assert sales_list.status_code == 200
        labels = {row["label"] for row in sales_list.json()["sales"]}
        assert SALES_AYUB_LABEL in labels
        assert SALES_EUIS_LABEL in labels

        # 1) PO with verified Sales → Sales follows PO
        ok_po = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711101",
            order_id=order_with_sales["id"],
            po_number=order_with_sales["po_number"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_user_id=SALES_EUIS_ID,  # conflicting claim ignored/rejected
            sales_label=SALES_EUIS_LABEL,
            sales_source="existing",
        ))
        assert ok_po.status_code == 400

        ok_po = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711101",
            order_id=order_with_sales["id"],
            po_number=order_with_sales["po_number"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
            sales_source="existing",
        ))
        assert ok_po.status_code == 200, ok_po.text
        report = ok_po.json()["report"]
        assert report["order_id"] == order_with_sales["id"]
        assert report["sales_user_id"] == SALES_AYUB_ID
        assert report["sales_label"] == SALES_AYUB_LABEL
        assert report["sales_source"] == "existing"
        rid = report["id"]

        # 2) PO without Sales → must choose existing
        no_sales_missing = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711102",
            order_id=order_no_sales["id"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_user_id=None,
            sales_label="",
            sales_source="existing",
        ))
        assert no_sales_missing.status_code == 400

        no_sales_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711102",
            order_id=order_no_sales["id"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_user_id=SALES_EUIS_ID,
            sales_label=SALES_EUIS_LABEL,
            sales_source="existing",
        ))
        assert no_sales_ok.status_code == 200, no_sales_ok.text
        assert no_sales_ok.json()["report"]["sales_user_id"] == SALES_EUIS_ID

        # 3) Change PO → Sales updates to new PO sales
        switched = await client.patch(f"/api/crm-daily-reports/{rid}", json={
            **_new_payload(
                status_customer="Repeat",
                customer_id=cust["id"],
                order_id=order_euis["id"],
                po_number=order_euis["po_number"],
                sales_user_id=SALES_EUIS_ID,
                sales_label=SALES_EUIS_LABEL,
            ),
        })
        assert switched.status_code == 200, switched.text
        assert switched.json()["report"]["sales_user_id"] == SALES_EUIS_ID
        assert switched.json()["report"]["order_id"] == order_euis["id"]

        # 5) Customer mismatch with PO
        mismatch = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711103",
            status_customer="Repeat",
            customer_id=other["id"],
            order_id=order_with_sales["id"],
            sales_user_id=SALES_AYUB_ID,
        ))
        assert mismatch.status_code == 400

        # 6) No PO → existing sales accepted
        no_po = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711104",
            sales_user_id=SALES_EUIS_ID,
            sales_label=SALES_EUIS_LABEL,
        ))
        assert no_po.status_code == 200
        assert no_po.json()["report"]["sales_source"] == "existing"
        assert no_po.json()["report"]["order_id"] in (None, "")

        # 7) TIDAK ORDER without PO → manual OK
        manual = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711105",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            sales_source="manual",
            sales_user_id=None,
            sales_label="Sales Freelancer X",
            order_id=None,
            po_number="",
        ))
        assert manual.status_code == 200, manual.text
        assert manual.json()["report"]["sales_source"] == "manual"
        assert manual.json()["report"]["sales_user_id"] in (None, "")
        assert manual.json()["report"]["sales_label"] == "Sales Freelancer X"

        # 8) TIDAK ORDER with PO → manual rejected
        manual_with_po = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711106",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            order_id=order_with_sales["id"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_source="manual",
            sales_user_id=None,
            sales_label="Manual Tidak Boleh",
        ))
        assert manual_with_po.status_code == 400

        # 9/10) ORDER / FOLLOW UP with manual rejected
        for status in ("ORDER", "FOLLOW UP", "PENGAJUAN PO", "PENGAJUAN DITOLAK"):
            bad = await client.post("/api/crm-daily-reports", json=_new_payload(
                whatsapp=f"0812777111{abs(hash(status)) % 90 + 10}",
                status_order=status,
                sales_source="manual",
                sales_user_id=None,
                sales_label="Manual Nama",
                no_order_reason="",
            ))
            assert bad.status_code == 400, status

        # 11) Manual empty rejected
        empty_manual = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711107",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            sales_source="manual",
            sales_label="",
            sales_user_id=None,
        ))
        assert empty_manual.status_code == 400

        # 12) Manual cannot forge sales_user_id
        forged_uid = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711108",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            sales_source="manual",
            sales_label="Manual Nama",
            sales_user_id=SALES_AYUB_ID,
        ))
        assert forged_uid.status_code == 400

        # 13) Invalid canonical sales rejected
        bad_sales = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711109",
            sales_user_id="not-a-sales",
            sales_label="Fake",
            sales_source="existing",
        ))
        assert bad_sales.status_code == 400

        # Orders search returns PO + customer + sales flags
        orders = await client.get("/api/crm-daily-reports/orders", params={"q": "PO/CRM"})
        assert orders.status_code == 200
        body = orders.json()["orders"]
        assert any(row["po_number"] == "PO/CRM/SALES/001" and row["sales_verified"] for row in body)
        assert any(row["po_number"] == "PO/CRM/NOSALES/002" and not row["sales_verified"] for row in body)

        # 17) Create does not mutate PO / customer master
        before_po = await server.db.orders.find_one({"id": order_with_sales["id"]})
        before_cust = await server.db[CUSTOMERS].find_one({"id": cust["id"]})
        await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081277711110",
            order_id=order_with_sales["id"],
            status_customer="Repeat",
            customer_id=cust["id"],
            sales_user_id=SALES_AYUB_ID,
        ))
        after_po = await server.db.orders.find_one({"id": order_with_sales["id"]})
        after_cust = await server.db[CUSTOMERS].find_one({"id": cust["id"]})
        assert after_po["sales"] == before_po["sales"]
        assert after_po["sales_user_id"] == before_po["sales_user_id"]
        assert after_cust["name"] == before_cust["name"]

    # 14) Role without PO permission sees empty order list
    async with await _client(helper) as client:
        # helper lacks crm daily report perms → 403 on endpoint
        denied = await client.get("/api/crm-daily-reports/orders")
        assert denied.status_code == 403

    # CRM without po.view/sales_order.view → empty orders
    crm_no_po = _user("crm", uid="crm-noperm", override={
        "po.view": False,
        "sales_order.view": False,
    })
    await _upsert_users(server.db, [crm_no_po])
    async with await _client(crm_no_po) as client:
        empty = await client.get("/api/crm-daily-reports/orders", params={"q": "PO"})
        assert empty.status_code == 200
        assert empty.json()["orders"] == []


@pytest.mark.anyio
@pytest.mark.anyio
async def test_crm_customer_search_active_only_and_po_identity_guards():
    import server
    crm = _user("crm")
    await _prepare(server.db, [crm])
    active = await _seed_customer(server.db, name="KOMMU SPACE KUDUS", phone="081211100001")
    inactive = await _seed_customer(server.db, name="KOMMU SPACE UNS", phone="081211100002")
    await server.db[CUSTOMERS].update_one({"id": inactive["id"]}, {"$set": {"is_active": False}})
    cirebon = await _seed_customer(server.db, name="KOMMU SPACE CIREBON", phone="081211100003")

    order_ok = await _seed_order(
        server.db,
        po_number="PO/CRM/ID/OK",
        customer_id=cirebon["id"],
        customer=cirebon["name"],
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )
    order_legacy = await _seed_order(
        server.db,
        po_number="PO/CRM/ID/LEGACY",
        customer_id="",
        customer="KOMMU SPACE CIREBON",
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )
    order_mismatch = await _seed_order(
        server.db,
        po_number="PO/CRM/ID/MISMATCH",
        customer_id=active["id"],
        customer=active["name"],
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )

    async with await _client(crm) as client:
        customers = await client.get("/api/crm-daily-reports/customers", params={"q": "KOMMU SPACE"})
        assert customers.status_code == 200
        names = {row["name"] for row in customers.json()["customers"]}
        assert active["name"] in names
        assert cirebon["name"] in names
        assert inactive["name"] not in names

        orders = await client.get("/api/crm-daily-reports/orders", params={"q": "PO/CRM/ID"})
        assert orders.status_code == 200
        by_po = {row["po_number"]: row for row in orders.json()["orders"]}
        assert by_po["PO/CRM/ID/OK"]["customer_master_status"] == "active"
        assert by_po["PO/CRM/ID/OK"]["customer_master"]["id"] == cirebon["id"]
        assert by_po["PO/CRM/ID/LEGACY"]["customer_master_status"] == "none"
        assert by_po["PO/CRM/ID/LEGACY"]["customer_id"] in (None, "")

        # Matching canonical IDs accepted
        ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_customer="Repeat",
            customer_id=cirebon["id"],
            order_id=order_ok["id"],
            po_number=order_ok["po_number"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
        ))
        assert ok.status_code == 200, ok.text

        # Mismatch rejected
        mismatch = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_customer="Repeat",
            customer_id=cirebon["id"],
            order_id=order_mismatch["id"],
            po_number=order_mismatch["po_number"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
        ))
        assert mismatch.status_code == 400
        assert "tidak sesuai" in mismatch.text.lower()

        # Legacy without confirm rejected
        legacy_no = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_customer="Repeat",
            customer_id=cirebon["id"],
            order_id=order_legacy["id"],
            po_number=order_legacy["po_number"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
        ))
        assert legacy_no.status_code == 400
        assert "legacy" in legacy_no.text.lower()

        # Legacy with explicit confirm accepted — does not mutate PO
        before = await server.db.orders.find_one({"id": order_legacy["id"]})
        legacy_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            status_customer="Repeat",
            customer_id=cirebon["id"],
            order_id=order_legacy["id"],
            po_number=order_legacy["po_number"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
            legacy_po_customer_confirmed=True,
        ))
        assert legacy_ok.status_code == 200, legacy_ok.text
        after = await server.db.orders.find_one({"id": order_legacy["id"]})
        assert after.get("customer_id") == before.get("customer_id")
        assert after.get("customer") == before.get("customer")

@pytest.mark.anyio
async def test_master_sales_without_eternal_account_order_and_guards():
    """Active Master Sales may be selected without Eternal user_id.

    sales_master_id is canonical; verified Master is not free-text manual.
    """
    import server
    from services import sales_master as sm
    from services.org_locations import BRANCHES_COLLECTION

    crm = _user("crm", uid="crm-master-no-acct")
    await _prepare(server.db, [crm])
    await sm.ensure_indexes(server.db)
    await server.db[sm.COLLECTION].delete_many({})
    await server.db[BRANCHES_COLLECTION].delete_many({"id": {"$regex": r"^br-cdr-"}})

    branch_a = f"br-cdr-{uuid.uuid4().hex[:6]}"
    branch_b = f"br-cdr-{uuid.uuid4().hex[:6]}"
    await server.db[BRANCHES_COLLECTION].insert_many([
        {"id": branch_a, "code": "CDA", "name": "Cabang A", "active": True},
        {"id": branch_b, "code": "CDB", "name": "Cabang B", "active": True},
    ])

    master_with_user = await sm.create_sales_master(server.db, {
        "sales_name": SALES_AYUB_LABEL,
        "user_id": SALES_AYUB_ID,
        "branch_ids": [],
    }, crm)
    master_no_user = await sm.create_sales_master(server.db, {
        "sales_name": "Sales Freelancer Offline",
        "branch_ids": [],
    }, crm)
    assert not master_no_user.get("user_id")
    master_other_branch = await sm.create_sales_master(server.db, {
        "sales_name": "Sales Cabang Lain",
        "branch_ids": [branch_b],
    }, crm)
    master_inactive = await sm.create_sales_master(server.db, {
        "sales_name": "Sales Nonaktif",
        "branch_ids": [],
        "active": False,
    }, crm)

    crm_branch = _user("crm", uid="crm-branch-a", override={})
    crm_branch["branch_ids"] = [branch_a]
    await _upsert_users(server.db, [crm_branch])

    cust = await _seed_customer(server.db, name="Outlet Master", phone="081288800001")
    order_verified = await _seed_order(
        server.db,
        po_number="PO/CRM/MASTER/001",
        customer_id=cust["id"],
        customer=cust["name"],
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )
    order_unmapped = await _seed_order(
        server.db,
        po_number="PO/CRM/MASTER/002",
        customer_id=cust["id"],
        customer=cust["name"],
        sales="Label Accurate Historis",
        sales_user_id=None,
    )
    order_no_sales = await _seed_order(
        server.db,
        po_number="PO/CRM/MASTER/003",
        customer_id=cust["id"],
        customer=cust["name"],
        sales="",
        sales_user_id=None,
    )

    async with await _client(crm) as client:
        # Active Master with Eternal user — ORDER, no PO
        with_user = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811101",
            sales_master_id=master_with_user["id"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
            sales_source="existing",
            status_order="ORDER",
            order_id=None,
            po_number="",
        ))
        assert with_user.status_code == 200, with_user.text
        body = with_user.json()["report"]
        assert body["sales_master_id"] == master_with_user["id"]
        assert body["sales_user_id"] == SALES_AYUB_ID
        assert body["sales_source"] == "existing"
        assert body["sales_label"] == SALES_AYUB_LABEL

        # Active Master without Eternal user — ORDER, no PO
        no_user_order = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811102",
            sales_master_id=master_no_user["id"],
            sales_user_id=None,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
            status_order="ORDER",
            order_id=None,
            po_number="",
        ))
        assert no_user_order.status_code == 200, no_user_order.text
        body = no_user_order.json()["report"]
        assert body["sales_master_id"] == master_no_user["id"]
        assert body["sales_user_id"] in (None, "")
        assert body["sales_source"] == "existing"
        assert body["sales_label"] == master_no_user["sales_name"]
        rid = body["id"]

        # Same Master without user — TIDAK ORDER, no PO
        no_user_tidak = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811103",
            sales_master_id=master_no_user["id"],
            sales_user_id=None,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            order_id=None,
            po_number="",
        ))
        assert no_user_tidak.status_code == 200, no_user_tidak.text
        assert no_user_tidak.json()["report"]["sales_source"] == "existing"
        assert no_user_tidak.json()["report"]["sales_user_id"] in (None, "")

        # True free-text manual still OK for TIDAK ORDER without PO
        manual = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811104",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            sales_source="manual",
            sales_user_id=None,
            sales_master_id=None,
            sales_label="Sales Freelancer X",
            order_id=None,
            po_number="",
        ))
        assert manual.status_code == 200, manual.text
        assert manual.json()["report"]["sales_source"] == "manual"

        # Manual + spoofed sales_master_id rejected
        manual_spoof = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811105",
            status_order="TIDAK ORDER",
            no_order_reason="Harga terlalu mahal",
            sales_source="manual",
            sales_user_id=None,
            sales_master_id=master_no_user["id"],
            sales_label="Sales Freelancer X",
        ))
        assert manual_spoof.status_code == 400

        # Spoofed sales_user_id on Master without account
        spoof_uid = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811106",
            sales_master_id=master_no_user["id"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
        ))
        assert spoof_uid.status_code == 400

        # Invalid Master id
        bad_id = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811107",
            sales_master_id="sm-does-not-exist",
            sales_user_id=None,
            sales_label="Ghost",
            sales_source="existing",
        ))
        assert bad_id.status_code == 400
        assert "Master Sales" in bad_id.text

        # Inactive Master
        inactive = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811108",
            sales_master_id=master_inactive["id"],
            sales_user_id=None,
            sales_label=master_inactive["sales_name"],
            sales_source="existing",
        ))
        assert inactive.status_code == 400
        assert "nonaktif" in inactive.text.lower()

        # PO verified Sales wins — unrelated Master id cannot substitute Sales identity
        po_sub = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811109",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=order_verified["id"],
            po_number=order_verified["po_number"],
            sales_master_id=master_no_user["id"],
            sales_user_id=SALES_EUIS_ID,  # conflicting uid vs PO
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
        ))
        assert po_sub.status_code == 400

        # PO verified Sales accepted; stale/unrelated preferred Master ignored for identity
        po_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811110",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=order_verified["id"],
            po_number=order_verified["po_number"],
            sales_master_id=master_no_user["id"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
            sales_source="existing",
        ))
        assert po_ok.status_code == 200, po_ok.text
        assert po_ok.json()["report"]["sales_user_id"] == SALES_AYUB_ID
        assert po_ok.json()["report"]["sales_label"] == SALES_AYUB_LABEL
        assert po_ok.json()["report"]["sales_source"] == "existing"
        # Unrelated preferred Master must not attach over PO Sales
        assert po_ok.json()["report"]["sales_master_id"] != master_no_user["id"]

        # PO with unresolved historical label — keep Accurate label (Master cannot substitute)
        hist_ok = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811112",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=order_unmapped["id"],
            po_number=order_unmapped["po_number"],
            sales_master_id=master_no_user["id"],
            sales_user_id=None,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
        ))
        assert hist_ok.status_code == 200, hist_ok.text
        assert hist_ok.json()["report"]["sales_label"] == "Label Accurate Historis"
        assert hist_ok.json()["report"]["sales_source"] == "legacy"
        assert hist_ok.json()["report"]["sales_master_id"] != master_no_user["id"]

        # PO without sales — Master without account allowed
        po_blank = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811113",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=order_no_sales["id"],
            po_number=order_no_sales["po_number"],
            sales_master_id=master_no_user["id"],
            sales_user_id=None,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
        ))
        assert po_blank.status_code == 200, po_blank.text
        assert po_blank.json()["report"]["sales_master_id"] == master_no_user["id"]
        assert po_blank.json()["report"]["sales_source"] == "existing"

        # Edit existing report keeps Master without account
        edited = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(
            whatsapp="081288811102",
            sales_master_id=master_no_user["id"],
            sales_user_id=None,
            sales_label=master_no_user["sales_name"],
            sales_source="existing",
            status_order="FOLLOW UP",
            notes="edited",
        ))
        assert edited.status_code == 200, edited.text
        assert edited.json()["report"]["sales_master_id"] == master_no_user["id"]
        assert edited.json()["report"]["sales_source"] == "existing"
        assert edited.json()["report"]["notes"] == "edited"

        # Mapping / PO / customer ownership unchanged
        mapping = await server.db.app_settings.find_one({"key": "sales_user_mapping"})
        assert mapping and any(e.get("user_id") == SALES_AYUB_ID for e in mapping.get("entries") or [])
        po_after = await server.db.orders.find_one({"id": order_verified["id"]})
        assert po_after["sales_user_id"] == SALES_AYUB_ID

    # Cross-branch Master rejected
    async with await _client(crm_branch) as client:
        cross = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081288811114",
            sales_master_id=master_other_branch["id"],
            sales_user_id=None,
            sales_label=master_other_branch["sales_name"],
            sales_source="existing",
        ))
        assert cross.status_code == 403
        assert "cabang" in cross.text.lower()


@pytest.mark.anyio
async def test_po_change_does_not_retain_stale_sales_master_id():
    """Edit/create with PO: sales_master_id follows authoritative PO only.

    Covers stale preferred Master on edit merge, spoofed client Master,
    unresolved PO-B snapshot, same-PO edit, no-PO Master-without-account,
    and save/readback.
    """
    import server
    from services import sales_master as sm

    crm = _user("crm", uid="crm-stale-master")
    await _prepare(server.db, [crm])
    await sm.ensure_indexes(server.db)
    await server.db[sm.COLLECTION].delete_many({})

    # Canonical Eternal users for Ayub + Fahdo
    fahdo_id = "sales-fahdo-test"
    fahdo_label = "Fahdo Pratama"
    await server.db.users.delete_many({"id": fahdo_id})
    await server.db.users.insert_one({
        "id": fahdo_id,
        "email": "fahdo@eternal.com",
        "name": "Fahdo",
        "employee_id": "EMP-0099",
        "role": "sales_eternal",
        "status": "active",
        "permissions_override": {},
    })
    mapping = await server.db.app_settings.find_one({"key": "sales_user_mapping"})
    entries = list((mapping or {}).get("entries") or [])
    entries = [e for e in entries if e.get("user_id") != fahdo_id]
    entries.append({"label": fahdo_label, "user_id": fahdo_id})
    await server.db.app_settings.update_one(
        {"key": "sales_user_mapping"},
        {"$set": {"entries": entries}},
        upsert=True,
    )

    master_ayub = await sm.create_sales_master(server.db, {
        "sales_name": SALES_AYUB_LABEL,
        "user_id": SALES_AYUB_ID,
        "branch_ids": [],
    }, crm)
    master_fahdo = await sm.create_sales_master(server.db, {
        "sales_name": fahdo_label,
        "user_id": fahdo_id,
        "branch_ids": [],
    }, crm)
    master_offline = await sm.create_sales_master(server.db, {
        "sales_name": "Sales Offline Stale",
        "branch_ids": [],
    }, crm)

    cust = await _seed_customer(server.db, name="Outlet Stale", phone="081255500001")
    po_a = await _seed_order(
        server.db,
        po_number="PO/CRM/STALE/A",
        customer_id=cust["id"],
        customer=cust["name"],
        sales=SALES_AYUB_LABEL,
        sales_user_id=SALES_AYUB_ID,
    )
    po_b = await _seed_order(
        server.db,
        po_number="PO/CRM/STALE/B",
        customer_id=cust["id"],
        customer=cust["name"],
        sales=fahdo_label,
        sales_user_id=fahdo_id,
    )
    po_b_unmapped = await _seed_order(
        server.db,
        po_number="PO/CRM/STALE/B2",
        customer_id=cust["id"],
        customer=cust["name"],
        sales="Label Historis Fahdo",
        sales_user_id=None,
    )

    async with await _client(crm) as client:
        # A) Create on PO-A (Ayub), then edit to PO-B (Fahdo)
        created = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081255511101",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=po_a["id"],
            po_number=po_a["po_number"],
            sales_master_id=master_ayub["id"],
            sales_user_id=SALES_AYUB_ID,
            sales_label=SALES_AYUB_LABEL,
            sales_source="existing",
        ))
        assert created.status_code == 200, created.text
        report = created.json()["report"]
        assert report["sales_user_id"] == SALES_AYUB_ID
        assert report["sales_master_id"] == master_ayub["id"]
        rid = report["id"]

        switched = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=po_b["id"],
            po_number=po_b["po_number"],
            # Spoof/stale: keep Ayub Master id from prior selection
            sales_master_id=master_ayub["id"],
            sales_user_id=fahdo_id,
            sales_label=fahdo_label,
            sales_source="existing",
        ))
        assert switched.status_code == 200, switched.text
        body = switched.json()["report"]
        assert body["sales_user_id"] == fahdo_id
        assert body["sales_label"] == fahdo_label
        assert body["sales_master_id"] == master_fahdo["id"]
        assert body["sales_master_id"] != master_ayub["id"]

        # F) Save/readback matches authoritative resolution
        fetched = await client.get(f"/api/crm-daily-reports/{rid}")
        assert fetched.status_code == 200
        got = fetched.json()["report"]
        assert got["sales_label"] == fahdo_label
        assert got["sales_user_id"] == fahdo_id
        assert got["sales_master_id"] == master_fahdo["id"]

        # D) Edit without changing PO — identity stays consistent
        same_po = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=po_b["id"],
            po_number=po_b["po_number"],
            sales_master_id=master_fahdo["id"],
            sales_user_id=fahdo_id,
            sales_label=fahdo_label,
            sales_source="existing",
            notes="same-po-edit",
        ))
        assert same_po.status_code == 200, same_po.text
        assert same_po.json()["report"]["sales_master_id"] == master_fahdo["id"]
        assert same_po.json()["report"]["sales_user_id"] == fahdo_id
        assert same_po.json()["report"]["notes"] == "same-po-edit"

        # B) Switch to PO with unresolved historical label → keep snapshot, null Master
        hist = await client.patch(f"/api/crm-daily-reports/{rid}", json=_new_payload(
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=po_b_unmapped["id"],
            po_number=po_b_unmapped["po_number"],
            sales_master_id=master_fahdo["id"],  # stale preferred
            sales_user_id=fahdo_id,
            sales_label=fahdo_label,
            sales_source="existing",
        ))
        assert hist.status_code == 200, hist.text
        hist_body = hist.json()["report"]
        assert hist_body["sales_label"] == "Label Historis Fahdo"
        assert hist_body["sales_user_id"] in (None, "")
        assert hist_body["sales_master_id"] in (None, "")
        assert hist_body["sales_master_id"] != master_fahdo["id"]
        assert hist_body["sales_master_id"] != master_ayub["id"]
        assert hist_body["sales_source"] == "legacy"

        # C) Direct API spoofed sales_master_id on PO-linked create is ignored
        spoof = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081255511102",
            status_customer="Repeat",
            customer_id=cust["id"],
            order_id=po_b["id"],
            po_number=po_b["po_number"],
            sales_master_id=master_ayub["id"],
            sales_user_id=fahdo_id,
            sales_label=fahdo_label,
            sales_source="existing",
        ))
        assert spoof.status_code == 200, spoof.text
        spoof_body = spoof.json()["report"]
        assert spoof_body["sales_user_id"] == fahdo_id
        assert spoof_body["sales_label"] == fahdo_label
        assert spoof_body["sales_master_id"] == master_fahdo["id"]
        assert spoof_body["sales_master_id"] != master_ayub["id"]

        # E) No-PO verified Master without Eternal account still works
        no_po = await client.post("/api/crm-daily-reports", json=_new_payload(
            whatsapp="081255511103",
            sales_master_id=master_offline["id"],
            sales_user_id=None,
            sales_label=master_offline["sales_name"],
            sales_source="existing",
            order_id=None,
            po_number="",
        ))
        assert no_po.status_code == 200, no_po.text
        assert no_po.json()["report"]["sales_master_id"] == master_offline["id"]
        assert no_po.json()["report"]["sales_user_id"] in (None, "")
        assert no_po.json()["report"]["sales_source"] == "existing"

        # G) Mapping / PO ownership unchanged
        mapping_after = await server.db.app_settings.find_one({"key": "sales_user_mapping"})
        assert any(e.get("user_id") == SALES_AYUB_ID for e in (mapping_after or {}).get("entries") or [])
        assert any(e.get("user_id") == fahdo_id for e in (mapping_after or {}).get("entries") or [])
        po_a_after = await server.db.orders.find_one({"id": po_a["id"]})
        po_b_after = await server.db.orders.find_one({"id": po_b["id"]})
        assert po_a_after["sales_user_id"] == SALES_AYUB_ID
        assert po_b_after["sales_user_id"] == fahdo_id
