"""Read-only widget aggregations. Isolated database. No production writes."""
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
os.environ["DB_NAME"] = f"eternal_packaging_widget_reads_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounts_receivable import aggregate_overdue_customers
from services.financial_dashboard import _performance
from services.purchase_period_summary import get_purchase_period_summary
from services.sales_dashboard import get_sales_dashboard
from services.widget_profit_loss import get_widget_profit_loss

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def local_branch_runtime(monkeypatch):
    """Keep unassigned admin fixtures on the local branch bypass."""
    monkeypatch.setenv("APP_ENV", "test")


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    names = (
        "orders", "sales_invoices", "sales_receipts", "sales_customer_credit_applications",
        "users", "app_settings", "purchase_invoices", "purchase_payments",
        "cash_bank_reconciliations", "journal_entries", "journal_lines", "ledger_accounts",
    )
    for name in names:
        await server.db[name].delete_many({})
    yield
    for name in names:
        await server.db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="admin", override=None):
    return {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "email": "widget@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": "u-widget",
        "permissions_override": override or {},
    }


def _order(**extra):
    doc = {
        "id": extra.pop("id", "po-1"),
        "po_number": "PO-1",
        "cancelled": False,
        "current_stage": 6,
        "customer_id": "cust-1",
        "customer": "Kedai Tuman",
        "sales_user_id": "sales-a",
        "line_items": [],
    }
    doc.update(extra)
    return doc


def _invoice(**extra):
    doc = {
        "id": extra.pop("id", uuid.uuid4().hex),
        "invoice_number": "INV-1",
        "invoice_date": "2026-10-04",
        "due_date": "2026-10-18",
        "po_id": "po-1",
        "financial_status": "finalized",
        "is_cancelled": False,
        "sales": "",
        "after_discount": 10000,
        "grand_total": 11000,
        "lines": [],
    }
    doc.update(extra)
    return doc


async def test_customer_rank_matches_omzet_scope_and_ignores_name(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="po-a", customer_id="cust-1", customer="Kedai Tuman", sales_user_id="sales-a", line_items=[
            {"line_idx": 0, "line_id": "l0", "accurate_item_id": "item-1", "name": "Kardus"},
        ]),
        _order(id="po-b", customer_id="cust-1", customer="kedai tuman", sales_user_id="sales-a"),
        _order(id="po-c", customer_id="cust-2", customer="Kedai Tuman", sales_user_id="sales-a"),
        _order(id="po-d", customer_id="Kedai Tuman|Yogyakarta", customer="Kedai Tuman", outlet="Yogyakarta", sales_user_id="sales-a"),
        _order(id="po-other", customer_id="cust-9", customer="Pelanggan Lain", sales_user_id="sales-b"),
    ])
    await db.sales_invoices.insert_many([
        _invoice(id="final-a", po_id="po-a", invoice_date="2026-10-04T16:00:00+00:00", after_discount=4000, grand_total=4000, lines=[
            {"line_idx": 0, "line_id": "l0", "line_subtotal": 4500, "quantity": 2, "unit": "pcs", "item_name": "Kardus"},
        ]),
        _invoice(id="final-b", po_id="po-b", invoice_date="2026-10-01", after_discount=2500, grand_total=2500),
        _invoice(id="same-name", po_id="po-c", invoice_date="2026-10-02", after_discount=1000, grand_total=1000),
        _invoice(id="no-id", po_id="po-d", invoice_date="2026-10-03", after_discount=9000, grand_total=9000),
        _invoice(id="draft", po_id="po-a", financial_status="draft", after_discount=8000, grand_total=8000),
        _invoice(id="cancelled", po_id="po-a", is_cancelled=True, after_discount=7000, grand_total=7000),
        _invoice(id="next-day", po_id="po-a", invoice_date="2026-10-05", after_discount=6000, grand_total=6000),
        _invoice(id="other-sales", po_id="po-other", invoice_date="2026-10-04", after_discount=5000, grand_total=5000),
    ])
    user = {"id": "sales-a", "user_id": "sales-a", "role": "sales_eternal"}
    body = await get_sales_dashboard(
        db,
        start_date="2026-10-01",
        end_date="2026-10-04",
        preset="custom",
        user=user,
        permissions=["dashboard.sales.view"],
        now=datetime(2026, 10, 4, 5, tzinfo=timezone.utc),
    )
    assert body["omzet"]["amount"] == 16500
    rank = body["customer_rank"]
    assert rank["status"] == "incomplete"
    assert rank["unidentified_invoice_count"] == 1
    by_key = {row["key"]: row for row in rank["groups"]}
    assert set(by_key) == {"cust-1", "cust-2"}
    assert by_key["cust-1"]["amount"] == 6500
    assert by_key["cust-1"]["invoice_count"] == 2
    assert by_key["cust-2"]["amount"] == 1000
    assert by_key["cust-2"]["invoice_count"] == 1
    assert rank["identified_amount"] == 7500
    assert body["products"]["items"][0]["amount"] == 4500
    assert body["products"]["all_items"][0]["amount"] == body["products"]["items"][0]["amount"]
    source = Path(__file__).resolve().parents[1] / "services" / "purchase_period_summary.py"
    assert "to_list(1000)" not in source.read_text()


async def test_partial_settlement_uses_full_grand_and_sales_share(clean_db):
    db = server.db
    await db.orders.insert_many([
        _order(id="po-part", customer_id="cust-1", sales_user_id="sales-a", line_items=[
            {"line_idx": 0, "line_id": "l0", "accurate_item_id": "item-1", "name": "Besar"},
            {"line_idx": 1, "line_id": "l1", "accurate_item_id": "item-2", "name": "Kecil"},
        ]),
        _order(id="po-paid", customer_id="cust-1", sales_user_id="sales-a"),
        _order(id="po-open", customer_id="cust-2", sales_user_id="sales-a"),
    ])
    await db.users.insert_one({"id": "sales-a", "name": "Ayu", "email": "ayu-widget@example.com"})
    await db.app_settings.insert_one({"key": "sales_user_mapping", "entries": [{"label": "Ayu", "user_id": "sales-a"}]})
    await db.sales_invoices.insert_many([
        _invoice(id="part", po_id="po-part", invoice_date="2026-10-02", sales="Ayu", after_discount=1000, grand_total=1000, lines=[
            {"line_idx": 0, "line_id": "l0", "line_subtotal": 900, "quantity": 1, "unit": "pcs", "item_name": "Besar"},
            {"line_idx": 1, "line_id": "l1", "line_subtotal": 100, "quantity": 1, "unit": "pcs", "item_name": "Kecil"},
        ]),
        _invoice(id="paid", po_id="po-paid", invoice_date="2026-10-03", sales="Ayu", after_discount=500, grand_total=500),
        _invoice(id="open", po_id="po-open", invoice_date="2026-10-04", sales="Ayu", after_discount=200, grand_total=200),
    ])
    await db.sales_receipts.insert_many([
        {"id": "rc-part", "invoice_id": "part", "amount": 400, "allocated_amount": 400, "financial_status": "active", "cancelled": False},
        {"id": "rc-paid", "invoice_id": "paid", "amount": 500, "allocated_amount": 500, "financial_status": "active", "cancelled": False},
        {"id": "rc-void", "invoice_id": "open", "amount": 200, "allocated_amount": 200, "financial_status": "cancelled", "cancelled": True},
    ])
    body = await get_sales_dashboard(
        db,
        start_date="2026-10-01",
        end_date="2026-10-04",
        preset="custom",
        user={"id": "admin-widget", "user_id": "admin-widget", "role": "admin"},
        permissions=["dashboard.sales.view"],
        now=datetime(2026, 10, 4, 5, tzinfo=timezone.utc),
    )
    settlement = body["settlement"]
    assert settlement["settled_grand_total"] == 500
    assert settlement["open_grand_total"] == 1200
    assert settlement["open_outstanding"] == 800
    assert settlement["settled_grand_total"] + settlement["open_grand_total"] == 1700
    assert settlement["open_grand_total"] != settlement["open_outstanding"]
    shares = [group["share"] for group in body["sales"]["groups"]]
    assert shares == [100.0]
    assert body["sales"]["status"] == "complete"
    products = {row["label"]: row for row in body["products"]["all_items"]}
    assert products["Besar"]["share"] == 90.0
    assert products["Kecil"]["share"] == 10.0
    assert body["products"]["items"][0]["share"] == products["Besar"]["share"]


async def test_purchase_period_uses_invoice_value_not_cash(clean_db):
    db = server.db
    await db.purchase_invoices.insert_many([
        {"id": "paid", "invoice_date": "2026-10-02", "grand_total": 100, "cancelled": False},
        {"id": "dp", "invoice_date": "2026-10-04", "grand_total": 80, "cancelled": False},
        {"id": "open", "invoice_date": "2026-10-04T10:00:00", "grand_total": 50, "cancelled": False},
        {"id": "void", "invoice_date": "2026-10-03", "grand_total": 999, "cancelled": True},
        {"id": "outside", "invoice_date": "2026-10-05", "grand_total": 70, "cancelled": False},
    ])
    await db.purchase_payments.insert_many([
        {"id": "p1", "purchase_invoice_id": "paid", "amount": 100, "settlement_status": "PAID", "cancelled": False},
        {"id": "p2", "purchase_invoice_id": "dp", "amount": 30, "settlement_status": "PAID", "cancelled": False},
        {"id": "p3", "purchase_invoice_id": "open", "amount": 50, "settlement_status": "PAID", "cancelled": True},
        {"id": "p4", "purchase_invoice_id": "open", "amount": 50, "settlement_status": "UNPAID", "cancelled": False},
    ])
    body = await get_purchase_period_summary(db, start_date="2026-10-01", end_date="2026-10-04")
    assert body["amount"] == 230
    assert body["paid_amount"] == 100
    assert body["unpaid_amount"] == 130
    assert body["paid_count"] == 1
    assert body["unpaid_count"] == 2
    assert body["paid_amount"] + body["unpaid_amount"] == body["amount"]


async def test_purchase_and_recon_permissions(clean_db):
    finance_only = _user(override={"purchase_invoices.view": False, "cash_bank.reconcile": False, "accounts_receivable.view": False})
    app.dependency_overrides[current_user] = lambda: finance_only
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        purchase = await client.get("/api/dashboard/pembelian", params={"start_date": "2026-10-01", "end_date": "2026-10-04"})
        recon = await client.get("/api/dashboard/widget/reconciliations")
        overdue = await client.get("/api/ledger/accounts-receivable/overdue-customers")
    assert purchase.status_code == 403
    assert recon.status_code == 403
    assert overdue.status_code == 403
    profit_denied = _user(role="widget_reader", override={"dashboard.finance.view": True})
    app.dependency_overrides[current_user] = lambda: profit_denied
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        finance_pl = await client.get("/api/dashboard/widget/laba-rugi", params={"start_date": "2026-10-01", "end_date": "2026-10-04"})
    assert finance_pl.status_code == 403
    profit_allowed = _user(role="widget_reader", override={"profit_loss.view": True})
    app.dependency_overrides[current_user] = lambda: profit_allowed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        profit_ok = await client.get("/api/dashboard/widget/laba-rugi", params={"start_date": "2026-10-01", "end_date": "2026-10-04"})
    assert profit_ok.status_code == 200
    denied = _user(role="widget_reader", override={})
    app.dependency_overrides[current_user] = lambda: denied
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.get("/api/delivery/schedule")
    assert blocked.status_code == 403
    for key in ("nav.delivery_schedule", "schedule.view", "delivery.schedule.view"):
        app.dependency_overrides[current_user] = lambda key=key: _user(role="widget_reader", override={key: True})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            allowed_schedule = await client.get("/api/delivery/schedule")
        assert allowed_schedule.status_code == 200, key
    stripped_admin = _user(role="admin", override={
        "delivery.schedule.view": False,
        "schedule.view": False,
        "nav.delivery_schedule": False,
        "po.view": False,
        "dashboard.sales.view": False,
        "dashboard.finance.view": False,
    })
    app.dependency_overrides[current_user] = lambda: stripped_admin
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        admin_schedule = await client.get("/api/delivery/schedule")
        admin_today = await client.get("/api/dashboard/today")
        admin_production = await client.get("/api/dashboard/production")
        admin_sales = await client.get("/api/dashboard/penjualan", params={"start_date": "2026-10-01", "end_date": "2026-10-04"})
    assert admin_schedule.status_code == 200
    assert admin_today.status_code == 403
    assert admin_production.status_code == 403
    assert admin_sales.status_code == 403
    app.dependency_overrides[current_user] = lambda: _user()
    await server.db.cash_bank_reconciliations.insert_one({
        "id": "recon-1",
        "number": "RK-1",
        "cash_bank_name": "Kas BCA",
        "reconciliation_date": "2026-10-04",
        "statement_balance": 1500,
        "book_balance": 1000,
        "status": "finalized",
    })
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.get("/api/dashboard/pembelian", params={"start_date": "2026-10-01", "end_date": "2026-10-04"})
        listed = await client.get("/api/dashboard/widget/reconciliations")
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "empty"
    assert listed.status_code == 200
    row = listed.json()["items"][0]
    assert row["status"] == "finalized"
    assert row["difference"] == 500
    await server.db.cash_bank_reconciliations.delete_many({})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.get("/api/dashboard/widget/reconciliations")
    assert empty.json()["status"] == "empty"
    assert empty.json()["items"] == []


async def test_widget_recon_separates_active_pairs_from_legacy(clean_db):
    db = server.db
    await db.cash_bank_reconciliation_matches.delete_many({})
    await db.cash_bank_accounts.delete_many({"id": "cb-uji"})
    pair = {
        "id": "pair-active",
        "match_kind": "pair",
        "status": "active",
        "cash_bank_id": "cb-uji",
        "date": "2026-10-20",
        "direction": "in",
        "amount": 999,
        "journal_line_id": "jl-secret",
        "statement_line_id": "st-secret",
        "description": "rahasia",
    }
    await db.cash_bank_accounts.insert_one({"id": "cb-uji", "name": "Kas Uji"})
    await db.cash_bank_reconciliation_matches.insert_many([
        pair,
        {"id": "pair-off", "match_kind": "pair", "status": "cancelled", "cash_bank_id": "cb-uji", "date": "2026-10-01"},
        {"id": "group-1", "match_kind": "pair_group", "status": "active", "cash_bank_id": "cb-uji", "date": "2026-10-02"},
    ])
    before = await db.cash_bank_reconciliation_matches.find_one({"id": "pair-active"}, {"_id": 0})
    app.dependency_overrides[current_user] = lambda: _user()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pairs_only = await client.get("/api/dashboard/widget/reconciliations")
    assert pairs_only.status_code == 200
    body = pairs_only.json()
    assert body["status"] == "complete"
    assert body["items"] == []
    assert [row["id"] for row in body["pairs"]] == ["pair-active"]
    assert body["pairs"][0]["cash_bank_name"] == "Kas Uji"
    assert "journal_line_id" not in body["pairs"][0]
    assert "statement_line_id" not in body["pairs"][0]
    assert "amount" not in body["pairs"][0]
    assert "description" not in body["pairs"][0]
    after = await db.cash_bank_reconciliation_matches.find_one({"id": "pair-active"}, {"_id": 0})
    assert after == before

    await db.cash_bank_reconciliations.insert_one({
        "id": "recon-uji",
        "number": "RK-UJI",
        "cash_bank_name": "Kas Lama",
        "reconciliation_date": "2026-10-04",
        "statement_balance": 10,
        "book_balance": 4,
        "status": "draft",
    })
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        both = await client.get("/api/dashboard/widget/reconciliations")
    both_body = both.json()
    assert both_body["items"][0]["number"] == "RK-UJI"
    assert both_body["pairs"][0]["id"] == "pair-active"
    assert both_body["items"][0]["id"] != both_body["pairs"][0]["id"]

    await db.cash_bank_reconciliation_matches.delete_many({})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        legacy_only = await client.get("/api/dashboard/widget/reconciliations")
    assert legacy_only.json()["pairs"] == []
    assert legacy_only.json()["items"][0]["status"] == "draft"

    await db.cash_bank_reconciliations.delete_many({})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.get("/api/dashboard/widget/reconciliations")
    assert empty.json()["status"] == "empty"
    assert empty.json()["items"] == []
    assert empty.json()["pairs"] == []

    denied = _user(role="widget_reader", override={"cash_bank.reconcile": False})
    app.dependency_overrides[current_user] = lambda: denied
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.get("/api/dashboard/widget/reconciliations")
    assert blocked.status_code == 403
    await db.cash_bank_accounts.delete_many({"id": "cb-uji"})


def test_overdue_groups_match_source_rows():
    rows = [
        {"customer_key": "id:cust-1", "customer_name": "Kedai A", "accounting_outstanding": 40, "days_overdue": 5, "ambiguous": False},
        {"customer_key": "id:cust-1", "customer_name": "Kedai A", "accounting_outstanding": 10, "days_overdue": 12, "ambiguous": False},
        {"customer_key": "ambiguous:kedai", "customer_name": "Kedai A", "accounting_outstanding": 7, "days_overdue": 9, "customer_identity_ambiguous": True},
        {"customer_key": "id:cust-2", "customer_name": "Kedai A", "accounting_outstanding": 3, "days_overdue": 2, "ambiguous": False},
        {"customer_key": "", "customer_name": "", "accounting_outstanding": 8, "days_overdue": 4},
        {"customer_key": "id:cust-1", "customer_name": "Kedai A", "accounting_outstanding": 100, "days_overdue": 0},
        {"customer_key": "id:cust-3", "customer_name": "Nol", "accounting_outstanding": 0, "days_overdue": 20},
    ]
    body = aggregate_overdue_customers(rows, aging_reference_date="2026-10-04")
    source = {}
    for row in rows:
        if row["accounting_outstanding"] <= 0 or row["days_overdue"] <= 0 or not row["customer_key"]:
            continue
        slot = source.setdefault(row["customer_key"], {"amount": 0, "days": 0})
        slot["amount"] += row["accounting_outstanding"]
        slot["days"] = max(slot["days"], row["days_overdue"])
    assert {item["customer_key"]: item["accounting_outstanding"] for item in body["items"]} == {
        key: slot["amount"] for key, slot in source.items()
    }
    by_key = {item["customer_key"]: item for item in body["items"]}
    assert by_key["id:cust-1"]["days_overdue"] == 12
    assert by_key["ambiguous:kedai"]["ambiguous"] is True
    assert by_key["id:cust-2"]["customer_key"] != by_key["ambiguous:kedai"]["customer_key"]
    assert body["unidentified_invoice_count"] == 1
    assert body["status"] == "incomplete"
    assert body["historical_as_of"] is False


async def test_widget_profit_matches_finance_performance(clean_db):
    from services.chart_of_accounts import create_account

    suffix = f"{uuid.uuid4().int % 10000:04d}"
    bank = await create_account(server.db, {"code": f"11{suffix}", "name": "Kas widget", "account_type": "BANK"}, user=_user())
    revenue = await create_account(server.db, {"code": f"41{suffix}", "name": "Pendapatan widget", "account_type": "REVE"}, user=_user())
    app.dependency_overrides[current_user] = lambda: _user()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/ledger/general-journals", json={
            "date": "2026-10-06",
            "description": "Pendapatan widget",
            "lines": [
                {"account_id": bank["id"], "debit": 30000, "credit": 0, "description": "Dr"},
                {"account_id": revenue["id"], "debit": 0, "credit": 30000, "description": "Cr"},
            ],
        })
        assert created.status_code == 201, created.text
        posted = await client.post(f"/api/ledger/general-journals/{created.json()['id']}/post")
        assert posted.status_code == 200, posted.text
    widget = await get_widget_profit_loss(server.db, start_date="2026-10-01", end_date="2026-10-31")
    source = await _performance(server.db, "2026-10-01", "2026-10-31")
    assert widget["revenue"] == source["revenue"] == 30000
    assert widget["net_profit"] == source["net_profit"]
    assert widget["status"] == source["status"] == "complete"
    await server.db.journal_entries.insert_one({
        "id": "je-widget-incomplete",
        "date": "2026-10-16",
        "status": "incomplete",
        "source_type": "general_journal",
        "reversal_of": "je-original-widget",
        "reversal_intent": {"operation_id": "op-widget", "phase": "header_written"},
    })
    await server.db.journal_lines.insert_one({
        "id": "jl-widget-incomplete",
        "journal_id": "je-widget-incomplete",
        "account_id": revenue["id"],
        "debit": 0,
        "credit": 99999,
    })
    widget_bad = await get_widget_profit_loss(server.db, start_date="2026-10-01", end_date="2026-10-31")
    source_bad = await _performance(server.db, "2026-10-01", "2026-10-31")
    assert widget_bad["status"] == source_bad["status"] == "incomplete"
    assert widget_bad["revenue"] == source_bad["revenue"] == 30000
