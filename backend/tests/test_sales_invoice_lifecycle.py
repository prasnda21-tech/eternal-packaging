"""Sales invoice financial lifecycle — Phase 0 (no journals)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes
from services.sales_accounting_settings import SETTINGS_KEY


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-lifecycle",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Lifecycle",
        "role": "admin",
    }


async def _seed_po(stage: int = 5, payment_status: str = "Waiting") -> dict:
    po_id = str(uuid.uuid4())
    po_number = f"PO/LC/{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": "PT Lifecycle",
        "outlet": "Outlet LC",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup LC", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": stage,
        "status_label": "Penjadwalan",
        "payment_status": payment_status,
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": [],
        "created_at": "2026-09-01T00:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _create_invoice(client, po_id, **extra) -> dict:
    payload = {
        "po_id": po_id,
        "invoice_date": "2026-09-01",
        "discount_amount": extra.pop("discount_amount", 0),
        "ppn_percent": extra.pop("ppn_percent", 0),
        "lines": [{"line_idx": 0, "quantity": extra.pop("quantity", 100), "unit_price": extra.pop("unit_price", 1000)}],
    }
    payload.update(extra)
    res = await client.post("/api/sales-invoices", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _journal_counts():
    entries = await server.db.journal_entries.count_documents({})
    lines = await server.db.journal_lines.count_documents({})
    return entries, lines


@pytest.mark.anyio
async def test_new_invoice_is_draft_and_editable():
    po = await _seed_po()
    _admin()
    before = await _journal_counts()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            assert inv["financial_status"] == "draft"
            assert inv["accounting_posted"] is False
            assert inv.get("after_discount") == inv["subtotal"]
            res = await client.put(
                f"/api/sales-invoices/{inv['id']}",
                json={"discount_amount": 5000, "lines": [{"line_idx": 0, "unit_price": 1000}]},
            )
            assert res.status_code == 200, res.text
            updated = res.json()["invoice"]
            assert updated["discount_amount"] == 5000.0
            assert updated["after_discount"] == 95000.0
            assert updated["financial_status"] == "draft"
            assert await _journal_counts() == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_missing_lifecycle_reads_draft_get_zero_write():
    po = await _seed_po()
    inv_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": inv_id,
        "invoice_number": f"INV/LC/{inv_id[:8]}",
        "po_id": po["id"],
        "po_number": po["po_number"],
        "invoice_date": "2026-09-01",
        "customer": "PT Legacy",
        "lines": [{"line_idx": 0, "quantity": 10, "unit_price": 1000, "line_total": 10000}],
        "subtotal": 10000.0,
        "discount_amount": 0.0,
        "after_discount": 10000.0,
        "ppn_percent": 0.0,
        "ppn_amount": 0.0,
        "grand_total": 10000.0,
        "created_at": "2026-08-01T00:00:00Z",
    })
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get(f"/api/sales-invoices/{inv_id}")
            assert res.status_code == 200, res.text
            body = res.json()["invoice"]
            assert body["financial_status"] == "draft"
            assert body["accounting_posted"] is False
        stored = await server.db.sales_invoices.find_one({"id": inv_id}, {"_id": 0})
        assert "financial_status" not in stored
        assert stored.get("finalized_at") is None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_finalize_checks_invariant_and_locks_financial_fields():
    po = await _seed_po(stage=7, payment_status="Waiting")
    stage_before = po["current_stage"]
    pay_before = po["payment_status"]
    _admin()
    before = await _journal_counts()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], discount_amount=10000, ppn_percent=11)
            assert inv["grand_total"] == inv["after_discount"] + inv["ppn_amount"]
            assert inv["after_discount"] == 90000.0

            bad_po = await _seed_po()
            bad_id = str(uuid.uuid4())
            await server.db.sales_invoices.insert_one({
                "id": bad_id,
                "invoice_number": f"INV/BAD/{bad_id[:6]}",
                "po_id": bad_po["id"],
                "invoice_date": "2026-09-01",
                "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 100}],
                "subtotal": 100.0,
                "discount_amount": 0.0,
                "after_discount": 100.0,
                "ppn_percent": 11.0,
                "ppn_amount": 11.0,
                "grand_total": 999.0,
            })
            bad = await client.post(f"/api/sales-invoices/{bad_id}/finalize")
            assert bad.status_code == 400

            ok = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert ok.status_code == 200, ok.text
            fin = ok.json()["invoice"]
            assert fin["financial_status"] == "finalized"
            assert fin["accounting_posted"] is False
            assert fin["finalized_at"]
            assert fin["finalized_by"]["role"] == "admin"

            retry = await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            assert retry.status_code == 200
            assert retry.json()["invoice"]["financial_status"] == "finalized"

            mut = await client.put(
                f"/api/sales-invoices/{inv['id']}",
                json={"invoice_date": "2026-12-31", "discount_amount": 1, "lines": [{"line_idx": 0, "unit_price": 9}]},
            )
            assert mut.status_code == 409

            deleted = await client.delete(f"/api/sales-invoices/{inv['id']}")
            assert deleted.status_code == 409

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["current_stage"] == stage_before
            assert order["payment_status"] == pay_before
            assert await _journal_counts() == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrent_finalize_single_transition():
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            results = await asyncio.gather(
                client.post(f"/api/sales-invoices/{inv['id']}/finalize"),
                client.post(f"/api/sales-invoices/{inv['id']}/finalize"),
            )
            assert all(r.status_code == 200 for r in results)
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["financial_status"] == "finalized"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_cancel_idempotent_receipt_blocked_and_cancelled_immutable():
    po = await _seed_po()
    _admin()
    before = await _journal_counts()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await client.post(f"/api/sales-invoices/{inv['id']}/finalize")

            c1 = await client.post(f"/api/sales-invoices/{inv['id']}/cancel")
            assert c1.status_code == 200, c1.text
            assert c1.json()["invoice"]["financial_status"] == "cancelled"

            c2 = await client.post(f"/api/sales-invoices/{inv['id']}/cancel")
            assert c2.status_code == 200
            assert c2.json()["invoice"]["financial_status"] == "cancelled"

            mut = await client.put(
                f"/api/sales-invoices/{inv['id']}",
                json={"lines": [{"line_idx": 0, "unit_price": 2000}]},
            )
            assert mut.status_code == 409
            assert (await client.delete(f"/api/sales-invoices/{inv['id']}")).status_code == 409
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 409

            preview = await client.get(f"/api/sales-invoices/po/{po['id']}/preview")
            assert preview.json()["lines"][0]["remaining_invoice_qty"] == 100

            inv_pay = await _create_invoice(client, po["id"], quantity=20, unit_price=1000)
            await client.post(f"/api/sales-invoices/{inv_pay['id']}/finalize")
            rec = await client.post("/api/sales-receipts", json={
                "invoice_id": inv_pay["id"],
                "receipt_date": "2026-09-02",
                "amount": 5000,
            })
            assert rec.status_code == 200, rec.text
            blocked = await client.post(f"/api/sales-invoices/{inv_pay['id']}/cancel")
            assert blocked.status_code == 409
            still = await server.db.sales_invoices.find_one({"id": inv_pay["id"]}, {"_id": 0})
            assert still["financial_status"] == "finalized"
            assert await _journal_counts() == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrent_cancel_safe():
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
            results = await asyncio.gather(
                client.post(f"/api/sales-invoices/{inv['id']}/cancel"),
                client.post(f"/api/sales-invoices/{inv['id']}/cancel"),
            )
            assert all(r.status_code == 200 for r in results)
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert stored["financial_status"] == "cancelled"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_accounting_ready_requires_settings_and_finalize():
    po = await _seed_po()
    await ensure_indexes(server.db)
    suffix = uuid.uuid4().hex[:6]
    rec = await create_account(
        server.db, {"code": f"11{suffix}", "name": "Piutang LC", "account_type": "AREC"},
        user={"id": "t", "name": "t", "role": "admin"},
    )
    rev = await create_account(
        server.db, {"code": f"41{suffix}", "name": "Pendapatan LC", "account_type": "REVE"},
        user={"id": "t", "name": "t", "role": "admin"},
    )
    tax = await create_account(
        server.db, {"code": f"21{suffix}", "name": "PPN LC", "account_type": "OCLY"},
        user={"id": "t", "name": "t", "role": "admin"},
    )
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
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            assert inv["accounting_ready"] is False
            fin = (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).json()["invoice"]
            assert fin["accounting_ready"] is True
            assert fin["accounting_posted"] is False
    finally:
        await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
        app.dependency_overrides.pop(current_user, None)
