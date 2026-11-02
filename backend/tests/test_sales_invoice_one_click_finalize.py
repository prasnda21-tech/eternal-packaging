"""Focused one-click Simpan Faktur → auto finalize (flag OFF path)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_invoice_one_click")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.sales_invoices import (
    FINANCIAL_DRAFT,
    FINANCIAL_FINALIZED,
    cancel_sales_invoice,
    create_and_finalize_sales_invoice,
    create_sales_invoice,
    finalize_sales_invoice,
    update_draft_and_finalize_sales_invoice,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


USER = {"id": "u1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


async def _order(*, qty=100, price=1000.0, customer_id="cust-1", currency="IDR", branch="br-1"):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Test",
        "customer_id": customer_id,
        "currency": currency,
        "operational_branch_id": branch,
        "branch_id": "1",
        "cancelled": False,
        "line_items": [{
            "line_id": line_id,
            "name": "Produk A",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
        }],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _sj_for(order, *, qty=None):
    sj_id = str(uuid.uuid4())
    line = (order.get("line_items") or [])[0]
    qty = int(qty if qty is not None else line["quantity"])
    line_id = line["line_id"]
    doc = {
        "id": sj_id,
        "po_id": order["id"],
        "sj_number": f"SJ-{sj_id[:8]}",
        "is_cancelled": False,
        "operational_branch_id": order.get("operational_branch_id"),
        "branch_id": order.get("branch_id"),
        "line_items": [{
            "line_id": line_id,
            "line_idx": 0,
            "qty": qty,
            "source_po_id": order["id"],
            "source_line_idx": 0,
        }],
        "source_allocations": [{
            "source_po_id": order["id"],
            "source_line_id": line_id,
            "source_line_idx": 0,
            "allocated_qty": qty,
            "unit_price": float(line.get("unit_price") or 0),
        }],
    }
    await server.db.sales_deliveries.insert_one(doc)
    doc.pop("_id", None)
    return doc


@pytest.mark.anyio
async def test_t1_single_po_create_and_finalize():
    order = await _order(qty=50)
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )
    assert inv["financial_status"] == FINANCIAL_FINALIZED
    assert inv["payment_status"] == "BELUM DIBAYAR"
    assert inv.get("accounting_posted") is not True
    assert inv["total_qty"] == 20
    assert inv.get("finalized_at")


@pytest.mark.anyio
async def test_t2_multi_po_create_and_finalize():
    a = await _order(qty=50, price=1000)
    b = await _order(qty=50, price=2000)
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {
            "source_mode": "po",
            "source_po_ids": [a["id"], b["id"]],
            "lines": [
                {"source_po_id": a["id"], "line_idx": 0, "quantity": 10},
                {"source_po_id": b["id"], "line_idx": 0, "quantity": 5},
            ],
        },
        USER,
    )
    assert inv["financial_status"] == FINANCIAL_FINALIZED
    assert inv["is_multi_source"] is True
    assert inv["payment_status"] == "BELUM DIBAYAR"
    assert inv["grand_total"] == 10 * 1000 + 5 * 2000


@pytest.mark.anyio
async def test_t3_t4_single_and_multi_sj_finalize():
    o1 = await _order(qty=40)
    sj1 = await _sj_for(o1, qty=40)
    inv1 = await create_and_finalize_sales_invoice(
        server.db,
        {
            "source_mode": "sj",
            "source_sj_ids": [sj1["id"]],
            "lines": [{
                "source_sj_id": sj1["id"],
                "source_po_id": o1["id"],
                "line_idx": 0,
                "quantity": 15,
                "unit_price": 1000,
            }],
        },
        USER,
    )
    assert inv1["financial_status"] == FINANCIAL_FINALIZED
    assert inv1["payment_status"] == "BELUM DIBAYAR"

    o2 = await _order(qty=30)
    o3 = await _order(qty=30, price=2500)
    sj2 = await _sj_for(o2, qty=30)
    sj3 = await _sj_for(o3, qty=30)
    inv2 = await create_and_finalize_sales_invoice(
        server.db,
        {
            "source_mode": "sj",
            "source_sj_ids": [sj2["id"], sj3["id"]],
            "lines": [
                {
                    "source_sj_id": sj2["id"],
                    "source_po_id": o2["id"],
                    "line_idx": 0,
                    "quantity": 10,
                    "unit_price": 1000,
                },
                {
                    "source_sj_id": sj3["id"],
                    "source_po_id": o3["id"],
                    "line_idx": 0,
                    "quantity": 8,
                    "unit_price": 2500,
                },
            ],
        },
        USER,
    )
    assert inv2["financial_status"] == FINANCIAL_FINALIZED
    assert inv2["is_multi_source"] is True
    assert inv2["payment_status"] == "BELUM DIBAYAR"


@pytest.mark.anyio
async def test_t5_t15_legacy_draft_put_finalizes_locked_after():
    order = await _order(qty=40)
    draft = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 10, "unit_price": 1000}]},
        USER,
    )
    assert draft["financial_status"] == FINANCIAL_DRAFT
    line = (draft.get("lines") or [])[0]
    fin = await update_draft_and_finalize_sales_invoice(
        server.db,
        draft["id"],
        {
            "discount_amount": 0,
            "lines": [{
                "invoice_line_id": line.get("invoice_line_id"),
                "line_idx": line.get("line_idx", 0),
                "line_id": line.get("line_id"),
                "quantity": 10,
                "unit_price": 1000,
            }],
        },
        USER,
    )
    assert fin["financial_status"] == FINANCIAL_FINALIZED
    with pytest.raises(HTTPException) as exc:
        await update_draft_and_finalize_sales_invoice(
            server.db,
            draft["id"],
            {
                "discount_amount": 1,
                "lines": [{
                    "invoice_line_id": line.get("invoice_line_id"),
                    "line_idx": line.get("line_idx", 0),
                    "quantity": 10,
                    "unit_price": 999,
                }],
            },
            USER,
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_t6_t7_idempotent_double_create():
    order = await _order(qty=50)
    key = f"idem-{uuid.uuid4()}"
    payload = {
        "po_id": order["id"],
        "idempotency_key": key,
        "lines": [{"line_idx": 0, "quantity": 12, "unit_price": 1000}],
    }
    a = await create_and_finalize_sales_invoice(server.db, payload, USER)
    b = await create_and_finalize_sales_invoice(server.db, payload, USER)
    assert a["id"] == b["id"]
    assert a["invoice_number"] == b["invoice_number"]
    assert b["financial_status"] == FINANCIAL_FINALIZED
    count = await server.db.sales_invoices.count_documents({"idempotency_key": key})
    assert count == 1


@pytest.mark.anyio
async def test_t8_finalize_failure_keeps_draft_identity():
    order = await _order(qty=20)
    draft = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 5, "unit_price": 1000}]},
        USER,
    )
    assert draft["financial_status"] == FINANCIAL_DRAFT
    # Break monetary invariant so finalize fails; draft identity must remain.
    await server.db.sales_invoices.update_one(
        {"id": draft["id"]},
        {"$set": {
            "subtotal": 5000.0,
            "discount_amount": 0.0,
            "after_discount": 5000.0,
            "ppn_percent": 0.0,
            "ppn_amount": 0.0,
            "other_charges_total": 0.0,
            "grand_total": 1.0,
        }},
    )
    with pytest.raises(HTTPException) as broken:
        await finalize_sales_invoice(server.db, draft["id"], USER)
    assert broken.value.status_code == 400
    stored = await server.db.sales_invoices.find_one({"id": draft["id"]}, {"_id": 0})
    assert stored["financial_status"] == FINANCIAL_DRAFT
    assert stored["invoice_number"] == draft["invoice_number"]
    assert stored["id"] == draft["id"]


@pytest.mark.anyio
async def test_t9_t16_billing_capacity_and_prices():
    order = await _order(qty=30, price=1500)
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 10, "unit_price": 1500}]},
        USER,
    )
    assert inv["lines"][0]["unit_price"] == 1500
    assert inv["lines"][0]["quantity"] == 10
    with pytest.raises(HTTPException):
        await create_and_finalize_sales_invoice(
            server.db,
            {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 25, "unit_price": 1500}]},
            USER,
        )
    inv2 = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1500}]},
        USER,
    )
    assert inv2["financial_status"] == FINANCIAL_FINALIZED
    assert inv2["total_qty"] == 20


@pytest.mark.anyio
async def test_t10_t11_no_journal_payment_unpaid():
    order = await _order(qty=10)
    before_j = await server.db.journal_entries.count_documents({})
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 5, "unit_price": 1000}]},
        USER,
    )
    assert inv["financial_status"] == FINANCIAL_FINALIZED
    assert inv["payment_status"] == "BELUM DIBAYAR"
    assert await server.db.journal_entries.count_documents({}) == before_j


@pytest.mark.anyio
async def test_t12_cancel_after_one_click_finalize():
    order = await _order(qty=15)
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 5, "unit_price": 1000}]},
        USER,
    )
    cancelled = await cancel_sales_invoice(server.db, inv["id"], USER)
    assert cancelled["financial_status"] == "cancelled"


@pytest.mark.anyio
async def test_t13_create_service_still_draft_pending_not_finalized():
    """Service-level create remains draft; pending invoices are not auto-finalized."""
    order = await _order(qty=10)
    draft = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 3, "unit_price": 1000}]},
        USER,
    )
    assert draft["financial_status"] == FINANCIAL_DRAFT
    stored = await server.db.sales_invoices.find_one({"id": draft["id"]}, {"_id": 0})
    assert stored["financial_status"] == FINANCIAL_DRAFT


@pytest.mark.anyio
async def test_t18_legacy_invoice_untouched():
    order = await _order(qty=10)
    legacy_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": legacy_id,
        "invoice_number": f"INV/LEG/{legacy_id[:6]}",
        "po_id": order["id"],
        "po_number": order["po_number"],
        "invoice_date": "2026-01-01",
        "customer": "PT Legacy",
        "lines": [{"line_idx": 0, "quantity": 2, "unit_price": 1000, "line_total": 2000}],
        "subtotal": 2000.0,
        "discount_amount": 0.0,
        "after_discount": 2000.0,
        "ppn_percent": 0.0,
        "ppn_amount": 0.0,
        "grand_total": 2000.0,
        "payment_status": "BELUM DIBAYAR",
        "created_at": "2026-01-01T00:00:00Z",
    })
    # One-click create on remaining capacity must not mutate legacy doc.
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000}]},
        USER,
    )
    assert inv["id"] != legacy_id
    legacy = await server.db.sales_invoices.find_one({"id": legacy_id}, {"_id": 0})
    assert "financial_status" not in legacy
    assert legacy.get("finalized_at") is None
    assert legacy["grand_total"] == 2000.0
