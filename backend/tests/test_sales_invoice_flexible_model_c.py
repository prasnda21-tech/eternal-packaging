"""Focused Model C Flexible Sales Invoice tests (PO/SJ shared billing)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_invoice_model_c")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.customer_tracking_invoices import invoice_exclusive_to_order
from services.sales_invoice_allocations import assert_sales_delivery_not_invoiced
from services.sales_delivery_allocations import ALLOCATION_COMMIT_PENDING_FIELD as SJ_PENDING
from services.sales_invoices import (
    FINANCIAL_CANCELLED,
    FINANCIAL_FINALIZED,
    cancel_sales_invoice,
    create_sales_invoice,
    finalize_sales_invoice,
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


@pytest.mark.anyio
async def test_t1_t2_legacy_single_po_partial():
    order = await _order(qty=100)
    inv1 = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 40, "unit_price": 1000}]},
        USER,
    )
    assert inv1["financial_status"] == "draft"
    assert inv1["lines"][0]["source_po_id"] == order["id"]
    inv2 = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 60, "unit_price": 1000}]},
        USER,
    )
    assert inv2["total_qty"] == 60
    with pytest.raises(HTTPException) as exc:
        await create_sales_invoice(
            server.db,
            {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000}]},
            USER,
        )
    assert exc.value.status_code in (400, 409)


@pytest.mark.anyio
async def test_t3_multi_po_one_invoice():
    a = await _order(qty=50, price=1000)
    b = await _order(qty=50, price=2000)
    inv = await create_sales_invoice(
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
    assert inv["is_multi_source"] is True
    assert inv["invoice_number"].startswith("FPX-")
    assert inv["grand_total"] == 10 * 1000 + 5 * 2000


@pytest.mark.anyio
async def test_t6_t7_po_then_sj_and_sj_then_po_shared_capacity():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    sj = str(uuid.uuid4())
    await server.db.sales_deliveries.insert_one({
        "id": sj,
        "po_id": order["id"],
        "sj_number": "SJ-T",
        "is_cancelled": False,
        "line_items": [{"line_id": line_id, "line_idx": 0, "qty": 100, "source_po_id": order["id"], "source_line_idx": 0}],
        "source_allocations": [{
            "source_po_id": order["id"],
            "source_line_id": line_id,
            "source_line_idx": 0,
            "allocated_qty": 100,
            "unit_price": 1000,
        }],
    })
    await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 70, "unit_price": 1000}]},
        USER,
    )
    inv_sj = await create_sales_invoice(
        server.db,
        {
            "source_mode": "sj",
            "source_sj_ids": [sj],
            "lines": [{
                "source_po_id": order["id"],
                "source_sj_id": sj,
                "line_idx": 0,
                "quantity": 30,
            }],
        },
        USER,
    )
    assert inv_sj["total_qty"] == 30
    with pytest.raises(HTTPException):
        await create_sales_invoice(
            server.db,
            {
                "source_mode": "sj",
                "source_sj_ids": [sj],
                "lines": [{
                    "source_po_id": order["id"],
                    "source_sj_id": sj,
                    "line_idx": 0,
                    "quantity": 1,
                }],
            },
            USER,
        )


@pytest.mark.anyio
async def test_t8_same_product_different_prices_kept_separate():
    a = await _order(qty=10, price=1000)
    b = await _order(qty=10, price=2500)
    inv = await create_sales_invoice(
        server.db,
        {
            "source_mode": "po",
            "source_po_ids": [a["id"], b["id"]],
            "lines": [
                {"source_po_id": a["id"], "line_idx": 0, "quantity": 2},
                {"source_po_id": b["id"], "line_idx": 0, "quantity": 2},
            ],
        },
        USER,
    )
    assert len(inv["lines"]) == 2
    assert inv["lines"][0]["unit_price"] != inv["lines"][1]["unit_price"]


@pytest.mark.anyio
async def test_t11_t12_finalize_and_cancel_restores():
    order = await _order(qty=20)
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )
    fin = await finalize_sales_invoice(server.db, inv["id"], USER)
    assert fin["financial_status"] == FINANCIAL_FINALIZED
    # Cancel may block if journal rules — draft cancel not allowed; create draft path release via delete?
    # Business cancel only for finalized without receipts/journal.
    cancelled = await cancel_sales_invoice(server.db, inv["id"], USER)
    assert cancelled["financial_status"] == FINANCIAL_CANCELLED
    again = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )
    assert again["total_qty"] == 20


@pytest.mark.anyio
async def test_t14_pending_sj_rejected():
    order = await _order(qty=10)
    sj = str(uuid.uuid4())
    await server.db.sales_deliveries.insert_one({
        "id": sj,
        "po_id": order["id"],
        "is_cancelled": False,
        SJ_PENDING: True,
        "line_items": [{"line_id": order["line_items"][0]["line_id"], "qty": 10, "line_idx": 0}],
        "source_allocations": [{
            "source_po_id": order["id"],
            "source_line_id": order["line_items"][0]["line_id"],
            "source_line_idx": 0,
            "allocated_qty": 10,
        }],
    })
    with pytest.raises(HTTPException) as exc:
        await create_sales_invoice(
            server.db,
            {
                "source_mode": "sj",
                "source_sj_ids": [sj],
                "lines": [{
                    "source_po_id": order["id"],
                    "source_sj_id": sj,
                    "line_idx": 0,
                    "quantity": 1,
                }],
            },
            USER,
        )
    assert exc.value.status_code in (404, 409)


@pytest.mark.anyio
async def test_t16_different_customer_rejected():
    a = await _order(customer_id="c-a")
    b = await _order(customer_id="c-b")
    with pytest.raises(HTTPException) as exc:
        await create_sales_invoice(
            server.db,
            {
                "source_mode": "po",
                "source_po_ids": [a["id"], b["id"]],
                "lines": [
                    {"source_po_id": a["id"], "line_idx": 0, "quantity": 1},
                    {"source_po_id": b["id"], "line_idx": 0, "quantity": 1},
                ],
            },
            USER,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_t20_tracking_fail_closed_multi():
    order = {"id": "po1", "po_number": "P1", "customer": "X"}
    inv = {
        "po_id": "po1",
        "po_number": "P1",
        "customer": "X",
        "is_multi_source": True,
        "source_po_ids": ["po1", "po2"],
        "lines": [],
    }
    assert invoice_exclusive_to_order(inv, order) is False


@pytest.mark.anyio
async def test_t21_sj_cancel_blocked_when_invoiced():
    order = await _order(qty=10)
    sj = str(uuid.uuid4())
    line_id = order["line_items"][0]["line_id"]
    await server.db.sales_deliveries.insert_one({
        "id": sj,
        "po_id": order["id"],
        "is_cancelled": False,
        "line_items": [{"line_id": line_id, "line_idx": 0, "qty": 10}],
        "source_allocations": [{
            "source_po_id": order["id"],
            "source_line_id": line_id,
            "source_line_idx": 0,
            "allocated_qty": 10,
            "unit_price": 1000,
        }],
    })
    await create_sales_invoice(
        server.db,
        {
            "source_mode": "sj",
            "source_sj_ids": [sj],
            "lines": [{
                "source_po_id": order["id"],
                "source_sj_id": sj,
                "line_idx": 0,
                "quantity": 5,
            }],
        },
        USER,
    )
    with pytest.raises(HTTPException) as exc:
        await assert_sales_delivery_not_invoiced(server.db, sj)
    assert exc.value.status_code == 409
