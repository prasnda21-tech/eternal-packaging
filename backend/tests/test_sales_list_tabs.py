"""Phase 2 Package A — five-tab Sales list classification & pagination."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27018")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_list_tabs_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server

pytestmark = pytest.mark.anyio


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
    for name in (
        "orders",
        "sales_invoices",
        "sales_receipts",
        "sales_business_cancellations",
        "sales_invoice_edit_revisions",
        "journal_entries",
        "sales_deliveries",
        "sales_customer_credit_applications",
    ):
        await server.db[name].delete_many({})
    yield
    for name in (
        "orders",
        "sales_invoices",
        "sales_receipts",
        "sales_business_cancellations",
        "sales_invoice_edit_revisions",
        "journal_entries",
        "sales_deliveries",
        "sales_customer_credit_applications",
    ):
        await server.db[name].delete_many({})


from services.sales_list_tabs import (
    TAB_ACTIVE,
    TAB_CANCELLED,
    TAB_COMPLETED,
    TAB_PENDING,
    classify_sales_invoice_list_tab,
    classify_sales_order_list_tab,
    classify_sales_receipt_list_tab,
    legacy_invoice_scope_includes,
    paginate_rows,
    parse_page_params,
    sales_order_awaits_approval,
)


# ---------------------------------------------------------------------------
# Unit: Pesanan
# ---------------------------------------------------------------------------

async def test_po_pending_payment_approval_only_when_files_present():
    assert sales_order_awaits_approval({
        "payment_files": [True],
        "payment_status": "Waiting",
    })
    # Missing payment proof is NOT Pending (operational wait / HOLD reason).
    assert not sales_order_awaits_approval({
        "payment_files": [],
        "payment_status": "Waiting",
    })
    assert classify_sales_order_list_tab({
        "cancelled": False,
        "current_stage": 2,
        "payment_files": [True],
        "payment_status": "Waiting",
    }) == TAB_PENDING


async def test_po_pending_design_approval_only_when_files_present():
    assert classify_sales_order_list_tab({
        "cancelled": False,
        "current_stage": 1,
        "_has_design_files": True,
        "design_status": "Waiting",
    }) == TAB_PENDING
    assert classify_sales_order_list_tab({
        "cancelled": False,
        "current_stage": 1,
        "_has_design_files": False,
        "design_status": "Waiting",
    }) == TAB_ACTIVE


async def test_po_hold_without_approval_wait_stays_active():
    row = {
        "cancelled": False,
        "current_stage": 3,
        "hold": True,
        "hold_reasons": ["Belum ada design"],
        "design_files": [],
        "payment_files": [],
    }
    assert classify_sales_order_list_tab(row) == TAB_ACTIVE


async def test_po_partial_delivery_active_and_completed_cancelled():
    assert classify_sales_order_list_tab({
        "cancelled": False,
        "current_stage": 10,
        "hold": False,
    }) == TAB_ACTIVE
    assert classify_sales_order_list_tab({
        "cancelled": False,
        "current_stage": 14,
    }) == TAB_COMPLETED
    assert classify_sales_order_list_tab({"cancelled": True, "current_stage": 14}) == TAB_CANCELLED


# ---------------------------------------------------------------------------
# Unit: Faktur
# ---------------------------------------------------------------------------

async def test_invoice_posted_unpaid_active_paid_completed():
    assert classify_sales_invoice_list_tab({
        "financial_status": "finalized",
        "accounting_status": "posted",
        "accounting_posted": True,
        "accounting_journal_id": "j1",
        "outstanding_amount": 1000,
        "list_class": "active",
        "business_cancel_progress": "none",
    }) == TAB_ACTIVE
    assert classify_sales_invoice_list_tab({
        "financial_status": "finalized",
        "accounting_status": "posted",
        "accounting_posted": True,
        "accounting_journal_id": "j1",
        "outstanding_amount": 0,
        "list_class": "active",
        "business_cancel_progress": "none",
    }) == TAB_COMPLETED


async def test_invoice_draft_unposted_auto_failed_revision_cancel_pending():
    assert classify_sales_invoice_list_tab({
        "financial_status": "draft",
        "accounting_status": "not_posted",
        "list_class": "active",
    }) == TAB_PENDING
    assert classify_sales_invoice_list_tab({
        "financial_status": "finalized",
        "accounting_status": "not_posted",
        "list_class": "active",
    }) == TAB_PENDING
    assert classify_sales_invoice_list_tab({
        "financial_status": "finalized",
        "accounting_status": "posted",
        "accounting_posted": True,
        "accounting_journal_id": "j1",
        "outstanding_amount": 0,
        "auto_accounting_status": "failed",
        "list_class": "active",
    }) == TAB_PENDING
    assert classify_sales_invoice_list_tab(
        {
            "financial_status": "finalized",
            "accounting_status": "posted",
            "accounting_posted": True,
            "accounting_journal_id": "j1",
            "outstanding_amount": 50,
            "list_class": "active",
        },
        revision_incomplete=True,
    ) == TAB_PENDING
    assert classify_sales_invoice_list_tab({
        "financial_status": "cancelled",
        "list_class": "active",
        "business_cancel_progress": "pending",
    }) == TAB_PENDING


async def test_invoice_cancel_complete_batal_not_pending():
    assert classify_sales_invoice_list_tab({
        "financial_status": "cancelled",
        "list_class": "cancelled",
        "business_cancel_progress": "complete",
    }) == TAB_CANCELLED
    assert classify_sales_invoice_list_tab({
        "financial_status": "cancelled",
        "list_class": "cancelled",
        "business_cancel_progress": "none",
    }) == TAB_CANCELLED


async def test_legacy_scope_active_includes_completed_and_pending():
    assert legacy_invoice_scope_includes(TAB_ACTIVE, "active")
    assert legacy_invoice_scope_includes(TAB_COMPLETED, "active")
    assert legacy_invoice_scope_includes(TAB_PENDING, "active")
    assert not legacy_invoice_scope_includes(TAB_CANCELLED, "active")


# ---------------------------------------------------------------------------
# Unit: Penerimaan
# ---------------------------------------------------------------------------

async def test_receipt_posted_settled_invoice_unpaid_active():
    assert classify_sales_receipt_list_tab(
        {
            "financial_status": "active",
            "accounting_status": "posted",
            "accounting_posted": True,
            "settlement_effective": True,
            "list_class": "active",
            "business_cancel_progress": "none",
        },
        invoice_outstanding=2500,
    ) == TAB_ACTIVE


async def test_receipt_invoice_paid_selesai_and_reopen_aktif():
    rec = {
        "financial_status": "active",
        "accounting_status": "posted",
        "accounting_posted": True,
        "settlement_effective": True,
        "list_class": "active",
        "business_cancel_progress": "none",
    }
    assert classify_sales_receipt_list_tab(rec, invoice_outstanding=0) == TAB_COMPLETED
    assert classify_sales_receipt_list_tab(rec, invoice_outstanding=100) == TAB_ACTIVE


async def test_receipt_settlement_false_unposted_cancel_pending():
    assert classify_sales_receipt_list_tab(
        {
            "financial_status": "active",
            "accounting_status": "posted",
            "accounting_posted": True,
            "settlement_effective": False,
            "list_class": "active",
        },
        invoice_outstanding=0,
    ) == TAB_PENDING
    assert classify_sales_receipt_list_tab(
        {
            "financial_status": "active",
            "accounting_status": "not_posted",
            "list_class": "active",
        },
        invoice_outstanding=0,
    ) == TAB_PENDING
    assert classify_sales_receipt_list_tab(
        {
            "financial_status": "cancelled",
            "list_class": "active",
            "business_cancel_progress": "pending",
        },
        invoice_outstanding=0,
    ) == TAB_PENDING
    assert classify_sales_receipt_list_tab(
        {
            "financial_status": "cancelled",
            "list_class": "cancelled",
            "business_cancel_progress": "complete",
        },
        invoice_outstanding=0,
    ) == TAB_CANCELLED


async def test_paginate_default_fifty_and_stable_slice():
    rows = [{"id": i} for i in range(120)]
    page, page_num, size = paginate_rows(rows, page=2, page_size=50)
    assert page_num == 2 and size == 50
    assert [r["id"] for r in page] == list(range(50, 100))
    p, s = parse_page_params(1, 50)
    assert (p, s) == (1, 50)


# ---------------------------------------------------------------------------
# Integration (synthetic Mongo)
# ---------------------------------------------------------------------------

async def test_query_invoices_five_tab_and_legacy_scope():
    from services.sales_invoices import query_sales_invoices

    await server.db.sales_invoices.insert_many([
        {
            "id": "inv-draft",
            "invoice_number": "INV/D1",
            "financial_status": "draft",
            "created_at": "2026-01-01T00:00:00+00:00",
            "grand_total": 100,
            "customer": "A",
        },
        {
            "id": "inv-paid",
            "invoice_number": "INV/P1",
            "financial_status": "finalized",
            "created_at": "2026-01-02T00:00:00+00:00",
            "grand_total": 100,
            "customer": "A",
            "accounting_status": "posted",
        },
        {
            "id": "inv-unpaid",
            "invoice_number": "INV/U1",
            "financial_status": "finalized",
            "created_at": "2026-01-03T00:00:00+00:00",
            "grand_total": 200,
            "customer": "B",
            "accounting_status": "posted",
        },
        {
            "id": "inv-batal",
            "invoice_number": "INV/B1",
            "financial_status": "cancelled",
            "created_at": "2026-01-04T00:00:00+00:00",
            "grand_total": 50,
            "customer": "C",
        },
    ])
    # Seed posted journals for paid/unpaid
    await server.db.journal_entries.insert_many([
        {
            "id": "j-paid",
            "status": "posted",
            "source_type": "sales_invoice",
            "source_id": "inv-paid",
            "is_primary_for_source": True,
        },
        {
            "id": "j-unpaid",
            "status": "posted",
            "source_type": "sales_invoice",
            "source_id": "inv-unpaid",
            "is_primary_for_source": True,
        },
    ])
    # Settlement-effective receipt covering inv-paid fully
    await server.db.sales_receipts.insert_one({
        "id": "rc-1",
        "invoice_id": "inv-paid",
        "amount": 100,
        "allocated_amount": 100,
        "settlement_effective": True,
        "financial_status": "active",
        "created_at": "2026-01-02T01:00:00+00:00",
    })

    pending = await query_sales_invoices(server.db, list_tab="pending", page=1, page_size=50)
    assert pending["pending_total"] >= 1
    assert all(r["list_tab"] == TAB_PENDING for r in pending["invoices"])
    assert any(r["id"] == "inv-draft" for r in pending["invoices"])

    completed = await query_sales_invoices(server.db, list_tab="completed", page=1, page_size=50)
    assert any(r["id"] == "inv-paid" for r in completed["invoices"])
    assert all(r["list_tab"] == TAB_COMPLETED for r in completed["invoices"])

    active = await query_sales_invoices(server.db, list_tab="active", page=1, page_size=50)
    assert any(r["id"] == "inv-unpaid" for r in active["invoices"])
    assert all(r["list_tab"] == TAB_ACTIVE for r in active["invoices"])

    batal = await query_sales_invoices(server.db, list_tab="cancelled", page=1, page_size=50)
    assert any(r["id"] == "inv-batal" for r in batal["invoices"])

    # Legacy scope=active includes draft+paid+unpaid, not batal
    legacy = await query_sales_invoices(server.db, scope="active", page=1, page_size=50)
    ids = {r["id"] for r in legacy["invoices"]}
    assert "inv-batal" not in ids
    assert "inv-draft" in ids and "inv-paid" in ids and "inv-unpaid" in ids
    assert legacy["active_total"] == legacy["all_total"] - legacy["cancelled_total"]


async def test_query_receipts_follow_invoice_outstanding():
    from services.sales_receipts import query_sales_receipts

    await server.db.sales_invoices.insert_one({
        "id": "inv-r1",
        "invoice_number": "INV/R1",
        "financial_status": "finalized",
        "grand_total": 100,
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    await server.db.journal_entries.insert_many([
        {
            "id": "j-inv-r1",
            "status": "posted",
            "source_type": "sales_invoice",
            "source_id": "inv-r1",
            "is_primary_for_source": True,
        },
        {
            "id": "j-rc-a",
            "status": "posted",
            "source_type": "sales_receipt",
            "source_id": "rc-a",
            "is_primary_for_source": True,
        },
        {
            "id": "j-rc-b",
            "status": "posted",
            "source_type": "sales_receipt",
            "source_id": "rc-b",
            "is_primary_for_source": True,
        },
    ])
    await server.db.sales_receipts.insert_many([
        {
            "id": "rc-a",
            "receipt_number": "RCV/A",
            "invoice_id": "inv-r1",
            "invoice_number": "INV/R1",
            "amount": 40,
            "allocated_amount": 40,
            "settlement_effective": True,
            "financial_status": "active",
            "created_at": "2026-01-02T00:00:00+00:00",
            "customer": "X",
        },
        {
            "id": "rc-pending",
            "receipt_number": "RCV/P",
            "invoice_id": "inv-r1",
            "amount": 10,
            "allocated_amount": 10,
            "settlement_effective": False,
            "financial_status": "active",
            "created_at": "2026-01-04T00:00:00+00:00",
            "customer": "X",
        },
    ])

    # Partial pay → settled receipt Aktif; settlement false → Pending
    aktif = await query_sales_receipts(server.db, list_tab="active", page=1, page_size=50)
    assert {r["id"] for r in aktif["receipts"]} == {"rc-a"}

    pending = await query_sales_receipts(server.db, list_tab="pending", page=1, page_size=50)
    assert {r["id"] for r in pending["receipts"]} == {"rc-pending"}

    # Second settled receipt completes invoice → both valid receipts Selesai
    await server.db.sales_receipts.insert_one({
        "id": "rc-b",
        "receipt_number": "RCV/B",
        "invoice_id": "inv-r1",
        "invoice_number": "INV/R1",
        "amount": 60,
        "allocated_amount": 60,
        "settlement_effective": True,
        "financial_status": "active",
        "created_at": "2026-01-03T00:00:00+00:00",
        "customer": "X",
    })
    selesai = await query_sales_receipts(server.db, list_tab="completed", page=1, page_size=50)
    assert {r["id"] for r in selesai["receipts"]} == {"rc-a", "rc-b"}

    # Reopen financially: remove one settled receipt → remaining valid returns Aktif
    await server.db.sales_receipts.delete_one({"id": "rc-b"})
    aktif2 = await query_sales_receipts(server.db, list_tab="active", page=1, page_size=50)
    assert {r["id"] for r in aktif2["receipts"]} == {"rc-a"}
    selesai2 = await query_sales_receipts(server.db, list_tab="completed", page=1, page_size=50)
    assert selesai2["receipts"] == []


async def test_sales_orders_list_tab_and_pagination_over_1000():
    from httpx import ASGITransport, AsyncClient

    # 1105 orders: 1000 active plain + 50 pending approval + 50 completed + 5 cancelled
    docs = []
    for i in range(1000):
        docs.append({
            "id": f"po-a-{i}",
            "po_number": f"PO/A/{i:04d}",
            "customer": "Cust",
            "outlet": "Out",
            "sales": "S",
            "current_stage": 6,
            "cancelled": False,
            "created_at": f"2026-01-01T00:00:{i % 60:02d}+00:00",
            "order_date": "2026-01-01",
            "total_quantity": 10,
            "design_status": "Approved",
            "payment_status": "Approved",
            "design_files": [{"n": 1}],
            "payment_files": [{"n": 1}],
            "line_items": [{"quantity": 10, "unit": "pcs"}],
        })
    for i in range(50):
        docs.append({
            "id": f"po-p-{i}",
            "po_number": f"PO/P/{i:04d}",
            "customer": "Cust",
            "outlet": "Out",
            "sales": "S",
            "current_stage": 2,
            "cancelled": False,
            "created_at": f"2026-02-01T00:00:{i % 60:02d}+00:00",
            "order_date": "2026-02-01",
            "total_quantity": 5,
            "design_status": "Waiting",
            "payment_status": "Approved",
            "design_files": [{"n": 1}],
            "payment_files": [{"n": 1}],
            "line_items": [{"quantity": 5, "unit": "pcs"}],
        })
    for i in range(50):
        docs.append({
            "id": f"po-c-{i}",
            "po_number": f"PO/C/{i:04d}",
            "customer": "Cust",
            "outlet": "Out",
            "sales": "S",
            "current_stage": 14,
            "cancelled": False,
            "created_at": f"2026-03-01T00:00:{i % 60:02d}+00:00",
            "order_date": "2026-03-01",
            "total_quantity": 5,
            "design_status": "Approved",
            "payment_status": "Approved",
            "design_files": [{"n": 1}],
            "payment_files": [{"n": 1}],
            "line_items": [{"quantity": 5, "unit": "pcs"}],
        })
    for i in range(5):
        docs.append({
            "id": f"po-x-{i}",
            "po_number": f"PO/X/{i:04d}",
            "customer": "Cust",
            "outlet": "Out",
            "sales": "S",
            "current_stage": 3,
            "cancelled": True,
            "created_at": f"2026-04-01T00:00:{i % 60:02d}+00:00",
            "order_date": "2026-04-01",
            "total_quantity": 1,
            "design_status": "Approved",
            "payment_status": "Approved",
            "line_items": [{"quantity": 1, "unit": "pcs"}],
        })
    await server.db.orders.insert_many(docs)

    # Auth bypass: call service path via endpoint with dependency override
    from server import app, current_user

    async def _user():
        return {
            "id": "u1",
            "email": "admin@eternalpackaging.com",
            "role": "admin",
            "name": "Admin",
        }

    app.dependency_overrides[current_user] = _user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/api/sales-orders", params={"list_tab": "active", "page": 1, "page_size": 50})
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["page_size"] == 50
        assert body["count"] == 50
        assert body["active_total"] == 1000
        assert body["total"] == 1000
        assert all(o.get("list_tab") == TAB_ACTIVE for o in body["orders"])

        # Page beyond 1000/50 = page 21 exists
        r_last = await client.get("/api/sales-orders", params={"list_tab": "active", "page": 20, "page_size": 50})
        assert r_last.status_code == 200
        assert r_last.json()["count"] == 50

        r_pend = await client.get("/api/sales-orders", params={"list_tab": "pending", "page": 1, "page_size": 50})
        assert r_pend.json()["pending_total"] == 50
        assert r_pend.json()["count"] == 50

        r_done = await client.get("/api/sales-orders", params={"list_tab": "completed", "page": 1, "page_size": 50})
        assert r_done.json()["completed_total"] == 50

        r_batal = await client.get("/api/sales-orders", params={"list_tab": "cancelled", "page": 1, "page_size": 50})
        assert r_batal.json()["cancelled_total"] == 5

        # Legacy unpaged still reports capped when using old fetch path without page
        legacy = await client.get("/api/sales-orders", params={"status": "aktif"})
        assert legacy.status_code == 200
        assert legacy.json().get("capped") is True or legacy.json()["count"] <= 1000

    app.dependency_overrides.pop(current_user, None)


async def test_invoice_cancel_pending_not_in_batal_scope():
    from services.sales_business_cancel import COLLECTION, DOC_INVOICE, PHASE_PENDING
    from services.sales_invoices import query_sales_invoices

    await server.db.sales_invoices.insert_one({
        "id": "inv-cancel-pend",
        "invoice_number": "INV/CP",
        "financial_status": "cancelled",
        "created_at": "2026-01-05T00:00:00+00:00",
        "grand_total": 10,
        "customer": "Z",
    })
    await server.db[COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "document_type": DOC_INVOICE,
        "document_id": "inv-cancel-pend",
        "phase": PHASE_PENDING,
        "complete": False,
        "created_at": "2026-01-05T00:00:01+00:00",
    })
    pending = await query_sales_invoices(server.db, list_tab="pending", page=1, page_size=50)
    assert any(r["id"] == "inv-cancel-pend" for r in pending["invoices"])
    batal = await query_sales_invoices(server.db, list_tab="cancelled", page=1, page_size=50)
    assert all(r["id"] != "inv-cancel-pend" for r in batal["invoices"])


async def test_batch_credit_chunks_more_than_500_invoice_ids():
    from decimal import Decimal

    from services.sales_customer_credit import (
        COLLECTION as CREDIT_COLL,
        CREDIT_INVOICE_ID_CHUNK,
        invoice_credit_applied_totals_by_invoice_ids,
    )

    assert CREDIT_INVOICE_ID_CHUNK == 250
    ids = [f"inv-chunk-{i}" for i in range(520)]
    apps = []
    for i, iid in enumerate(ids):
        if i % 17 == 0:
            apps.append({
                "id": f"app-chunk-{i}",
                "invoice_id": iid,
                "amount": "3.50",
                "financial_status": "active",
                "status": "posted",
            })
    if apps:
        await server.db[CREDIT_COLL].insert_many(apps)
    totals = await invoice_credit_applied_totals_by_invoice_ids(server.db, ids)
    assert len(totals) == 520
    credited = [iid for iid, amt in totals.items() if amt == Decimal("3.50")]
    assert len(credited) == len(apps)
    assert sum(1 for amt in totals.values() if amt == Decimal("0.00")) == 520 - len(apps)
    # No double-count: re-run same ids twice equivalent
    again = await invoice_credit_applied_totals_by_invoice_ids(server.db, ids + ids)
    assert again == totals


async def test_batch_credit_totals_match_single_and_affect_outstanding():
    from decimal import Decimal

    from services.sales_customer_credit import (
        COLLECTION as CREDIT_COLL,
        invoice_credit_applied_total,
        invoice_credit_applied_totals_by_invoice_ids,
    )
    from services.sales_invoices import query_sales_invoices
    from services.sales_receipts import _invoice_outstanding_by_ids

    await server.db.sales_invoices.insert_many([
        {
            "id": "inv-c1",
            "invoice_number": "INV/C1",
            "financial_status": "finalized",
            "created_at": "2026-03-01T00:00:00+00:00",
            "grand_total": 100,
            "customer": "CreditCo",
        },
        {
            "id": "inv-c2",
            "invoice_number": "INV/C2",
            "financial_status": "finalized",
            "created_at": "2026-03-01T00:00:00+00:00",
            "grand_total": 200,
            "customer": "CreditCo",
        },
    ])
    await server.db.journal_entries.insert_many([
        {
            "id": "j-c1",
            "status": "posted",
            "source_type": "sales_invoice",
            "source_id": "inv-c1",
            "is_primary_for_source": True,
        },
        {
            "id": "j-c2",
            "status": "posted",
            "source_type": "sales_invoice",
            "source_id": "inv-c2",
            "is_primary_for_source": True,
        },
    ])
    await server.db[CREDIT_COLL].insert_many([
        {
            "id": "app-1",
            "invoice_id": "inv-c1",
            "amount": "40.00",
            "financial_status": "active",
            "status": "posted",
        },
        {
            "id": "app-2",
            "invoice_id": "inv-c1",
            "amount": "10.00",
            "financial_status": "active",
            "status": "posted",
        },
        {
            "id": "app-rev",
            "invoice_id": "inv-c1",
            "amount": "99.00",
            "financial_status": "cancelled",
            "status": "posted",
        },
        {
            "id": "app-draft",
            "invoice_id": "inv-c2",
            "amount": "25.00",
            "financial_status": "active",
            "status": "draft",
        },
        {
            "id": "app-3",
            "invoice_id": "inv-c2",
            "amount": "50.00",
            "financial_status": "active",
            "status": "posted",
        },
    ])

    batch = await invoice_credit_applied_totals_by_invoice_ids(
        server.db, ["inv-c1", "inv-c2", "inv-missing"]
    )
    assert batch["inv-c1"] == Decimal("50.00")
    assert batch["inv-c2"] == Decimal("50.00")
    assert batch["inv-missing"] == Decimal("0.00")
    assert await invoice_credit_applied_total(server.db, "inv-c1") == Decimal("50.00")
    assert await invoice_credit_applied_total(server.db, "inv-c2") == Decimal("50.00")

    outstanding = await _invoice_outstanding_by_ids(server.db, ["inv-c1", "inv-c2"])
    assert outstanding["inv-c1"] == 50.0
    assert outstanding["inv-c2"] == 150.0

    page = await query_sales_invoices(server.db, list_tab="active", page=1, page_size=50)
    by_id = {r["id"]: r for r in page["invoices"]}
    assert by_id["inv-c1"]["outstanding_amount"] == 50.0
    assert by_id["inv-c2"]["outstanding_amount"] == 150.0


async def test_stable_sort_identical_created_at_no_page_dupes():
    from services.sales_invoices import query_sales_invoices

    ts = "2026-04-01T12:00:00+00:00"
    docs = []
    for i in range(120):
        docs.append({
            "id": f"inv-tie-{i:03d}",
            "invoice_number": f"INV/T{i:03d}",
            "financial_status": "draft",
            "created_at": ts,
            "grand_total": 10,
            "customer": "Tie",
        })
    await server.db.sales_invoices.insert_many(docs)

    page1 = await query_sales_invoices(server.db, list_tab="all", page=1, page_size=50)
    page2 = await query_sales_invoices(server.db, list_tab="all", page=2, page_size=50)
    page3 = await query_sales_invoices(server.db, list_tab="all", page=3, page_size=50)
    ids1 = [r["id"] for r in page1["invoices"]]
    ids2 = [r["id"] for r in page2["invoices"]]
    ids3 = [r["id"] for r in page3["invoices"]]
    assert len(ids1) == 50 and len(ids2) == 50 and len(ids3) == 20
    assert page1["all_total"] == 120
    all_ids = ids1 + ids2 + ids3
    assert len(all_ids) == len(set(all_ids))
    # Secondary key id desc for equal created_at
    assert ids1 == sorted(ids1, reverse=True)
    assert ids1[0] > ids1[-1]
    # Stable across refetch
    page1b = await query_sales_invoices(server.db, list_tab="all", page=1, page_size=50)
    assert [r["id"] for r in page1b["invoices"]] == ids1


async def test_partial_payment_keeps_invoice_active():
    from services.sales_invoices import query_sales_invoices

    await server.db.sales_invoices.insert_one({
        "id": "inv-partial",
        "invoice_number": "INV/PART",
        "financial_status": "finalized",
        "created_at": "2026-05-01T00:00:00+00:00",
        "grand_total": 100,
        "customer": "P",
    })
    await server.db.journal_entries.insert_one({
        "id": "j-part",
        "status": "posted",
        "source_type": "sales_invoice",
        "source_id": "inv-partial",
        "is_primary_for_source": True,
    })
    await server.db.sales_receipts.insert_one({
        "id": "rc-part",
        "invoice_id": "inv-partial",
        "amount": 40,
        "allocated_amount": 40,
        "settlement_effective": True,
        "financial_status": "active",
        "created_at": "2026-05-01T01:00:00+00:00",
    })
    active = await query_sales_invoices(server.db, list_tab="active", page=1, page_size=50)
    row = next(r for r in active["invoices"] if r["id"] == "inv-partial")
    assert row["outstanding_amount"] == 60.0
    assert row["list_tab"] == TAB_ACTIVE
    completed = await query_sales_invoices(server.db, list_tab="completed", page=1, page_size=50)
    assert all(r["id"] != "inv-partial" for r in completed["invoices"])
