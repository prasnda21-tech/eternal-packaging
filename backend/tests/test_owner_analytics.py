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


@pytest.mark.anyio
async def test_finance_section_isolated_and_profit_still_not_ready(clean_db):
    """Finance may be empty/unavailable without breaking Phase 2 omzet."""
    await server.db.sales_invoices.insert_one(
        _invoice(id="fin1", after_discount=2500, invoice_date="2026-03-10"),
    )
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
    )
    assert owner["omzet"]["amount"] == 2500.0
    assert owner["profit"]["status"] == "not_ready"
    assert "finance" in owner
    finance = owner["finance"]
    assert finance["cash_balance"]["scope_label"] == "Saat Ini"
    assert finance["cash_in"]["scope_label"] == "Periode"
    assert finance["cash_out"]["scope_label"] == "Periode"
    assert finance["reconciliation"]["label"] == "Rekonsiliasi Bulan Ini"
    assert "cash_trend" in finance
    # No finance.view / cash_bank.view grant required for Owner payload.
    assert owner["omzet"]["amount"] == 2500.0


@pytest.mark.anyio
async def test_finance_balance_uses_today_not_historical_period(clean_db, monkeypatch):
    from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
    from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED, ensure_indexes as ensure_journal

    await ensure_coa(server.db)
    await ensure_journal(server.db)
    bank = await create_account(
        server.db, {"code": "1111", "name": "Kas Owner", "account_type": "BANK"},
        user=_user(),
    )
    equity = await create_account(
        server.db, {"code": "3101", "name": "Ekuitas Owner", "account_type": "EQTY"},
        user=_user(),
    )
    jid = uuid.uuid4().hex
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "number": "JE-OWN-1",
        "date": "2026-01-15",
        "status": STATUS_POSTED,
        "source_type": "general_journal",
        "source_id": "gj1",
        "description": "Saldo kas",
    })
    await server.db[JOURNAL_LINES].insert_many([
        {"id": uuid.uuid4().hex, "journal_id": jid, "account_id": bank["id"], "debit": 50000, "credit": 0},
        {"id": uuid.uuid4().hex, "journal_id": jid, "account_id": equity["id"], "debit": 0, "credit": 50000},
    ])
    from datetime import datetime, timezone
    now = datetime(2026, 3, 20, 3, 0, 0, tzinfo=timezone.utc)  # WIB 2026-03-20
    owner = await get_owner_analytics(
        server.db,
        start_date="2025-01-01",
        end_date="2025-01-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
        now=now,
    )
    assert owner["as_of_date"] == "2026-03-20"
    assert owner["finance"]["cash_balance"]["as_of"] == "2026-03-20"
    assert owner["finance"]["cash_balance"]["amount"] == 50000.0
    # Historical period has no cash movement in Jan 2025.
    assert owner["finance"]["cash_in"]["amount"] in (None, 0.0) or owner["finance"]["cash_in"]["status"] in {
        "empty", "zero", "unavailable",
    }
    assert owner["finance"]["reconciliation"]["start_date"] == "2026-03-01"
    assert owner["finance"]["reconciliation"]["end_date"] == "2026-03-20"


@pytest.mark.anyio
async def test_finance_cash_trend_matches_totals_and_excludes_transfer(clean_db):
    from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
    from services.journal_core import (
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        SOURCE_TRANSFER,
        STATUS_POSTED,
        ensure_indexes as ensure_journal,
    )

    await ensure_coa(server.db)
    await ensure_journal(server.db)
    bank_a = await create_account(
        server.db, {"code": "1112", "name": "Bank A", "account_type": "BANK"}, user=_user(),
    )
    bank_b = await create_account(
        server.db, {"code": "1113", "name": "Bank B", "account_type": "BANK"}, user=_user(),
    )
    revenue = await create_account(
        server.db, {"code": "4101", "name": "Pendapatan", "account_type": "REVE"}, user=_user(),
    )
    expense = await create_account(
        server.db, {"code": "6101", "name": "Beban", "account_type": "EXPS"}, user=_user(),
    )

    async def post_je(date, lines, source_type="general_journal"):
        jid = uuid.uuid4().hex
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": jid,
            "number": f"JE-{jid[:6]}",
            "date": date,
            "status": STATUS_POSTED,
            "source_type": source_type,
            "source_id": uuid.uuid4().hex,
            "description": source_type,
        })
        for line in lines:
            await server.db[JOURNAL_LINES].insert_one({
                "id": uuid.uuid4().hex,
                "journal_id": jid,
                "account_id": line["account_id"],
                "debit": line.get("debit", 0),
                "credit": line.get("credit", 0),
            })
        return jid

    await post_je("2026-03-05", [
        {"account_id": bank_a["id"], "debit": 20000},
        {"account_id": revenue["id"], "credit": 20000},
    ])
    await post_je("2026-03-06", [
        {"account_id": expense["id"], "debit": 7000},
        {"account_id": bank_a["id"], "credit": 7000},
    ])
    # Internal transfer — must not inflate external in/out.
    await post_je("2026-03-07", [
        {"account_id": bank_b["id"], "debit": 3000},
        {"account_id": bank_a["id"], "credit": 3000},
    ], source_type=SOURCE_TRANSFER)
    # Draft must be excluded.
    draft_id = uuid.uuid4().hex
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": draft_id,
        "number": "JE-DRAFT",
        "date": "2026-03-08",
        "status": "draft",
        "source_type": "general_journal",
        "source_id": "d1",
        "description": "draft",
    })
    await server.db[JOURNAL_LINES].insert_many([
        {"id": uuid.uuid4().hex, "journal_id": draft_id, "account_id": bank_a["id"], "debit": 99999, "credit": 0},
        {"id": uuid.uuid4().hex, "journal_id": draft_id, "account_id": revenue["id"], "debit": 0, "credit": 99999},
    ])

    from datetime import datetime, timezone
    now = datetime(2026, 3, 15, 3, 0, 0, tzinfo=timezone.utc)
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
        now=now,
    )
    finance = owner["finance"]
    assert finance["cash_in"]["amount"] == 20000.0
    assert finance["cash_out"]["amount"] == 7000.0
    trend = finance["cash_trend"]
    assert trend["status"] in {"complete", "incomplete"}
    assert trend["cash_in"] == 20000.0
    assert trend["cash_out"] == 7000.0
    assert trend["grain"] in {"day", "month"}
    assert isinstance(trend["points"], list) and trend["points"]
    assert sum(p["cash_in"] for p in trend["points"]) == 20000.0
    assert sum(p["cash_out"] for p in trend["points"]) == 7000.0


@pytest.mark.anyio
async def test_finance_error_does_not_zero_phase2(clean_db, monkeypatch):
    await server.db.sales_invoices.insert_one(
        _invoice(id="iso1", after_discount=1200, invoice_date="2026-03-10"),
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("finance boom")

    monkeypatch.setattr("services.owner_analytics._build_finance", boom)
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
    )
    assert owner["omzet"]["amount"] == 1200.0
    assert owner["finance"]["status"] == "error"
    assert owner["finance"]["cash_balance"]["amount"] is None
    assert owner["profit"]["status"] == "not_ready"


def test_finance_arap_from_rows_buckets_fallback_and_mismatch():
    from services.owner_analytics import _finance_arap_from_rows
    from services.accounts_receivable import (
        BUCKET_NOT_DUE,
        BUCKET_1_30,
        BUCKET_31_60,
        BUCKET_61_90,
        BUCKET_OVER_90,
    )

    rows = [
        {
            "accounting_outstanding": 100,
            "aging_bucket": BUCKET_NOT_DUE,
            "due_date_fallback": False,
            "document_mismatch": False,
            "integrity_warnings": [],
        },
        {
            "accounting_outstanding": 40,
            "aging_bucket": BUCKET_1_30,
            "due_date_fallback": True,
            "document_mismatch": False,
            "integrity_warnings": [],
        },
        {
            "accounting_outstanding": 30,
            "aging_bucket": BUCKET_31_60,
            "due_date_fallback": False,
            "document_mismatch": True,
            "integrity_warnings": [],
        },
        {
            "accounting_outstanding": 20,
            "aging_bucket": BUCKET_61_90,
            "due_date_fallback": False,
            "document_mismatch": False,
            "integrity_warnings": [],
        },
        {
            "accounting_outstanding": 10,
            "aging_bucket": BUCKET_OVER_90,
            "due_date_fallback": False,
            "document_mismatch": False,
            "integrity_warnings": [],
        },
        {
            "accounting_outstanding": 0,
            "aging_bucket": None,
            "due_date_fallback": False,
            "document_mismatch": False,
            "integrity_warnings": [],
        },
    ]
    card = _finance_arap_from_rows(
        rows,
        as_of="2026-03-20",
        label="Piutang Usaha",
        source="accounts_receivable.accounting_outstanding",
        not_due_key=BUCKET_NOT_DUE,
        bucket_keys=(BUCKET_NOT_DUE, BUCKET_1_30, BUCKET_31_60, BUCKET_61_90, BUCKET_OVER_90),
    )
    assert card["amount"] == 200.0
    assert card["not_due_amount"] == 100.0
    assert card["overdue_amount"] == 100.0
    assert card["aging"]["not_due"] == 100.0
    assert card["aging"]["d1_30"] == 40.0
    assert card["aging"]["d31_60"] == 30.0
    assert card["aging"]["d61_90"] == 20.0
    assert card["aging"]["over_90"] == 10.0
    assert round(
        card["aging"]["not_due"]
        + card["aging"]["d1_30"]
        + card["aging"]["d31_60"]
        + card["aging"]["d61_90"]
        + card["aging"]["over_90"],
        2,
    ) == 200.0
    assert card["due_date_fallback_count"] == 1
    assert card["document_mismatch_count"] == 1
    assert card["completeness"] == "incomplete"
    assert card["scope_label"] == "Saat Ini"
    assert card["aging_reconcile_ok"] is True


def test_finance_arap_due_today_is_not_due_bucket_contract():
    """Due today uses service bucket NOT_DUE (Belum Jatuh Tempo)."""
    from services.accounts_receivable import aging_bucket_for, BUCKET_NOT_DUE

    assert aging_bucket_for("2026-03-20", "2026-03-20")[0] == BUCKET_NOT_DUE
    assert aging_bucket_for("2026-03-21", "2026-03-20")[0] == BUCKET_NOT_DUE
    assert aging_bucket_for("2026-03-19", "2026-03-20") == ("1_30", 1)
    assert aging_bucket_for("2026-02-18", "2026-03-20") == ("1_30", 30)
    assert aging_bucket_for("2026-02-17", "2026-03-20") == ("31_60", 31)
    assert aging_bucket_for("2026-01-19", "2026-03-20") == ("31_60", 60)
    assert aging_bucket_for("2026-01-18", "2026-03-20") == ("61_90", 61)
    assert aging_bucket_for("2025-12-20", "2026-03-20") == ("61_90", 90)
    assert aging_bucket_for("2025-12-19", "2026-03-20") == ("OVER_90", 91)


@pytest.mark.anyio
async def test_owner_arap_snapshot_ignores_historical_period(clean_db, monkeypatch):
    from datetime import datetime, timezone

    captured = {}

    async def fake_ar(db, *, as_of):
        captured["ar_as_of"] = as_of
        return {
            "status": "complete",
            "amount": 111.0,
            "label": "Piutang Usaha",
            "scope": "current_snapshot",
            "scope_label": "Saat Ini",
            "as_of": as_of,
            "source": "accounts_receivable.accounting_outstanding",
            "overdue_amount": 11.0,
            "not_due_amount": 100.0,
            "aging": {"not_due": 100.0, "d1_30": 11.0, "d31_60": 0.0, "d61_90": 0.0, "over_90": 0.0},
            "invoice_count": 1,
            "overdue_invoice_count": 1,
            "completeness": "complete",
            "detail": None,
            "note": None,
        }

    async def fake_ap(db, *, as_of):
        captured["ap_as_of"] = as_of
        return {
            "status": "complete",
            "amount": 222.0,
            "label": "Hutang Usaha",
            "scope": "current_snapshot",
            "scope_label": "Saat Ini",
            "as_of": as_of,
            "source": "accounts_payable.accounting_outstanding",
            "overdue_amount": 0.0,
            "not_due_amount": 222.0,
            "aging": {"not_due": 222.0, "d1_30": 0.0, "d31_60": 0.0, "d61_90": 0.0, "over_90": 0.0},
            "invoice_count": 1,
            "overdue_invoice_count": 0,
            "completeness": "complete",
            "detail": None,
            "note": "Nilai hutang mengikuti jurnal AP yang telah diposting.",
        }

    monkeypatch.setattr("services.owner_analytics._owner_receivables", fake_ar)
    monkeypatch.setattr("services.owner_analytics._owner_payables", fake_ap)
    now = datetime(2026, 3, 20, 3, 0, 0, tzinfo=timezone.utc)
    owner = await get_owner_analytics(
        server.db,
        start_date="2025-01-01",
        end_date="2025-01-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
        now=now,
    )
    assert captured["ar_as_of"] == "2026-03-20"
    assert captured["ap_as_of"] == "2026-03-20"
    assert owner["finance"]["receivables"]["amount"] == 111.0
    assert owner["finance"]["payables"]["amount"] == 222.0
    assert owner["finance"]["receivables"]["scope_label"] == "Saat Ini"
    assert owner["finance"]["payables"]["scope_label"] == "Saat Ini"
    assert "jurnal ap" in (owner["finance"]["payables"].get("note") or "").lower()


@pytest.mark.anyio
async def test_arap_error_isolation_and_phase3a_preserved(clean_db, monkeypatch):
    await server.db.sales_invoices.insert_one(
        _invoice(id="iso-arap", after_discount=900, invoice_date="2026-03-10"),
    )

    async def boom_ar(*args, **kwargs):
        raise RuntimeError("ar boom")

    async def ok_ap(db, *, as_of):
        return {
            "status": "complete",
            "amount": 50.0,
            "label": "Hutang Usaha",
            "scope_label": "Saat Ini",
            "as_of": as_of,
            "overdue_amount": 0.0,
            "not_due_amount": 50.0,
            "aging": {"not_due": 50.0, "d1_30": 0.0, "d31_60": 0.0, "d61_90": 0.0, "over_90": 0.0},
            "completeness": "complete",
            "note": "Nilai hutang mengikuti jurnal AP yang telah diposting.",
        }

    monkeypatch.setattr("services.owner_analytics._owner_receivables", boom_ar)
    monkeypatch.setattr("services.owner_analytics._owner_payables", ok_ap)
    owner = await get_owner_analytics(
        server.db,
        start_date="2026-03-01",
        end_date="2026-03-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
    )
    assert owner["omzet"]["amount"] == 900.0
    assert owner["finance"]["receivables"]["amount"] is None
    assert owner["finance"]["receivables"]["status"] == "error"
    assert owner["finance"]["payables"]["amount"] == 50.0
    assert "cash_balance" in owner["finance"]
    assert "cash_trend" in owner["finance"]
    assert owner["profit"]["status"] == "not_ready"


@pytest.mark.anyio
async def test_arap_matches_accounting_summary_engine(clean_db):
    """Owner AR/AP totals follow the same current-invoice loader as AR/AP summary."""
    from services.accounts_receivable import get_accounts_receivable_summary
    from services.accounts_payable import get_accounts_payable_summary
    from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
    from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED, ensure_indexes as ensure_journal
    from datetime import datetime, timezone

    await ensure_coa(server.db)
    await ensure_journal(server.db)
    arec = await create_account(
        server.db, {"code": "1121", "name": "Piutang Usaha", "account_type": "AREC"}, user=_user(),
    )
    reve = await create_account(
        server.db, {"code": "4109", "name": "Pendapatan AR", "account_type": "REVE"}, user=_user(),
    )
    apay = await create_account(
        server.db, {"code": "2111", "name": "Hutang Usaha", "account_type": "APAY"}, user=_user(),
    )
    exps = await create_account(
        server.db, {"code": "6109", "name": "Beban AP", "account_type": "EXPS"}, user=_user(),
    )

    inv_id = "ar-owner-1"
    await server.db.sales_invoices.insert_one({
        "id": inv_id,
        "invoice_number": "INV-AR-1",
        "invoice_date": "2026-03-01",
        "due_date": "2026-03-25",
        "grand_total": 10000,
        "financial_status": "finalized",
        "customer": "Cust A",
        "po_id": None,
    })
    j_ar = uuid.uuid4().hex
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": j_ar,
        "number": "JE-AR-1",
        "date": "2026-03-01",
        "status": STATUS_POSTED,
        "source_type": "sales_invoice",
        "source_id": inv_id,
        "reversal_of": None,
        "description": "AR invoice",
    })
    await server.db[JOURNAL_LINES].insert_many([
        {"id": uuid.uuid4().hex, "journal_id": j_ar, "account_id": arec["id"], "debit": 10000, "credit": 0},
        {"id": uuid.uuid4().hex, "journal_id": j_ar, "account_id": reve["id"], "debit": 0, "credit": 10000},
    ])

    pin_id = "ap-owner-1"
    await server.db.purchase_invoices.insert_one({
        "id": pin_id,
        "invoice_number": "PIN-AP-1",
        "invoice_date": "2026-03-02",
        "due_date": "2026-02-01",
        "grand_total": 7000,
        "cancelled": False,
        "supplier": "Supp A",
    })
    j_ap = uuid.uuid4().hex
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": j_ap,
        "number": "JE-AP-1",
        "date": "2026-03-02",
        "status": STATUS_POSTED,
        "source_type": "purchase_invoice",
        "source_id": pin_id,
        "reversal_of": None,
        "description": "AP invoice",
    })
    await server.db[JOURNAL_LINES].insert_many([
        {"id": uuid.uuid4().hex, "journal_id": j_ap, "account_id": exps["id"], "debit": 7000, "credit": 0},
        {"id": uuid.uuid4().hex, "journal_id": j_ap, "account_id": apay["id"], "debit": 0, "credit": 7000},
    ])

    now = datetime(2026, 3, 20, 3, 0, 0, tzinfo=timezone.utc)
    owner = await get_owner_analytics(
        server.db,
        start_date="2025-01-01",
        end_date="2025-01-31",
        preset="custom",
        user=_user(),
        permissions=[PERM_VIEW],
        now=now,
    )
    ar_summary = await get_accounts_receivable_summary(server.db, aging_reference_date="2026-03-20")
    ap_summary = await get_accounts_payable_summary(server.db, aging_reference_date="2026-03-20")
    assert owner["finance"]["receivables"]["amount"] == ar_summary["total_accounting_ar"]
    assert owner["finance"]["receivables"]["aging"]["not_due"] == ar_summary["not_due"]
    assert owner["finance"]["payables"]["amount"] == ap_summary["total_accounting_ap"]
    assert owner["finance"]["payables"]["aging"]["d31_60"] == ap_summary["d31_60"]
    assert owner["finance"]["receivables"]["as_of"] == "2026-03-20"
    assert owner["finance"]["payables"]["as_of"] == "2026-03-20"
    assert "credit/advance/return" in (owner["finance"]["payables"].get("note") or "").lower()
