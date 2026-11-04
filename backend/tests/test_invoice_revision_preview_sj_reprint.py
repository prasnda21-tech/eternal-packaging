"""Regression: revised PO billing preview must match atomic invoice capacity.

No production data or financial counters are mutated in application paths.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_invoice_revision")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.sales_invoices import get_po_invoice_preview
from services.sales_invoice_allocations import BILLING_COUNTERS


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


def order_doc(po_id, qty):
    return {
        "id": po_id, "po_number": f"PO/REV/{po_id[:8]}", "customer": "Test",
        "line_items": [
            {"line_id": f"{po_id}_line_0", "line_idx": 0, "name": "Cup", "unit": "pcs", "quantity": qty, "unit_price": 450},
        ],
    }


@pytest.mark.anyio
async def test_revision_500_to_1000_active_invoice_only_allows_remaining_500(db):
    po_id = str(uuid.uuid4())
    other_po = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    await db.orders.insert_one(order_doc(po_id, 1000))
    await db.sales_invoices.insert_one({
        "id": str(uuid.uuid4()), "po_id": po_id,
        "financial_status": "finalized",
        "lines": [{"line_idx": 0, "source_po_id": po_id, "source_po_line_id": line_id, "quantity": 500}],
    })
    # A different PO in the same multi-source Faktur must not consume this line.
    await db.sales_invoices.insert_one({
        "id": str(uuid.uuid4()), "po_id": other_po,
        "source_po_ids": [other_po, po_id],
        "financial_status": "finalized",
        "lines": [
            {"line_idx": 0, "source_po_id": other_po, "source_po_line_id": f"{other_po}_line_0", "quantity": 900},
        ],
    })
    try:
        preview = await get_po_invoice_preview(db, po_id)
        assert preview["lines"][0]["invoiced_cumulative"] == 500
        assert preview["lines"][0]["remaining_invoice_qty"] == 500
        assert preview["total_remaining_invoice_qty"] == 500
        assert preview["billing_reconciliation_required"] is False
    finally:
        await db.sales_invoices.delete_many({"po_id": {"$in": [po_id, other_po]}})
        await db.orders.delete_many({"id": po_id})


@pytest.mark.anyio
async def test_revision_orphan_billing_counter_does_not_offer_full_1000(db):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    counter_id = f"{po_id}::{line_id}"
    await db.orders.insert_one(order_doc(po_id, 1000))
    await db[BILLING_COUNTERS].insert_one({
        "id": counter_id, "source_po_id": po_id, "source_line_id": line_id,
        "invoiced_qty": 500, "ordered_qty": 500,
        "claims_by_invoice": {"historic_missing_invoice": {"qty": 500, "state": "committed"}},
    })
    try:
        preview = await get_po_invoice_preview(db, po_id)
        assert preview["lines"][0]["billing_counter_discrepancy"] is True
        assert preview["lines"][0]["invoice_document_qty"] == 0
        assert preview["lines"][0]["billing_counter_qty"] == 500
        assert preview["lines"][0]["remaining_invoice_qty"] == 500
        assert preview["total_remaining_invoice_qty"] == 500
        assert preview["billing_reconciliation_required"] is True
        persisted = await db[BILLING_COUNTERS].find_one({"id": counter_id})
        assert persisted["invoiced_qty"] == 500
        assert "historic_missing_invoice" in persisted["claims_by_invoice"]
    finally:
        await db[BILLING_COUNTERS].delete_one({"id": counter_id})
        await db.orders.delete_many({"id": po_id})


def test_sj_reprint_uses_existing_pdf_without_creating_new_delivery():
    page = (Path(__file__).resolve().parents[2] /
            "frontend/src/pages/PengirimanPesanan.jsx").read_text(encoding="utf-8")
    panel = page.split('data-testid="existing-sj-reprint"', 1)[1].split(
        '<div className="ux90-table-head">', 1,
    )[0]
    assert "poSummary.deliveries" in panel
    assert "handlePrintPdf(d.id)" in panel
    assert "api.createSalesDelivery" not in panel
    assert "Cetak Ulang" in panel
