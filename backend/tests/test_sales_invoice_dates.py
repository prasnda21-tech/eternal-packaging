"""Sales Invoice legacy date identification. No production data. No date migration."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_dates_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_dates import (
    SalesInvoiceDateError,
    canonical_sales_invoice_date,
    format_sales_invoice_user_date,
    parse_sales_invoice_date,
    persist_sales_invoice_date,
    sales_invoice_date_sort_key,
    try_canonical_sales_invoice_date,
)
from services.sales_invoice_posting import SOURCE_TYPE, journal_date_from_invoice, post_sales_invoice_accounting
from services.sales_invoices import _invoice_day, query_sales_invoices


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def indexes(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await server.db.sales_invoices.delete_many({})
    await server.db.journal_entries.delete_many({})
    await server.db.accounting_periods.delete_many({})


def test_identifies_supported_formats_without_swapping():
    assert parse_sales_invoice_date("09/10/2026") == date(2026, 10, 9)
    assert parse_sales_invoice_date("2026-10-09") == date(2026, 10, 9)
    assert parse_sales_invoice_date("2026/10/09") == date(2026, 10, 9)
    assert parse_sales_invoice_date("09-10-2026") == date(2026, 10, 9)
    assert parse_sales_invoice_date("2026-10-09T08:00:00+00:00") == date(2026, 10, 9)
    assert parse_sales_invoice_date("2026-10-09T17:00:00-07:00") == date(2026, 10, 9)
    assert parse_sales_invoice_date("2026-01-05T01:00:00.123456+00:00") == date(2026, 1, 5)
    assert format_sales_invoice_user_date("2026/10/09") == "09/10/2026"
    assert canonical_sales_invoice_date("09/10/2026") == "2026-10-09"


def test_leap_years_and_invalid_dates_fail_closed():
    assert parse_sales_invoice_date("29/02/2024") == date(2024, 2, 29)
    assert parse_sales_invoice_date("29/02/2000") == date(2000, 2, 29)
    for raw in (
        "31/02/2026", "29/02/2023", "29/02/1900", "09/13/2026", "2026/13/01", "09/10/26",
        "2026-10-09 anything", "2026-10-09 garbage", "2026-10-09T25:00:00",
    ):
        with pytest.raises(SalesInvoiceDateError):
            parse_sales_invoice_date(raw)
    assert try_canonical_sales_invoice_date("31/02/2026") == ""


def test_same_day_preserves_historical_spelling():
    parsed, stored = persist_sales_invoice_date("2026/10/09", "09/10/2026")
    assert parsed == date(2026, 10, 9)
    assert stored == "2026/10/09"
    parsed, stored = persist_sales_invoice_date("2026/10/09", "10/10/2026")
    assert stored == "2026-10-10"


def test_sort_and_filter_keys_are_canonical():
    mixed = ["2026/02/01", "2026-01-15", "31/02/2026", "15-01-2026"]
    ordered = sorted(mixed, key=sales_invoice_date_sort_key)
    assert ordered[:3] == ["2026-01-15", "15-01-2026", "2026/02/01"]
    assert _invoice_day({"invoice_date": "2026/01/20"}) == "2026-01-20"
    assert _invoice_day({"invoice_date": "31/02/2026", "created_at": "2026-01-01T00:00:00+00:00"}) == ""
    assert journal_date_from_invoice({"invoice_date": "2026/09/01"}) == "2026-09-01"


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-dates",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Dates",
        "role": "admin",
    }


async def _seed_po():
    po_id = str(uuid.uuid4())
    await server.db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO/DATE/{uuid.uuid4().hex[:6].upper()}",
        "customer": "PT Tanggal",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 10, "unit": "pcs"}],
        "total_quantity": 10,
        "current_stage": 5,
        "created_at": "2026-01-01T00:00:00Z",
    })
    return po_id


@pytest.mark.anyio
async def test_create_accepts_dmy_and_rejects_impossible_date(indexes):
    import httpx

    _admin()
    po_id = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/api/sales-invoices", json={
            "po_id": po_id,
            "invoice_date": "09/10/2026",
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000}],
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["invoice"]["invoice_date"] == "2026-10-09"
        bad = await client.post("/api/sales-invoices", json={
            "po_id": po_id,
            "invoice_date": "31/02/2026",
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000}],
        })
        assert bad.status_code == 400
        blank = await client.post("/api/sales-invoices", json={
            "po_id": po_id,
            "invoice_date": "",
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000}],
        })
        assert blank.status_code == 400
    assert await server.db.sales_invoices.count_documents({"invoice_date": "31/02/2026"}) == 0


@pytest.mark.anyio
async def test_filter_uses_canonical_day_and_leaves_stored_value(indexes):
    early_id = str(uuid.uuid4())
    late_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_many([
        {
            "id": early_id,
            "invoice_number": "INV-EARLY",
            "invoice_date": "2026/01/20",
            "financial_status": "finalized",
            "grand_total": 10,
            "outstanding_amount": 10,
            "created_at": "2026-01-20T00:00:00+00:00",
        },
        {
            "id": late_id,
            "invoice_number": "INV-LATE",
            "invoice_date": "2026-03-01",
            "financial_status": "finalized",
            "grand_total": 10,
            "outstanding_amount": 10,
            "created_at": "2026-03-01T00:00:00+00:00",
        },
    ])
    page = await query_sales_invoices(server.db, date_from="2026-01-01", date_to="2026-01-31", scope="active")
    ids = [row["id"] for row in page["invoices"]]
    assert ids == [early_id]
    stored = await server.db.sales_invoices.find_one({"id": early_id}, {"_id": 0, "invoice_date": 1})
    assert stored["invoice_date"] == "2026/01/20"


async def _posted_ready(invoice_date: str) -> str:
    invoice_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"INV-{invoice_id[:6]}",
        "invoice_date": invoice_date,
        "financial_status": "finalized",
        "subtotal": 1000,
        "discount_amount": 0,
        "after_discount": 1000,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "other_charges": [],
        "other_charges_total": 0,
        "grand_total": 1000,
        "outstanding_amount": 1000,
        "created_at": "2026-08-01T00:00:00+00:00",
    })
    rec = await create_account(server.db, {"code": f"11{uuid.uuid4().hex[:4]}", "name": "Piutang", "account_type": "AREC"}, user={"id": "t", "name": "t", "role": "admin"})
    rev = await create_account(server.db, {"code": f"41{uuid.uuid4().hex[:4]}", "name": "Pendapatan", "account_type": "REVE"}, user={"id": "t", "name": "t", "role": "admin"})
    tax = await create_account(server.db, {"code": f"21{uuid.uuid4().hex[:4]}", "name": "PPN", "account_type": "OCLY"}, user={"id": "t", "name": "t", "role": "admin"})
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "receivable_account_id": rec["id"],
            "revenue_account_id": rev["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )
    return invoice_id


@pytest.mark.anyio
async def test_legacy_date_posts_canonical_journal_without_rewriting_invoice(indexes):
    invoice_id = await _posted_ready("2026/08/15")
    user = {"id": "admin-dates", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
    await post_sales_invoice_accounting(server.db, invoice_id, user)
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "invoice_date": 1})
    assert stored["invoice_date"] == "2026/08/15"
    journal = await server.db.journal_entries.find_one(
        {"source_type": SOURCE_TYPE, "source_id": invoice_id, "reversal_of": None},
        {"_id": 0, "date": 1, "status": 1},
    )
    assert journal["date"] == "2026-08-15"
    assert journal["status"] == "posted"


@pytest.mark.anyio
async def test_closed_period_rejects_legacy_date_before_journal_write(indexes):
    invoice_id = await _posted_ready("2026/09/01")
    await close_period(
        server.db,
        "2026-09",
        user={"email": "admin@eternalpackaging.com"},
        reason="uji tutup periode tanggal lama",
    )
    user = {"id": "admin-dates", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
    with pytest.raises(Exception) as exc:
        await post_sales_invoice_accounting(server.db, invoice_id, user)
    assert getattr(exc.value, "status_code", None) == 409
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "invoice_date": 1})
    assert stored["invoice_date"] == "2026/09/01"
    assert await server.db.journal_entries.count_documents({"source_type": SOURCE_TYPE, "source_id": invoice_id}) == 0
