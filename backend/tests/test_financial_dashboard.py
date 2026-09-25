"""Dashboard Keuangan — isolated read model. Does not touch production data."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_financial_dashboard_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.financial_dashboard import get_financial_dashboard, wib_business_date
from services.financial_inventory_opening import wib_business_date as opening_wib
from services.general_journal import COLLECTION as GJ_COLLECTION, ensure_indexes as ensure_gj
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, REVERSAL_INCOMPLETE, ensure_indexes as ensure_journal
from services.opening_balance import COLLECTION as OB_COLLECTION, ensure_indexes as ensure_ob
from services.purchase_accounting_settings import SETTINGS_KEY

pytestmark = pytest.mark.anyio
PATH = "/api/dashboard/keuangan"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_gj(db)
    await ensure_ob(db)
    names = (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, GJ_COLLECTION, OB_COLLECTION,
        "app_settings", "sales_invoices", "sales_receipts", "purchase_invoices",
        "purchase_payments", "purchase_goods_receipts", "financial_inventory_openings",
    )
    for name in names:
        await db[name].delete_many({})
    yield
    for name in names:
        await db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="admin", perms=None):
    uid = f"u-{role}-{uuid.uuid4().hex[:8]}"
    if role == "admin":
        override = {}
    elif perms is not None:
        override = {perm: True for perm in perms}
    else:
        override = {}
    return {
        "id": uid,
        "email": f"{uid}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name, account_type):
    return await create_account(server.db, {"code": code, "name": name, "account_type": account_type}, user=_user())


def _lines(debit_id, credit_id, amount):
    return [
        {"account_id": debit_id, "debit": amount, "credit": 0, "description": "Dr"},
        {"account_id": credit_id, "debit": 0, "credit": amount, "description": "Cr"},
    ]


async def _gj(client, debit_id, credit_id, amount, date, desc):
    created = await client.post("/api/ledger/general-journals", json={
        "date": date,
        "description": desc,
        "lines": _lines(debit_id, credit_id, amount),
    })
    assert created.status_code == 201, created.text
    return created.json()


async def _post_gj(client, debit_id, credit_id, amount, date, desc):
    draft = await _gj(client, debit_id, credit_id, amount, date, desc)
    posted = await client.post(f"/api/ledger/general-journals/{draft['id']}/post")
    assert posted.status_code == 200, posted.text
    return posted.json()


async def _seed(client):
    inventory = await _coa("1144", "Persediaan Uji Dashboard", "INTR")
    bank = await _coa("1110", "Kas Uji Dashboard", "BANK")
    other = await _coa("1190", "Aset lain uji", "OCAS")
    equity = await _coa("3100", "Ekuitas uji", "EQTY")
    grni = await _coa("2190", "GRNI Uji Dashboard", "OCLY")
    payable = await _coa("2110", "Utang usaha uji", "APAY")
    receivable = await _coa("1120", "Piutang uji", "AREC")
    revenue = await _coa("4100", "Pendapatan uji", "REVE")
    cogs = await _coa("5100", "HPP uji", "COGS")
    expense = await _coa("6100", "Beban operasional uji", "EXPS")
    opening = await client.post("/api/ledger/opening-balances", json={
        "cutoff_date": "2026-09-30",
        "description": "Saldo awal uji dashboard",
        "lines": [
            {"account_id": inventory["id"], "debit": 160000, "credit": 0, "description": "Persediaan"},
            {"account_id": bank["id"], "debit": 100000, "credit": 0, "description": "Kas"},
            {"account_id": other["id"], "debit": 60000, "credit": 0, "description": "Aset lain"},
            {"account_id": equity["id"], "debit": 0, "credit": 200000, "description": "Ekuitas"},
            {"account_id": grni["id"], "debit": 0, "credit": 60000, "description": "GRNI"},
            {"account_id": payable["id"], "debit": 0, "credit": 60000, "description": "Utang"},
        ],
    })
    assert opening.status_code == 201, opening.text
    posted_opening = await client.post(f"/api/ledger/opening-balances/{opening.json()['id']}/post")
    assert posted_opening.status_code == 200, posted_opening.text
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "payable_account_id": payable["id"],
            "purchase_clearing_account_id": grni["id"],
            "inventory_accounting_effective_date": "2026-10-01",
        }},
        upsert=True,
    )
    await _post_gj(client, cogs["id"], inventory["id"], 16000, "2026-10-05", "HPP uji")
    await _post_gj(client, receivable["id"], revenue["id"], 30000, "2026-10-06", "Pendapatan uji")
    await _post_gj(client, bank["id"], receivable["id"], 30000, "2026-10-07", "Pelunasan uji")
    await _post_gj(client, payable["id"], bank["id"], 60000, "2026-10-08", "Bayar supplier uji")
    await _post_gj(client, expense["id"], bank["id"], 5000, "2026-10-09", "Beban uji")
    return {
        "inventory": inventory, "bank": bank, "grni": grni, "payable": payable,
        "receivable": receivable, "revenue": revenue, "cogs": cogs, "expense": expense,
    }


async def _dashboard(client, start="2026-10-01", end="2026-10-31"):
    response = await client.get(PATH, params={"start_date": start, "end_date": end})
    assert response.status_code == 200, response.text
    return response.json()


async def test_finance_permission_follows_existing_finance_catalog_not_operational_roles():
    blocked = (
        "head_production", "head_operations", "operator", "helper", "crm",
        "holding_team", "sales_eternal", "designer", "driver",
    )
    for role in blocked:
        assert "dashboard.finance.view" not in ROLE_DEFAULT_PERMISSIONS[role]
        assert "nav.dashboard.finance" not in ROLE_DEFAULT_PERMISSIONS[role]
    assert "nav.dashboard" in ROLE_DEFAULT_PERMISSIONS["head_production"]
    assert "dashboard.production.view" in ROLE_DEFAULT_PERMISSIONS["head_operations"]
    assert "dashboard.finance.view" in ROLE_DEFAULT_PERMISSIONS["admin"]
    assert "nav.dashboard.finance" in ROLE_DEFAULT_PERMISSIONS["admin"]


async def test_jakarta_business_date_boundary():
    assert opening_wib("2026-09-30T16:30:00Z") == "2026-09-30"
    assert wib_business_date("2026-09-30T17:30:00Z") == "2026-10-01"
    assert wib_business_date("2026-10-01") == "2026-10-01"


async def test_books_match_existing_statements_and_opening_is_not_period_cash():
    async with await client_for(_user()) as client:
        await _seed(client)
        body = await _dashboard(client)
    perf = body["performance"]
    assert perf["revenue"] == 30000.0
    assert perf["cogs"] == 16000.0
    assert perf["gross_profit"] == 14000.0
    assert perf["operating_expense"] == 5000.0
    assert perf["net_profit"] == 9000.0
    assert perf["gross_margin"] == pytest.approx(14000 / 30000)
    assert perf["net_margin"] == pytest.approx(9000 / 30000)
    assert perf["source"] == "profit_loss.journal_lines"
    position = body["position"]
    assert position["inventory_book"]["amount"] == 144000.0
    assert position["inventory_book"]["published_pricing_is_book"] is False
    assert position["cash_and_bank"]["amount"] == 65000.0
    assert position["grni"]["amount"] == 60000.0
    assert position["accounts_receivable"]["amount"] == 0.0
    assert position["accounts_payable"]["amount"] == 0.0
    cash = body["cash_movement"]
    assert cash["formal_cash_flow_statement"] is False
    assert cash["net_change"] == -35000.0
    assert cash["net_change"] != position["cash_and_bank"]["amount"]
    assert cash["cash_in"] == 30000.0
    assert cash["cash_out"] == 65000.0
    trend_revenue = sum(point["revenue"] for point in body["charts"]["trend"]["points"])
    assert trend_revenue == pytest.approx(30000.0)
    assert body["writes"] == 0


async def test_date_edges_include_start_and_end_only():
    async with await client_for(_user()) as client:
        await _seed(client)
        on_start = await _dashboard(client, "2026-10-06", "2026-10-06")
        before_revenue = await _dashboard(client, "2026-10-01", "2026-10-05")
        empty = await _dashboard(client, "2020-01-01", "2020-01-31")
        after_period = await _dashboard(client, "2026-11-01", "2026-11-30")
    assert on_start["performance"]["revenue"] == 30000.0
    assert before_revenue["performance"]["revenue"] == 0.0
    assert before_revenue["performance"]["cogs"] == 16000.0
    assert empty["performance"]["status"] == "empty"
    assert empty["performance"]["revenue"] is None
    assert after_period["performance"]["status"] == "empty"
    assert on_start["position"]["cash_and_bank"]["amount"] == 100000.0


async def test_unposted_draft_and_invoice_do_not_enter_books_and_pre_effective_is_split():
    async with await client_for(_user()) as client:
        accounts = await _seed(client)
        draft = await _gj(client, accounts["expense"]["id"], accounts["bank"]["id"], 8000, "2026-10-11", "Draft tidak diposting")
        assert draft["id"]
        await server.db.sales_invoices.insert_one({
            "id": "inv-dashboard-uji",
            "invoice_number": "FP-UJI-DASH",
            "financial_status": "finalized",
            "cancelled": False,
            "invoice_date": "2026-10-12",
            "customer": "Pelanggan Uji Dashboard",
        })
        await server.db.purchase_invoices.insert_many([
            {"id": "pi-historical", "invoice_number": "FPB-LAMA", "cancelled": False, "invoice_date": "2026-09-30T16:30:00Z"},
            {"id": "pi-action", "invoice_number": "FPB-BARU", "cancelled": False, "invoice_date": "2026-09-30T17:30:00Z"},
        ])
        before = {
            "journals": await server.db[JOURNAL_ENTRIES].count_documents({}),
            "lines": await server.db[JOURNAL_LINES].count_documents({}),
            "invoices": await server.db.sales_invoices.count_documents({}),
        }
        body = await _dashboard(client)
        after = {
            "journals": await server.db[JOURNAL_ENTRIES].count_documents({}),
            "lines": await server.db[JOURNAL_LINES].count_documents({}),
            "invoices": await server.db.sales_invoices.count_documents({}),
        }
    assert before == after
    assert body["performance"]["revenue"] == 30000.0
    assert body["performance"]["operating_expense"] == 5000.0
    controls = {row["key"]: row for row in body["controls"]["items"]}
    assert controls["sales_invoices_unposted"]["count"] == 1
    assert controls["purchase_invoices_unposted"]["count"] == 1
    assert controls["pre_effective"]["count"] >= 1
    assert controls["pre_effective"]["kind"] == "historical"


async def test_complete_reversal_nets_and_incomplete_reversal_warns_without_doubling():
    async with await client_for(_user()) as client:
        accounts = await _seed(client)
        extra = await _post_gj(client, accounts["receivable"]["id"], accounts["revenue"]["id"], 4000, "2026-10-15", "Pendapatan dibalik")
        reversed_doc = await client.post(f"/api/ledger/general-journals/{extra['id']}/reverse")
        assert reversed_doc.status_code == 200, reversed_doc.text
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": "je-incomplete-dashboard",
            "date": "2026-10-16",
            "status": "incomplete",
            "source_type": "general_journal",
            "source_id": "gj-incomplete-dashboard",
            "reversal_of": "je-original-dashboard",
            "reversal_intent": {"operation_id": "op-dashboard", "phase": "header_written"},
        })
        await server.db[JOURNAL_LINES].insert_one({
            "id": "jl-incomplete-dashboard",
            "journal_id": "je-incomplete-dashboard",
            "account_id": accounts["revenue"]["id"],
            "debit": 0,
            "credit": 99999,
        })
        body = await _dashboard(client)
    assert body["performance"]["revenue"] == 30000.0
    assert body["performance"]["blocker_code"] == REVERSAL_INCOMPLETE
    assert body["performance"]["status"] == "incomplete"
    assert body["completeness"]["status"] == "incomplete"


async def test_internal_transfer_is_excluded_from_external_cash():
    async with await client_for(_user()) as client:
        accounts = await _seed(client)
        other_bank = await _coa("1111", "Bank kedua uji", "BANK")
        origin = await client.post("/api/cash-bank/accounts", json={"name": "Kas asal", "account_id": accounts["bank"]["id"], "active": True})
        dest = await client.post("/api/cash-bank/accounts", json={"name": "Kas tujuan", "account_id": other_bank["id"], "active": True})
        assert origin.status_code == 201, origin.text
        assert dest.status_code == 201, dest.text
        draft = await client.post("/api/cash-bank/transfers", json={
            "date": "2026-10-10",
            "source_cash_bank_id": origin.json()["id"],
            "dest_cash_bank_id": dest.json()["id"],
            "amount": 10000,
            "description": "Transfer internal uji",
        })
        assert draft.status_code == 201, draft.text
        posted = await client.post(f"/api/cash-bank/transfers/{draft.json()['id']}/post")
        assert posted.status_code == 200, posted.text
        body = await _dashboard(client)
    assert body["cash_movement"]["cash_in"] == 30000.0
    assert body["cash_movement"]["cash_out"] == 65000.0
    assert body["cash_movement"]["net_change"] == -35000.0
    assert body["cash_movement"]["internal_transfer_volume"] == 10000.0
    assert body["position"]["cash_and_bank"]["amount"] == 65000.0


async def test_subledger_difference_is_reported_not_forced():
    async with await client_for(_user()) as client:
        accounts = await _seed(client)
        await _post_gj(client, accounts["receivable"]["id"], accounts["revenue"]["id"], 1500, "2026-10-20", "Jurnal AR tanpa faktur")
        body = await _dashboard(client)
    assert body["receivables"]["historical_as_of"] is False
    assert body["receivables"]["as_of_label"] == "Saat ini"
    assert body["receivables"]["difference"] not in (None, 0, 0.0)
    assert "tidak dipaksa" in (body["receivables"]["difference_note"] or "").lower() or body["receivables"]["difference"] != 0
    assert body["charts"]["aging"]["historical_as_of"] is False
    assert body["charts"]["aging"]["label"] == "Saat ini"


async def test_failed_section_does_not_zero_the_rest(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("laba rugi gagal")

    monkeypatch.setattr("services.financial_dashboard.get_profit_loss", boom)
    async with await client_for(_user()) as client:
        await _seed(client)
        body = await _dashboard(client)
    assert body["performance"]["status"] == "error"
    assert body["performance"].get("revenue") is None
    assert body["position"]["cash_and_bank"]["amount"] == 65000.0
    assert body["position"]["inventory_book"]["amount"] == 144000.0


async def test_permission_gates_api_and_finance_view_does_not_open_production_dashboard():
    async with await client_for(_user(role="helper")) as client:
        denied = await client.get(PATH, params={"start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert denied.status_code == 403
    async with await client_for(_user(role="holding_team", perms=["dashboard.finance.view"])) as client:
        allowed = await client.get(PATH, params={"start_date": "2026-10-01", "end_date": "2026-10-31"})
        production = await client.get("/api/dashboard/production")
    assert allowed.status_code == 200
    assert allowed.json()["performance"]["status"] == "empty"
    assert production.status_code == 403
    async with await client_for(_user()) as client:
        bad = await client.get(PATH, params={"start_date": "2026-13-01", "end_date": "2026-10-31"})
    assert bad.status_code == 400


async def test_zero_revenue_margin_is_null_not_a_percent():
    async with await client_for(_user()) as client:
        bank = await _coa("1112", "Kas kosong", "BANK")
        equity = await _coa("3101", "Ekuitas kosong", "EQTY")
        await _post_gj(client, bank["id"], equity["id"], 1, "2026-10-02", "Mutasi neraca tanpa pendapatan")
        body = await _dashboard(client)
    assert body["performance"]["status"] == "zero"
    assert body["performance"]["revenue"] == 0.0
    assert body["performance"]["gross_margin"] is None
    assert body["performance"]["net_margin"] is None
