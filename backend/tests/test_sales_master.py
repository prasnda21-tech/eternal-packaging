"""Focused tests for Master Sales + CRM options + PO resolve compatibility."""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sales_master")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services import crm_daily_report_settings as crm_opts
from services import sales_master as sm
from services.org_locations import BRANCHES_COLLECTION
from services.sales_user_mapping import SETTINGS_KEY as MAPPING_KEY


def _user(role, *, uid=None, name=None, email=None, override=None):
    ident = uid or f"{role}-{uuid.uuid4().hex[:6]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": email or f"{ident}@eternal.test",
        "name": name or role.upper(),
        "role": role,
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


def _override(user):
    async def _dep():
        return user
    return _dep


async def _client(user):
    app.dependency_overrides[current_user] = _override(user)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _prepare(db, users):
    await sm.ensure_indexes(db)
    await db[sm.COLLECTION].delete_many({})
    await db[BRANCHES_COLLECTION].delete_many({"id": {"$regex": r"^br-sm-"}})
    await db.app_settings.delete_many({"key": {"$in": [MAPPING_KEY, crm_opts.SETTINGS_KEY]}})
    await db.audit_logs.delete_many({"action": {"$regex": r"^settings\.(sales_master|crm_daily_report)"}})
    await _upsert_users(db, users)


async def _seed_branch(db, *, code="JKT"):
    bid = f"br-sm-{uuid.uuid4().hex[:6]}"
    await db[BRANCHES_COLLECTION].insert_one({
        "id": bid,
        "code": code,
        "name": f"Cabang {code}",
        "active": True,
    })
    return bid


@pytest.mark.anyio
async def test_permissions_in_catalog_not_auto_manage():
    assert "settings.sales_master.view" in ALL_PERMISSIONS
    assert "settings.sales_master.manage" in ALL_PERMISSIONS
    assert "settings.crm_daily_report.view" in ALL_PERMISSIONS
    assert "settings.crm_daily_report.manage" in ALL_PERMISSIONS
    # Must not auto-grant manage just because role name is admin-like CRM
    crm_perms = set(ROLE_DEFAULT_PERMISSIONS.get("crm") or [])
    assert "settings.sales_master.manage" not in crm_perms
    assert "settings.crm_daily_report.manage" not in crm_perms


@pytest.mark.anyio
async def test_create_update_duplicate_normalize_and_concurrency():
    import server
    admin = _user("admin")
    await _prepare(server.db, [admin])
    b1 = await _seed_branch(server.db, code="A")
    b2 = await _seed_branch(server.db, code="B")

    async with await _client(admin) as client:
        created = await client.post("/api/settings/sales-master", json={
            "sales_name": "  Ayub  Sales ",
            "branch_ids": [b1, b2],
            "sort_order": 10,
        })
        assert created.status_code == 200, created.text
        item = created.json()["item"]
        assert item["sales_name"] == "Ayub Sales"
        assert set(item["branch_ids"]) == {b1, b2}
        assert item["doc_version"] == 1

        dup = await client.post("/api/settings/sales-master", json={"sales_name": "ayub sales"})
        assert dup.status_code == 409

        patched = await client.patch(f"/api/settings/sales-master/{item['id']}", json={
            "sales_name": "Ayub Sales",
            "active": False,
            "doc_version": 1,
        })
        assert patched.status_code == 200, patched.text
        assert patched.json()["item"]["active"] is False
        assert patched.json()["item"]["doc_version"] == 2

        stale = await client.patch(f"/api/settings/sales-master/{item['id']}", json={
            "active": True,
            "doc_version": 1,
        })
        assert stale.status_code == 409


@pytest.mark.anyio
async def test_inactive_blocked_for_new_but_history_resolve():
    import server
    admin = _user("admin")
    await _prepare(server.db, [admin])
    pub = await sm.create_sales_master(server.db, {"sales_name": "Vira"}, admin)
    await sm.update_sales_master(server.db, pub["id"], {
        "sales_name": "Vira", "active": False, "doc_version": 1,
    }, admin)

    with pytest.raises(Exception) as exc:
        await sm.require_active_sales(server.db, pub["id"])
    assert exc.value.status_code == 400

    hist = await sm.get_sales_master(server.db, pub["id"])
    assert hist["sales_name"] == "Vira"
    assert hist["active"] is False

    mid, label, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label="Vira", user=None,
    )
    # inactive master must not soft-attach for new writes
    assert mid is None
    assert label == "Vira"


@pytest.mark.anyio
async def test_mapping_preserved_and_rename_conflict():
    import server
    from services.sales_user_mapping import save_mapping

    admin = _user("admin")
    sales_user = _user("sales_eternal", uid="su-1", name="Sales One")
    other = _user("sales_eternal", uid="su-2", name="Sales Two")
    await _prepare(server.db, [admin, sales_user, other])
    await save_mapping(server.db, {"entries": [{"label": "OldLabel", "user_id": sales_user["id"]}]}, admin)

    pub = await sm.create_sales_master(server.db, {
        "sales_name": "OldLabel",
        "user_id": sales_user["id"],
    }, admin)
    assert pub["sales_name"] == "OldLabel"

    # Conflict: rename to label already mapped to another user
    await save_mapping(server.db, {
        "entries": [
            {"label": "OldLabel", "user_id": sales_user["id"]},
            {"label": "Taken", "user_id": other["id"]},
        ],
    }, admin)
    with pytest.raises(Exception) as exc:
        await sm.update_sales_master(server.db, pub["id"], {
            "sales_name": "Taken",
            "user_id": sales_user["id"],
            "doc_version": 1,
        }, admin)
    assert exc.value.status_code == 409

    # Safe rename updates mapping label, does not rewrite orders
    await server.db.orders.insert_one({
        "id": f"ord-{uuid.uuid4().hex[:6]}",
        "po_number": f"PO-SM-{uuid.uuid4().hex[:4]}",
        "sales": "OldLabel",
        "cancelled": False,
    })
    updated = await sm.update_sales_master(server.db, pub["id"], {
        "sales_name": "NewLabel",
        "user_id": sales_user["id"],
        "doc_version": 1,
    }, admin)
    assert updated["sales_name"] == "NewLabel"
    hist = await server.db.orders.find_one({"sales": "OldLabel"}, {"_id": 0})
    assert hist is not None  # historical label untouched


@pytest.mark.anyio
async def test_catalog_rbac_and_crm_options_other():
    import server
    admin = _user("admin")
    crm = _user("crm")
    await _prepare(server.db, [admin, crm])
    await sm.create_sales_master(server.db, {"sales_name": "EUIS"}, admin)

    async with await _client(crm) as client:
        catalog = await client.get("/api/sales-master/catalog")
        assert catalog.status_code == 200, catalog.text
        assert any(i["sales_name"] == "EUIS" for i in catalog.json()["items"])

        denied = await client.get("/api/settings/sales-master")
        assert denied.status_code == 403

        opts = await client.get("/api/crm-daily-reports/options")
        assert opts.status_code == 200
        body = opts.json()
        assert body["other_option_id"] == "__other__"
        assert any(o["id"] == "__other__" for o in body["no_order_reasons"])

    async with await _client(admin) as client:
        put = await client.put("/api/settings/crm-daily-report-options", json={
            "doc_version": 1,
            "no_order_reasons": [
                {"id": "r1", "label": "Stok", "active": True, "sort_order": 1},
                {"id": "__other__", "label": "Lainnya", "active": True, "sort_order": 9999, "is_other": True},
            ],
            "complaint_categories": [
                {"id": "__other__", "label": "Lainnya", "active": True, "sort_order": 9999, "is_other": True},
            ],
            "issue_categories": [
                {"id": "__other__", "label": "Lainnya", "active": True, "sort_order": 9999, "is_other": True},
            ],
        })
        assert put.status_code == 200, put.text
        # permanent delete blocked
        bad = await client.put("/api/settings/crm-daily-report-options", json={
            "doc_version": put.json()["settings"]["doc_version"],
            "no_order_reasons": [
                {"id": "__other__", "label": "Lainnya", "active": True, "sort_order": 9999, "is_other": True},
            ],
            "complaint_categories": put.json()["settings"]["complaint_categories"],
            "issue_categories": put.json()["settings"]["issue_categories"],
        })
        assert bad.status_code == 400


@pytest.mark.anyio
async def test_po_resolve_matched_unmatched_and_optional_user():
    import server
    admin = _user("admin")
    await _prepare(server.db, [admin])
    pub = await sm.create_sales_master(server.db, {"sales_name": "Alfi"}, admin)

    mid, label, uid = await sm.resolve_for_po_write(
        server.db, sales_master_id=pub["id"], sales_label=None, user=None,
    )
    assert mid == pub["id"]
    assert label == "Alfi"
    assert uid is None  # optional user

    mid2, label2, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label="Unknown Accurate", user=None,
    )
    assert mid2 is None
    assert label2 == "Unknown Accurate"

    mid3, label3, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label="Alfi", user=None,
    )
    assert mid3 == pub["id"]
    assert label3 == "Alfi"
