"""Sales omzet branch scope. Isolated fixtures, APP_ENV=production.

Proves the card, trend, and omzet rincian share one invoice population:
permission + operational branch + sales ownership + period + finalized status.
Does not touch a production database.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TEST_DB_PREFIX = "eternal_packaging_sales_omzet_scope_test_"
_LOCAL_MONGO = "mongodb://127.0.0.1:27017"
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"


def _guard_test_mongo():
    """Refuse a non-local host or a database outside this test prefix.

    Does not print the URL. A pre-set MONGO_URL is accepted only when its
    host is loopback; otherwise the process stops before any fixture write.
    """
    url = os.environ.get("MONGO_URL") or ""
    if not url:
        os.environ["MONGO_URL"] = _LOCAL_MONGO
        url = _LOCAL_MONGO
    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Tes ditolak: host Mongo di luar lingkungan tes lokal")
    name = os.environ.get("DB_NAME") or ""
    if not name.startswith(_TEST_DB_PREFIX):
        raise RuntimeError("Tes ditolak: nama database di luar lingkungan tes")


if not os.environ.get("MONGO_URL"):
    os.environ["MONGO_URL"] = _LOCAL_MONGO
os.environ["DB_NAME"] = f"{_TEST_DB_PREFIX}{_worker}"
_guard_test_mongo()
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.dashboard_rincian import query_dashboard_rincian
from services.operational_branch import operational_query
from services.owner_analytics import get_owner_analytics
from services.sales_dashboard import get_sales_dashboard, wib_today
from services.sales_user_mapping import sales_owner_match

pytestmark = pytest.mark.anyio

YGY = "br-omzet-ygy"
SMG = "br-omzet-smg"
SBY = "br-omzet-sby"
ACCURATE_YGY = "91001"
START = "2026-03-01"
END = "2026-03-31"
SALES_PATH = "/api/dashboard/penjualan"
RINCIAN_PATH = "/api/dashboard/rincian"
DATA_PERMS = {
    "dashboard.sales.view",
    "dashboard.finance.view",
    "sales.invoice.view",
    "sales_order.view",
    "po.view",
}


def _user(user_id, role, branch_ids=None, **perms):
    user = {
        "id": user_id,
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "name": user_id,
        "role": role,
        "status": "active",
    }
    if branch_ids is not None:
        user["branch_ids"] = list(branch_ids)
    if perms:
        user["permissions_override"] = perms
    return user


VIEW = {
    "dashboard.sales.view": True,
    "sales.invoice.view": True,
    "dashboard.finance.view": True,
    "po.view": True,
}


def _order(oid, branch_id, sales_user_id):
    return {
        "id": oid,
        "po_number": oid.upper(),
        "cancelled": False,
        "operational_branch_id": branch_id,
        "sales_user_id": sales_user_id,
        "customer_id": f"cust-{oid}",
        "customer": oid,
        "current_stage": 6,
    }


def _invoice(iid, po_id, operational_branch, amount, **extra):
    doc = {
        "id": iid,
        "invoice_number": iid.upper(),
        "invoice_date": "2026-03-10",
        "po_id": po_id,
        "financial_status": "finalized",
        "is_cancelled": False,
        "operational_branch_id": operational_branch,
        "after_discount": amount,
        "grand_total": None if amount is None else int(amount) + 1000,
        "customer_name": iid,
    }
    doc.update(extra)
    return doc


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")


@pytest.fixture
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def scoped_db(bind_motor):
    _guard_test_mongo()
    name = os.environ["DB_NAME"]
    if not name.startswith(_TEST_DB_PREFIX):
        raise RuntimeError("Tes ditolak: nama database di luar lingkungan tes")
    previous = server.db
    db = server.client[name]
    server.db = db
    await db.orders.delete_many({})
    await db.sales_invoices.delete_many({})
    await db.sales_receipts.delete_many({})
    await db.sales_customer_credit_applications.delete_many({})
    await db.branches.delete_many({})
    await db.users.delete_many({})
    await db.roles.delete_many({})
    await db.branches.insert_many([
        {"id": YGY, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": SMG, "code": "SMG", "name": "Semarang", "active": True},
        {"id": SBY, "code": "SBY", "name": "Surabaya", "active": True},
    ])
    await db.orders.insert_many([
        _order("po-y1", YGY, "sales-a"),
        _order("po-y2", YGY, "sales-b"),
        _order("po-s1", SMG, "sales-a"),
    ])
    await db.sales_invoices.insert_many([
        _invoice("inv-y1", "po-y1", YGY, 4000, branch_id=ACCURATE_YGY),
        _invoice("inv-y2", "po-y2", YGY, 6000),
        _invoice("inv-s1", "po-s1", SMG, 7000),
        _invoice("inv-draft", "po-y1", YGY, 5000, financial_status="draft"),
        _invoice("inv-cancel", "po-y1", YGY, 8000, is_cancelled=True),
        _invoice("inv-status-cancel", "po-y2", YGY, 8000, financial_status="cancelled"),
        _invoice("inv-before", "po-y1", YGY, 9000, invoice_date="2026-02-28"),
        _invoice("inv-after", "po-y1", YGY, 3000, invoice_date="2026-04-01"),
        _invoice("inv-blank", "po-y1", YGY, 12345, financial_status=""),
        _invoice("inv-sby", "po-y1", SBY, 2000, invoice_date="2026-11-15"),
    ])
    yield db
    await db.orders.delete_many({})
    await db.sales_invoices.delete_many({})
    await db.sales_receipts.delete_many({})
    await db.sales_customer_credit_applications.delete_many({})
    await db.branches.delete_many({})
    await db.users.delete_many({})
    server.db = previous
    app.dependency_overrides.pop(current_user, None)


def _bind(user):
    app.dependency_overrides[current_user] = lambda: user


async def _card(db, user, start=START, end=END, preset="custom"):
    return await get_sales_dashboard(
        db,
        start_date=start,
        end_date=end,
        preset=preset,
        user=user,
        permissions=sorted(DATA_PERMS),
    )


async def _detail(db, user, start=START, end=END, widget="omzet", dashboard="penjualan", page=1, page_size=50, q=""):
    return await query_dashboard_rincian(
        db,
        widget=widget,
        dashboard=dashboard,
        permissions=DATA_PERMS,
        q=q,
        page=page,
        page_size=page_size,
        start_date=start,
        end_date=end,
        sales_scope=sales_owner_match(user),
        branch_scope=await operational_query(db, user),
    )


def _amounts(card, detail):
    omzet = card["omzet"]["amount"]
    trend = round(sum(point["omzet"] for point in card["trend"]["points"]), 2)
    kpi = detail["kpi_summary"]["amount"]
    return omzet, trend, kpi


async def test_yogyakarta_semarang_and_global_share_one_population(scoped_db):
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    smg = _user("u-smg", "crm", [SMG], **VIEW)
    both = _user("u-both", "crm", [YGY, SMG], **VIEW)
    director = _user("u-dir", "direktur_utama")

    y_card, y_detail = await _card(scoped_db, ygy), await _detail(scoped_db, ygy)
    s_card, s_detail = await _card(scoped_db, smg), await _detail(scoped_db, smg)
    m_card, m_detail = await _card(scoped_db, both), await _detail(scoped_db, both)
    g_card, g_detail = await _card(scoped_db, director), await _detail(scoped_db, director)

    assert _amounts(y_card, y_detail) == (10000, 10000, 10000)
    y_settle = await _detail(scoped_db, ygy, widget="settlement")
    g_settle = await _detail(scoped_db, director, widget="settlement")
    assert y_card["settlement"]["open_grand_total"] == 12000
    assert y_card["settlement"]["settled_grand_total"] == 0
    assert y_settle["kpi_summary"]["amount"] == 12000
    assert y_settle["kpi_summary"]["basis"] == "grand_total"
    assert y_settle["kpi_summary"]["open_grand_total"] == 12000
    assert y_settle["kpi_summary"]["settled_grand_total"] == 0
    assert y_settle["kpi_summary"]["comparable_to_card"] is True
    assert sum(row["grand_total"] for row in y_settle["rows"]) == 12000
    assert g_card["settlement"]["open_grand_total"] == 20000
    assert g_settle["kpi_summary"]["amount"] == 20000
    assert g_settle["kpi_summary"]["basis"] == "grand_total"
    assert y_detail["total"] == 2
    assert {row["id"] for row in y_detail["rows"]} == {"inv-y1", "inv-y2"}
    assert _amounts(s_card, s_detail) == (7000, 7000, 7000)
    assert {row["id"] for row in s_detail["rows"]} == {"inv-s1"}
    assert _amounts(m_card, m_detail) == (17000, 17000, 17000)
    assert m_detail["total"] == 3
    assert _amounts(g_card, g_detail) == (17000, 17000, 17000)
    assert g_card["omzet"]["basis"] == "after_discount"
    assert g_card["omzet"]["grand_total"] == 20000
    assert y_detail["kpi_summary"]["comparable_to_card"] is True

    owner = await get_owner_analytics(
        scoped_db,
        start_date=START,
        end_date=END,
        preset="custom",
        user=director,
        permissions=sorted(DATA_PERMS | {"dashboard.owner.view"}),
    )
    assert owner["branch_filter"] is None
    assert owner["omzet"]["amount"] == 17000

    finance = await _detail(scoped_db, ygy, widget="sales", dashboard="keuangan")
    finance_global = await _detail(scoped_db, director, widget="sales", dashboard="keuangan")
    assert finance["kpi_summary"]["amount"] == 10000
    assert finance["kpi_summary"]["basis"] == "after_discount"
    assert finance_global["kpi_summary"]["amount"] == 17000
    assert finance_global["kpi_summary"]["basis"] == "after_discount"


async def test_unassigned_non_global_is_denied_and_url_cannot_widen(scoped_db):
    naked = _user("u-naked", "crm", **VIEW)
    with pytest.raises(HTTPException) as raised:
        await _card(scoped_db, naked)
    assert raised.value.status_code == 403
    assert "17000" not in str(raised.value.detail)

    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    _bind(ygy)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        widened = await client.get(SALES_PATH, params={
            "start_date": START,
            "end_date": END,
            "preset": "custom",
            "branch_id": SMG,
            "operational_branch_id": SMG,
        })
        assert widened.status_code == 200, widened.text
        assert widened.json()["omzet"]["amount"] == 10000
        detail = await client.get(RINCIAN_PATH, params={
            "widget": "omzet",
            "dashboard": "penjualan",
            "start_date": START,
            "end_date": END,
            "branch_id": SMG,
            "operational_branch_id": SBY,
        })
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["kpi_summary"]["amount"] == 10000
        assert body["total"] == 2
        assert {row["id"] for row in body["rows"]} == {"inv-y1", "inv-y2"}

    _bind(naked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get(SALES_PATH, params={"start_date": START, "end_date": END, "preset": "custom"})
        assert denied.status_code == 403
        assert "inv-s1" not in denied.text
        rincian_denied = await client.get(RINCIAN_PATH, params={
            "widget": "omzet",
            "dashboard": "penjualan",
            "start_date": START,
            "end_date": END,
        })
        assert rincian_denied.status_code == 403

    operator = _user("u-op", "operator", [YGY])
    _bind(operator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        no_module = await client.get(SALES_PATH, params={"start_date": START, "end_date": END, "preset": "custom"})
        assert no_module.status_code == 403
        no_detail = await client.get(RINCIAN_PATH, params={"widget": "omzet", "dashboard": "penjualan"})
        assert no_detail.status_code == 403


async def test_sales_owner_and_branch_apply_together(scoped_db):
    sales = _user("sales-a", "sales_eternal", [YGY], **VIEW)
    card = await _card(scoped_db, sales)
    detail = await _detail(scoped_db, sales)
    assert _amounts(card, detail) == (4000, 4000, 4000)
    assert {row["id"] for row in detail["rows"]} == {"inv-y1"}
    settlement = await _detail(scoped_db, sales, widget="settlement")
    assert settlement["kpi_summary"]["amount"] == 5000
    assert {row["id"] for row in settlement["rows"]} == {"inv-y1"}
    assert settlement["rows"][0]["grand_total"] == 5000

    other_branch = _user("sales-a", "sales_eternal", [SMG], **VIEW)
    smg_card = await _card(scoped_db, other_branch)
    assert smg_card["omzet"]["amount"] == 7000


async def test_accurate_branch_id_does_not_grant_operational_access(scoped_db):
    await scoped_db.sales_invoices.insert_many([
        _invoice(
            "inv-legacy", "po-y1", "", 20,
            invoice_date="2026-09-10",
            operational_branch_id="",
            branch_id=YGY,
        ),
        _invoice(
            "inv-accurate-only", "po-y1", "", 1111,
            invoice_date="2026-09-10",
            operational_branch_id="",
            branch_id=ACCURATE_YGY,
        ),
    ])
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    director = _user("u-dir", "direktur_utama")
    y_card = await _card(scoped_db, ygy, "2026-09-01", "2026-09-30")
    y_detail = await _detail(scoped_db, ygy, "2026-09-01", "2026-09-30")
    g_card = await _card(scoped_db, director, "2026-09-01", "2026-09-30")
    assert y_card["omzet"]["amount"] == 20
    assert {row["id"] for row in y_detail["rows"]} == {"inv-legacy"}
    assert g_card["omzet"]["amount"] == 1131
    march = await _card(scoped_db, director)
    assert march["omzet"]["amount"] == 17000


async def test_multi_branch_user_does_not_see_unassigned_branch(scoped_db):
    both = _user("u-both", "crm", [YGY, SMG], **VIEW)
    card = await _card(scoped_db, both, "2026-11-01", "2026-11-30")
    detail = await _detail(scoped_db, both, "2026-11-01", "2026-11-30")
    director = _user("u-dir", "direktur_utama")
    global_card = await _card(scoped_db, director, "2026-11-01", "2026-11-30")
    assert card["omzet"]["status"] == "empty"
    assert detail["kpi_summary"]["amount"] is None
    assert detail["total"] == 0
    assert global_card["omzet"]["amount"] == 2000


async def test_wib_period_bounds_match_between_card_and_detail(scoped_db):
    await scoped_db.sales_invoices.insert_many([
        _invoice("inv-jan-31", "po-y1", YGY, 50, invoice_date="2026-01-31"),
        _invoice("inv-feb-01", "po-y1", YGY, 80, invoice_date="2026-02-01"),
        _invoice("inv-dec-31", "po-y1", YGY, 70, invoice_date="2025-12-31"),
    ])
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    card = await _card(scoped_db, ygy, "2026-01-01", "2026-01-31")
    detail = await _detail(scoped_db, ygy, "2026-01-01", "2026-01-31")
    assert _amounts(card, detail) == (50, 50, 50)
    assert {row["id"] for row in detail["rows"]} == {"inv-jan-31"}

    today = wib_today().isoformat()
    await scoped_db.sales_invoices.insert_one(
        _invoice("inv-today", "po-y1", YGY, 15, invoice_date=today)
    )
    today_card = await _card(scoped_db, ygy, today, today, preset="today")
    today_detail = await _detail(scoped_db, ygy, today, today, widget="today")
    default_today = await _detail(scoped_db, ygy, "", "", widget="today")
    assert _amounts(today_card, today_detail) == (15, 15, 15)
    assert default_today["kpi_summary"]["amount"] == 15


async def test_null_missing_and_zero_follow_the_card_contract(scoped_db):
    await scoped_db.sales_invoices.insert_many([
        _invoice("inv-null", "po-y1", YGY, None, invoice_date="2026-05-10", grand_total=100),
        _invoice("inv-null-sibling", "po-y1", YGY, 100, invoice_date="2026-05-11"),
        _invoice("inv-zero", "po-y1", YGY, 0, invoice_date="2026-07-10"),
    ])
    await scoped_db.sales_invoices.insert_one(
        _invoice("inv-missing", "po-y1", YGY, 0, invoice_date="2026-06-10")
    )
    await scoped_db.sales_invoices.update_one({"id": "inv-missing"}, {"$unset": {"after_discount": ""}})

    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    null_card = await _card(scoped_db, ygy, "2026-05-01", "2026-05-31")
    null_detail = await _detail(scoped_db, ygy, "2026-05-01", "2026-05-31")
    assert null_card["omzet"]["status"] == "error"
    assert null_card["omzet"]["amount"] is None
    assert null_detail["kpi_summary"]["status"] == "error"
    assert null_detail["kpi_summary"]["amount"] is None
    assert null_detail["kpi_summary"]["count"] is None
    assert null_detail["total"] == 2
    assert null_detail["kpi_summary"]["comparable_to_card"] is False

    missing_card = await _card(scoped_db, ygy, "2026-06-01", "2026-06-30")
    missing_detail = await _detail(scoped_db, ygy, "2026-06-01", "2026-06-30")
    assert missing_card["omzet"]["amount"] is None
    assert missing_detail["kpi_summary"]["amount"] is None
    assert missing_detail["kpi_summary"]["status"] == "error"

    zero_card = await _card(scoped_db, ygy, "2026-07-01", "2026-07-31")
    zero_detail = await _detail(scoped_db, ygy, "2026-07-01", "2026-07-31")
    assert zero_card["omzet"]["status"] == "zero"
    assert zero_card["omzet"]["amount"] == 0
    assert zero_detail["kpi_summary"]["status"] == "zero"
    assert zero_detail["kpi_summary"]["amount"] == 0
    assert zero_detail["rows"][0]["grand_total"] == 0
    null_settle = await _detail(scoped_db, ygy, "2026-05-01", "2026-05-31", widget="settlement")
    assert null_card["settlement"]["status"] == "complete"
    assert null_card["settlement"]["open_grand_total"] == 1200
    assert null_settle["kpi_summary"]["status"] == "complete"
    assert null_settle["kpi_summary"]["amount"] == 1200
    assert null_settle["kpi_summary"]["basis"] == "grand_total"
    zero_settle = await _detail(scoped_db, ygy, "2026-07-01", "2026-07-31", widget="settlement")
    assert zero_card["settlement"]["open_grand_total"] == 1000
    assert zero_settle["kpi_summary"]["amount"] == 1000
    assert zero_settle["kpi_summary"]["status"] == "complete"
    assert zero_settle["rows"][0]["grand_total"] == 1000


async def test_detail_total_is_not_the_page_or_the_old_scan_cap(scoped_db):
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    page1 = await _detail(scoped_db, ygy, page=1, page_size=1)
    page2 = await _detail(scoped_db, ygy, page=2, page_size=1)
    assert page1["kpi_summary"]["amount"] == 10000
    assert page2["kpi_summary"]["amount"] == 10000
    assert page1["total"] == page2["total"] == 2
    assert len(page1["rows"]) == len(page2["rows"]) == 1
    assert page1["rows"][0]["id"] != page2["rows"][0]["id"]

    searched = await _detail(scoped_db, ygy, q="INV-Y1")
    assert searched["kpi_summary"]["amount"] == 4000
    assert searched["kpi_summary"]["comparable_to_card"] is False
    assert "hanya bila pencarian kosong" in searched["kpi_summary"]["detail"]
    settle_page = await _detail(scoped_db, ygy, widget="settlement", page=2, page_size=1)
    assert settle_page["kpi_summary"]["amount"] == 12000
    assert settle_page["total"] == 2
    assert len(settle_page["rows"]) == 1
    settle_search = await _detail(scoped_db, ygy, widget="settlement", q="INV-Y1")
    assert settle_search["kpi_summary"]["amount"] == 5000
    assert settle_search["kpi_summary"]["comparable_to_card"] is False
    assert "filter yang identik" in settle_search["kpi_summary"]["detail"]

    bulk = [
        _invoice(f"bulk-{index}", "po-y1", YGY, 1, invoice_date="2026-08-15", invoice_number=f"BULK-{index}")
        for index in range(5001)
    ]
    await scoped_db.sales_invoices.insert_many(bulk, ordered=False)
    card = await _card(scoped_db, ygy, "2026-08-01", "2026-08-31")
    detail = await _detail(scoped_db, ygy, "2026-08-01", "2026-08-31", page=2, page_size=1)
    assert card["omzet"]["amount"] == 5001
    assert card["omzet"]["invoice_count"] == 5001
    assert detail["kpi_summary"]["amount"] == 5001
    assert detail["total"] == 5001
    assert len(detail["rows"]) == 1
    assert detail["kpi_summary"]["comparable_to_card"] is True
    settle = await _detail(scoped_db, ygy, "2026-08-01", "2026-08-31", widget="settlement", page=2, page_size=1)
    assert card["settlement"]["open_grand_total"] == 5001 * 1001
    assert settle["kpi_summary"]["amount"] == 5001 * 1001
    assert settle["total"] == 5001
    assert len(settle["rows"]) == 1
    assert settle["kpi_summary"]["comparable_to_card"] is True


async def test_holding_with_module_permission_sees_every_branch(scoped_db):
    holding = _user("u-hold", "holding_team", **VIEW)
    card = await _card(scoped_db, holding)
    detail = await _detail(scoped_db, holding)
    assert _amounts(card, detail) == (17000, 17000, 17000)
    settlement = await _detail(scoped_db, holding, widget="settlement")
    assert settlement["kpi_summary"]["amount"] == 20000
    assert card["settlement"]["open_grand_total"] == 20000


async def test_settlement_null_discount_and_grand_contract(scoped_db):
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    await scoped_db.sales_invoices.insert_many([
        _invoice("inv-only-null", "po-y1", YGY, None, invoice_date="2026-10-10", grand_total=100),
        _invoice("inv-grand-null", "po-y1", YGY, 40, invoice_date="2026-10-20", grand_total=None),
        _invoice("inv-grand-zero", "po-y1", YGY, 10, invoice_date="2026-01-15", grand_total=0),
        _invoice("inv-grand-missing", "po-y1", YGY, 25, invoice_date="2026-06-20"),
    ])
    await scoped_db.sales_invoices.update_one({"id": "inv-grand-missing"}, {"$unset": {"grand_total": ""}})

    alone = await _detail(scoped_db, ygy, "2026-10-01", "2026-10-12", widget="settlement")
    alone_card = await _card(scoped_db, ygy, "2026-10-01", "2026-10-12")
    assert alone_card["omzet"]["status"] == "error"
    assert alone_card["omzet"]["amount"] is None
    assert alone_card["settlement"]["open_grand_total"] == 100
    assert alone["kpi_summary"]["amount"] == 100
    assert alone["kpi_summary"]["status"] == "complete"

    mixed = await _detail(scoped_db, ygy, "2026-10-01", "2026-10-31", widget="settlement")
    mixed_card = await _card(scoped_db, ygy, "2026-10-01", "2026-10-31")
    assert mixed_card["settlement"]["status"] == "error"
    assert mixed_card["settlement"]["open_grand_total"] is None
    assert mixed["kpi_summary"]["status"] == "error"
    assert mixed["kpi_summary"]["amount"] is None
    assert mixed["kpi_summary"]["amount"] != 0
    assert mixed["kpi_summary"]["comparable_to_card"] is False
    assert "grand_total" in mixed["kpi_summary"]["detail"]

    zero = await _detail(scoped_db, ygy, "2026-01-01", "2026-01-31", widget="settlement")
    zero_card = await _card(scoped_db, ygy, "2026-01-01", "2026-01-31")
    assert zero_card["omzet"]["amount"] == 10
    assert zero_card["settlement"]["status"] == "complete"
    assert zero_card["settlement"]["open_grand_total"] == 0
    assert zero["kpi_summary"]["amount"] == 0
    assert zero["kpi_summary"]["status"] == "complete"
    assert zero["rows"][0]["grand_total"] == 0

    missing = await _detail(scoped_db, ygy, "2026-06-01", "2026-06-30", widget="settlement")
    assert missing["kpi_summary"]["status"] == "error"
    assert missing["kpi_summary"]["amount"] is None


async def test_settlement_paid_unpaid_and_partial_match_the_card(scoped_db):
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    await scoped_db.sales_invoices.insert_many([
        _invoice("pay-open", "po-y1", YGY, 1, invoice_date="2026-12-02", grand_total=5000, invoice_number="PAY-OPEN"),
        _invoice("pay-part", "po-y1", YGY, 1, invoice_date="2026-12-03", grand_total=7000, invoice_number="PAY-PART"),
        _invoice("pay-full", "po-y1", YGY, 1, invoice_date="2026-12-04", grand_total=8000, invoice_number="PAY-FULL"),
    ])
    await scoped_db.sales_receipts.insert_many([
        {"id": "rc-cancel", "invoice_id": "pay-open", "amount": 5000, "financial_status": "cancelled", "cancelled": True},
        {"id": "rc-ineffective", "invoice_id": "pay-open", "amount": 5000, "settlement_effective": False},
        {"id": "rc-part", "invoice_id": "pay-part", "amount": 9000, "allocated_amount": 2000},
        {"id": "rc-full", "invoice_id": "pay-full", "amount": 8000},
    ])
    await scoped_db.sales_customer_credit_applications.insert_many([
        {"id": "cr-part", "invoice_id": "pay-part", "amount": 1000, "financial_status": "active", "status": "posted"},
        {"id": "cr-draft", "invoice_id": "pay-part", "amount": 7000, "financial_status": "active", "status": "draft"},
    ])
    card = await _card(scoped_db, ygy, "2026-12-01", "2026-12-31")
    detail = await _detail(scoped_db, ygy, "2026-12-01", "2026-12-31", widget="settlement")
    assert card["omzet"]["amount"] == 3
    assert card["settlement"]["settled_grand_total"] == 8000
    assert card["settlement"]["open_grand_total"] == 12000
    assert detail["kpi_summary"]["amount"] == 20000
    assert detail["kpi_summary"]["settled_grand_total"] == 8000
    assert detail["kpi_summary"]["open_grand_total"] == 12000
    assert detail["kpi_summary"]["comparable_to_card"] is True
    by_id = {row["id"]: row for row in detail["rows"]}
    assert by_id["pay-open"]["payment_status"] == "BELUM DIBAYAR"
    assert by_id["pay-open"]["grand_total"] == 5000
    assert by_id["pay-part"]["payment_status"] == "DP"
    assert by_id["pay-part"]["grand_total"] == 7000
    assert by_id["pay-full"]["payment_status"] == "LUNAS"
    assert by_id["pay-full"]["grand_total"] == 8000

    await scoped_db.sales_customer_credit_applications.insert_one({
        "id": "cr-bad",
        "invoice_id": "pay-full",
        "amount": None,
        "financial_status": "active",
        "status": "posted",
    })
    bad = await _detail(scoped_db, ygy, "2026-12-01", "2026-12-31", widget="settlement")
    bad_card = await _card(scoped_db, ygy, "2026-12-01", "2026-12-31")
    assert bad_card["settlement"]["status"] == "error"
    assert bad["kpi_summary"]["status"] == "error"
    assert bad["kpi_summary"]["amount"] is None
    assert "saldo pelanggan" in bad["kpi_summary"]["detail"]


async def test_march_calendar_bounds_include_only_march_days(scoped_db):
    ygy = _user("u-ygy", "crm", [YGY], **VIEW)
    await scoped_db.sales_invoices.insert_many([
        _invoice("edge-feb28", "po-y1", YGY, 1, invoice_date="2026-02-28", grand_total=11, invoice_number="EDGE-FEB"),
        _invoice("edge-mar01", "po-y1", YGY, 1, invoice_date="2026-03-01", grand_total=12, invoice_number="EDGE-MAR1"),
        _invoice("edge-mar31", "po-y1", YGY, 1, invoice_date="2026-03-31", grand_total=13, invoice_number="EDGE-MAR31"),
        _invoice("edge-apr01", "po-y1", YGY, 1, invoice_date="2026-04-01", grand_total=14, invoice_number="EDGE-APR"),
    ])
    detail = await _detail(scoped_db, ygy, widget="settlement", page_size=50)
    ids = {row["id"] for row in detail["rows"]}
    assert "edge-mar01" in ids
    assert "edge-mar31" in ids
    assert "edge-feb28" not in ids
    assert "edge-apr01" not in ids
    assert detail["kpi_summary"]["amount"] == 12025
    card = await _card(scoped_db, ygy)
    assert card["settlement"]["open_grand_total"] == 12025
    assert card["omzet"]["amount"] == 10002
