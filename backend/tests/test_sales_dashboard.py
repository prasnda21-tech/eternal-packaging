"""Dashboard Penjualan — isolated read model. Does not touch production data."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_dashboard_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user
from services.journal_core import JOURNAL_ENTRIES
from services.sales_dashboard import (
    comparable_previous_range,
    get_sales_dashboard,
    grain_for,
    wib_today,
)
from services.sales_user_mapping import SETTINGS_KEY

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
        "orders", "sales_invoices", "sales_receipts", "sales_customer_credit_applications",
        "users", "app_settings", JOURNAL_ENTRIES,
    )
    for name in names:
        await db[name].delete_many({})
    yield
    for name in names:
        await db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="admin", perms=None, uid=None):
    user_id = uid or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = {}
    if role != "admin" and perms is not None:
        override = {perm: True for perm in perms}
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
        "po_id": "po-1",
        "financial_status": "finalized",
        "is_cancelled": False,
        "sales": "",
        "after_discount": 10000,
        "grand_total": 11000,
        "ppn_amount": 1000,
        "lines": [],
        "updated_at": "2026-03-10T00:00:00+00:00",
    }
    doc.update(extra)
    return doc


def _order(**extra):
    doc = {
        "id": extra.pop("id", "po-1"),
        "po_number": "PO-1",
        "cancelled": False,
        "current_stage": 6,
        "customer_id": "cust-1",
        "customer": "Kedai Tuman",
        "sales_user_id": None,
        "design_files": [{"name": "a"}],
        "payment_files": [{"name": "b"}],
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [],
        "updated_at": "2026-03-01T00:00:00+00:00",
    }
    doc.update(extra)
    return doc


async def _seed_mapping(entries):
    await server.db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": entries})


def test_wib_today_crosses_midnight():
    assert wib_today(datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc)) == datetime(2026, 9, 30).date()
    assert wib_today(datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc)) == datetime(2026, 10, 1).date()


def test_comparable_ranges_and_edges():
    from datetime import date

    assert comparable_previous_range("today", "2026-03-01", "2026-03-01", date(2026, 3, 1)) == ("2026-02-28", "2026-02-28")
    assert comparable_previous_range("month", "2026-03-01", "2026-03-28", date(2026, 3, 28)) == ("2026-02-01", "2026-02-28")
    assert comparable_previous_range("month", "2026-03-01", "2026-03-31", date(2026, 3, 31)) is None
    assert comparable_previous_range("month", "2024-03-01", "2024-03-31", date(2024, 3, 31)) is None
    assert comparable_previous_range("month", "2026-03-01", "2026-03-01", date(2026, 3, 1)) == ("2026-02-01", "2026-02-01")
    assert comparable_previous_range("year", "2026-01-01", "2026-03-01", date(2026, 3, 1)) == ("2025-01-01", "2025-03-01")
    assert comparable_previous_range("year", "2024-01-01", "2024-02-29", date(2024, 2, 29)) is None
    assert comparable_previous_range("custom", "2026-03-01", "2026-03-28", date(2026, 3, 28)) is None
    assert comparable_previous_range("month", "2026-02-01", "2026-02-28", date(2026, 3, 28)) is None
    assert grain_for("2026-03-01", "2026-03-31") == "day"
    assert grain_for("2026-01-01", "2026-03-15") == "week"
    assert grain_for("2026-01-01", "2026-09-26") == "month"


def test_non_admin_roles_do_not_auto_gain_sales_dashboard():
    assert "dashboard.sales.view" in ROLE_DEFAULT_PERMISSIONS["admin"]
    assert "nav.dashboard.sales" in ROLE_DEFAULT_PERMISSIONS["admin"]
    for role in ("helper", "operator", "crm", "head_production", "head_operations", "sales_eternal"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        assert "dashboard.sales.view" not in defaults, role
        assert "nav.dashboard.sales" not in derived, role


@pytest.mark.anyio
async def test_forbidden_roles_get_403(clean_db):
    async with await client_for(_user(role="operator")) as client:
        denied = await client.get(PATH, params={"start_date": "2026-03-01", "end_date": "2026-03-01", "preset": "custom"})
        assert denied.status_code == 403
    async with await client_for(_user(role="sales_eternal", perms=["dashboard.sales.view"], uid="sales-locked")) as client:
        locked = await client.get(PATH, params={"start_date": "2026-03-01", "end_date": "2026-03-01", "preset": "custom"})
        assert locked.status_code == 403
        assert "sales-b" not in locked.text


@pytest.mark.anyio
async def test_invalid_range_is_400(clean_db):
    async with await client_for(_user()) as client:
        bad = await client.get(PATH, params={"start_date": "2026-03-10", "end_date": "2026-03-01", "preset": "custom"})
        assert bad.status_code == 400


@pytest.mark.anyio
async def test_reconciliation_partial_status_payment_and_no_writes(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="po-part", customer_id="cust-a", customer="Kedai A", sales_user_id="user-ayu", line_items=[
            {"line_idx": 0, "line_id": "L0", "accurate_item_id": "item-1", "name": "Kardus", "unit": "pcs", "quantity": 10},
        ]),
        _order(id="po-hold", current_stage=2, design_files=[], payment_files=[], customer_id="cust-b"),
        _order(id="po-done", current_stage=14, customer_id="cust-c"),
        _order(id="po-cancel", cancelled=True, current_stage=2, customer_id="cust-d"),
        _order(id="po-old", customer_id="cust-a", customer="Kedai A ejaan lain", line_items=[
            {"line_idx": 0, "line_id": "L9", "accurate_item_id": "item-1", "name": "Kardus", "unit": "pcs", "quantity": 2},
        ]),
        _order(id="po-name", customer_id="cust-z", customer="Kedai A", line_items=[]),
        _order(id="po-4", customer_id="Kedai Tuman|Yogyakarta", customer="Kedai Tuman", outlet="Yogyakarta"),
    ])
    await db.sales_invoices.insert_many([
        _invoice(id="inv-part-1", po_id="po-part", invoice_date="2026-03-10", sales="Ayu", after_discount=4000, grand_total=4400, ppn_amount=400, lines=[
            {"line_idx": 0, "line_id": "L0", "item_name": "Kardus", "quantity": 4, "unit": "pcs", "line_subtotal": 4000},
        ], updated_at="stamp-1"),
        _invoice(id="inv-part-2", po_id="po-part", invoice_date="2026-03-12", sales="Ayu", after_discount=5000, grand_total=5000, ppn_amount=0, lines=[
            {"line_idx": 0, "line_id": "L0", "item_name": "Kardus", "quantity": 5, "unit": "pcs", "line_subtotal": 3000},
            {"line_idx": 0, "line_id": "L0", "item_name": "Kardus", "quantity": 1, "unit": "dus", "line_subtotal": 2000},
        ], updated_at="stamp-2"),
        _invoice(id="inv-draft", po_id="po-part", financial_status="draft", after_discount=99999, grand_total=99999, updated_at="stamp-draft"),
        _invoice(id="inv-cancel", po_id="po-part", financial_status="cancelled", after_discount=88888, grand_total=88888, updated_at="stamp-cancel"),
        _invoice(id="inv-old", po_id="po-old", invoice_date="2026-02-02", sales="Ayu", after_discount=1000, grand_total=1000, lines=[], updated_at="stamp-old"),
        _invoice(id="inv-open", po_id="po-name", invoice_date="2026-01-15", due_date="2026-01-20", sales="Tanpa peta", after_discount=7000, grand_total=7000, lines=[
            {"line_idx": 0, "line_id": "missing", "item_name": "Tanpa identitas", "quantity": 3, "unit": "pcs", "line_subtotal": 7000},
        ], updated_at="stamp-open"),
        _invoice(id="inv-loose", po_id="po-4", invoice_date="2026-03-08", sales="Bukan siapa", after_discount=2000, grand_total=2000, lines=[
            {"line_idx": 0, "line_id": "none", "item_name": "Tanpa identitas", "quantity": 2, "unit": "pcs", "line_subtotal": 2000},
        ], updated_at="stamp-loose"),
        _invoice(id="inv-legacy", po_id="po-done", financial_status="", after_discount=12345, grand_total=12345, updated_at="stamp-legacy"),
    ])
    await db.sales_receipts.insert_many([
        {"id": "rc-1", "invoice_id": "inv-part-1", "amount": 5000, "allocated_amount": 2000, "customer_credit_amount": 3000, "financial_status": "active", "cancelled": False},
        {"id": "rc-dead", "invoice_id": "inv-part-2", "amount": 5000, "allocated_amount": 5000, "financial_status": "cancelled", "cancelled": True},
    ])
    await db.sales_customer_credit_applications.insert_one({
        "id": "app-1", "invoice_id": "inv-part-1", "amount": 2400, "financial_status": "active", "status": "posted",
    })
    await _seed_mapping([{"label": "Ayu", "user_id": "user-ayu"}])
    await db.users.insert_one({"id": "user-ayu", "name": "Ayu Penjualan", "email": "ayu@example.com"})
    before = {row["id"]: row["updated_at"] async for row in db.sales_invoices.find({}, {"id": 1, "updated_at": 1})}
    journals_before = await db[JOURNAL_ENTRIES].count_documents({})

    async with await client_for(_user(perms=["sales_order.view", "dashboard.sales.view"], role="crm")) as client:
        response = await client.get(PATH, params={"start_date": "2026-03-01", "end_date": "2026-03-28", "preset": "month"})
    assert response.status_code == 200, response.text
    body = response.json()
    after = {row["id"]: row["updated_at"] async for row in db.sales_invoices.find({}, {"id": 1, "updated_at": 1})}
    assert after == before
    assert await db[JOURNAL_ENTRIES].count_documents({}) == journals_before

    assert body["omzet"]["amount"] == 11000
    assert body["omzet"]["invoice_count"] == 3
    assert body["omzet"]["grand_total"] == 11400
    assert body["omzet"]["basis"] == "after_discount"
    trend_sum = sum(point["omzet"] for point in body["trend"]["points"])
    assert round(trend_sum, 2) == 11000
    assert sum(point["invoice_count"] for point in body["trend"]["points"]) == 3
    assert body["trend"]["grain"] == "day"
    sales_sum = sum(group["omzet"] for group in body["sales"]["groups"])
    assert round(sales_sum, 2) == 11000
    labels = {group["label"]: group["omzet"] for group in body["sales"]["groups"]}
    assert labels["Ayu Penjualan"] == 9000
    assert labels["Belum ditetapkan"] == 2000
    assert "ayu@example.com" not in response.text

    settlement = body["settlement"]
    assert settlement["settled_count"] == 1
    assert settlement["settled_grand_total"] == 4400
    assert settlement["open_count"] == 2
    assert settlement["open_grand_total"] == 7000
    assert settlement["open_outstanding"] == 7000
    assert all(group["share"] is None for group in body["sales"]["groups"])
    assert round(sum(point["grand_total"] for point in settlement["points"]), 2) == 11400
    assert round(sum(point["settled_grand"] for point in settlement["points"]), 2) == 4400
    assert round(sum(point["outstanding"] for point in settlement["points"]), 2) == 7000

    receivables = body["receivables"]
    assert receivables["historical_as_of"] is False
    assert receivables["as_of_label"] == "Saat ini"
    assert receivables["outstanding"] == 15000
    assert receivables["open_invoice_count"] == 4
    assert receivables["customer_count"] is None
    assert receivables["unidentified_invoice_count"] == 1
    assert "125.430" not in response.text
    assert "150000000" not in response.text

    orders = {row["key"]: row for row in body["orders"]["buckets"]}
    assert body["orders"]["total"] == 7
    assert orders["hold"]["count"] == 1
    assert orders["selesai"]["count"] == 1
    assert orders["cancelled"]["count"] == 1
    assert orders["aktif"]["count"] == 4
    assert orders["aktif"]["href"].endswith("status=aktif")
    assert body["orders"]["scope"] == "Saat ini"

    customers = body["customers"]
    assert customers["repeat_count"] == 1
    assert customers["new_count"] == 0
    assert customers["unidentified_invoice_count"] == 1
    assert customers["percents_available"] is False
    assert customers["new_percent"] is None

    products = body["products"]
    assert products["items"][0]["label"] == "Kardus"
    assert products["items"][0]["amount"] == 9000
    assert products["items"][0]["quantity"] is None
    assert products["unidentified_line_count"] == 1
    assert "Target tercapai" in {row["metric"] for row in body["omitted"]}
    assert "hpp" not in response.text.lower()


@pytest.mark.anyio
async def test_scoped_sales_cannot_see_other_sales_numbers(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="po-a", sales_user_id="sales-a", customer_id="cust-a"),
        _order(id="po-b", sales_user_id="sales-b", customer_id="cust-b"),
    ])
    await db.sales_invoices.insert_many([
        _invoice(id="inv-a", po_id="po-a", sales="Ayu", after_discount=4000, grand_total=4000),
        _invoice(id="inv-b", po_id="po-b", sales="Budi Rahasia", after_discount=9000, grand_total=9000),
    ])
    await _seed_mapping([
        {"label": "Ayu", "user_id": "sales-a"},
        {"label": "Budi Rahasia", "user_id": "sales-b"},
    ])
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user={"id": "sales-a", "user_id": "sales-a", "role": "sales_eternal"},
        permissions=["sales_order.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    assert body["omzet"]["amount"] == 4000
    assert all("Budi Rahasia" not in group["label"] for group in body["sales"]["groups"])
    assert body["receivables"]["outstanding"] == 4000


@pytest.mark.anyio
async def test_wib_today_filter_and_previous_day_comparison(clean_db):
    db = server.db
    await db.orders.insert_one(_order())
    await db.sales_invoices.insert_many([
        _invoice(id="today", invoice_date="2026-10-01", after_discount=3000, grand_total=3000),
        _invoice(id="yesterday", invoice_date="2026-09-30", after_discount=1000, grand_total=1000),
    ])
    body = await get_sales_dashboard(
        db,
        start_date="2026-10-01",
        end_date="2026-10-01",
        preset="today",
        user={"id": "admin", "role": "admin", "user_id": "admin"},
        permissions=["sales_order.view"],
        now=datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc),
    )
    assert body["as_of_date"] == "2026-10-01"
    assert body["omzet"]["amount"] == 3000
    assert body["omzet"]["comparison"]["available"] is True
    assert body["omzet"]["comparison"]["previous_amount"] == 1000
    assert body["omzet"]["comparison"]["direction"] == "up"
    assert len(body["trend"]["points"]) == 1
    assert body["receivables"]["outstanding"] == 4000


@pytest.mark.anyio
async def test_zero_previous_has_no_percent_and_empty_is_not_zero(clean_db):
    db = server.db
    await db.orders.insert_one(_order())
    await db.sales_invoices.insert_one(_invoice(invoice_date="2026-03-02", after_discount=2500, grand_total=2500))
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-02",
        end_date="2026-03-02",
        preset="today",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 2, 3, tzinfo=timezone.utc),
    )
    assert body["omzet"]["comparison"]["available"] is True
    assert body["omzet"]["comparison"]["previous_amount"] == 0
    assert body["omzet"]["comparison"]["percent"] is None
    assert body["omzet"]["comparison"]["direction"] is None
    empty = await get_sales_dashboard(
        db,
        start_date="2026-04-01",
        end_date="2026-04-01",
        preset="custom",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert empty["omzet"]["status"] == "empty"
    assert empty["omzet"]["amount"] is None
    assert empty["trend"]["detail"] == "Belum ada faktur pada periode ini"
    assert empty["omzet"]["comparison"]["percent"] is None


@pytest.mark.anyio
async def test_custom_and_unequal_month_have_no_comparison(clean_db):
    db = server.db
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="month",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 31, 3, tzinfo=timezone.utc),
    )
    assert body["omzet"]["comparison"]["available"] is False
    custom = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-15",
        preset="custom",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    assert custom["omzet"]["comparison"]["available"] is False


@pytest.mark.anyio
async def test_section_failure_does_not_zero_other_metrics(clean_db, monkeypatch):
    db = server.db
    await db.orders.insert_one(_order())
    await db.sales_invoices.insert_one(_invoice(after_discount=4200, grand_total=4200))

    def boom(*_args, **_kwargs):
        raise RuntimeError("produk gagal")

    monkeypatch.setattr("services.sales_dashboard._build_products", boom)
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    assert body["products"]["status"] == "error"
    assert body["products"].get("amount") is None
    assert body["omzet"]["amount"] == 4200
    assert body["omzet"]["status"] == "complete"


@pytest.mark.anyio
async def test_invalid_amount_is_not_reported_as_zero(clean_db):
    db = server.db
    await db.orders.insert_one(_order())
    await db.sales_invoices.insert_one(_invoice(after_discount="bukan-angka", grand_total=1000))
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    assert body["omzet"]["status"] == "error"
    assert body["omzet"]["amount"] is None


@pytest.mark.anyio
async def test_orders_without_list_permission_have_no_href(clean_db):
    db = server.db
    await db.orders.insert_one(_order())
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user={"role": "crm", "id": "crm", "user_id": "crm"},
        permissions=["dashboard.sales.view"],
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    assert all(row["href"] is None for row in body["orders"]["buckets"])


@pytest.mark.anyio
async def test_customer_identity_does_not_merge_names_or_split_ids(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="po-1", customer_id="cust-1", customer="Kedai Tuman"),
        _order(id="po-2", customer_id="cust-1", customer="kedai   tuman"),
        _order(id="po-3", customer_id="cust-2", customer="Kedai Tuman"),
        _order(id="po-4", customer_id="Kedai Tuman|Yogyakarta", customer="Kedai Tuman", outlet="Yogyakarta"),
    ])
    await db.sales_invoices.insert_many([
        _invoice(id="a", po_id="po-1", invoice_date="2026-03-05", after_discount=1000, grand_total=1000),
        _invoice(id="b", po_id="po-2", invoice_date="2026-03-06", after_discount=1000, grand_total=1000),
        _invoice(id="c", po_id="po-3", invoice_date="2026-03-07", after_discount=1000, grand_total=1000),
        _invoice(id="d", po_id="po-4", invoice_date="2026-03-08", after_discount=1000, grand_total=1000),
    ])
    body = await get_sales_dashboard(
        db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user={"role": "admin", "id": "admin", "user_id": "admin"},
        permissions=[],
        now=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    assert body["customers"]["identified_count"] == 2
    assert body["customers"]["new_count"] == 2
    assert body["customers"]["repeat_count"] == 0
    assert body["customers"]["unidentified_invoice_count"] == 1
    assert body["customers"]["percents_available"] is False


def test_load_receipts_projection_includes_settlement_effective():
    """Inclusion projection must keep settlement_effective for AR semantics."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "services" / "sales_dashboard.py"
    text = src.read_text(encoding="utf-8")
    start = text.index("async def _load_receipts")
    chunk = text[start:start + 500]
    assert '"settlement_effective": 1' in chunk or "'settlement_effective': 1" in chunk
    assert '"financial_status": 1' in chunk or "'financial_status': 1" in chunk


@pytest.mark.anyio
async def test_settlement_effective_false_does_not_reduce_receivables(clean_db):
    """Pending auto-lifecycle receipts must not count as paid on the dashboard."""
    db = server.db
    await db.orders.insert_one(_order(id="po-pend", customer_id="cust-pend", customer="Pending Cust"))
    await db.sales_invoices.insert_one(
        _invoice(
            id="inv-pend",
            po_id="po-pend",
            invoice_date="2026-03-10",
            due_date="2026-03-24",
            after_discount=10000,
            grand_total=10000,
            ppn_amount=0,
        )
    )
    await db.sales_receipts.insert_one({
        "id": "rc-pending-settle",
        "invoice_id": "inv-pend",
        "amount": 10000,
        "allocated_amount": 10000,
        "financial_status": "active",
        "cancelled": False,
        "settlement_effective": False,
    })
    async with await client_for(_user()) as client:
        response = await client.get(
            PATH,
            params={"start_date": "2026-03-01", "end_date": "2026-03-31", "preset": "custom"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["receivables"]["outstanding"] == 10000
    assert body["settlement"]["open_outstanding"] == 10000
    assert body["settlement"]["settled_count"] == 0
