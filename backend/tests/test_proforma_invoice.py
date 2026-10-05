"""Proforma Invoice reads one saved non-cancelled invoice. Local test database only."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import pdfplumber
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_proforma_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

assert os.environ["DB_NAME"] != "eternal_packaging"

from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS  # noqa: E402
from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402
from services.sales_invoices import (  # noqa: E402
    PROFORMA_WARNING,
    cancel_sales_invoice,
    finalize_sales_invoice,
    generate_sales_invoice_pdf_bytes,
    update_sales_invoice,
)

VIEWER = {
    "id": "u-proforma-view",
    "email": "proforma-view@example.com",
    "role": "crm",
    "name": "CRM Proforma",
    "permissions_override": {
        "proforma_invoice.view": True,
        "sales.invoice.create": False,
        "sales.invoice.edit": False,
        "sales_order.create": False,
        "po.edit": False,
        "sales.receipt.create": False,
    },
}
OPERATOR = {
    "id": "u-proforma-operator",
    "email": "operator-proforma@example.com",
    "role": "operator",
    "name": "Operator",
}
GUEST = {
    "id": "u-proforma-guest",
    "email": "guest-proforma@example.com",
    "role": "guest",
    "name": "Guest",
    "permissions_override": {"po.view": True},
}
ADMIN = {
    "id": "u-proforma-admin",
    "email": "admin-proforma@example.com",
    "role": "admin",
    "name": "Admin",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]
    yield
    app.dependency_overrides.pop(current_user, None)


def db():
    return server_mod.db


def test_permission_catalog_lists_proforma_view_only():
    labeled = {key: (module, label) for module, key, label in PERMISSION_CATALOG}
    assert labeled["proforma_invoice.view"] == ("Penjualan", "Lihat Proforma Invoice")
    assert "proforma_invoice.create" not in labeled
    assert "proforma_invoice.edit" not in labeled
    assert "nav.proforma_invoice" not in labeled
    assert "proforma_invoice.view" in ALL_PERMISSIONS
    assert "proforma_invoice.view" in ROLE_DEFAULT_PERMISSIONS["admin"]
    assert "proforma_invoice.view" not in ROLE_DEFAULT_PERMISSIONS["crm"]


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _assert_proforma_warning(text: str) -> None:
    assert PROFORMA_WARNING in text
    assert "belum final" not in text.lower()
    assert "bukan Faktur Penjualan final" not in text


async def _counts():
    return {
        "invoices": await db().sales_invoices.count_documents({}),
        "orders": await db().orders.count_documents({}),
        "journals": await db().journal_entries.count_documents({}),
        "receipts": await db().sales_receipts.count_documents({}),
    }


async def _seed_order(**extra):
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": f"PO/PRF/{uuid.uuid4().hex[:6].upper()}",
        "customer": "PT Proforma Uji",
        "address": "Jl. Draft 1",
        "sales": "Sari Sales",
        "cancelled": False,
        "current_stage": 0,
        "notes": "CATATAN PRODUKSI INTERNAL",
        "line_items": [{
            "name": "Paper Cup",
            "quantity": 100,
            "unit": "pcs",
            "unit_price": 1,
            "notes": "CATATAN SABLON INTERNAL",
        }],
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }
    doc.update(extra)
    await db().orders.insert_one(doc)
    return doc


def _draft(order, **extra):
    doc = {
        "id": str(uuid.uuid4()),
        "invoice_number": f"INV/PRF/{uuid.uuid4().hex[:6].upper()}",
        "invoice_index": 1,
        "po_id": order["id"],
        "po_number": order["po_number"],
        "customer": order["customer"],
        "address": order["address"],
        "sales": order["sales"],
        "invoice_date": "2026-10-01",
        "payment_term_days": 14,
        "due_date": "2026-10-15",
        "notes": "Catatan faktur uji",
        "lines": [{
            "line_idx": 0,
            "line_id": "line-1",
            "item_name": "Paper Cup",
            "quantity": 100,
            "unit": "pcs",
            "unit_price": 10000,
            "line_subtotal": 1000000,
            "line_total": 1000000,
        }],
        "total_qty": 100,
        "subtotal": 1000000,
        "discount_amount": 50000,
        "after_discount": 950000,
        "ppn_percent": 11,
        "ppn_amount": 104500,
        "other_charges": [{"description": "Biaya kirim", "amount": 25000}],
        "other_charges_total": 25000,
        "grand_total": 1079500,
        "paid_total": 0,
        "outstanding_amount": 1079500,
        "payment_status": "BELUM DIBAYAR",
        "financial_status": "draft",
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }
    doc.update(extra)
    return doc


async def _insert_invoice(doc):
    await db().sales_invoices.insert_one(dict(doc))
    return doc


async def _cleanup(order_id):
    await db().sales_invoices.delete_many({"po_id": order_id})
    await db().orders.delete_many({"id": order_id})


def _as(user):
    app.dependency_overrides[current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_draft_proforma_matches_saved_invoice_and_writes_nothing():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    before = await _counts()
    stored_order = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    stored_invoice = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    try:
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            assert availability.status_code == 200, availability.text
            assert availability.json() == {
                "available": True,
                "invoice_id": invoice["id"],
                "invoice_number": invoice["invoice_number"],
            }
            response = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/pdf")
        disposition = response.headers["content-disposition"]
        assert "inline" in disposition
        safe_number = invoice["invoice_number"].replace("/", "_")
        assert f'filename="Proforma_{safe_number}.pdf"' in disposition
        text = _pdf_text(response.content)
        assert "PROFORMA INVOICE" in text
        _assert_proforma_warning(text)
        assert "FAKTUR PENJUALAN" not in text
        assert "Telah Dibayar" not in text
        assert "Sisa Tagihan" not in text
        assert "BELUM DIBAYAR" not in text
        assert invoice["invoice_number"] in text
        assert "PT Proforma Uji" in text
        assert "Jl. Draft 1" in text
        assert "Sari Sales" in text
        assert "01/10/26" in text
        assert "15/10/26" in text
        assert "14" in text
        assert "Catatan faktur uji" in text
        assert "CATATAN PRODUKSI INTERNAL" not in text
        assert "100 pcs" in text
        assert "Rp 10.000" in text
        assert "Rp 1.000.000" in text
        assert "Rp 50.000" in text
        assert "11%" in text
        assert "Rp 104.500" in text
        assert "Biaya kirim" in text
        assert "Rp 25.000" in text
        assert "Rp 1.079.500" in text
        assert await _counts() == before
        assert await db().orders.find_one({"id": order["id"]}, {"_id": 0}) == stored_order
        fresh = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert fresh == stored_invoice
        assert fresh["invoice_number"] == invoice["invoice_number"]
        assert fresh["financial_status"] == "draft"
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_proforma_forbidden_without_permission_even_with_po_view():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    try:
        async with _as(OPERATOR) as client:
            denied = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
            hidden = await client.get(f"/api/orders/{order['id']}/proforma")
        assert denied.status_code == 403
        assert hidden.status_code == 403
        async with _as(GUEST) as client:
            guest = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
        assert guest.status_code == 403
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_crm_can_view_proforma_but_cannot_create_invoice():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    before = await db().sales_invoices.count_documents({"po_id": order["id"]})
    try:
        async with _as(VIEWER) as client:
            viewed = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
            created = await client.post("/api/sales-invoices", json={"po_id": order["id"], "lines": []})
        assert viewed.status_code == 200, viewed.text
        assert created.status_code == 403
        assert await db().sales_invoices.count_documents({"po_id": order["id"]}) == before
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_edited_draft_is_what_the_next_proforma_prints():
    order = await _seed_order()
    invoice = _draft(
        order,
        discount_amount=0,
        after_discount=1000000,
        ppn_percent=0,
        ppn_amount=0,
        other_charges=[],
        other_charges_total=0,
        grand_total=1000000,
        outstanding_amount=1000000,
    )
    await _insert_invoice(invoice)
    try:
        await update_sales_invoice(db(), invoice["id"], {
            "lines": [{"line_idx": 0, "unit_price": 11000}],
            "discount_amount": 20000,
            "ppn_percent": 11,
            "notes": "Harga diperbarui",
        }, ADMIN)
        saved = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert saved["lines"][0]["unit_price"] == 11000
        assert saved["lines"][0]["line_subtotal"] == 1100000
        assert saved["discount_amount"] == 20000
        assert saved["ppn_percent"] == 11
        assert saved["ppn_amount"] == 118800
        assert saved["grand_total"] == 1198800
        before = await _counts()
        async with _as(VIEWER) as client:
            response = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
        assert response.status_code == 200, response.text
        text = _pdf_text(response.content)
        assert "Rp 11.000" in text
        assert "Rp 1.100.000" in text
        assert "Rp 20.000" in text
        assert "PPN (11.0%)" in text
        assert "Rp 118.800" in text
        assert "Rp 1.198.800" in text
        assert "Harga diperbarui" in text
        assert await _counts() == before
        assert (await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}))["updated_at"] == saved["updated_at"]
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_finalized_invoice_keeps_proforma_and_official_pdf():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    try:
        await finalize_sales_invoice(db(), invoice["id"], ADMIN)
        stored = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        assert stored["financial_status"] == "finalized"
        before = await _counts()
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            proforma = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
        assert availability.status_code == 200
        assert availability.json()["available"] is True
        assert availability.json()["invoice_id"] == invoice["id"]
        assert proforma.status_code == 200, proforma.text
        text = _pdf_text(proforma.content)
        assert "PROFORMA INVOICE" in text
        _assert_proforma_warning(text)
        assert "FAKTUR PENJUALAN" not in text
        assert await _counts() == before
        assert await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) == stored
        async with _as(ADMIN) as client:
            official = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
        assert official.status_code == 200, official.text
        assert "inline" in official.headers["content-disposition"]
        assert "Faktur_Penjualan_" in official.headers["content-disposition"]
        assert "Proforma_" not in official.headers["content-disposition"]
        official_text = _pdf_text(official.content)
        assert "FAKTUR PENJUALAN" in official_text
        assert "Telah Dibayar" in official_text
        assert "Sisa Tagihan" in official_text
        assert "BELUM DIBAYAR" in official_text
        assert "PROFORMA INVOICE" not in official_text
        assert PROFORMA_WARNING not in official_text
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_posted_accounting_keeps_proforma_without_reading_the_journal():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    journal_id = str(uuid.uuid4())
    try:
        await finalize_sales_invoice(db(), invoice["id"], ADMIN)
        await db().journal_entries.insert_one({
            "id": journal_id,
            "source_type": "sales_invoice",
            "source_id": invoice["id"],
            "status": "posted",
            "reversal_of": None,
        })
        stored = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
        before = await _counts()
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            proforma = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
        assert availability.json()["invoice_id"] == invoice["id"]
        assert proforma.status_code == 200, proforma.text
        _assert_proforma_warning(_pdf_text(proforma.content))
        assert await _counts() == before
        assert await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) == stored
        assert await db().journal_entries.count_documents({"id": journal_id}) == 1
    finally:
        await db().journal_entries.delete_many({"id": journal_id})
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_payment_status_does_not_hide_proforma():
    order = await _seed_order()
    invoice = await _insert_invoice(_draft(order))
    try:
        await finalize_sales_invoice(db(), invoice["id"], ADMIN)
        for payment_status, paid_total in (("DP", 100000), ("LUNAS", 1079500)):
            await db().sales_invoices.update_one(
                {"id": invoice["id"]},
                {"$set": {
                    "payment_status": payment_status,
                    "paid_total": paid_total,
                    "outstanding_amount": 1079500 - paid_total,
                }},
            )
            stored = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
            before = await _counts()
            async with _as(VIEWER) as client:
                availability = await client.get(f"/api/orders/{order['id']}/proforma")
                proforma = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
            assert availability.json()["available"] is True
            assert proforma.status_code == 200, proforma.text
            text = _pdf_text(proforma.content)
            _assert_proforma_warning(text)
            assert payment_status not in text
            assert "Telah Dibayar" not in text
            assert "Sisa Tagihan" not in text
            assert await _counts() == before
            assert await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) == stored
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_cancelled_po_and_missing_invoice_reject_proforma():
    order = await _seed_order(cancelled=True)
    invoice = await _insert_invoice(_draft(order))
    before_order = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    try:
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            rejected = await client.get(f"/api/sales-invoices/{invoice['id']}/proforma")
            missing = await client.get(f"/api/sales-invoices/{uuid.uuid4()}/proforma")
        assert availability.status_code == 200
        assert availability.json()["available"] is False
        assert rejected.status_code == 409
        assert missing.status_code == 404
        assert await db().orders.find_one({"id": order["id"]}, {"_id": 0}) == before_order
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_cancelled_invoice_is_replaced_only_by_a_new_single_draft():
    order = await _seed_order()
    first = await _insert_invoice(_draft(order))
    try:
        await finalize_sales_invoice(db(), first["id"], ADMIN)
        await cancel_sales_invoice(db(), first["id"], ADMIN)
        async with _as(VIEWER) as client:
            gone = await client.get(f"/api/orders/{order['id']}/proforma")
            rejected = await client.get(f"/api/sales-invoices/{first['id']}/proforma")
        assert gone.json()["available"] is False
        assert rejected.status_code == 409
        second = await _insert_invoice(_draft(order, invoice_index=2))
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            old = await client.get(f"/api/sales-invoices/{first['id']}/proforma")
            new = await client.get(f"/api/sales-invoices/{second['id']}/proforma")
        assert availability.json()["invoice_id"] == second["id"]
        assert old.status_code == 409
        assert new.status_code == 200
        assert second["invoice_number"] in _pdf_text(new.content)
        assert first["invoice_number"] not in _pdf_text(new.content)
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_two_active_drafts_are_not_chosen():
    order = await _seed_order()
    first = await _insert_invoice(_draft(order))
    second = await _insert_invoice(_draft(order))
    try:
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            left = await client.get(f"/api/sales-invoices/{first['id']}/proforma")
            right = await client.get(f"/api/sales-invoices/{second['id']}/proforma")
        assert availability.json()["available"] is False
        assert left.status_code == 409
        assert right.status_code == 409
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_draft_and_finalized_together_are_not_chosen():
    order = await _seed_order(line_items=[{
        "name": "Paper Cup",
        "quantity": 200,
        "unit": "pcs",
        "unit_price": 1,
        "notes": "CATATAN SABLON INTERNAL",
    }])
    draft = await _insert_invoice(_draft(order))
    finalized = await _insert_invoice(_draft(order, invoice_index=2))
    try:
        await finalize_sales_invoice(db(), finalized["id"], ADMIN)
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            left = await client.get(f"/api/sales-invoices/{draft['id']}/proforma")
            right = await client.get(f"/api/sales-invoices/{finalized['id']}/proforma")
        assert availability.json() == {"available": False, "invoice_id": None, "invoice_number": None}
        assert left.status_code == 409
        assert right.status_code == 409
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_cancelled_invoice_plus_one_finalized_uses_the_finalized():
    order = await _seed_order()
    first = await _insert_invoice(_draft(order))
    try:
        await finalize_sales_invoice(db(), first["id"], ADMIN)
        await cancel_sales_invoice(db(), first["id"], ADMIN)
        second = await _insert_invoice(_draft(order, invoice_index=2))
        await finalize_sales_invoice(db(), second["id"], ADMIN)
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
            old = await client.get(f"/api/sales-invoices/{first['id']}/proforma")
            new = await client.get(f"/api/sales-invoices/{second['id']}/proforma")
        assert availability.json()["invoice_id"] == second["id"]
        assert old.status_code == 409
        assert new.status_code == 200, new.text
        text = _pdf_text(new.content)
        _assert_proforma_warning(text)
        assert second["invoice_number"] in text
        assert first["invoice_number"] not in text
    finally:
        await _cleanup(order["id"])


@pytest.mark.anyio
async def test_no_invoice_is_unavailable():
    order = await _seed_order()
    try:
        async with _as(VIEWER) as client:
            availability = await client.get(f"/api/orders/{order['id']}/proforma")
        assert availability.status_code == 200
        assert availability.json() == {"available": False, "invoice_id": None, "invoice_number": None}
    finally:
        await _cleanup(order["id"])


def test_official_pdf_renderer_stays_on_the_invoice_title():
    invoice = _draft({
        "id": "po-local",
        "po_number": "PO/LOCAL/1",
        "customer": "PT Proforma Uji",
        "address": "Jl. Draft 1",
        "sales": "Sari Sales",
    })
    official = _pdf_text(generate_sales_invoice_pdf_bytes(invoice))
    proforma = _pdf_text(generate_sales_invoice_pdf_bytes(invoice, document_type="proforma"))
    assert "FAKTUR PENJUALAN" in official
    assert "Telah Dibayar" in official
    assert "Sisa Tagihan" in official
    assert "BELUM DIBAYAR" in official
    assert "PROFORMA INVOICE" not in official
    assert "PROFORMA INVOICE" in proforma
    _assert_proforma_warning(proforma)
    assert "FAKTUR PENJUALAN" not in proforma
    assert PROFORMA_WARNING not in official
    assert "Telah Dibayar" not in proforma
    assert "Sisa Tagihan" not in proforma
