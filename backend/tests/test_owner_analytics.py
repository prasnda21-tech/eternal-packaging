"""Owner Analytics — isolated read model. No production data."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_owner_analytics_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "local")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user, user_permissions
from services.owner_analytics import (
    OWNER_ROLES,
    PERM_VIEW,
    assert_owner_dashboard_access,
    get_average_po_value,
    get_owner_analytics,
)
from services.sales_dashboard import get_sales_dashboard

PATH = "/api/dashboard/owner"


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
    for name in ("orders", "sales_invoices", "sales_receipts", "sales_customer_credit_applications",
                 "users", "app_settings", "role_permissions"):
        await db[name].delete_many({})
    yield
    for name in ("orders", "sales_invoices", "sales_receipts", "sales_customer_credit_applications",
                 "users", "app_settings", "role_permissions"):
        await db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="direktur_utama", perms=None, uid=None, deny=None):
    user_id = uid or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = {}
    if perms is not None:
        override = {perm: True for perm in perms}
    for key in deny or []:
        override[key] = False
    return {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": user_id,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _invoice(**extra):
    doc = {
        "id": extra.pop("id", uuid.uuid4().hex),
        "invoice_number": extra.pop("invoice_number", "INV-1"),
        "invoice_date": "2026-03-10",
        "due_date": "2026-03-24",
        "po_id": extra.pop("po_id", "po-1"),
        "financial_status": "finalized",
        "is_cancelled": False,
        "sales": "",
        "after_discount": 10000,
        "grand_total": 11000,
        "ppn_amount": 1000,
        "lines": [],
    }
    doc.update(extra)
    return doc


def _order(**extra):
    doc = {
        "id": extra.pop("id", uuid.uuid4().hex),
        "po_number": extra.pop("po_number", "PO-1"),
        "order_date": "2026-03-05",
        "customer": "Cust A",
        "cancelled": False,
        "current_stage": 8,
        "selling_total": 50000,
        "selling_prices_complete": True,
        "line_items": [{"name": "Cup", "quantity": 100}],
        "shipments": [],
        "priority": "normal",
        "hold": False,
        "buffer_stock": False,
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "total_quantity": 100,
    }
    doc.update(extra)
    return doc


def test_builtin_roles_get_owner_perm():
    assert PERM_VIEW in ROLE_DEFAULT_PERMISSIONS["direktur_utama"]
    assert PERM_VIEW in ROLE_DEFAULT_PERMISSIONS["holding_team"]
    assert "direktur_utama" in OWNER_ROLES
    assert "holding_team" in OWNER_ROLES
    assert "admin" not in OWNER_ROLES


def test_assert_owner_blocks_admin_even_with_perm():
    with pytest.raises(Exception) as exc:
        assert_owner_dashboard_access({"role": "admin"}, [PERM_VIEW])
    assert "Direktur" in str(exc.value) or "Holding" in str(exc.value)


def test_assert_owner_blocks_missing_perm():
    with pytest.raises(Exception):
        assert_owner_dashboard_access({"role": "direktur_utama"}, [])


@pytest.mark.anyio
async def test_omzet_matches_sales_dashboard(clean_db):
    db = server.db
    await db.sales_invoices.insert_many([
        _invoice(id="i1", after_discount=1000, invoice_date="2026-03-10"),
        _invoice(id="i2", after_discount=3000, invoice_date="2026-03-11"),
        _invoice(id="i3", after_discount=9999, invoice_date="2026-03-10", financial_status="draft"),
        _invoice(id="i4", after_discount=8888, invoice_date="2026-03-10", is_cancelled=True),
    ])
    user = _user("direktur_utama")
    sales = await get_sales_dashboard(
        db, start_date="2026-03-01", end_date="2026-03-31", preset="custom", user=user, permissions=[],
    )
    owner = await get_owner_analytics(
        db, start_date="2026-03-01", end_date="2026-03-31", preset="custom", user=user, permissions=[PERM_VIEW],
    )
    assert owner["omzet"]["amount"] == sales["omzet"]["amount"] == 4000.0
    assert owner["omzet"]["invoice_count"] == sales["omzet"]["invoice_count"] == 2
    assert owner["omzet"]["basis"] == "after_discount"
    assert owner["aov_invoice"]["amount"] == 2000.0
    assert owner["aov_invoice"]["invoice_count"] == 2
    assert owner["branch_filter"] is None
    assert owner["profit"]["status"] == "not_ready"
    assert owner["otp_otd"]["status"] == "not_ready"
    assert "otp_percent" not in owner
    assert owner.get("operations", {}).get("otp_otd", {}).get("otp") is None


@pytest.mark.anyio
async def test_aov_empty_when_no_invoices(clean_db):
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
    )
    assert owner["aov_invoice"]["status"] == "empty"
    assert owner["aov_invoice"]["amount"] is None


@pytest.mark.anyio
async def test_average_po_excludes_incomplete_and_cancelled(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="a", selling_total=10000, selling_prices_complete=True, order_date="2026-03-05"),
        _order(id="b", selling_total=30000, selling_prices_complete=True, order_date="2026-03-06"),
        _order(id="c", selling_total=99999, selling_prices_complete=False, order_date="2026-03-07"),
        _order(id="d", selling_total=50000, selling_prices_complete=True, cancelled=True, order_date="2026-03-08"),
        _order(id="e", selling_total=1000, selling_prices_complete=True, order_date="2026-02-01"),
        _order(id="f", selling_total=2000, selling_prices_complete=True, order_date="2026-03-09", branch_id=None),
    ])
    avg = await get_average_po_value(db, start_date="2026-03-01", end_date="2026-03-31")
    assert avg["po_count"] == 3  # a, b, f (null branch included)
    assert avg["excluded_incomplete_price_count"] == 1
    assert avg["amount"] == 14000.0  # (10000+30000+2000)/3
    assert avg["date_basis"] == "order_date"


@pytest.mark.anyio
async def test_http_role_gates(clean_db):
    await server.db.sales_invoices.insert_one(_invoice(after_discount=5000))

    async def expect_status(user, code):
        async with await client_for(user) as client:
            r = await client.get(PATH, params={"start_date": "2026-03-01", "end_date": "2026-03-31", "preset": "custom"})
            assert r.status_code == code, r.text
            return r

    # Director with builtin perm path: permissions_override empty → role defaults include owner.
    r = await expect_status(_user("direktur_utama", perms=None), 200)
    assert r.json()["omzet"]["amount"] == 5000.0

    r = await expect_status(_user("holding_team", perms=None), 200)
    assert r.json()["omzet"]["amount"] == 5000.0

    # Admin has ALL_PERMISSIONS including owner key via effective_permissions, but role blocked.
    await expect_status(_user("admin", perms=None), 403)

    # Admin with explicit owner perm still blocked by role allowlist.
    await expect_status(_user("admin", perms=[PERM_VIEW]), 403)

    await expect_status(_user("crm", perms=[PERM_VIEW]), 403)
    await expect_status(_user("sales_eternal", perms=[PERM_VIEW]), 403)
    await expect_status(_user("operator", perms=[PERM_VIEW]), 403)
    await expect_status(_user("direktur_utama", deny=[PERM_VIEW]), 403)


@pytest.mark.anyio
async def test_owner_perm_does_not_grant_writes_in_catalog():
    # Sanity: owner key is view-only naming; write keys stay separate.
    assert PERM_VIEW.endswith(".view")
    write_keys = (
        "journal.post", "current_stock.update", "payment.create",
        "payroll.finalize", "purchase_orders.create", "sales.invoice.create",
    )
    for key in write_keys:
        assert key != PERM_VIEW


@pytest.mark.anyio
async def test_operations_late_uses_active_sj_only(clean_db):
    today = date(2026, 3, 15).isoformat()
    await server.db.app_settings.insert_one({"key": "delivery_deadline", "lead_time_days": 3})
    await server.db.orders.insert_many([
        _order(
            id="late1",
            po_number="PO-LATE",
            shipments=[{
                "id": "sj1",
                "number": "SJ-1",
                "sj_number": "SJ-1",
                "delivery_date": "2026-03-10",
                "shipment_index": 1,
                "lines": [{"line_idx": 0, "qty": 10}],
            }],
            design_approved_at="2026-03-01T00:00:00+07:00",
            payment_approved_at="2026-03-01T00:00:00+07:00",
        ),
        _order(
            id="cancel-sj",
            po_number="PO-CAN",
            shipments=[{
                "id": "sj2",
                "number": "SJ-2",
                "sj_number": "SJ-2",
                "delivery_date": "2026-03-10",
                "shipment_index": 1,
                "cancelled": True,
                "lines": [{"line_idx": 0, "qty": 10}],
            }],
        ),
    ])
    # Freeze "today" via now=
    from datetime import datetime, timezone
    now = datetime(2026, 3, 15, 6, 0, 0, tzinfo=timezone.utc)
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
        now=now,
    )
    assert owner["delivery_late"]["count"] >= 1
    assert owner["operations"]["delivery"]["source"] == "delivery_schedule.schedule_rows"
    # Cancelled SJ must not create late rows for that PO alone as valid schedule.
    late_pos = {r.get("po_number") for r in owner["operations"]["delivery"]["late"]}
    assert "PO-CAN" not in late_pos
    assert owner["operations"]["po_status"]["partition"] == "disjoint_active_completed"
    assert owner["operations"]["stage_breakdown"]["unit"] == "po_count"
