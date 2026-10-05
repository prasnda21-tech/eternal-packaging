"""Draft Faktur qty edit and finalize revalidation. Local test database only."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import httpx
import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.sales_invoices import (
    MSG_INVOICE_QTY_OVER,
    MSG_INVOICE_QTY_OVER_FINALIZE,
    REASON_INVOICE_QTY_OVER,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-draft-qty",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Draft Qty",
        "role": "admin",
    }


async def _seed_po(quantity=12000, lines=None):
    po_id = str(uuid.uuid4())
    if lines is None:
        lines = [{
            "line_idx": 0,
            "line_id": "line-0",
            "name": "Cup Draft",
            "quantity": quantity,
            "unit": "pcs",
            "unit_price": 500,
        }]
    doc = {
        "id": po_id,
        "po_number": f"PO/DQ/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Draft Qty",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Uji",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": lines,
        "total_quantity": sum(int(li.get("quantity") or 0) for li in lines),
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "shipments": [],
        "created_at": "2026-10-05T00:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _cleanup(po_id):
    await server.db.sales_invoices.delete_many({"po_id": po_id})
    await server.db.journal_entries.delete_many({"source_id": {"$exists": True}, "description": "draft-qty-test"})
    await server.db.orders.delete_one({"id": po_id})


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _create(client, po_id, quantity, unit_price=500, line_idx=0):
    res = await client.post("/api/sales-invoices", json={
        "po_id": po_id,
        "invoice_date": "2026-10-05",
        "lines": [{"line_idx": line_idx, "quantity": quantity, "unit_price": unit_price}],
    })
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _put_qty(client, invoice_id, quantity, **extra):
    lines = extra.pop("lines", None)
    payload = {
        "lines": lines if lines is not None else [{"line_idx": 0, "quantity": quantity}],
    }
    payload.update(extra)
    return await client.put(f"/api/sales-invoices/{invoice_id}", json=payload)


@pytest.mark.anyio
async def test_draft_qty_edit_bounds_and_price_recalc():
    po = await _seed_po(12000)
    _admin()
    try:
        async with _client() as client:
            created = await _create(client, po["id"], 5000, unit_price=500)
            detail = await client.get(f"/api/sales-invoices/{created['id']}")
            inv = detail.json()["invoice"]
            assert inv["lines"][0]["max_qty"] == 12000
            assert inv["lines"][0]["po_qty"] == 12000
            assert inv["lines"][0]["other_active_invoiced_qty"] == 0
            assert inv["lines"][0]["current_qty"] == 5000

            for target in (3000, 7000, 10000, 12000):
                res = await _put_qty(client, inv["id"], target)
                assert res.status_code == 200, res.text
                body = res.json()["invoice"]
                assert body["lines"][0]["quantity"] == target
                assert body["lines"][0]["unit_price"] == 500
                assert body["lines"][0]["line_subtotal"] == target * 500
                assert body["total_qty"] == target

            priced = await _put_qty(
                client,
                inv["id"],
                7000,
                discount_amount=100000,
                ppn_percent=11,
            )
            assert priced.status_code == 200, priced.text
            saved = priced.json()["invoice"]
            assert saved["lines"][0]["unit_price"] == 500
            assert saved["lines"][0]["line_subtotal"] == 3500000
            assert saved["subtotal"] == 3500000
            assert saved["discount_amount"] == 100000
            assert saved["after_discount"] == 3400000
            assert saved["ppn_amount"] == 374000
            assert saved["other_charges_total"] == 0
            assert saved["grand_total"] == 3774000
            assert saved["outstanding_amount"] == 3774000
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["lines"][0]["unit_price"] == 500
            assert stored["paid_total"] == 0
            assert "po_qty" not in stored["lines"][0]
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["line_items"][0]["quantity"] == 12000

            zero = await _put_qty(client, inv["id"], 0)
            assert zero.status_code == 400
            assert zero.json()["detail"]["message"] == "Qty Faktur harus berupa bilangan bulat lebih dari 0."

            over = await _put_qty(client, inv["id"], 12001)
            assert over.status_code == 409
            detail = over.json()["detail"]
            assert detail["code"] == REASON_INVOICE_QTY_OVER
            assert detail["message"] == MSG_INVOICE_QTY_OVER
            assert detail["po_qty"] == 12000
            assert detail["other_invoiced_qty"] == 0
            assert detail["invoice_qty"] == 12001
            assert detail["max_qty"] == 12000
            still = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert still["lines"][0]["quantity"] == 7000

            dup = await _put_qty(client, inv["id"], 7000, lines=[
                {"line_idx": 0, "quantity": 6000},
                {"line_idx": 0, "quantity": 8000},
            ])
            assert dup.status_code == 400
            assert dup.json()["detail"]["code"] == "FAKTUR_BARIS_DUPLIKAT"
    finally:
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_other_active_invoices_exclude_current_and_ignore_cancelled():
    po = await _seed_po(12000)
    _admin()
    try:
        async with _client() as client:
            current = await _create(client, po["id"], 5000)
            other = await _create(client, po["id"], 3000)
            fin = await client.post(f"/api/sales-invoices/{other['id']}/finalize")
            assert fin.status_code == 200, fin.text

            detail = await client.get(f"/api/sales-invoices/{current['id']}")
            line = detail.json()["invoice"]["lines"][0]
            assert line["other_active_invoiced_qty"] == 3000
            assert line["max_qty"] == 9000

            ok = await _put_qty(client, current["id"], 8000)
            assert ok.status_code == 200, ok.text
            exact = await _put_qty(client, current["id"], 9000)
            assert exact.status_code == 200, exact.text
            assert exact.json()["invoice"]["lines"][0]["quantity"] == 9000

            for target in (9001, 10000):
                rejected = await _put_qty(client, current["id"], target)
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["detail"]["max_qty"] == 9000
                assert rejected.json()["detail"]["other_invoiced_qty"] == 3000

            cancelled = await client.post(f"/api/sales-invoices/{other['id']}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            widened = await _put_qty(client, current["id"], 12000)
            assert widened.status_code == 200, widened.text
            assert widened.json()["invoice"]["lines"][0]["max_qty"] == 12000
            assert widened.json()["invoice"]["lines"][0]["other_active_invoiced_qty"] == 0
    finally:
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_po_change_expands_or_blocks_finalize_without_rewriting_draft():
    po = await _seed_po(10000)
    _admin()
    try:
        async with _client() as client:
            inv = await _create(client, po["id"], 5000)
            await server.db.orders.update_one(
                {"id": po["id"]},
                {"$set": {"line_items.0.quantity": 12000, "total_quantity": 12000}},
            )
            raised = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert raised.json()["invoice"]["lines"][0]["max_qty"] == 12000
            assert raised.json()["invoice"]["lines"][0]["quantity"] == 5000
            edited = await _put_qty(client, inv["id"], 12000)
            assert edited.status_code == 200, edited.text

            await _put_qty(client, inv["id"], 5000)
            await server.db.orders.update_one(
                {"id": po["id"]},
                {"$set": {"line_items.0.quantity": 6000, "total_quantity": 6000}},
            )
            still_valid = await _put_qty(client, inv["id"], 5000)
            assert still_valid.status_code == 200, still_valid.text
            assert still_valid.json()["invoice"]["lines"][0]["quantity"] == 5000
            assert still_valid.json()["invoice"]["lines"][0]["max_qty"] == 6000

            await server.db.orders.update_one(
                {"id": po["id"]},
                {"$set": {"line_items.0.quantity": 4000, "total_quantity": 4000}},
            )
            before = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert before["lines"][0]["quantity"] == 5000
            blocked = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert blocked.status_code == 409, blocked.text
            body = blocked.json()["detail"]
            assert body["code"] == REASON_INVOICE_QTY_OVER
            assert body["message"] == MSG_INVOICE_QTY_OVER_FINALIZE
            assert body["po_qty"] == 4000
            assert body["invoice_qty"] == 5000
            assert body["max_qty"] == 4000
            after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert after["financial_status"] == "draft"
            assert after["lines"][0]["quantity"] == 5000
            assert after.get("finalized_at") is None
    finally:
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_finalize_exact_max_allows_and_above_max_rejects():
    po = await _seed_po(10000)
    _admin()
    try:
        async with _client() as client:
            other = await _create(client, po["id"], 3000)
            assert (await client.post(f"/api/sales-invoices/{other['id']}/finalize")).status_code == 200
            current = await _create(client, po["id"], 5000)
            exact = await _put_qty(client, current["id"], 7000)
            assert exact.status_code == 200, exact.text
            ok = await client.post(f"/api/sales-invoices/{current['id']}/finalize")
            assert ok.status_code == 200, ok.text
            assert ok.json()["invoice"]["financial_status"] == "finalized"

            over_id = str(uuid.uuid4())
            await server.db.sales_invoices.insert_one({
                "id": over_id,
                "invoice_number": f"INV/DQ/{over_id[:6]}",
                "po_id": po["id"],
                "invoice_date": "2026-10-05",
                "financial_status": "draft",
                "lines": [{
                    "line_idx": 0,
                    "quantity": 8000,
                    "unit_price": 500,
                    "line_subtotal": 4000000,
                    "line_total": 4000000,
                }],
                "subtotal": 4000000,
                "discount_amount": 0,
                "after_discount": 4000000,
                "ppn_percent": 0,
                "ppn_amount": 0,
                "other_charges": [],
                "other_charges_total": 0,
                "grand_total": 4000000,
                "total_qty": 8000,
            })
            rejected = await client.post(f"/api/sales-invoices/{over_id}/finalize")
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["detail"]["message"] == MSG_INVOICE_QTY_OVER_FINALIZE
            assert rejected.json()["detail"]["max_qty"] == 0
    finally:
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_finalized_and_posted_invoice_cannot_change_qty():
    po = await _seed_po(10000)
    _admin()
    journal_id = str(uuid.uuid4())
    try:
        async with _client() as client:
            inv = await _create(client, po["id"], 5000)
            finalized = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert finalized.status_code == 200, finalized.text
            locked = await _put_qty(client, inv["id"], 7000)
            assert locked.status_code == 409
            assert "tidak dapat diubah" in locked.json()["detail"]
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["lines"][0]["quantity"] == 5000
            assert stored["financial_status"] == "finalized"

            await server.db.journal_entries.insert_one({
                "id": journal_id,
                "source_type": "sales_invoice",
                "source_id": inv["id"],
                "reversal_of": None,
                "status": "posted",
                "description": "draft-qty-test",
            })
            posted = await client.get(f"/api/sales-invoices/{inv['id']}")
            assert posted.json()["invoice"]["accounting_posted"] is True
            again = await _put_qty(client, inv["id"], 8000)
            assert again.status_code == 409
            unchanged = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert unchanged["lines"][0]["quantity"] == 5000
            assert unchanged["grand_total"] == stored["grand_total"]
    finally:
        await server.db.journal_entries.delete_one({"id": journal_id})
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_proforma_follows_saved_draft_qty_and_create_stays_within_remaining():
    po = await _seed_po(12000)
    _admin()
    try:
        async with _client() as client:
            other = await _create(client, po["id"], 3000)
            assert (await client.post(f"/api/sales-invoices/{other['id']}/finalize")).status_code == 200
            # A second active invoice makes Proforma unavailable. Cancel it after
            # the remaining check, then edit the only remaining draft.
            current = await _create(client, po["id"], 5000)
            edited = await _put_qty(client, current["id"], 7000)
            assert edited.status_code == 200, edited.text

            too_much = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-10-05",
                "lines": [{"line_idx": 0, "quantity": 2001, "unit_price": 500}],
            })
            assert too_much.status_code == 400, too_much.text
            assert "melebihi sisa PO" in too_much.json()["detail"]
            fits = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-10-05",
                "lines": [{"line_idx": 0, "quantity": 2000, "unit_price": 500}],
            })
            assert fits.status_code == 200, fits.text
            extra = fits.json()["invoice"]
            await server.db.sales_invoices.delete_one({"id": extra["id"]})
            await server.db.sales_invoices.delete_one({"id": other["id"]})

            proforma = await client.get(f"/api/sales-invoices/{current['id']}/proforma")
            assert proforma.status_code == 200, proforma.text
            with pdfplumber.open(io.BytesIO(proforma.content)) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
            assert "7000" in text
            saved = await server.db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0})
            assert saved["lines"][0]["quantity"] == 7000
            assert saved["financial_status"] == "draft"
    finally:
        await _cleanup(po["id"])


@pytest.mark.anyio
async def test_each_line_has_its_own_remaining_qty():
    po = await _seed_po(lines=[
        {"line_idx": 0, "line_id": "a", "name": "Cup", "quantity": 10000, "unit": "pcs", "unit_price": 100},
        {"line_idx": 1, "line_id": "b", "name": "Box", "quantity": 5000, "unit": "pcs", "unit_price": 200},
    ])
    _admin()
    try:
        async with _client() as client:
            res = await client.post("/api/sales-invoices", json={
                "po_id": po["id"],
                "invoice_date": "2026-10-05",
                "lines": [
                    {"line_idx": 0, "quantity": 1000, "unit_price": 100},
                    {"line_idx": 1, "quantity": 1000, "unit_price": 200},
                ],
            })
            assert res.status_code == 200, res.text
            inv = res.json()["invoice"]
            updated = await _put_qty(client, inv["id"], None, lines=[
                {"line_idx": 0, "quantity": 10000},
                {"line_idx": 1, "quantity": 5001},
            ])
            assert updated.status_code == 409, updated.text
            assert updated.json()["detail"]["line_idx"] == 1
            assert updated.json()["detail"]["max_qty"] == 5000
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["lines"][0]["quantity"] == 1000
            assert stored["lines"][1]["quantity"] == 1000
    finally:
        await _cleanup(po["id"])
