"""PUR-INV-01: purchase invoice payment terms + due date + audit + edit guards."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_pur_inv_01")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS  # noqa: E402
from services.purchase_common import (  # noqa: E402
    add_days_ymd,
    invoice_due_display_status,
    parse_optional_ymd,
    parse_payment_term_days,
)
from services.purchase_invoices import (  # noqa: E402
    AUDIT_ACTION_TERMS,
    AUDIT_COLLECTION,
    COLLECTION,
    attach_payment_fields,
    create_purchase_invoice,
    update_purchase_invoice_payment_terms,
)
from services.purchase_invoice_finance import STATUS_PAID  # noqa: E402

TEST_DB = os.environ["DB_NAME"]
pytestmark = pytest.mark.anyio

USER = {"id": "u-pur-inv-01", "email": "buyer@example.com", "name": "Buyer", "role": "admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await database[COLLECTION].create_index("id", unique=True)
    await database[COLLECTION].create_index("invoice_number", unique=True)
    await database.purchase_orders.create_index("id", unique=True)
    await database.master_items.create_index("id", unique=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _seed_po(db, *, qty=10, price=1000.0):
    item_id = f"item-{uuid.uuid4().hex[:8]}"
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"pol-{uuid.uuid4().hex[:8]}"
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": "BR-PURINV",
        "name": "Bahan PUR-INV",
        "item_name": "Bahan PUR-INV",
        "is_active": True,
        "base_unit": "PCS",
    })
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-20261022-{uuid.uuid4().hex[:3].upper()}",
        "supplier": "Supplier Termin",
        "cancelled": False,
        "lines": [{
            "line_id": line_id,
            "line_idx": 0,
            "master_item_id": item_id,
            "accurate_item_id": "",
            "item_code": "BR-PURINV",
            "item_name": "Bahan PUR-INV",
            "unit": "PCS",
            "quantity": qty,
            "ordered_qty": qty,
            "unit_price": price,
            "line_subtotal": qty * price,
        }],
        "created_at": "2026-10-22T00:00:00+00:00",
    })
    return po_id, line_id


async def _create_invoice(db, *, term_days=30, due_date=None, invoice_date="2026-10-01", qty=2):
    po_id, line_id = await _seed_po(db, qty=max(qty, 5))
    payload = {
        "invoice_source": "purchase_order",
        "purchase_order_id": po_id,
        "invoice_date": invoice_date,
        "payment_term_days": term_days,
        "precedes_goods_receipt": True,
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": qty}],
        "idempotency_key": str(uuid.uuid4()),
    }
    if due_date is not None:
        payload["due_date"] = due_date
    return await create_purchase_invoice(db, payload, USER)


def test_helpers_parse_and_due_status():
    assert parse_optional_ymd("") is None
    assert parse_optional_ymd("2026-10-05T12:00:00Z") == "2026-10-05"
    with pytest.raises(HTTPException):
        parse_optional_ymd("31-12-2026")
    assert parse_payment_term_days(14) == 14
    assert parse_payment_term_days(None, required=False) == 0
    assert add_days_ymd("2026-10-01", 30) == "2026-10-31"
    assert invoice_due_display_status(due_date="2020-01-01", payment_status="BELUM_DIBAYAR", today="2026-10-01") == "TERLAMBAT"
    assert invoice_due_display_status(due_date="2026-10-01", payment_status="LUNAS", today="2026-10-01") == "LUNAS"


def test_permission_catalog_includes_edit():
    assert "purchase_invoices.edit" in ALL_PERMISSIONS
    for role in ("head_production", "head_operations", "holding_team"):
        assert "purchase_invoices.edit" in ROLE_DEFAULT_PERMISSIONS[role]


async def test_create_computes_due_and_accepts_manual_override(db):
    inv = await _create_invoice(db, term_days=14, invoice_date="2026-10-01")
    assert inv["payment_term_days"] == 14
    assert inv["due_date"] == "2026-10-15"
    assert inv["can_edit_payment_terms"] is True

    inv2 = await _create_invoice(db, term_days=7, due_date="2026-11-20", invoice_date="2026-10-01")
    assert inv2["payment_term_days"] == 7
    assert inv2["due_date"] == "2026-11-20"


async def test_create_rejects_invalid_manual_due(db):
    po_id, line_id = await _seed_po(db)
    with pytest.raises(HTTPException) as ei:
        await create_purchase_invoice(db, {
            "invoice_source": "purchase_order",
            "purchase_order_id": po_id,
            "invoice_date": "2026-10-01",
            "payment_term_days": 7,
            "due_date": "not-a-date",
            "precedes_goods_receipt": True,
            "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 1}],
            "idempotency_key": str(uuid.uuid4()),
        }, USER)
    assert ei.value.status_code == 400


async def test_update_terms_writes_audit_and_history(db):
    inv = await _create_invoice(db, term_days=30, invoice_date="2026-10-01")
    with pytest.raises(HTTPException) as missing_reason:
        await update_purchase_invoice_payment_terms(
            db, inv["id"], {"payment_term_days": 7, "due_date": "2026-10-20"}, USER
        )
    assert missing_reason.value.status_code == 400

    updated = await update_purchase_invoice_payment_terms(
        db,
        inv["id"],
        {"payment_term_days": 7, "due_date": "2026-10-20", "reason": "nego supplier"},
        USER,
    )
    assert updated["payment_term_days"] == 7
    assert updated["due_date"] == "2026-10-20"
    hist = updated.get("payment_terms_history") or []
    assert hist and hist[-1]["after"]["due_date"] == "2026-10-20"
    assert hist[-1]["reason"] == "nego supplier"
    audits = await db[AUDIT_COLLECTION].find({"action": AUDIT_ACTION_TERMS, "entity_id": inv["id"]}).to_list(10)
    assert len(audits) == 1
    assert audits[0]["before"]["payment_term_days"] == 30
    assert audits[0]["after"]["payment_term_days"] == 7
    assert audits[0]["reason"] == "nego supplier"
    assert audits[0]["note"] == "nego supplier"
    assert audits[0].get("actor_id") or audits[0].get("actor_email")
    assert audits[0].get("at")


async def test_update_rejects_lunas_and_cancelled(db):
    inv = await _create_invoice(db, term_days=7, invoice_date="2026-10-01")
    await db[COLLECTION].update_one({"id": inv["id"]}, {"$set": {"cancelled": True}})
    with pytest.raises(HTTPException) as ei:
        await update_purchase_invoice_payment_terms(
            db, inv["id"], {"due_date": "2026-12-01", "reason": "coba ubah batal"}, USER
        )
    assert ei.value.status_code == 409

    inv2 = await _create_invoice(db, term_days=7, invoice_date="2026-10-01")
    # Force paid_total == grand_total via confirmed payment row.
    await db.purchase_payments.insert_one({
        "id": f"pay-{uuid.uuid4().hex[:8]}",
        "purchase_invoice_id": inv2["id"],
        "amount": float(inv2["grand_total"]),
        "settlement_status": "PAID",
        "cancelled": False,
    })
    live = await attach_payment_fields(db, await db[COLLECTION].find_one({"id": inv2["id"]}, {"_id": 0}))
    assert live["payment_status"] == STATUS_PAID
    with pytest.raises(HTTPException) as ei2:
        await update_purchase_invoice_payment_terms(
            db, inv2["id"], {"due_date": "2026-12-01", "reason": "coba ubah lunas"}, USER
        )
    assert ei2.value.status_code == 409


async def test_update_allows_partially_paid(db):
    inv = await _create_invoice(db, term_days=30, invoice_date="2026-10-01", qty=4)
    await db.purchase_payments.insert_one({
        "id": f"pay-{uuid.uuid4().hex[:8]}",
        "purchase_invoice_id": inv["id"],
        "amount": float(inv["grand_total"]) / 2,
        "settlement_status": "PAID",
        "cancelled": False,
    })
    updated = await update_purchase_invoice_payment_terms(
        db, inv["id"], {"payment_term_days": 14, "reason": "partial ok edit"}, USER
    )
    assert updated["payment_term_days"] == 14
    assert updated["due_date"] == add_days_ymd(inv["invoice_date"], 14)
    assert updated["payment_status"] != STATUS_PAID


async def test_legacy_invoice_without_due_enriches(db):
    po_id, line_id = await _seed_po(db)
    legacy_id = f"inv-{uuid.uuid4().hex[:8]}"
    await db[COLLECTION].insert_one({
        "id": legacy_id,
        "invoice_number": "FPB-LEGACY-1",
        "purchase_order_id": po_id,
        "po_number": "PB-LEGACY",
        "supplier": "Old",
        "invoice_date": "2026-01-01",
        "grand_total": 1000,
        "lines": [{"purchase_order_line_id": line_id, "invoice_qty": 1, "unit_price": 1000, "line_subtotal": 1000}],
        "cancelled": False,
    })
    doc = await db[COLLECTION].find_one({"id": legacy_id}, {"_id": 0})
    enriched = await attach_payment_fields(db, doc)
    assert enriched.get("due_date") is None
    assert enriched["due_status"] == "BELUM ADA JATUH TEMPO"
    assert enriched["can_edit_payment_terms"] is True


async def test_http_permission_and_patch(db, monkeypatch):
    import httpx
    import server
    from server import app, current_user

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[TEST_DB]

    inv = await _create_invoice(db, term_days=30, invoice_date="2026-10-01")

    async def as_admin():
        return {**USER, "permissions": list(ALL_PERMISSIONS)}

    async def as_view_only():
        return {
            "id": "viewer",
            "email": "v@example.com",
            "name": "Viewer",
            "role": "crm",
            "permissions": ["purchase_invoices.view"],
        }

    app.dependency_overrides[current_user] = as_admin
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.patch(
            f"/api/purchase-invoices/{inv['id']}/payment-terms",
            json={"payment_term_days": 7, "due_date": "2026-10-12", "reason": "api patch"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["invoice"]["due_date"] == "2026-10-12"

        app.dependency_overrides[current_user] = as_view_only
        denied = await client.patch(
            f"/api/purchase-invoices/{inv['id']}/payment-terms",
            json={"due_date": "2026-12-01"},
        )
        assert denied.status_code in (401, 403)

    app.dependency_overrides.pop(current_user, None)
