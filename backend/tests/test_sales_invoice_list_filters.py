"""Active/cancelled invoice tabs, server-side filters, and delete eligibility.

Uses the isolated test database. Does not touch production data.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from fastapi import HTTPException
from server import app, current_user
from services.sales_invoices import (
    bulk_delete_sales_invoices,
    delete_sales_invoice,
    list_sales_invoices,
    query_sales_invoices,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _invoice(**overrides):
    doc = {
        "id": overrides.get("id"),
        "invoice_number": overrides.get("invoice_number"),
        "po_id": overrides.get("po_id", "po-filter"),
        "po_number": overrides.get("po_number", "PO-FILTER"),
        "customer": overrides.get("customer", "PT Filter"),
        "outlet": "Gudang",
        "sales": "Sari",
        "invoice_date": overrides.get("invoice_date", "2026-03-01"),
        "created_at": overrides.get("created_at", "2026-03-01T00:00:00+00:00"),
        "grand_total": overrides.get("grand_total", 100),
        "after_discount": overrides.get("grand_total", 100),
        "ppn_amount": 0,
        "financial_status": overrides.get("financial_status", "draft"),
        "lines": [],
    }
    return doc


@pytest.mark.anyio
async def test_tabs_filters_pagination_and_delete_rules():
    db = server.db
    prefix = f"fp-filter-{uuid.uuid4().hex[:8]}"
    ids = {
        "draft": f"{prefix}-draft",
        "lunas": f"{prefix}-lunas",
        "reversed": f"{prefix}-reversed",
        "zero": f"{prefix}-zero",
        "paid_draft": f"{prefix}-paid-draft",
        "cancelled": f"{prefix}-cancelled",
        "far": f"{prefix}-far",
        "newest": f"{prefix}-newest",
        "mid": f"{prefix}-mid",
        "void_receipt": f"{prefix}-void-receipt",
    }
    docs = [
        _invoice(id=ids["draft"], invoice_number=f"{prefix}/DRAFT", customer="PT Aktif Draft", po_number=f"{prefix}-PO-DRAFT", invoice_date="2026-01-05", created_at="2026-01-05T01:00:00+00:00", grand_total=100, financial_status="draft"),
        _invoice(id=ids["lunas"], invoice_number=f"{prefix}/LUNAS", customer="PT Lunas Sentosa", po_number=f"{prefix}-PO-LUNAS", invoice_date="2026-03-15", created_at="2026-03-15T01:00:00+00:00", grand_total=1000, financial_status="finalized"),
        _invoice(id=ids["reversed"], invoice_number=f"{prefix}/REV", customer="PT Reversed MasihAktif", po_number=f"{prefix}-PO-REV", invoice_date="2026-04-01", created_at="2026-04-01T01:00:00+00:00", grand_total=500, financial_status="finalized"),
        _invoice(id=ids["zero"], invoice_number=f"{prefix}/ZERO", customer="PT Nol", po_number=f"{prefix}-PO-ZERO", invoice_date="2026-02-01", created_at="2026-02-01T01:00:00+00:00", grand_total=0, financial_status="draft"),
        _invoice(id=ids["paid_draft"], invoice_number=f"{prefix}/DP", customer="PT Draft Bayar", po_number=f"{prefix}-PO-DP", invoice_date="2026-02-10", created_at="2026-02-10T01:00:00+00:00", grand_total=800, financial_status="draft"),
        _invoice(id=ids["cancelled"], invoice_number=f"{prefix}/BATAL", customer="PT Batal", po_number=f"{prefix}-PO-BATAL", invoice_date="2026-05-01", created_at="2026-05-01T01:00:00+00:00", grand_total=250, financial_status="cancelled"),
        _invoice(id=ids["far"], invoice_number=f"{prefix}/JAUH", customer="PT Halaman Jauh", po_number=f"{prefix}-PO-JAUH", invoice_date="2026-01-01", created_at="2024-01-01T01:00:00+00:00", grand_total=42, financial_status="draft"),
        _invoice(id=ids["newest"], invoice_number=f"{prefix}/NEW", customer="PT Terbaru", po_number=f"{prefix}-PO-NEW", invoice_date="2026-09-01", created_at="2026-09-01T01:00:00+00:00", grand_total=70, financial_status="draft"),
        _invoice(id=ids["mid"], invoice_number=f"{prefix}/MID", customer="PT Tengah", po_number=f"{prefix}-PO-MID", invoice_date="2026-06-01", created_at="2026-06-01T01:00:00+00:00", grand_total=55, financial_status="finalized"),
        _invoice(id=ids["void_receipt"], invoice_number=f"{prefix}/VOID", customer="PT Draft Penerimaan Batal", po_number=f"{prefix}-PO-VOID", invoice_date="2026-02-20", created_at="2026-02-20T01:00:00+00:00", grand_total=90, financial_status="draft"),
    ]
    receipt_ids = [f"{prefix}-pay-lunas", f"{prefix}-pay-dp", f"{prefix}-pay-void"]
    journal_ids = [f"{prefix}-journal-lunas", f"{prefix}-journal-rev"]
    try:
        await db.sales_invoices.insert_many(docs)
        await db.sales_receipts.insert_many([
            {"id": receipt_ids[0], "receipt_number": f"{prefix}-RCPT-LUNAS", "invoice_id": ids["lunas"], "amount": 1000, "allocated_amount": 1000, "financial_status": "active"},
            {"id": receipt_ids[1], "receipt_number": f"{prefix}-RCPT-DP", "invoice_id": ids["paid_draft"], "amount": 100, "allocated_amount": 100, "financial_status": "active"},
            {"id": receipt_ids[2], "receipt_number": f"{prefix}-RCPT-VOID", "invoice_id": ids["void_receipt"], "amount": 90, "allocated_amount": 0, "financial_status": "cancelled", "cancelled": True},
        ])
        await db.journal_entries.insert_many([
            {"id": journal_ids[0], "source_type": "sales_invoice", "source_id": ids["lunas"], "status": "posted", "reversal_of": None},
            {"id": journal_ids[1], "source_type": "sales_invoice", "source_id": ids["reversed"], "status": "reversed", "reversal_of": None},
        ])

        active = await query_sales_invoices(db, q=prefix, scope="active", page=1, page_size=50)
        cancelled = await query_sales_invoices(db, q=prefix, scope="cancelled", page=1, page_size=50)
        active_ids = {row["id"] for row in active["invoices"]}
        cancelled_ids = {row["id"] for row in cancelled["invoices"]}
        assert ids["cancelled"] not in active_ids
        assert cancelled_ids == {ids["cancelled"]}
        assert ids["lunas"] in active_ids
        assert ids["reversed"] in active_ids
        lunas = next(row for row in active["invoices"] if row["id"] == ids["lunas"])
        assert lunas["payment_status"] == "LUNAS"
        assert lunas["financial_status"] == "finalized"
        assert lunas["accounting_status"] == "posted"
        assert lunas["can_delete"] is False
        reversed_row = next(row for row in active["invoices"] if row["id"] == ids["reversed"])
        assert reversed_row["financial_status"] == "finalized"
        assert reversed_row["accounting_status"] == "reversed"
        assert reversed_row["can_delete"] is False
        draft = next(row for row in active["invoices"] if row["id"] == ids["draft"])
        assert draft["can_delete"] is True
        paid_draft = next(row for row in active["invoices"] if row["id"] == ids["paid_draft"])
        assert paid_draft["can_delete"] is False
        assert paid_draft["payment_status"] == "DP"
        void_row = next(row for row in active["invoices"] if row["id"] == ids["void_receipt"])
        assert void_row["financial_status"] == "draft"
        assert void_row["can_delete"] is False
        assert next(row for row in cancelled["invoices"] if row["id"] == ids["cancelled"])["can_delete"] is False
        assert active["active_total"] >= 8
        assert active["cancelled_total"] >= 1
        assert active["tab_total"] == active["active_total"]
        assert cancelled["tab_total"] == cancelled["cancelled_total"]

        first_page = await query_sales_invoices(db, q=prefix, scope="active", page=1, page_size=1)
        assert first_page["page_size"] == 1
        assert first_page["total"] > first_page["page_size"]
        assert first_page["invoices"][0]["id"] == ids["newest"]
        assert ids["far"] not in {row["id"] for row in first_page["invoices"]}
        found = await query_sales_invoices(
            db, q=prefix, scope="active", party="Halaman Jauh", page=1, page_size=1,
        )
        assert [row["id"] for row in found["invoices"]] == [ids["far"]]
        assert found["total"] == 1
        assert found["tab_total"] == active["active_total"]
        assert found["count"] == 1

        combined = await query_sales_invoices(
            db,
            q=f"{prefix}/LUNAS",
            scope="active",
            party="Lunas",
            po_number=f"{prefix}-PO-LUNAS",
            date_from="2026-03-01",
            date_to="2026-03-31",
            total_min=0,
            total_max=1000,
            outstanding_min=0,
            outstanding_max=0,
            financial_status="finalized",
            accounting_status="posted",
            payment_status="LUNAS",
            page=1,
            page_size=20,
        )
        assert [row["id"] for row in combined["invoices"]] == [ids["lunas"]]

        zeros = await query_sales_invoices(
            db,
            q=prefix,
            scope="active",
            total_min=0,
            total_max=0,
            outstanding_min=0,
            outstanding_max=0,
            page=1,
            page_size=20,
        )
        zero_ids = {row["id"] for row in zeros["invoices"]}
        assert ids["zero"] in zero_ids
        assert ids["draft"] not in zero_ids
        assert ids["lunas"] not in zero_ids

        with pytest.raises(HTTPException) as total_range:
            await query_sales_invoices(db, scope="active", total_min=10, total_max=0)
        assert total_range.value.status_code == 400
        assert "Total Faktur" in str(total_range.value.detail)
        with pytest.raises(HTTPException) as date_range:
            await query_sales_invoices(db, scope="active", date_from="2026-05-02", date_to="2026-05-01")
        assert date_range.value.status_code == 400
        assert "tanggal" in str(date_range.value.detail).lower()

        legacy = await list_sales_invoices(db, q=prefix)
        legacy_ids = {row["id"] for row in legacy}
        assert ids["cancelled"] in legacy_ids
        assert ids["lunas"] in legacy_ids

        with pytest.raises(HTTPException) as blocked_delete:
            await delete_sales_invoice(db, ids["lunas"], {"role": "admin"})
        assert blocked_delete.value.status_code == 409
        with pytest.raises(HTTPException) as blocked_void:
            await delete_sales_invoice(db, ids["void_receipt"], {"role": "admin"})
        assert blocked_void.value.status_code == 400
        assert "Hapus Penerimaan Penjualan terlebih dahulu." in str(blocked_void.value.detail)
        assert await db.sales_invoices.find_one({"id": ids["void_receipt"]})
        with pytest.raises(HTTPException) as blocked_bulk:
            await bulk_delete_sales_invoices(db, [ids["cancelled"], ids["reversed"]], {"role": "admin"})
        assert blocked_bulk.value.status_code == 400
        assert await db.sales_invoices.find_one({"id": ids["cancelled"]})
        assert await db.sales_invoices.find_one({"id": ids["lunas"]})
    finally:
        await db.sales_invoices.delete_many({"id": {"$in": list(ids.values())}})
        await db.sales_receipts.delete_many({"id": {"$in": receipt_ids}})
        await db.journal_entries.delete_many({"id": {"$in": journal_ids}})


@pytest.mark.anyio
async def test_list_endpoint_keeps_unpaged_contract_and_pages_new_callers():
    db = server.db
    prefix = f"fp-http-{uuid.uuid4().hex[:8]}"
    active_id = f"{prefix}-active"
    cancelled_id = f"{prefix}-cancelled"
    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        await db.sales_invoices.insert_many([
            _invoice(id=active_id, invoice_number=f"{prefix}/A", customer="PT Http Aktif", financial_status="finalized", created_at="2026-07-01T00:00:00+00:00"),
            _invoice(id=cancelled_id, invoice_number=f"{prefix}/C", customer="PT Http Batal", financial_status="cancelled", created_at="2026-07-02T00:00:00+00:00"),
        ])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            legacy = await client.get("/api/sales-invoices", params={"q": prefix})
            assert legacy.status_code == 200, legacy.text
            body = legacy.json()
            assert "invoices" in body and "count" in body
            assert "total" not in body
            assert {row["id"] for row in body["invoices"]} >= {active_id, cancelled_id}

            paged = await client.get("/api/sales-invoices", params={"q": prefix, "scope": "active", "page": 1, "page_size": 20})
            assert paged.status_code == 200, paged.text
            page_body = paged.json()
            assert {row["id"] for row in page_body["invoices"]} == {active_id}
            assert page_body["total"] == 1
            assert page_body["cancelled_total"] >= 1
            assert page_body["active_total"] >= 1

            invalid = await client.get("/api/sales-invoices", params={"scope": "active", "outstanding_min": 5, "outstanding_max": 0})
            assert invalid.status_code == 400
            assert "Sisa Tagihan" in str(invalid.json().get("detail"))

        app.dependency_overrides[current_user] = lambda: {
            "email": f"viewer-none-{prefix}@eternalpackaging.com",
            "name": "None",
            "role": "no-invoice-access",
            "permissions_override": {},
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            forbidden = await client.get("/api/sales-invoices", params={"scope": "active"})
            assert forbidden.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.sales_invoices.delete_many({"id": {"$in": [active_id, cancelled_id]}})
