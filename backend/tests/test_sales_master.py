"""Focused tests for Master Sales + CRM options + PO resolve compatibility."""
import os
import sys
import uuid
from decimal import Decimal
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


@pytest.mark.anyio
async def test_pra_po_catalog_permission_branch_and_inactive():
    """Pra-PO readers may use the operator catalog. Branch and active filters stay."""
    import server
    from fastapi import HTTPException

    admin = _user("admin")
    await _prepare(server.db, [admin])
    branch_a = await _seed_branch(server.db, code="PRA")
    branch_b = await _seed_branch(server.db, code="PRB")
    own = await sm.create_sales_master(server.db, {
        "sales_name": "Euis Cabang A",
        "branch_ids": [branch_a],
    }, admin)
    other = await sm.create_sales_master(server.db, {
        "sales_name": "Ayub Cabang B",
        "branch_ids": [branch_b],
    }, admin)
    shared = await sm.create_sales_master(server.db, {
        "sales_name": "Sales Tanpa Cabang",
        "branch_ids": [],
    }, admin)
    inactive = await sm.create_sales_master(server.db, {
        "sales_name": "Vira Nonaktif",
        "branch_ids": [branch_a],
        "active": False,
    }, admin)

    deny_po_view = {"po.view": False}
    for perm in (
        "pra_po.create",
        "pra_po.edit_own",
        "pra_po.view_own",
        "pra_po.view_all",
        "pra_po.verify",
    ):
        reader = _user("operator", uid=f"pra-{perm}", override={**deny_po_view, perm: True})
        reader["branch_ids"] = [branch_a]
        await _upsert_users(server.db, [reader])
        async with await _client(reader) as client:
            allowed = await client.get("/api/sales-master/catalog")
            assert allowed.status_code == 200, allowed.text
            names = {row["sales_name"] for row in allowed.json()["items"]}
            assert "Euis Cabang A" in names
            assert "Sales Tanpa Cabang" in names
            assert "Ayub Cabang B" not in names
            assert "Vira Nonaktif" not in names
            settings = await client.get("/api/settings/sales-master")
            assert settings.status_code == 403

    outsider = _user("operator", uid="pra-outsider", override=dict(deny_po_view))
    outsider["branch_ids"] = [branch_a]
    await _upsert_users(server.db, [outsider])
    async with await _client(outsider) as client:
        denied = await client.get("/api/sales-master/catalog")
        assert denied.status_code == 403

    from services.pra_po import create_draft
    with pytest.raises(HTTPException) as cross:
        await create_draft(server.db, {
            "customer": "Toko Lintas",
            "line_items": [{"name": "Dus", "quantity": 1, "unit": "pcs"}],
            "sales_master_id": other["id"],
            "sales": other["sales_name"],
        }, outsider)
    assert cross.value.status_code == 403
    with pytest.raises(HTTPException) as inactive_new:
        await create_draft(server.db, {
            "customer": "Toko Nonaktif",
            "line_items": [{"name": "Dus", "quantity": 1, "unit": "pcs"}],
            "sales_master_id": inactive["id"],
            "sales": inactive["sales_name"],
        }, admin)
    assert inactive_new.value.status_code == 400
    assert "nonaktif" in str(inactive_new.value.detail).lower()

    assert own["id"] and shared["id"] and inactive["id"]
    with pytest.raises(HTTPException) as inactive_exc:
        await sm.require_active_sales(server.db, inactive["id"], user=admin)
    assert inactive_exc.value.status_code == 400
    with pytest.raises(HTTPException) as branch_exc:
        await sm.require_active_sales(server.db, other["id"], user=outsider)
    assert branch_exc.value.status_code == 403


@pytest.mark.anyio
async def test_pra_po_sales_survives_draft_invoice_and_report_bucket():
    import server
    from fastapi import HTTPException
    from services.pra_po import PERM_EDIT_OWN, create_draft, update_draft
    from services.sales_invoices import create_sales_invoice
    from services.sales_reports import UNVERIFIED_SALES_LABEL, _sales_rows
    from services.sales_user_mapping import SALES_PWA_ROLE

    admin = _user("admin")
    await _prepare(server.db, [admin])
    pub = await sm.create_sales_master(server.db, {"sales_name": "Euis Risnawati"}, admin)
    sales_user = _user(SALES_PWA_ROLE, uid="euis-map", name="Euis User")
    await _upsert_users(server.db, [sales_user])

    line = {"name": "Dus", "quantity": 2, "unit": "pcs", "unit_price": 1000}
    created = await create_draft(server.db, {
        "customer": "Toko Uji",
        "customer_phone": "0812",
        "address": "Jl. Uji",
        "sales": pub["sales_name"],
        "sales_master_id": pub["id"],
        "line_items": [line],
    }, admin)
    assert created["sales"] == "Euis Risnawati"
    assert created["sales_master_id"] == pub["id"]

    kept = await update_draft(server.db, created["id"], {
        "customer": "Toko Uji",
        "sales": "Euis Risnawati",
        "line_items": [line],
    }, admin, {PERM_EDIT_OWN})
    assert kept["sales_master_id"] == pub["id"]
    assert kept["sales"] == "Euis Risnawati"

    renamed = await update_draft(server.db, created["id"], {
        "customer": "Toko Uji 2",
        "sales": "Nama Palsu",
        "sales_master_id": pub["id"],
        "line_items": [line],
    }, admin, {PERM_EDIT_OWN})
    assert renamed["customer"] == "Toko Uji 2"
    assert renamed["sales"] == "Euis Risnawati"
    assert renamed["sales_master_id"] == pub["id"]

    forced = await create_draft(server.db, {
        "customer": "Toko Kanonik",
        "customer_phone": "0814",
        "address": "Jl. Kanonik",
        "sales": "Bukan Nama Master",
        "sales_master_id": pub["id"],
        "line_items": [line],
    }, admin)
    assert forced["sales"] == "Euis Risnawati"
    assert forced["sales_master_id"] == pub["id"]

    with pytest.raises(HTTPException) as free_text:
        await create_draft(server.db, {
            "customer": "Toko Teks",
            "customer_phone": "0815",
            "address": "Jl. Teks",
            "sales": "Teks Baru",
            "line_items": [line],
        }, admin)
    assert free_text.value.status_code == 400
    assert "Master Sales" in str(free_text.value.detail)

    legacy = await create_draft(server.db, {
        "customer": "Toko Lama",
        "customer_phone": "0813",
        "address": "Jl. Lama",
        "line_items": [line],
    }, admin)
    await server.db.pra_pos.update_one(
        {"id": legacy["id"]},
        {"$set": {"sales": "Catatan Bebas"}},
    )
    legacy = await update_draft(server.db, legacy["id"], {
        "customer": "Toko Lama 2",
        "sales": "Catatan Bebas",
        "line_items": [line],
    }, admin, {PERM_EDIT_OWN})
    assert legacy["customer"] == "Toko Lama 2"
    assert legacy.get("sales") == "Catatan Bebas"
    assert not legacy.get("sales_master_id")
    mid, label, _uid = await sm.resolve_for_po_write(
        server.db,
        sales_master_id=legacy.get("sales_master_id"),
        sales_label=legacy.get("sales"),
        user=admin,
    )
    assert mid is None
    assert label == "Catatan Bebas"

    resolved_id, resolved_label, _hint = await sm.resolve_for_po_write(
        server.db,
        sales_master_id=kept["sales_master_id"],
        sales_label=kept["sales"],
        user=admin,
    )
    assert resolved_id == pub["id"]
    assert resolved_label == "Euis Risnawati"

    order_id = str(uuid.uuid4())
    await server.db.orders.insert_one({
        "id": order_id,
        "po_number": f"PO-PRA-{uuid.uuid4().hex[:6]}",
        "customer": "Toko Uji",
        "order_date": "2026-10-29",
        "cancelled": False,
        "sales": resolved_label,
        "seller": resolved_label,
        "sales_master_id": resolved_id,
        "line_items": [{
            "line_idx": 0,
            "line_id": "L1",
            "name": "Dus",
            "quantity": 2,
            "unit": "pcs",
        }],
    })
    invoice = await create_sales_invoice(server.db, {
        "po_id": order_id,
        "invoice_date": "2026-10-29",
        "lines": [{"line_idx": 0, "quantity": 2, "unit_price": 1000}],
    }, admin)
    assert invoice["sales"] == "Euis Risnawati"
    assert "sales_master_id" not in invoice

    mapped = _sales_rows(
        [{
            "sales_label": invoice["sales"],
            "penjualan": Decimal("2000.00"),
            "quantity": Decimal("2"),
            "qty_verified": True,
            "invoice_id": invoice["id"],
        }],
        {"Euis Risnawati": sales_user["id"]},
        set(),
        {sales_user["id"]: {"id": sales_user["id"], "name": "Euis User", "role": SALES_PWA_ROLE, "status": "active"}},
    )
    assert len(mapped) == 1
    assert mapped[0]["sales_user_id"] == sales_user["id"]
    assert mapped[0]["penjualan"] == Decimal("2000.00")
    assert mapped[0]["quantity"] == Decimal("2")

    legacy_rows = _sales_rows(
        [{
            "sales_label": "Catatan Bebas",
            "penjualan": Decimal("500.00"),
            "quantity": Decimal("1"),
            "qty_verified": True,
            "invoice_id": "inv-legacy",
        }],
        {"Euis Risnawati": sales_user["id"]},
        set(),
        {sales_user["id"]: {"id": sales_user["id"], "name": "Euis User", "role": SALES_PWA_ROLE, "status": "active"}},
    )
    assert legacy_rows[0]["sales"] == UNVERIFIED_SALES_LABEL
    assert legacy_rows[0]["penjualan"] == Decimal("500.00")

    await server.db.sales_master.update_one({"id": pub["id"]}, {"$set": {"active": False}})
    preserved = await update_draft(server.db, created["id"], {
        "customer": "Toko Setelah Nonaktif",
        "sales": "Euis Risnawati",
        "sales_master_id": pub["id"],
        "line_items": [line],
    }, admin, {PERM_EDIT_OWN})
    assert preserved["customer"] == "Toko Setelah Nonaktif"
    assert preserved["sales"] == "Euis Risnawati"
    assert preserved["sales_master_id"] == pub["id"]
    with pytest.raises(HTTPException) as inactive_verify:
        await sm.resolve_for_po_write(
            server.db,
            sales_master_id=preserved["sales_master_id"],
            sales_label=preserved["sales"],
            user=admin,
        )
    assert inactive_verify.value.status_code == 400
    assert "nonaktif" in str(inactive_verify.value.detail).lower()
