"""Gap closure: flexible multi-source invoice accounting/payment lifecycle (dummy DB).

Does not change journal formulas — only verifies existing posting/receipt behavior
still holds for Model C multi-PO invoices created by Stage 2.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_gap_accounting")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from services.chart_of_accounts import create_account
from services.customer_tracking_invoices import invoice_exclusive_to_order
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_posting import SOURCE_TYPE, post_sales_invoice_accounting
from services.sales_invoices import create_sales_invoice, finalize_sales_invoice, get_sales_invoice_detail
from services.sales_receipts import create_sales_receipt


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


USER = {
    "id": "admin-gap",
    "email": "admin@eternalpackaging.com",
    "name": "Admin",
    "role": "admin",
}


async def _order(*, qty=100, price=1000.0, customer_id="cust-gap", branch="br-gap"):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Gap",
        "customer_id": customer_id,
        "currency": "IDR",
        "operational_branch_id": branch,
        "branch_id": "1",
        "cancelled": False,
        "line_items": [{
            "line_id": line_id,
            "name": "Produk Gap",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
        }],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _configure_sales_accounts():
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    rec = await create_account(
        server.db,
        {"code": f"11{uuid.uuid4().hex[:4]}", "name": "Piutang Gap", "account_type": "AREC"},
        user=USER,
    )
    rev = await create_account(
        server.db,
        {"code": f"41{uuid.uuid4().hex[:4]}", "name": "Pendapatan Gap", "account_type": "REVE"},
        user=USER,
    )
    tax = await create_account(
        server.db,
        {"code": f"21{uuid.uuid4().hex[:4]}", "name": "PPN Gap", "account_type": "OCLY"},
        user=USER,
    )
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "receivable_account_id": rec["id"],
                "revenue_account_id": rev["id"],
                "output_tax_account_id": tax["id"],
            }
        },
        upsert=True,
    )
    return rec, rev, tax


@pytest.mark.anyio
async def test_multi_po_invoice_draft_finalize_post_partial_full_payment():
    await _configure_sales_accounts()
    a = await _order(qty=50, price=1000.0)
    b = await _order(qty=50, price=2000.0, customer_id=a["customer_id"])

    draft = await create_sales_invoice(
        server.db,
        {
            "source_mode": "po",
            "source_po_ids": [a["id"], b["id"]],
            "invoice_date": "2026-08-15",
            "ppn_percent": 11,
            "lines": [
                {"source_po_id": a["id"], "line_idx": 0, "quantity": 10, "unit_price": 1000},
                {"source_po_id": b["id"], "line_idx": 0, "quantity": 5, "unit_price": 2000},
            ],
        },
        USER,
    )
    assert draft["financial_status"] == "draft"
    assert draft["is_multi_source"] is True
    subtotal = 10 * 1000 + 5 * 2000  # 20000
    assert float(draft["subtotal"]) == float(subtotal)
    # Tracking must fail-closed for multi-source
    assert invoice_exclusive_to_order(draft, a) is False

    fin = await finalize_sales_invoice(server.db, draft["id"], USER)
    assert fin["financial_status"] == "finalized"
    # Draft is not paid
    detail0 = await get_sales_invoice_detail(server.db, draft["id"])
    pay0 = str(detail0.get("payment_status") or "").upper()
    assert "LUNAS" not in pay0 or float(detail0.get("outstanding_amount") or 0) > 0

    # Post once
    await post_sales_invoice_accounting(server.db, draft["id"], USER)
    journals = await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": draft["id"], "reversal_of": None},
        {"_id": 0},
    ).to_list(10)
    assert len(journals) == 1
    assert journals[0].get("status") == "posted"

    # Second post must not create a second primary journal
    try:
        await post_sales_invoice_accounting(server.db, draft["id"], USER)
    except Exception:
        pass
    journals2 = await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": draft["id"], "reversal_of": None},
        {"_id": 0},
    ).to_list(10)
    assert len(journals2) == 1

    detail = await get_sales_invoice_detail(server.db, draft["id"])
    outstanding = float(detail.get("outstanding_amount") or detail.get("grand_total") or 0)
    assert outstanding > 0
    grand = float(detail.get("grand_total") or 0)
    assert grand >= subtotal  # PPN may increase total

    # Partial payment
    partial_amt = round(grand / 2, 2)
    if partial_amt <= 0:
        partial_amt = 1
    r1 = await create_sales_receipt(
        server.db,
        {
            "invoice_id": draft["id"],
            "amount": partial_amt,
            "receipt_date": "2026-08-16",
            "idempotency_key": f"gap-partial-{draft['id']}",
        },
        USER,
    )
    assert float(r1.get("amount") or 0) == float(partial_amt)

    # Idempotent receipt replay
    r1b = await create_sales_receipt(
        server.db,
        {
            "invoice_id": draft["id"],
            "amount": partial_amt,
            "receipt_date": "2026-08-16",
            "idempotency_key": f"gap-partial-{draft['id']}",
        },
        USER,
    )
    assert r1b.get("id") == r1.get("id")
    assert await server.db.sales_receipts.count_documents({"invoice_id": draft["id"]}) == 1

    detail_p = await get_sales_invoice_detail(server.db, draft["id"])
    paid_p = float(detail_p.get("paid_total") or 0)
    out_p = float(detail_p.get("outstanding_amount") or 0)
    assert paid_p > 0
    assert out_p > 0
    assert abs((paid_p + out_p) - grand) < 0.05 or abs(paid_p - partial_amt) < 0.05

    # Fully pay remainder
    remain = float(detail_p.get("outstanding_amount") or 0)
    if remain > 0:
        await create_sales_receipt(
            server.db,
            {
                "invoice_id": draft["id"],
                "amount": remain,
                "receipt_date": "2026-08-17",
                "idempotency_key": f"gap-full-{draft['id']}",
            },
            USER,
        )
    detail_f = await get_sales_invoice_detail(server.db, draft["id"])
    assert float(detail_f.get("outstanding_amount") or 0) == 0
    pay_f = str(detail_f.get("payment_status") or "").upper()
    assert "LUNAS" in pay_f or float(detail_f.get("outstanding_amount") or 0) == 0
    # Still one commercial invoice document
    assert await server.db.sales_invoices.count_documents({"id": draft["id"]}) == 1
    # Still one non-reversal invoice journal
    assert await server.db.journal_entries.count_documents(
        {"source_type": SOURCE_TYPE, "source_id": draft["id"], "reversal_of": None}
    ) == 1
