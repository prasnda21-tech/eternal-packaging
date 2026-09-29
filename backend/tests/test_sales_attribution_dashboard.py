"""Sales attribution for Dashboard Penjualan.

Local fixture only. Does not read or write production data.
Omzet stays after_discount of finalized, non-cancelled invoices.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_attr_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_dashboard import get_sales_dashboard
from services.sales_user_mapping import (
    SETTINGS_KEY,
    account_is_active,
    dashboard_sales_owner,
    save_mapping,
    snapshot_invoice_sales,
)

PATH = "/api/dashboard/penjualan"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    db = server.db
    names = (
        "orders", "sales_invoices", "sales_receipts", "users", "app_settings",
        "audit_logs", "sales_customers",
    )
    for name in names:
        await db[name].delete_many({})
    yield db
    for name in names:
        await db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _admin():
    return {
        "id": "admin-1",
        "user_id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin Pembuat",
        "role": "admin",
        "status": "active",
    }


def _legacy_owner(invoice, mapping):
    """Previous dashboard rule: exact sales label map only. Ignores stored ids."""
    label = str(invoice.get("sales") or "").strip()
    uid = str(mapping.get(label) or "").strip()
    return uid or None


def test_snapshot_and_conflict_rules_do_not_read_names():
    mapping = {"Ayu": "sales-1"}
    assert dashboard_sales_owner({"sales": "Ayu"}, mapping) == ("sales-1", "mapping")
    assert dashboard_sales_owner({"sales": "Holding Satu", "sales_user_id": "hold-1"}, mapping) == ("hold-1", "snapshot")
    assert dashboard_sales_owner({"sales": "", "sales_user_id": "old-1"}, mapping) == ("old-1", "snapshot")
    assert dashboard_sales_owner({"sales": ""}, mapping) == (None, "empty")
    assert dashboard_sales_owner({"sales": "Tanpa peta"}, mapping) == (None, "unmatched")
    assert dashboard_sales_owner(
        {"sales": "Ayu", "sales_user_id": "hold-1", "sales_id": "sales-1"},
        mapping,
    ) == (None, "conflict")
    assert dashboard_sales_owner(
        {"sales": "Ayu", "sales_attribution_conflict": True, "sales_user_id": "sales-1"},
        mapping,
    ) == (None, "conflict")
    snap = snapshot_invoice_sales(
        {"sales": "Holding Satu", "sales_user_id": "hold-1", "created_by": {"id": "admin-1"}},
        mapping,
    )
    assert snap["sales_user_id"] == "hold-1"
    assert snap["sales_attribution_conflict"] is False
    conflicted = snapshot_invoice_sales(
        {"sales": "Ayu", "sales_user_id": "hold-1"},
        mapping,
    )
    assert conflicted["sales_user_id"] is None
    assert conflicted["sales_attribution_conflict"] is True
    assert account_is_active({"status": "active"}) is True
    assert account_is_active({}) is True
    assert account_is_active({"status": "inactive"}) is False


def test_role_defaults_do_not_gain_access():
    for role in ("holding_team", "direktur_utama", "sales_eternal"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "dashboard.sales.view" not in defaults
        assert "nav.dashboard.sales" not in defaults
        assert "sales_order.create" not in defaults
        assert "po.edit" not in defaults
        assert "sales.invoice.create" not in defaults
    assert "sales.pwa.view" not in ROLE_DEFAULT_PERMISSIONS["holding_team"]
    assert "sales.pwa.view" not in ROLE_DEFAULT_PERMISSIONS["direktur_utama"]


@pytest.mark.anyio
async def test_fixture_split_keeps_the_same_total(clean_db):
    db = clean_db
    await db.users.insert_many([
        {"id": "sales-1", "name": "Ayu Penjualan", "role": "sales_eternal", "status": "active", "email": "ayu@example.com"},
        {"id": "hold-1", "name": "Holding Satu", "role": "holding_team", "status": "active", "email": "holding@example.com"},
        {"id": "dir-1", "name": "Direktur Satu", "role": "direktur_utama", "status": "active", "email": "direktur@example.com"},
        {"id": "old-1", "name": "Lama", "role": "sales_eternal", "status": "inactive", "email": "lama@example.com"},
        {"id": "moved-1", "name": "Pindah", "role": "designer", "status": "active", "email": "pindah@example.com"},
        {"id": "sari-1", "name": "Sari", "role": "sales_eternal", "status": "active", "email": "sari.a@example.com"},
        {"id": "sari-2", "name": "Sari", "role": "holding_team", "status": "active", "email": "sari.b@example.com"},
        {"id": "admin-1", "name": "Admin Pembuat", "role": "admin", "status": "active", "email": "admin@example.com"},
    ])
    await db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": [{"label": "Ayu", "user_id": "sales-1"}]})
    await db.orders.insert_one({
        "id": "po-current",
        "sales": "Tidak di peta",
        "sales_user_id": "dir-1",
        "cancelled": False,
        "current_stage": 6,
        "customer_id": "cust-1",
    })
    invoices = [
        {"id": "inv-sales", "sales": "Ayu", "after_discount": 10000, "po_id": "po-sales"},
        {"id": "inv-hold", "sales": "Holding Satu", "sales_user_id": "hold-1", "after_discount": 20000, "po_id": "po-hold"},
        {"id": "inv-dir", "sales": "Direktur Satu", "sales_user_id": "dir-1", "after_discount": 15000, "po_id": "po-dir"},
        {"id": "inv-old", "sales": "Lama", "sales_user_id": "old-1", "after_discount": 8000, "po_id": "po-old"},
        {"id": "inv-moved", "sales": "Pindah", "sales_user_id": "moved-1", "after_discount": 6000, "po_id": "po-moved"},
        {"id": "inv-sari-1", "sales": "Sari", "sales_user_id": "sari-1", "after_discount": 3000, "po_id": "po-s1"},
        {"id": "inv-sari-2", "sales": "Sari", "sales_user_id": "sari-2", "after_discount": 4000, "po_id": "po-s2"},
        {"id": "inv-empty", "sales": "", "after_discount": 2500, "po_id": "po-empty"},
        {"id": "inv-conflict", "sales": "Ayu", "sales_user_id": "hold-1", "sales_id": "sales-1", "after_discount": 1200, "po_id": "po-conflict"},
        {"id": "inv-po-ignored", "sales": "Tidak di peta", "after_discount": 900, "po_id": "po-current", "created_by": {"id": "admin-1", "role": "admin"}},
    ]
    docs = []
    for row in invoices:
        docs.append({
            "invoice_number": row["id"],
            "invoice_date": "2026-03-10",
            "due_date": "2026-03-24",
            "financial_status": "finalized",
            "is_cancelled": False,
            "grand_total": row["after_discount"],
            "lines": [],
            **row,
        })
    await db.sales_invoices.insert_many(docs)
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_admin(),
        permissions=["sales_order.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    groups = {row["key"]: row for row in body["sales"]["groups"]}
    labels = {row["label"]: row["omzet"] for row in body["sales"]["groups"]}
    assert body["omzet"]["amount"] == 70600
    assert round(sum(row["omzet"] for row in body["sales"]["groups"]), 2) == 70600
    assert labels["Ayu Penjualan"] == 10000
    assert labels["Holding Satu"] == 20000
    assert labels["Direktur Satu"] == 15000
    assert labels["Lama"] == 8000
    assert labels["Pindah"] == 6000
    assert "user:sari-1" in groups and "user:sari-2" in groups
    assert groups["user:sari-1"]["omzet"] == 3000
    assert groups["user:sari-2"]["omzet"] == 4000
    assert groups["user:sari-1"]["label"] != groups["user:sari-2"]["label"]
    assert labels["Belum ditetapkan"] == 4600
    assert "Admin Pembuat" not in labels
    assert body["sales"]["unassigned_invoice_count"] == 3

    mapping = {"Ayu": "sales-1"}
    legacy = {}
    for row in invoices:
        uid = _legacy_owner(row, mapping)
        key = uid or "unassigned"
        legacy[key] = legacy.get(key, 0) + row["after_discount"]
    assert sum(legacy.values()) == 70600
    assert legacy["sales-1"] == 11200
    assert legacy["unassigned"] == 59400


@pytest.mark.anyio
async def test_options_and_assignment_follow_active_responsible_roles(clean_db):
    db = clean_db
    before_roles = {
        row["id"]: (row["role"], row.get("permissions_override"))
        for row in []
    }
    await db.users.insert_many([
        {"id": "sales-1", "name": "Ayu Penjualan", "role": "sales_eternal", "status": "active", "email": "ayu@example.com"},
        {"id": "hold-1", "name": "Sari", "role": "holding_team", "status": "active", "email": "holding@example.com"},
        {"id": "dir-1", "name": "Sari", "role": "direktur_utama", "status": "active", "email": "direktur@example.com"},
        {"id": "old-1", "name": "Lama", "role": "sales_eternal", "status": "inactive", "email": "lama@example.com"},
        {"id": "admin-1", "name": "Admin Pembuat", "role": "admin", "status": "active", "email": "admin@example.com", "permissions_override": {"dashboard.sales.view": True}},
    ])
    before_roles = {
        row["id"]: (row["role"], row.get("permissions_override"))
        async for row in db.users.find({}, {"id": 1, "role": 1, "permissions_override": 1})
    }
    app.dependency_overrides[current_user] = lambda: _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        options = await client.get("/api/sales-orders/options")
        assert options.status_code == 200, options.text
        accounts = {row["id"]: row for row in options.json()["sales_accounts"]}
        assert set(accounts) == {"sales-1", "hold-1", "dir-1"}
        assert accounts["hold-1"]["role"] == "holding_team"
        assert accounts["dir-1"]["role"] == "direktur_utama"
        assert accounts["hold-1"]["name"] == "Sari" and accounts["dir-1"]["name"] == "Sari"
        assert "old-1" not in accounts and "admin-1" not in accounts

        created = await client.post("/api/sales-orders", json={
            "po_number": f"PO-HOLD-{uuid.uuid4().hex[:6]}",
            "customer": "PT Atribusi",
            "sales": "Sari",
            "sales_user_id": "hold-1",
            "line_items": [{"name": "Kardus", "quantity": 2, "unit": "pcs"}],
        })
        assert created.status_code == 200, created.text
        order = created.json()["order"]
        assert order["sales_user_id"] == "hold-1"
        assert order["sales"] == "Sari"
        assert order["created_by_user"] != "hold-1"

        rejected = await client.post("/api/sales-orders", json={
            "po_number": f"PO-ADMIN-{uuid.uuid4().hex[:6]}",
            "customer": "PT Atribusi",
            "sales": "Admin Pembuat",
            "sales_user_id": "admin-1",
            "line_items": [{"name": "Kardus", "quantity": 2, "unit": "pcs"}],
        })
        assert rejected.status_code == 400
        inactive = await client.post("/api/sales-orders", json={
            "po_number": f"PO-OLD-{uuid.uuid4().hex[:6]}",
            "customer": "PT Atribusi",
            "sales": "Lama",
            "sales_user_id": "old-1",
            "line_items": [{"name": "Kardus", "quantity": 2, "unit": "pcs"}],
        })
        assert inactive.status_code == 400

    after_roles = {
        row["id"]: (row["role"], row.get("permissions_override"))
        async for row in db.users.find({}, {"id": 1, "role": 1, "permissions_override": 1})
    }
    assert after_roles == before_roles


@pytest.mark.anyio
async def test_invoice_keeps_po_owner_not_the_creator(clean_db):
    db = clean_db
    await db.users.insert_many([
        {"id": "hold-1", "name": "Holding Satu", "role": "holding_team", "status": "active"},
        {"id": "sales-1", "name": "Ayu Penjualan", "role": "sales_eternal", "status": "active"},
        {"id": "admin-1", "name": "Admin Pembuat", "role": "admin", "status": "active"},
    ])
    await db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": [{"label": "Ayu", "user_id": "sales-1"}]})
    po_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO-INV-{uuid.uuid4().hex[:6]}",
        "customer": "PT Orang Lain",
        "sales": "Holding Satu",
        "sales_user_id": "hold-1",
        "seller": "Holding Satu",
        "line_items": [{"line_idx": 0, "line_id": "L0", "name": "Kardus", "quantity": 3, "unit": "pcs"}],
        "current_stage": 5,
    })
    app.dependency_overrides[current_user] = lambda: _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sales-invoices", json={
            "po_id": po_id,
            "invoice_date": "2026-03-11",
            "lines": [{"line_idx": 0, "unit_price": 1000}],
        })
    assert created.status_code == 200, created.text
    invoice = created.json()["invoice"]
    assert invoice["sales"] == "Holding Satu"
    assert invoice["sales_user_id"] == "hold-1"
    assert invoice["sales_attribution_conflict"] is False
    assert invoice["created_by"]["id"] == "admin-1"
    assert invoice["created_by"]["id"] != invoice["sales_user_id"]
    stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0, "sales_user_id": 1, "created_by": 1})
    assert stored["sales_user_id"] == "hold-1"
    order = await db.orders.find_one({"id": po_id}, {"_id": 0, "sales_user_id": 1, "sales": 1})
    assert order["sales_user_id"] == "hold-1"
    assert order["sales"] == "Holding Satu"


@pytest.mark.anyio
async def test_new_invoice_freezes_mapped_identity(clean_db):
    db = clean_db
    await db.users.insert_many([
        {"id": "sales-1", "name": "Ayu Penjualan", "role": "sales_eternal", "status": "active"},
        {"id": "sales-2", "name": "Budi", "role": "sales_eternal", "status": "active"},
    ])
    await db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": [{"label": "Ayu", "user_id": "sales-1"}]})
    po_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO-MAP-{uuid.uuid4().hex[:6]}",
        "customer": "PT Peta",
        "sales": "Ayu",
        "line_items": [{"line_idx": 0, "line_id": "L0", "name": "Kardus", "quantity": 1, "unit": "pcs"}],
        "current_stage": 5,
    })
    app.dependency_overrides[current_user] = lambda: _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sales-invoices", json={
            "po_id": po_id,
            "invoice_date": "2026-03-12",
            "lines": [{"line_idx": 0, "unit_price": 5000}],
        })
    assert created.status_code == 200, created.text
    invoice = created.json()["invoice"]
    assert invoice["sales_user_id"] == "sales-1"
    await db.sales_invoices.update_one(
        {"id": invoice["id"]},
        {"$set": {"financial_status": "finalized", "after_discount": 5000, "grand_total": 5000}},
    )
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"entries": [{"label": "Ayu", "user_id": "sales-2"}]}},
    )
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_admin(),
        permissions=["sales_order.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    labels = {row["label"]: row["omzet"] for row in body["sales"]["groups"]}
    assert labels == {"Ayu Penjualan": 5000}


@pytest.mark.anyio
async def test_mapping_accepts_holding_and_director_without_role_edits(clean_db):
    db = clean_db
    await db.users.insert_many([
        {"id": "hold-1", "name": "Holding Satu", "role": "holding_team", "status": "active", "permissions_override": {"inventory_valuation.view": True}},
        {"id": "dir-1", "name": "Direktur Satu", "role": "direktur_utama", "status": "active"},
        {"id": "old-1", "name": "Lama", "role": "sales_eternal", "status": "inactive"},
        {"id": "admin-1", "name": "Admin", "role": "admin", "status": "active"},
    ])
    admin = _admin()
    saved = await save_mapping(db, {"entries": [
        {"label": "Holding Satu", "user_id": "hold-1"},
        {"label": "Direktur Satu", "user_id": "dir-1"},
    ]}, admin)
    assert {row["user_id"] for row in saved["entries"]} == {"hold-1", "dir-1"}
    hold = await db.users.find_one({"id": "hold-1"}, {"_id": 0, "role": 1, "permissions_override": 1})
    assert hold["role"] == "holding_team"
    assert hold["permissions_override"] == {"inventory_valuation.view": True}
    director = await db.users.find_one({"id": "dir-1"}, {"_id": 0, "role": 1})
    assert director["role"] == "direktur_utama"
    with pytest.raises(Exception) as inactive:
        await save_mapping(db, {"entries": [{"label": "Lama", "user_id": "old-1"}]}, admin)
    assert getattr(inactive.value, "status_code", None) == 400
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"entries": [{"label": "Lama", "user_id": "old-1"}]}},
    )
    kept = await save_mapping(db, {"entries": [
        {"label": "Holding Satu", "user_id": "hold-1"},
        {"label": "Lama", "user_id": "old-1"},
    ]}, admin)
    assert {row["user_id"] for row in kept["entries"]} == {"hold-1", "old-1"}
    with pytest.raises(Exception) as admin_target:
        await save_mapping(db, {"entries": [{"label": "Admin", "user_id": "admin-1"}]}, admin)
    assert getattr(admin_target.value, "status_code", None) == 400


@pytest.mark.anyio
async def test_missing_user_keeps_its_id_and_conflict_does_not_use_the_label(clean_db):
    db = clean_db
    await db.users.insert_one({
        "id": "sales-1", "name": "Ayu Penjualan", "role": "sales_eternal", "status": "active",
    })
    await db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": [{"label": "Ayu", "user_id": "sales-1"}]})
    await db.orders.insert_one({"id": "po-1", "cancelled": False, "current_stage": 6, "customer_id": "cust-1"})
    await db.sales_invoices.insert_many([
        {
            "id": "inv-ghost", "invoice_number": "INV-GHOST", "invoice_date": "2026-03-10",
            "po_id": "po-1", "financial_status": "finalized", "is_cancelled": False,
            "sales": "Ayu", "sales_user_id": "ghost-9", "after_discount": 5000, "grand_total": 5000, "lines": [],
        },
        {
            "id": "inv-conflict", "invoice_number": "INV-CONFLICT", "invoice_date": "2026-03-10",
            "po_id": "po-1", "financial_status": "finalized", "is_cancelled": False,
            "sales": "Ayu", "sales_user_id": "ghost-9", "sales_id": "sales-1",
            "after_discount": 700, "grand_total": 700, "lines": [],
        },
        {
            "id": "inv-mapped", "invoice_number": "INV-MAPPED", "invoice_date": "2026-03-10",
            "po_id": "po-1", "financial_status": "finalized", "is_cancelled": False,
            "sales": "Ayu", "after_discount": 1100, "grand_total": 1100, "lines": [],
        },
    ])
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_admin(),
        permissions=["sales_order.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    groups = {row["key"]: row for row in body["sales"]["groups"]}
    assert body["omzet"]["amount"] == 6800
    assert round(sum(row["omzet"] for row in body["sales"]["groups"]), 2) == 6800
    ghost = groups["user:ghost-9"]
    assert ghost["omzet"] == 5000
    assert ghost["identity_status"] == "missing"
    assert "identitas tidak ditemukan" in ghost["label"]
    assert "Ayu" not in ghost["label"]
    assert groups["user:sales-1"]["omzet"] == 1100
    assert groups["unassigned"]["omzet"] == 700
    assert groups["unassigned"]["invoice_count"] == 1


@pytest.mark.anyio
async def test_issue_endpoint_keeps_snapshot_through_journal_tracking_and_retry(clean_db):
    """Real Simpan path: /api/sales-invoices/issue, journal, and Live Tracking."""
    from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
    from services.customer_tracking import ensure_tracking_token
    from services.customer_tracking_invoices import public_invoice_summary
    from services.journal_core import JOURNAL_ENTRIES, ensure_indexes as ensure_journal
    from services.sales_accounting_settings import SETTINGS_KEY as ACCOUNTING_SETTINGS

    db = clean_db
    await ensure_coa(db)
    await ensure_journal(db)
    for name in (
        JOURNAL_ENTRIES, "chart_of_accounts", "accounting_periods",
        "sales_invoice_qty_holds", "sales_invoice_line_qty", "tracking_tokens",
    ):
        await db[name].delete_many({})
    await db.users.insert_many([
        {"id": "hold-1", "name": "Holding Satu", "role": "holding_team", "status": "active", "email": "holding@example.com"},
        {"id": "admin-1", "name": "Admin Pembuat", "role": "admin", "status": "active"},
    ])
    po_id = str(uuid.uuid4())
    po = {
        "id": po_id,
        "po_number": f"PO/ATTR/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Jalur Simpan",
        "sales": "Holding Satu",
        "sales_user_id": "hold-1",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup Uji", "quantity": 4, "unit": "pcs"}],
        "current_stage": 5,
        "cancelled": False,
    }
    await db.orders.insert_one(dict(po))

    async def _account(prefix, name, account_type):
        return await create_account(
            db,
            {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
            user={"id": "admin-1", "name": "Admin", "role": "admin"},
        )

    receivable = await _account("11", "Piutang Atribusi", "AREC")
    revenue = await _account("41", "Pendapatan Atribusi", "REVE")
    tax = await _account("21", "PPN Atribusi", "OCLY")
    await db.app_settings.update_one(
        {"key": ACCOUNTING_SETTINGS},
        {"$set": {
            "key": ACCOUNTING_SETTINGS,
            "receivable_account_id": receivable["id"],
            "revenue_account_id": revenue["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )
    token = await ensure_tracking_token(db, po)
    app.dependency_overrides[current_user] = lambda: _admin()
    body = {
        "po_id": po_id,
        "invoice_date": "2026-03-18",
        "payment_term_days": 0,
        "discount_amount": 0,
        "ppn_percent": 0,
        "notes": "",
        "idempotency_key": "attr-issue-1",
        "lines": [{"line_idx": 0, "line_id": "l0", "quantity": 1, "unit_price": 25000}],
        "other_charges": [],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/sales-invoices/issue", json=body)
        assert first.status_code == 200, first.text
        invoice = first.json()["invoice"]
        assert invoice["sales_user_id"] == "hold-1"
        assert invoice["created_by"]["id"] == "admin-1"
        assert invoice["issue_state"] == "issued"
        journals = await db[JOURNAL_ENTRIES].count_documents({"source_id": invoice["id"], "status": "posted"})
        assert journals == 1
        listed = await client.get(f"/api/public/tracking/{token}/invoices")
        assert listed.status_code == 200, listed.text
        public_rows = listed.json().get("invoices") or []
        assert any(row.get("id") == invoice["id"] for row in public_rows)
        assert "sales_user_id" not in public_invoice_summary(invoice, token)
        assert "hold-1" not in listed.text

        patched = await client.patch(f"/api/orders/{po_id}/sales", json={"sales": "Orang Lain"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["order"]["sales"] == "Orang Lain"
        assert patched.json()["order"]["sales_user_id"] in (None, "")
        second = await client.post("/api/sales-invoices/issue", json=body)
        assert second.status_code == 200, second.text
        assert second.json()["invoice"]["id"] == invoice["id"]
        assert second.json()["invoice"]["sales_user_id"] == "hold-1"
        assert await db.sales_invoices.count_documents({"po_id": po_id}) == 1
        assert await db[JOURNAL_ENTRIES].count_documents({"source_id": invoice["id"]}) == 1
        stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0, "sales_user_id": 1, "sales": 1})
        assert stored["sales_user_id"] == "hold-1"
        assert stored["sales"] == "Holding Satu"

    report = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_admin(),
        permissions=["sales_order.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    labels = {row["label"]: row["omzet"] for row in report["sales"]["groups"]}
    assert labels == {"Holding Satu": 25000.0}
    assert report["omzet"]["amount"] == 25000.0
    assert round(sum(row["omzet"] for row in report["sales"]["groups"]), 2) == report["omzet"]["amount"]
