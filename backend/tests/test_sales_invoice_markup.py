"""Faktur Markup — separate presentation documents. Local DB only."""
from __future__ import annotations

import asyncio
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
os.environ.setdefault("DB_NAME", "eternal_packaging_markup_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

assert os.environ["DB_NAME"] != "eternal_packaging"

from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS  # noqa: E402
from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402
from services.sales_invoices import MARKUP_WARNING, generate_sales_invoice_pdf_bytes  # noqa: E402
from services.sales_invoice_markup import COLLECTION, compute_markup_totals  # noqa: E402

ADMIN = {
    "id": "u-markup-admin",
    "email": "admin-markup@example.com",
    "role": "admin",
    "name": "Admin Markup",
}
VIEWER = {
    "id": "u-markup-view",
    "email": "markup-view@example.com",
    "role": "crm",
    "name": "CRM Markup View",
    "permissions_override": {
        "sales.invoice_markup.view": True,
        "sales.invoice_markup.create": False,
        "sales.invoice.view": True,
    },
}
CREATOR = {
    "id": "u-markup-create",
    "email": "markup-create@example.com",
    "role": "crm",
    "name": "CRM Markup Create",
    "permissions_override": {
        "sales.invoice_markup.view": True,
        "sales.invoice_markup.create": True,
        "sales.invoice.view": True,
        "sales.invoice.create": True,
    },
}
GUEST = {
    "id": "u-markup-guest",
    "email": "guest-markup@example.com",
    "role": "guest",
    "name": "Guest",
    "permissions_override": {"po.view": True, "sales.invoice.view": True},
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


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def test_permission_catalog_lists_markup_perms():
    labeled = {key: (module, label) for module, key, label in PERMISSION_CATALOG}
    assert labeled["sales.invoice_markup.view"] == ("Penjualan", "Lihat Faktur Markup")
    assert labeled["sales.invoice_markup.create"] == ("Penjualan", "Buat Faktur Markup")
    assert "sales.invoice_markup.view" in ALL_PERMISSIONS
    assert "sales.invoice_markup.create" in ALL_PERMISSIONS
    assert "sales.invoice_markup.view" in ROLE_DEFAULT_PERMISSIONS["admin"]
    assert "sales.invoice_markup.view" not in ROLE_DEFAULT_PERMISSIONS["crm"]
    assert "sales.invoice_markup.create" not in ROLE_DEFAULT_PERMISSIONS["crm"]


async def _counts():
    return {
        "invoices": await db().sales_invoices.count_documents({}),
        "markups": await db()[COLLECTION].count_documents({}),
        "journals": await db().journal_entries.count_documents({}),
        "receipts": await db().sales_receipts.count_documents({}),
        "orders": await db().orders.count_documents({}),
    }


async def _seed_invoice(**extra):
    oid = str(uuid.uuid4())
    iid = str(uuid.uuid4())
    order = {
        "id": oid,
        "po_number": f"PO/M/{oid[:8]}",
        "customer": "PT Reseller",
        "address": "Jl. Markup 1",
        "sales": "Sales A",
        "order_date": "2026-01-10",
        "line_items": [
            {
                "line_idx": 0,
                "line_id": "line-a",
                "item_code": "A-1",
                "name": "Cup Same",
                "quantity": 100,
                "unit": "pcs",
                "unit_price": 1000,
            },
            {
                "line_idx": 1,
                "line_id": "line-b",
                "item_code": "B-1",
                "name": "Cup Same",
                "quantity": 50,
                "unit": "pcs",
                "unit_price": 2000,
            },
        ],
    }
    invoice = {
        "id": iid,
        "invoice_number": f"INV/M/{iid[:8]}",
        "po_id": oid,
        "po_number": order["po_number"],
        "customer": order["customer"],
        "address": order["address"],
        "sales": order["sales"],
        "invoice_date": "2026-01-15",
        "due_date": "2026-01-29",
        "notes": "Catatan asli",
        "lines": [
            {
                "line_idx": 0,
                "line_id": "line-a",
                "item_code": "A-1",
                "item_name": "Cup Same",
                "quantity": 100,
                "unit": "pcs",
                "unit_price": 1000,
                "line_subtotal": 100000,
                "line_total": 100000,
            },
            {
                "line_idx": 1,
                "line_id": "line-b",
                "item_code": "B-1",
                "item_name": "Cup Same",
                "quantity": 50,
                "unit": "pcs",
                "unit_price": 2000,
                "line_subtotal": 100000,
                "line_total": 100000,
            },
        ],
        "subtotal": 200000,
        "discount_amount": 10000,
        "after_discount": 190000,
        "ppn_percent": 11,
        "ppn_amount": 20900,
        "other_charges": [
            {
                "id": "chg-1",
                "account_id": "acc-1",
                "account_code": "5100",
                "account_name": "Ongkir",
                "description": "Ongkir",
                "amount": 5000,
            }
        ],
        "other_charges_total": 5000,
        "grand_total": 215900,
        "paid_total": 50000,
        "outstanding_amount": 165900,
        "payment_status": "SEBAGIAN",
        "financial_status": "finalized",
        "created_at": "2026-01-15T00:00:00+00:00",
        "updated_at": "2026-01-15T00:00:00+00:00",
    }
    invoice.update(extra)
    await db().orders.insert_one(order)
    await db().sales_invoices.insert_one(invoice)
    return order, invoice


@pytest.mark.anyio
async def test_compute_totals_nominal_discount_and_ppn_percent():
    _, invoice = await _seed_invoice()
    # Clean seeded docs for pure unit calc without relying on HTTP.
    totals = compute_markup_totals(
        invoice,
        [
            {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1500},
            {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 3000},
        ],
    )
    # 1500*100 + 3000*50 = 300000; disc 10000; after 290000; ppn 11% = 31900; +5000 = 326900
    assert totals["subtotal"] == 300000.0
    assert totals["discount_amount"] == 10000.0
    assert totals["ppn_percent"] == 11.0
    assert totals["ppn_amount"] == 31900.0
    assert totals["other_charges_total"] == 5000.0
    assert totals["grand_total"] == 326900.0
    assert totals["lines"][0]["markup_unit_price"] == 1500.0
    assert totals["lines"][1]["item_name"] == "Cup Same"
    assert totals["lines"][0]["line_id"] != totals["lines"][1]["line_id"]


@pytest.mark.anyio
async def test_create_markup_does_not_mutate_invoice_or_side_effects():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    await db().journal_entries.delete_many({})
    await db().sales_receipts.delete_many({})
    await db().audit_logs.delete_many({})

    _, invoice = await _seed_invoice()
    before = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    before_counts = await _counts()

    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1500},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 2500},
                ],
                "bank_name": "BCA",
                "account_number": "0123456789",
                "account_holder": "Reseller Reseller",
                "idempotency_key": "idem-markup-1",
            },
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["markup"]["revision"] == 1
    assert body["markup"]["account_number"] == "0123456789"
    assert body["markup"]["original_invoice_number"] == invoice["invoice_number"]

    after = await db().sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    assert after["grand_total"] == before["grand_total"]
    assert after["lines"][0]["unit_price"] == before["lines"][0]["unit_price"]
    assert after["lines"][1]["unit_price"] == before["lines"][1]["unit_price"]
    assert after["paid_total"] == before["paid_total"]
    assert after["ppn_amount"] == before["ppn_amount"]

    after_counts = await _counts()
    assert after_counts["markups"] == before_counts["markups"] + 1
    assert after_counts["journals"] == before_counts["journals"]
    assert after_counts["receipts"] == before_counts["receipts"]
    assert after_counts["invoices"] == before_counts["invoices"]
    assert after_counts["orders"] == before_counts["orders"]


@pytest.mark.anyio
async def test_idempotent_retry_same_key_no_duplicate():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    await db()["sales_invoice_markup_counters"].delete_many({})
    _, invoice = await _seed_invoice()
    payload = {
        "lines": [
            {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1100},
            {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 2100},
        ],
        "bank_name": "Mandiri",
        "account_number": "000111",
        "account_holder": "AN Test",
        "idempotency_key": "idem-dup",
    }
    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(f"/api/sales-invoices/{invoice['id']}/markup", json=payload)
        r2 = await client.post(f"/api/sales-invoices/{invoice['id']}/markup", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["markup"]["id"] == r2.json()["markup"]["id"]
    assert await db()[COLLECTION].count_documents({"original_invoice_id": invoice["id"]}) == 1


@pytest.mark.anyio
async def test_revisions_monotonic_and_history_scoped():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    await db()["sales_invoice_markup_counters"].delete_many({})
    _, inv_a = await _seed_invoice()
    _, inv_b = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)

    async def create(inv, price_a, key):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/sales-invoices/{inv['id']}/markup",
                json={
                    "lines": [
                        {"line_id": "line-a", "line_idx": 0, "markup_unit_price": price_a},
                        {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 2000},
                    ],
                    "bank_name": "BRI",
                    "account_number": "99",
                    "account_holder": "Holder",
                    "idempotency_key": key,
                },
            )

    r1 = await create(inv_a, 1200, "a1")
    r2 = await create(inv_a, 1300, "a2")
    r3 = await create(inv_a, 1400, "a3")
    rb = await create(inv_b, 1500, "b1")
    assert [r1.json()["markup"]["revision"], r2.json()["markup"]["revision"], r3.json()["markup"]["revision"]] == [1, 2, 3]
    assert rb.json()["markup"]["revision"] == 1

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        hist = await client.get(f"/api/sales-invoices/{inv_a['id']}/markup")
    assert hist.status_code == 200
    assert hist.json()["count"] == 3
    assert [x["revision"] for x in hist.json()["items"]] == [3, 2, 1]


@pytest.mark.anyio
async def test_concurrent_revision_unique():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    await db()["sales_invoice_markup_counters"].delete_many({})
    _, invoice = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)

    async def one(i):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/sales-invoices/{invoice['id']}/markup",
                json={
                    "lines": [
                        {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1000 + i},
                        {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 2000},
                    ],
                    "bank_name": "BCA",
                    "account_number": f"00{i}",
                    "account_holder": "C",
                    "idempotency_key": f"conc-{i}-{uuid.uuid4()}",
                },
            )

    results = await asyncio.gather(*[one(i) for i in range(5)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    revs = sorted(r.json()["markup"]["revision"] for r in results)
    assert revs == [1, 2, 3, 4, 5]


@pytest.mark.anyio
async def test_pdf_markup_uses_snapshot_and_omits_payment():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    await db()["sales_invoice_markup_counters"].delete_many({})
    _, invoice = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 5000},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 7000},
                ],
                "bank_name": "Bank Snapshot",
                "account_number": "00770077",
                "account_holder": "AN Snapshot",
            },
        )
        assert created.status_code == 200, created.text
        mid = created.json()["markup"]["id"]
        # Mutate live invoice prices — reprint must ignore them.
        await db().sales_invoices.update_one(
            {"id": invoice["id"]},
            {"$set": {"lines.0.unit_price": 1, "grand_total": 1, "paid_total": 999999}},
        )
        pdf = await client.get(f"/api/sales-invoices/{invoice['id']}/markup/{mid}/pdf")
        original = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
    assert pdf.status_code == 200
    assert original.status_code == 200
    text = _pdf_text(pdf.content)
    assert "Faktur Markup" in text
    assert MARKUP_WARNING in text
    assert invoice["invoice_number"] in text
    assert "Bank Snapshot" in text
    assert "00770077" in text
    assert "AN Snapshot" in text
    assert "Rekening Markup" in text
    assert "Uang dimuka" not in text
    # Markup price should appear (5000)
    assert "5.000" in text or "5000" in text
    orig_text = _pdf_text(original.content)
    assert "Faktur Penjualan" in orig_text
    assert "Faktur Markup" not in orig_text


@pytest.mark.anyio
async def test_rbac_forbidden_without_perm():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    _, invoice = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: GUEST
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(f"/api/sales-invoices/{invoice['id']}/markup")
        created = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 1},
                ],
                "bank_name": "X",
                "account_number": "1",
                "account_holder": "Y",
            },
        )
    assert listed.status_code == 403
    assert created.status_code == 403


@pytest.mark.anyio
async def test_viewer_cannot_create():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    _, invoice = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: VIEWER
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(f"/api/sales-invoices/{invoice['id']}/markup")
        created = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 1},
                ],
                "bank_name": "X",
                "account_number": "1",
                "account_holder": "Y",
            },
        )
    assert listed.status_code == 200
    assert created.status_code == 403


@pytest.mark.anyio
async def test_invalid_price_rejected():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    _, invoice = await _seed_invoice()
    app.dependency_overrides[current_user] = lambda: CREATOR
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": -5},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 1},
                ],
                "bank_name": "X",
                "account_number": "1",
                "account_holder": "Y",
            },
        )
        blank_bank = await client.post(
            f"/api/sales-invoices/{invoice['id']}/markup",
            json={
                "lines": [
                    {"line_id": "line-a", "line_idx": 0, "markup_unit_price": 1},
                    {"line_id": "line-b", "line_idx": 1, "markup_unit_price": 1},
                ],
                "bank_name": "",
                "account_number": "1",
                "account_holder": "Y",
            },
        )
    assert bad.status_code == 400
    assert blank_bank.status_code == 400


@pytest.mark.anyio
async def test_get_list_is_zero_write():
    await db().sales_invoices.delete_many({})
    await db().orders.delete_many({})
    await db()[COLLECTION].delete_many({})
    _, invoice = await _seed_invoice()
    before = await _counts()
    app.dependency_overrides[current_user] = lambda: VIEWER
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get(f"/api/sales-invoices/{invoice['id']}/markup")
    after = await _counts()
    assert after == before


@pytest.mark.anyio
async def test_original_generator_invoice_unchanged_path():
    pdf = generate_sales_invoice_pdf_bytes(
        {
            "invoice_number": "INV/KEEP/1",
            "customer": "C",
            "lines": [{"item_name": "X", "quantity": 1, "unit_price": 10, "line_subtotal": 10}],
            "subtotal": 10,
            "discount_amount": 0,
            "ppn_percent": 0,
            "ppn_amount": 0,
            "grand_total": 10,
            "paid_total": 3,
        }
    )
    text = _pdf_text(pdf)
    assert "Faktur Penjualan" in text
    assert "Uang dimuka" in text
    assert "Faktur Markup" not in text
