"""PWA Sales read-only scope: session users.id only. Role sales_eternal."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_user_mapping import SETTINGS_KEY

AYUB_ID = str(uuid.uuid4())
EUIS_ID = str(uuid.uuid4())
ACCURATE_AYUB = "Sholahudin Al - Ayubi"
ACCOUNT_AYUB = "Sholahuddin Al Ayyubi"
ACCURATE_EUIS = "Euis Risnawati"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    app.dependency_overrides.pop(current_user, None)
    yield
    app.dependency_overrides.pop(current_user, None)


def _sales_user(uid, email, name):
    return {
        "id": uid,
        "user_id": uid,
        "email": email,
        "name": name,
        "role": "sales_eternal",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["sales_eternal"]),
    }


def _admin():
    return {
        "id": "admin-1",
        "user_id": "admin-1",
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["admin"]),
    }


def _order(po, sales, sales_user_id, customer, **extra):
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "customer_id": extra.get("customer_id", f"cust-{customer}"),
        "sales": sales,
        "sales_user_id": sales_user_id,
        "order_date": extra.get("order_date", "2026-01-01"),
        "cancelled": False,
        "current_stage": extra.get("current_stage", 5),
        "status_label": extra.get("status_label", "Penjadwalan"),
        "line_items": [{"name": extra.get("item", "Paper cup"), "quantity": 1000, "unit": "pcs"}],
        "created_at": now,
        "updated_at": now,
        "source_pdf": extra.get("source_pdf"),
        "delivery_proofs": extra.get("delivery_proofs") or [],
        "buffer_stock": bool(extra.get("buffer_stock")),
    }
    return doc


async def _seed_users(db):
    await db.users.delete_many({"id": {"$in": [AYUB_ID, EUIS_ID, "admin-1"]}})
    await db.users.insert_many([
        {"id": AYUB_ID, "email": "ayub.preview@eternal.com", "name": ACCOUNT_AYUB, "role": "sales_eternal", "employee_id": "EMP-0049"},
        {"id": EUIS_ID, "email": "euis.preview@eternal.com", "name": ACCURATE_EUIS, "role": "sales_eternal", "employee_id": "EMP-0052"},
        {"id": "admin-1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"},
    ])


@pytest.mark.anyio
async def test_pwa_scope_and_spoof_ignored():
    import server
    db = server.db
    await _seed_users(db)
    suffix = uuid.uuid4().hex[:6]
    ayub_po = _order(f"PO-AYU-{suffix}", ACCURATE_AYUB, AYUB_ID, "Kopi Ayub")
    euis_po = _order(f"PO-EUI-{suffix}", ACCURATE_EUIS, EUIS_ID, "Toko Euis")
    await db.orders.delete_many({"po_number": {"$regex": suffix}})
    await db.orders.insert_many([ayub_po, euis_po])

    app.dependency_overrides[current_user] = lambda: _sales_user(AYUB_ID, "ayub.preview@eternal.com", ACCOUNT_AYUB)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/sales-pwa/orders", params={"sales_user_id": EUIS_ID, "limit": 50})
        assert listed.status_code == 200, listed.text
        pos = [o["po_number"] for o in listed.json()["orders"]]
        assert ayub_po["po_number"] in pos
        assert euis_po["po_number"] not in pos
        for row in listed.json()["orders"]:
            assert row.get("customer") != "Toko Euis"

        other = await client.get(f"/api/sales-pwa/orders/{euis_po['id']}")
        assert other.status_code == 404

        docs = await client.get(f"/api/sales-pwa/orders/{euis_po['id']}/documents")
        assert docs.status_code == 404

        cust = await client.get("/api/sales-pwa/customers")
        assert cust.status_code == 200
        names = [c["customer"] for c in cust.json()["customers"]]
        assert "Kopi Ayub" in names
        assert "Toko Euis" not in names

        pred = await client.get("/api/sales-pwa/predictions", params={"sales": ACCURATE_EUIS})
        assert pred.status_code == 200
        for row in pred.json().get("rows") or []:
            assert "Toko Euis" not in str(row.get("customer") or "")

        me = await client.get("/api/sales-pwa/me", params={"sales_user_id": EUIS_ID})
        assert me.status_code == 200, me.text
        assert me.json()["role"] == "sales_eternal"
        assert me.json()["role_label"] == "Sales Eternal"
        assert me.json()["name"] == ACCOUNT_AYUB

        bypass_customers = await client.get("/api/sales-customers")
        assert bypass_customers.status_code == 403

        own = await client.get(f"/api/orders/{ayub_po['id']}")
        assert own.status_code == 403
        assert "Kopi Ayub" not in own.text
        leak = await client.get(f"/api/orders/{euis_po['id']}")
        assert leak.status_code == 403
        assert "Toko Euis" not in leak.text
        assert "Kopi Ayub" not in leak.text

    app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_create_and_reassign_use_explicit_mapping():
    import server
    db = server.db
    await _seed_users(db)
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    await db.app_settings.insert_one({
        "key": SETTINGS_KEY,
        "entries": [
            {"label": ACCURATE_AYUB, "user_id": AYUB_ID},
            {"label": ACCURATE_EUIS, "user_id": EUIS_ID},
        ],
    })

    app.dependency_overrides[current_user] = lambda: _admin()
    transport = ASGITransport(app=app)
    po_num = f"PO-MAP-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "order_date": "2026-09-01",
        "customer": "PT Mapping Test",
        "sales": ACCURATE_AYUB,
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "print"}],
        "estimated_delivery": "2026-09-04",
        "deadline": "2026-09-04",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/sales-orders", json=payload)
        assert created.status_code == 200, created.text
        order = created.json()["order"]
        assert order["sales"] == ACCURATE_AYUB
        assert order["sales_user_id"] == AYUB_ID

        # Account name without mapping row must not attach.
        po2 = f"PO-NOM-{uuid.uuid4().hex[:6].upper()}"
        created2 = await client.post("/api/sales-orders", json={
            **payload,
            "po_number": po2,
            "sales": ACCOUNT_AYUB,
        })
        assert created2.status_code == 200, created2.text
        order2 = created2.json()["order"]
        assert order2["sales"] == ACCOUNT_AYUB
        assert order2.get("sales_user_id") in (None, "")

        reassigned = await client.patch(f"/api/orders/{order['id']}/sales", json={"sales": ACCURATE_EUIS})
        assert reassigned.status_code == 200, reassigned.text
        assert reassigned.json()["order"]["sales"] == ACCURATE_EUIS
        assert reassigned.json()["order"]["sales_user_id"] == EUIS_ID

        unmatched = await client.patch(f"/api/orders/{order['id']}/sales", json={"sales": "Orang Tidak Ada"})
        assert unmatched.status_code == 200, unmatched.text
        body = unmatched.json()["order"]
        assert body["sales"] == "Orang Tidak Ada"
        assert body.get("sales_user_id") in (None, "")
        assert body.get("sales_ownership_status") == "unmatched"
        assert body.get("sales_needs_verification") is True

        # Mapping admin endpoint is not open to sales_eternal.
    app.dependency_overrides[current_user] = lambda: _sales_user(AYUB_ID, "ayub.preview@eternal.com", ACCOUNT_AYUB)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.put("/api/settings/sales-user-mapping", json={"entries": []})
        assert denied.status_code in (403, 401)

    app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_sales_eternal_role_defaults_are_scoped_and_po_read_only():
    perms = set(ROLE_DEFAULT_PERMISSIONS["sales_eternal"])
    assert "sales.pwa.view" in perms
    assert "sales.pwa.visit.create" in perms
    assert "po.view" not in perms
    assert "sales_order.view" not in perms
    assert "reports.order_prediction.view" not in perms
    for write in (
        "po.edit", "sales_order.create", "sales.delivery.create",
        "sales.invoice.create", "po.import.view", "accurate.import",
    ):
        assert write not in perms


@pytest.mark.anyio
async def test_dashboard_orders_and_visits_use_canonical_scoped_sources():
    import server
    db = server.db
    await _seed_users(db)
    suffix = uuid.uuid4().hex[:6]
    ayub_po = _order(
        f"PO-DASH-AYU-{suffix}",
        ACCURATE_AYUB,
        AYUB_ID,
        "Dashboard Ayub",
        order_date="2026-09-05",
        buffer_stock=True,
    )
    euis_po = _order(
        f"PO-DASH-EUI-{suffix}",
        ACCURATE_EUIS,
        EUIS_ID,
        "Dashboard Euis",
        order_date="2026-09-06",
        buffer_stock=True,
    )
    await db.orders.insert_many([ayub_po, euis_po])
    ayub_invoice_id = str(uuid.uuid4())
    euis_invoice_id = str(uuid.uuid4())
    draft_invoice_id = str(uuid.uuid4())
    cancelled_invoice_id = str(uuid.uuid4())
    await db.sales_invoices.insert_many([
        {
            "id": ayub_invoice_id,
            "po_id": ayub_po["id"],
            "invoice_number": f"INV-AYU-{suffix}",
            "invoice_date": "2026-09-10",
            "grand_total": 1_000_000,
            "financial_status": "finalized",
        },
        {
            "id": euis_invoice_id,
            "po_id": euis_po["id"],
            "invoice_number": f"INV-EUI-{suffix}",
            "invoice_date": "2026-09-10",
            "grand_total": 9_000_000,
            "financial_status": "finalized",
        },
        {
            "id": draft_invoice_id,
            "po_id": ayub_po["id"],
            "invoice_number": f"INV-DRAFT-{suffix}",
            "invoice_date": "2026-09-11",
            "grand_total": 7_000_000,
            "financial_status": "draft",
        },
        {
            "id": cancelled_invoice_id,
            "po_id": ayub_po["id"],
            "invoice_number": f"INV-CAN-{suffix}",
            "invoice_date": "2026-09-12",
            "grand_total": 5_000_000,
            "financial_status": "cancelled",
        },
    ])
    await db.sales_receipts.insert_many([
        {
            "id": str(uuid.uuid4()),
            "invoice_id": ayub_invoice_id,
            "po_id": ayub_po["id"],
            "amount": 250_000,
        },
        {
            "id": str(uuid.uuid4()),
            "invoice_id": ayub_invoice_id,
            "po_id": ayub_po["id"],
            "amount": 100_000,
            "financial_status": "cancelled",
            "cancelled": True,
        },
    ])
    ayub_outlet_id = str(uuid.uuid4())
    euis_outlet_id = str(uuid.uuid4())
    await db.sales_outlets.insert_many([
        {"id": ayub_outlet_id, "name": "Outlet Visit Ayub", "address": "Yogyakarta"},
        {"id": euis_outlet_id, "name": "Outlet Visit Euis", "address": "Yogyakarta"},
    ])
    await db.sales_ownership.insert_many([
        {"id": str(uuid.uuid4()), "outlet_id": ayub_outlet_id, "current_owner_id": AYUB_ID, "next_follow_up_at": "2026-09-20T09:00:00+07:00"},
        {"id": str(uuid.uuid4()), "outlet_id": euis_outlet_id, "current_owner_id": EUIS_ID, "next_follow_up_at": "2026-09-21T09:00:00+07:00"},
    ])

    app.dependency_overrides[current_user] = lambda: _sales_user(AYUB_ID, "ayub.preview@eternal.com", ACCOUNT_AYUB)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/sales-pwa/orders", params={
            "date_from": "2026-09-01",
            "date_to": "2026-09-30",
            "buffer": "ya",
            "sort": "order_date",
        })
        assert listed.status_code == 200, listed.text
        rows = listed.json()["orders"]
        assert any(row["po_number"] == ayub_po["po_number"] for row in rows)
        assert all(row["po_number"] != euis_po["po_number"] for row in rows)
        ayub_row = next(row for row in rows if row["po_number"] == ayub_po["po_number"])
        assert ayub_row["order_date"] == "2026-09-05"
        assert ayub_row["buffer_stock"] is True

        dashboard = await client.get("/api/sales-pwa/dashboard", params={"month": "2026-09", "sales_user_id": EUIS_ID})
        assert dashboard.status_code == 200, dashboard.text
        metrics = dashboard.json()["metrics"]
        assert metrics["revenue"] == 1_000_000
        assert metrics["receivables"] == 750_000
        assert metrics["invoice_count"] == 1
        assert metrics["receivable_invoice_count"] == 1
        assert metrics["buffer_po"] >= 1

        invoice_list = await client.get("/api/sales-pwa/invoices", params={"month": "2026-09", "sales_user_id": EUIS_ID, "sales_id": EUIS_ID})
        assert invoice_list.status_code == 200, invoice_list.text
        invoice_body = invoice_list.json()
        assert invoice_body["summary"]["total_amount"] == metrics["revenue"]
        assert invoice_body["summary"]["invoice_count"] == metrics["invoice_count"]
        invoice_numbers = [row["invoice_number"] for row in invoice_body["invoices"]]
        assert invoice_numbers == [f"INV-AYU-{suffix}"]
        assert invoice_body["invoices"][0]["grand_total"] == 1_000_000
        assert invoice_body["invoices"][0]["payment_status"] == "DP"
        assert sum(row["grand_total"] for row in invoice_body["invoices"]) == metrics["revenue"]

        receivable_list = await client.get("/api/sales-pwa/receivables", params={"sales_user_id": EUIS_ID})
        assert receivable_list.status_code == 200, receivable_list.text
        receivable_body = receivable_list.json()
        assert receivable_body["summary"]["outstanding_total"] == metrics["receivables"]
        assert receivable_body["summary"]["invoice_count"] == metrics["receivable_invoice_count"]
        assert [row["invoice_number"] for row in receivable_body["receivables"]] == [f"INV-AYU-{suffix}"]
        assert receivable_body["receivables"][0]["paid_amount"] == 250_000
        assert receivable_body["receivables"][0]["outstanding_amount"] == 750_000

        visits = await client.get("/api/sales-pwa/visits", params={"month": "2026-09", "sales_user_id": EUIS_ID})
        assert visits.status_code == 200, visits.text
        names = [row["name"] for row in visits.json()["outlets"]]
        assert "Outlet Visit Ayub" in names
        assert "Outlet Visit Euis" not in names

    app.dependency_overrides.pop(current_user, None)
    await db.orders.delete_many({"id": {"$in": [ayub_po["id"], euis_po["id"]]}})
    await db.sales_invoices.delete_many({"id": {"$in": [ayub_invoice_id, euis_invoice_id, draft_invoice_id, cancelled_invoice_id]}})
    await db.sales_receipts.delete_many({"invoice_id": ayub_invoice_id})
    await db.sales_outlets.delete_many({"id": {"$in": [ayub_outlet_id, euis_outlet_id]}})
    await db.sales_ownership.delete_many({"outlet_id": {"$in": [ayub_outlet_id, euis_outlet_id]}})


@pytest.mark.anyio
async def test_sales_pwa_finance_lists_match_dashboard_and_ignore_other_sales():
    import server
    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    email_a = f"finance-a-{sales_a[:8]}@eternal.com"
    email_b = f"finance-b-{sales_b[:8]}@eternal.com"
    suffix = uuid.uuid4().hex[:6]
    await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Sales A", "role": "sales_eternal"},
        {"id": sales_b, "email": email_b, "name": "Sales B", "role": "sales_eternal"},
    ])
    po_a = _order(f"PO-FIN-A-{suffix}", "Label A", sales_a, "Pelanggan A", order_date="2026-10-01")
    po_cancelled = _order(f"PO-FIN-X-{suffix}", "Label A", sales_a, "Pelanggan A", order_date="2026-10-02")
    po_cancelled["cancelled"] = True
    po_b = _order(f"PO-FIN-B-{suffix}", "Label B", sales_b, "Pelanggan B", order_date="2026-10-03")
    await db.orders.insert_many([po_a, po_cancelled, po_b])

    inv_partial = str(uuid.uuid4())
    inv_paid = str(uuid.uuid4())
    inv_old = str(uuid.uuid4())
    inv_draft = str(uuid.uuid4())
    inv_cancelled = str(uuid.uuid4())
    inv_flagged = str(uuid.uuid4())
    inv_dead_po = str(uuid.uuid4())
    inv_other = str(uuid.uuid4())
    invoice_ids = [inv_partial, inv_paid, inv_old, inv_draft, inv_cancelled, inv_flagged, inv_dead_po, inv_other]
    await db.sales_invoices.insert_many([
        {
            "id": inv_partial,
            "po_id": po_a["id"],
            "invoice_number": f"INV-PART-{suffix}",
            "customer": "Pelanggan A",
            "sales": "Label B",
            "invoice_date": "2026-10-05",
            "due_date": "2026-11-04",
            "grand_total": 1_000_000,
            "financial_status": "finalized",
            "payment_status": "BELUM DIBAYAR",
        },
        {
            "id": inv_paid,
            "po_id": po_a["id"],
            "invoice_number": f"INV-PAID-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-10-20",
            "due_date": "2026-11-19",
            "grand_total": 120_000,
            "financial_status": "finalized",
        },
        {
            "id": inv_old,
            "po_id": po_a["id"],
            "invoice_number": f"INV-OLD-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-08-15",
            "due_date": "2026-09-14",
            "grand_total": 400_000,
            "financial_status": "finalized",
        },
        {
            "id": inv_draft,
            "po_id": po_a["id"],
            "invoice_number": f"INV-DRAFT2-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-10-06",
            "grand_total": 500_000,
            "financial_status": "draft",
        },
        {
            "id": inv_cancelled,
            "po_id": po_a["id"],
            "invoice_number": f"INV-CAN2-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-10-07",
            "grand_total": 300_000,
            "financial_status": "cancelled",
        },
        {
            "id": inv_flagged,
            "po_id": po_a["id"],
            "invoice_number": f"INV-FLAG-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-10-08",
            "grand_total": 200_000,
            "financial_status": "finalized",
            "is_cancelled": True,
        },
        {
            "id": inv_dead_po,
            "po_id": po_cancelled["id"],
            "invoice_number": f"INV-DEAD-{suffix}",
            "customer": "Pelanggan A",
            "invoice_date": "2026-10-09",
            "grand_total": 999_000,
            "financial_status": "finalized",
        },
        {
            "id": inv_other,
            "po_id": po_b["id"],
            "invoice_number": f"INV-B-{suffix}",
            "customer": "Pelanggan B",
            "sales": "Label A",
            "invoice_date": "2026-10-09",
            "due_date": "2026-10-09",
            "grand_total": 8_000_000,
            "financial_status": "finalized",
        },
    ])
    await db.sales_receipts.insert_many([
        {"id": str(uuid.uuid4()), "invoice_id": inv_partial, "amount": 250_000},
        {"id": str(uuid.uuid4()), "invoice_id": inv_partial, "amount": 100_000, "financial_status": "cancelled", "cancelled": True},
        {"id": str(uuid.uuid4()), "invoice_id": inv_partial, "amount": 50_000, "financial_status": "reversed"},
        {"id": str(uuid.uuid4()), "invoice_id": inv_paid, "amount": 120_000},
        {"id": str(uuid.uuid4()), "invoice_id": inv_other, "amount": 1_000_000},
    ])

    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Sales A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            dashboard = await client.get("/api/sales-pwa/dashboard", params={"month": "2026-10", "sales": "Label B"})
            assert dashboard.status_code == 200, dashboard.text
            metrics = dashboard.json()["metrics"]
            assert metrics["revenue"] == 1_120_000
            assert metrics["invoice_count"] == 2
            assert metrics["receivables"] == 1_150_000
            assert metrics["receivable_invoice_count"] == 2

            listed = await client.get("/api/sales-pwa/invoices", params={"month": "2026-10", "sales_user_id": sales_b})
            assert listed.status_code == 200, listed.text
            body = listed.json()
            assert body["month"] == "2026-10"
            assert body["summary"]["total_amount"] == metrics["revenue"]
            assert body["summary"]["invoice_count"] == metrics["invoice_count"]
            assert [row["invoice_number"] for row in body["invoices"]] == [f"INV-PAID-{suffix}", f"INV-PART-{suffix}"]
            by_number = {row["invoice_number"]: row for row in body["invoices"]}
            assert by_number[f"INV-PART-{suffix}"]["customer_name"] == "Pelanggan A"
            assert by_number[f"INV-PART-{suffix}"]["payment_status"] == "DP"
            assert by_number[f"INV-PART-{suffix}"]["grand_total"] == 1_000_000
            assert by_number[f"INV-PAID-{suffix}"]["payment_status"] == "LUNAS"
            assert "Pelanggan B" not in listed.text
            assert f"INV-B-{suffix}" not in listed.text
            assert f"INV-DRAFT2-{suffix}" not in listed.text
            assert f"INV-OLD-{suffix}" not in listed.text

            august = await client.get("/api/sales-pwa/invoices", params={"month": "2026-08"})
            assert august.status_code == 200, august.text
            august_body = august.json()
            august_dash = await client.get("/api/sales-pwa/dashboard", params={"month": "2026-08"})
            assert august_body["summary"]["total_amount"] == august_dash.json()["metrics"]["revenue"] == 400_000
            assert august_body["summary"]["invoice_count"] == 1
            assert august_body["invoices"][0]["invoice_number"] == f"INV-OLD-{suffix}"
            assert august_body["invoices"][0]["payment_status"] == "BELUM DIBAYAR"

            receivables = await client.get("/api/sales-pwa/receivables", params={"sales_user_id": sales_b, "sales_id": sales_b})
            assert receivables.status_code == 200, receivables.text
            recv = receivables.json()
            assert recv["summary"]["outstanding_total"] == metrics["receivables"]
            assert recv["summary"]["invoice_count"] == metrics["receivable_invoice_count"]
            assert [row["invoice_number"] for row in recv["receivables"]] == [f"INV-OLD-{suffix}", f"INV-PART-{suffix}"]
            old = recv["receivables"][0]
            partial = recv["receivables"][1]
            assert old["outstanding_amount"] == 400_000
            assert old["paid_amount"] == 0
            assert old["due_date"] == "2026-09-14"
            assert partial["paid_amount"] == 250_000
            assert partial["outstanding_amount"] == 750_000
            assert partial["grand_total"] == 1_000_000
            assert sum(row["outstanding_amount"] for row in recv["receivables"]) == metrics["receivables"]
            assert f"INV-PAID-{suffix}" not in receivables.text
            assert "Pelanggan B" not in receivables.text

            app.dependency_overrides[current_user] = lambda: _admin()
            denied = await client.get("/api/sales-pwa/invoices", params={"month": "2026-10"})
            assert denied.status_code == 403
            denied_recv = await client.get("/api/sales-pwa/receivables")
            assert denied_recv.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.orders.delete_many({"id": {"$in": [po_a["id"], po_cancelled["id"], po_b["id"]]}})
        await db.sales_invoices.delete_many({"id": {"$in": invoice_ids}})
        await db.sales_receipts.delete_many({"invoice_id": {"$in": invoice_ids}})
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})


@pytest.mark.anyio
async def test_sales_pwa_agenda_keeps_today_and_cross_month_overdue():
    import server
    from services.order_prediction import wib_today

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    today = wib_today()
    previous = today.replace(day=1) - timedelta(days=1)
    future = today + timedelta(days=10)
    month = today.strftime("%Y-%m")
    outlet_today = str(uuid.uuid4())
    outlet_overdue = str(uuid.uuid4())
    outlet_future = str(uuid.uuid4())
    outlet_other = str(uuid.uuid4())
    outlet_ids = [outlet_today, outlet_overdue, outlet_future, outlet_other]
    activity_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    await db.users.insert_many([
        {"id": sales_a, "email": f"agenda-{sales_a[:8]}@eternal.com", "name": "Agenda A", "role": "sales_eternal"},
        {"id": sales_b, "email": f"agenda-{sales_b[:8]}@eternal.com", "name": "Agenda B", "role": "sales_eternal"},
    ])
    await db.sales_outlets.insert_many([
        {"id": outlet_today, "name": "Outlet Hari Ini", "address": "Jl. Agenda 1"},
        {"id": outlet_overdue, "name": "Outlet Terlambat", "address": "Jl. Lama"},
        {"id": outlet_future, "name": "Outlet Mendatang", "address": "Jl. Besok"},
        {"id": outlet_other, "name": "Outlet Sales Lain", "address": "Jl. Orang"},
    ])
    await db.sales_ownership.insert_many([
        {"id": str(uuid.uuid4()), "outlet_id": outlet_today, "current_owner_id": sales_a, "next_follow_up_at": f"{today.isoformat()}T09:00:00+07:00"},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_overdue, "current_owner_id": sales_a, "next_follow_up_at": f"{previous.isoformat()}T08:00:00+07:00"},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_future, "current_owner_id": sales_a, "next_follow_up_at": f"{future.isoformat()}T11:30:00+07:00"},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_other, "current_owner_id": sales_b, "next_follow_up_at": f"{today.isoformat()}T09:00:00+07:00"},
    ])
    await db.sales_activities.insert_many([
        {
            "id": activity_ids[0],
            "outlet_id": outlet_today,
            "sales_user_id": sales_a,
            "activity_type": "visit",
            "activity_at": f"{today.isoformat()}T01:00:00+00:00",
            "result": "Sudah datang",
            "next_action": "Bawa sampel",
        },
        {
            "id": activity_ids[1],
            "outlet_id": outlet_today,
            "sales_user_id": sales_a,
            "activity_type": "visit",
            "activity_at": "2020-01-02T01:00:00+00:00",
            "result": "Lama",
        },
    ])
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, f"agenda-{sales_a[:8]}@eternal.com", "Agenda A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            dashboard = await client.get("/api/sales-pwa/dashboard", params={"month": month, "sales_user_id": sales_b})
            assert dashboard.status_code == 200, dashboard.text
            body = dashboard.json()
            assert body["metrics"]["visit_month"] == 1
            assert body["metrics"]["revenue"] == 0
            assert body["metrics"]["receivables"] == 0
            assert body["metrics"]["active_po"] == 0
            today_names = [row["outlet"] for row in body["agenda_today"]]
            overdue_names = [row["outlet"] for row in body["follow_up_overdue"]]
            upcoming_names = [row["outlet"] for row in body["follow_up_upcoming"]]
            assert today_names == ["Outlet Hari Ini"]
            assert "Outlet Terlambat" in overdue_names
            assert "Outlet Mendatang" in upcoming_names
            assert "Outlet Sales Lain" not in today_names + overdue_names + upcoming_names
            today_row = body["agenda_today"][0]
            assert today_row["address"] == "Jl. Agenda 1"
            assert today_row["next_action"] == "Bawa sampel"
            assert today_row["date"] == today.isoformat()
            assert len(body["upcoming_visits"]) == len(today_names) + len(overdue_names) + len(upcoming_names)
            assert body["metrics"]["visit_overdue"] == len(overdue_names)
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.sales_outlets.delete_many({"id": {"$in": outlet_ids}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": outlet_ids}})
        await db.sales_activities.delete_many({"id": {"$in": activity_ids}})


@pytest.mark.anyio
async def test_sales_pwa_follow_up_read_model_and_wib_wall_clock():
    import server
    from services.sales_pwa import normalize_pwa_local_datetime

    assert normalize_pwa_local_datetime("2099-06-15T09:00") == "2099-06-15T02:00:00+00:00"
    assert normalize_pwa_local_datetime("2099-06-15T09:00:00+07:00") == "2099-06-15T02:00:00+00:00"
    assert normalize_pwa_local_datetime("") is None

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    outlet_id = str(uuid.uuid4())
    other_outlet = str(uuid.uuid4())
    email_a = f"follow-{sales_a[:8]}@eternal.com"
    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Follow A", "role": "sales_eternal"},
        {"id": sales_b, "email": f"follow-{sales_b[:8]}@eternal.com", "name": "Follow B", "role": "sales_eternal"},
    ])
    await db.sales_outlets.insert_many([
        {"id": outlet_id, "name": "Outlet Follow A", "address": "Jl. Follow"},
        {"id": other_outlet, "name": "Outlet Follow B", "address": "Jl. Lain"},
    ])
    await db.sales_ownership.insert_many([
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_id,
            "current_owner_id": sales_a,
            "automation_status": "PAUSED_MANUAL",
            "in_pool": False,
            "next_follow_up_at": None,
            "updated_at": "2020-01-01T00:00:00+00:00",
            "version": 1,
        },
        {
            "id": str(uuid.uuid4()),
            "outlet_id": other_outlet,
            "current_owner_id": sales_b,
            "automation_status": "PAUSED_MANUAL",
            "in_pool": False,
            "next_follow_up_at": "2099-06-15T09:00:00+07:00",
            "version": 1,
        },
    ])
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Follow A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/sales-pwa/visits", json={
                "outlet_id": outlet_id,
                "result": "Ketemu pemilik",
                "next_action": "Kirim sampel",
                "next_follow_up_at": "2099-06-15T09:00",
                "notes": "Pagi",
                "sales_user_id": sales_b,
                "sales_id": sales_b,
            })
            assert created.status_code == 200, created.text
            activity = created.json()["activity"]
            assert activity["sales_user_id"] == sales_a
            assert activity["next_follow_up_at"] == "2099-06-15T02:00:00+00:00"
            listed = await client.get("/api/sales-pwa/visits", params={"sales_user_id": sales_b})
            assert listed.status_code == 200, listed.text
            body = listed.json()
            assert [row["outlet"] for row in body["follow_up_upcoming"] if row["outlet"] == "Outlet Follow A"]
            match = next(row for row in body["reminders"] if row["outlet_id"] == outlet_id)
            assert match["date"] == "2099-06-15"
            assert str(match["at"]).startswith("2099-06-15T09:00")
            assert match["kind"] == "follow_up"
            assert match["next_action"] == "Kirim sampel"
            assert all(row["outlet_id"] != other_outlet for row in body["reminders"])
            assert sum(1 for row in body["reminders"] if row["outlet_id"] == outlet_id) == 1

            own = await db.sales_ownership.find_one({"outlet_id": outlet_id})
            assert own.get("next_follow_up_at") == "2099-06-15T02:00:00+00:00"

            updated = await db.sales_ownership.update_one(
                {"outlet_id": outlet_id},
                {"$set": {"next_follow_up_at": "2099-08-01T10:00:00+07:00", "updated_at": "2099-07-01T00:00:00+00:00"}},
            )
            assert updated.modified_count == 1
            stored = await db.sales_ownership.find_one({"outlet_id": outlet_id})
            assert stored["next_follow_up_at"] == "2099-08-01T10:00:00+07:00"
            assert stored["updated_at"] == "2099-07-01T00:00:00+00:00"
            refreshed = await client.get("/api/sales-pwa/dashboard", params={"month": "2099-07"})
            assert refreshed.status_code == 200, refreshed.text
            dash = refreshed.json()
            rows = [row for row in dash["follow_up_upcoming"] if row["outlet_id"] == outlet_id]
            assert len(rows) == 1
            assert rows[0]["date"] == "2099-08-01"
            assert rows[0]["source"] == "ownership"
            assert rows[0]["kind"] == "schedule"
            assert dash["metrics"]["visit_month"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.sales_outlets.delete_many({"id": {"$in": [outlet_id, other_outlet]}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": [outlet_id, other_outlet]}})
        await db.sales_activities.delete_many({"outlet_id": outlet_id, "sales_user_id": sales_a})


@pytest.mark.anyio
async def test_sales_pwa_schedule_does_not_create_completed_visit():
    import server
    from services.order_prediction import wib_today

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    outlet_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    future = wib_today() + timedelta(days=4)
    email_a = f"plan-{sales_a[:8]}@eternal.com"
    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Plan A", "role": "sales_eternal"},
        {"id": sales_b, "email": f"plan-{sales_b[:8]}@eternal.com", "name": "Plan B", "role": "sales_eternal"},
    ])
    await db.sales_outlets.insert_many([
        {"id": outlet_id, "name": "Outlet Jadwal", "address": "Jl. Jadwal"},
        {"id": other_id, "name": "Outlet Orang Lain", "address": "Jl. Lain"},
    ])
    await db.sales_ownership.insert_many([
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_id,
            "current_owner_id": sales_a,
            "in_pool": False,
            "version": 3,
            "updated_at": "2020-01-01T00:00:00+00:00",
            "next_follow_up_at": "2020-01-02T09:00:00+07:00",
            "eligible_at": "2030-01-01T00:00:00+00:00",
            "warning_at": "2029-12-01T00:00:00+00:00",
        },
        {
            "id": str(uuid.uuid4()),
            "outlet_id": other_id,
            "current_owner_id": sales_b,
            "in_pool": False,
            "version": 1,
            "next_follow_up_at": f"{future.isoformat()}T09:00:00+07:00",
        },
    ])
    await db.sales_activities.insert_one({
        "id": str(uuid.uuid4()),
        "outlet_id": outlet_id,
        "sales_user_id": sales_a,
        "activity_type": "visit",
        "activity_at": "2020-01-02T02:00:00+00:00",
        "result": "Lama",
        "next_follow_up_at": "2020-01-03T02:00:00+00:00",
    })
    before = await db.sales_activities.count_documents({"outlet_id": outlet_id, "sales_user_id": sales_a})
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Plan A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": other_id,
                "next_follow_up_at": f"{future.isoformat()}T09:00",
                "sales_user_id": sales_b,
            })
            assert denied.status_code == 404, denied.text
            scheduled = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": outlet_id,
                "next_follow_up_at": f"{future.isoformat()}T09:00",
                "sales_user_id": sales_b,
                "user_id": sales_b,
            })
            assert scheduled.status_code == 200, scheduled.text
            assert scheduled.json()["scheduled"] is True
            assert scheduled.json()["date"] == future.isoformat()
            after = await db.sales_activities.count_documents({"outlet_id": outlet_id, "sales_user_id": sales_a})
            assert after == before
            stored = await db.sales_ownership.find_one({"outlet_id": outlet_id})
            assert stored["eligible_at"] == "2030-01-01T00:00:00+00:00"
            assert stored["warning_at"] == "2029-12-01T00:00:00+00:00"
            assert stored["current_owner_id"] == sales_a
            assert stored["version"] == 4
            listed = await client.get("/api/sales-pwa/visits")
            rows = [row for row in listed.json()["reminders"] if row["outlet_id"] == outlet_id]
            assert len(rows) == 1
            assert rows[0]["date"] == future.isoformat()
            assert rows[0]["kind"] == "schedule"
            assert rows[0]["source"] == "ownership"
            assert all(row["outlet_id"] != other_id for row in listed.json()["reminders"])
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.sales_outlets.delete_many({"id": {"$in": [outlet_id, other_id]}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": [outlet_id, other_id]}})
        await db.sales_activities.delete_many({"outlet_id": outlet_id})


@pytest.mark.anyio
async def test_customer_visit_history_uses_only_linked_customer_id():
    import server
    from services.sales_pwa import _stable_customer_link

    assert _stable_customer_link("CUST-1") == "CUST-1"
    assert _stable_customer_link("name:kedai") is None
    assert _stable_customer_link("Kedai|Yogyakarta") is None
    assert _stable_customer_link("") is None

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    email_a = f"custvis-{sales_a[:8]}@eternal.com"
    po_linked = _order("PO-LINK", "Label A", sales_a, "Pelanggan Aman", customer_id="CUST-AMAN", order_date="2026-09-01")
    po_named = _order("PO-NAME", "Label A", sales_a, "Pelanggan Nama", order_date="2026-09-02")
    po_named.pop("customer_id", None)
    outlet_linked = str(uuid.uuid4())
    outlet_named = str(uuid.uuid4())
    outlet_other = str(uuid.uuid4())
    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Cust A", "role": "sales_eternal"},
        {"id": sales_b, "email": f"custvis-{sales_b[:8]}@eternal.com", "name": "Cust B", "role": "sales_eternal"},
    ])
    await db.orders.insert_many([po_linked, po_named])
    await db.sales_outlets.insert_many([
        {"id": outlet_linked, "name": "Outlet Beda Nama", "linked_customer_id": "CUST-AMAN"},
        {"id": outlet_named, "name": "Pelanggan Nama", "linked_customer_id": None},
        {"id": outlet_other, "name": "Outlet Sales Lain", "linked_customer_id": "CUST-AMAN"},
    ])
    await db.sales_ownership.insert_many([
        {"id": str(uuid.uuid4()), "outlet_id": outlet_linked, "current_owner_id": sales_a, "in_pool": False},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_named, "current_owner_id": sales_a, "in_pool": False},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_other, "current_owner_id": sales_b, "in_pool": False},
    ])
    await db.sales_activities.insert_many([
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_linked,
            "sales_user_id": sales_a,
            "activity_type": "visit",
            "activity_at": "2026-09-10T02:00:00+00:00",
            "result": "Hasil aman",
            "next_action": "Kirim harga",
            "notes": "Catatan aman",
            "next_follow_up_at": "2026-09-20T02:00:00+00:00",
        },
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_named,
            "sales_user_id": sales_a,
            "activity_type": "visit",
            "activity_at": "2026-09-11T02:00:00+00:00",
            "result": "Jangan ikut nama",
        },
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_other,
            "sales_user_id": sales_b,
            "activity_type": "visit",
            "activity_at": "2026-09-12T02:00:00+00:00",
            "result": "Visit sales lain",
        },
    ])
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Cust A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-pwa/customers", params={"sales_user_id": sales_b})
            assert listed.status_code == 200, listed.text
            customers = {row["customer"]: row for row in listed.json()["customers"]}
            linked = customers["Pelanggan Aman"]
            assert linked["visit_link"] == "linked"
            assert [row["result"] for row in linked["visit_history"]] == ["Hasil aman"]
            assert linked["visit_history"][0]["next_action"] == "Kirim harga"
            assert linked["visit_history"][0]["notes"] == "Catatan aman"
            assert "Visit sales lain" not in listed.text
            named = customers["Pelanggan Nama"]
            assert named["customer_key"].startswith("name:")
            assert named["visit_link"] == "unlinked"
            assert named["visit_history"] == []
            assert "Jangan ikut nama" not in listed.text
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.orders.delete_many({"id": {"$in": [po_linked["id"], po_named["id"]]}})
        await db.sales_outlets.delete_many({"id": {"$in": [outlet_linked, outlet_named, outlet_other]}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": [outlet_linked, outlet_named, outlet_other]}})
        await db.sales_activities.delete_many({"outlet_id": {"$in": [outlet_linked, outlet_named, outlet_other]}})


@pytest.mark.anyio
async def test_schedule_then_visit_follow_up_keeps_one_intent():
    import server

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    outlet_id = str(uuid.uuid4())
    email_a = f"slot-{sales_a[:8]}@eternal.com"
    await db.users.insert_one({"id": sales_a, "email": email_a, "name": "Slot A", "role": "sales_eternal"})
    await db.sales_outlets.insert_one({"id": outlet_id, "name": "Outlet Slot"})
    await db.sales_ownership.insert_one({
        "id": str(uuid.uuid4()),
        "outlet_id": outlet_id,
        "current_owner_id": sales_a,
        "automation_status": "PAUSED_MANUAL",
        "in_pool": False,
        "version": 2,
        "updated_at": "2020-01-01T00:00:00+00:00",
        "next_follow_up_at": None,
        "eligible_at": "2031-01-01T00:00:00+00:00",
        "warning_at": "2030-12-01T00:00:00+00:00",
    })
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Slot A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            scheduled = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": outlet_id,
                "next_follow_up_at": "2099-04-02T09:00",
                "sales_user_id": sales_b,
                "sales_id": sales_b,
                "user_id": sales_b,
            })
            assert scheduled.status_code == 200, scheduled.text
            assert scheduled.json()["date"] == "2099-04-02"
            listed = await client.get("/api/sales-pwa/visits", params={"sales_user_id": sales_b, "user_id": sales_b})
            assert listed.status_code == 200, listed.text
            planned = [row for row in listed.json()["reminders"] if row["outlet_id"] == outlet_id]
            assert len(planned) == 1
            assert planned[0]["kind"] == "schedule"
            assert planned[0]["date"] == "2099-04-02"
            assert await db.sales_activities.count_documents({"outlet_id": outlet_id, "activity_type": "visit"}) == 0

            completed = await client.post("/api/sales-pwa/visits", json={
                "outlet_id": outlet_id,
                "result": "Sudah datang",
                "next_action": "Kirim harga",
                "next_follow_up_at": "2099-06-15T10:30",
                "sales_user_id": sales_b,
                "sales_id": sales_b,
                "user_id": sales_b,
            })
            assert completed.status_code == 200, completed.text
            activity = completed.json()["activity"]
            assert activity["sales_user_id"] == sales_a
            assert activity["activity_type"] == "visit"
            assert activity["next_follow_up_at"] == "2099-06-15T03:30:00+00:00"
            assert await db.sales_activities.count_documents({"outlet_id": outlet_id, "activity_type": "visit"}) == 1

            refreshed = await client.get("/api/sales-pwa/visits")
            body = refreshed.json()
            rows = [row for row in body["reminders"] if row["outlet_id"] == outlet_id]
            assert len(rows) == 1
            assert rows[0]["date"] == "2099-06-15"
            assert str(rows[0]["at"]).startswith("2099-06-15T10:30")
            assert rows[0]["kind"] == "follow_up"
            assert "2099-04-02" not in refreshed.text
            stored = await db.sales_ownership.find_one({"outlet_id": outlet_id})
            assert stored["next_follow_up_at"] == "2099-06-15T03:30:00+00:00"
            assert stored["eligible_at"] == "2031-01-01T00:00:00+00:00"
            assert stored["warning_at"] == "2030-12-01T00:00:00+00:00"
            assert stored["current_owner_id"] == sales_a
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": sales_a})
        await db.sales_outlets.delete_many({"id": outlet_id})
        await db.sales_ownership.delete_many({"outlet_id": outlet_id})
        await db.sales_activities.delete_many({"outlet_id": outlet_id})


@pytest.mark.anyio
async def test_wib_midnight_crossing_stays_on_jakarta_date():
    from services.sales_pwa import _classify_reminders, _reminder_for_outlet, _wib_follow_fields, _written_after, normalize_pwa_local_datetime

    assert normalize_pwa_local_datetime("2026-10-01T09:00") == "2026-10-01T02:00:00+00:00"
    assert normalize_pwa_local_datetime("2026-10-01T00:30") == "2026-09-30T17:30:00+00:00"
    day, at = _wib_follow_fields("2026-09-30T17:30:00+00:00")
    assert day == "2026-10-01"
    assert str(at).startswith("2026-10-01T00:30")
    assert _written_after("2026-10-01T09:00:00+07:00", "2026-10-01T03:00:00+00:00") is False
    assert _written_after("2026-10-01T03:00:00+00:00", "2026-10-01T09:00:00+07:00") is True
    reminder = _reminder_for_outlet(
        "outlet-midnight",
        {"next_follow_up_at": "2026-09-30T17:30:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00"},
        {"name": "Outlet Tengah Malam"},
        {},
        "2026-10-01",
    )
    assert reminder["date"] == "2026-10-01"
    assert str(reminder["at"]).startswith("2026-10-01T00:30")
    buckets = _classify_reminders([reminder], "2026-10-01")
    assert [row["outlet"] for row in buckets["agenda_today"]] == ["Outlet Tengah Malam"]
    assert buckets["follow_up_overdue"] == []


@pytest.mark.anyio
async def test_agenda_limit_applies_after_classification():
    import server
    from services.order_prediction import wib_today

    db = server.db
    sales_a = str(uuid.uuid4())
    email_a = f"cap-{sales_a[:8]}@eternal.com"
    today = wib_today().isoformat()
    outlet_ids = [str(uuid.uuid4()) for _ in range(32)]
    today_outlet = outlet_ids[0]
    await db.users.insert_one({"id": sales_a, "email": email_a, "name": "Cap A", "role": "sales_eternal"})
    await db.sales_outlets.insert_many([
        {"id": outlet_id, "name": "Hari Ini" if outlet_id == today_outlet else f"Telat {index}"}
        for index, outlet_id in enumerate(outlet_ids)
    ])
    await db.sales_ownership.insert_many([
        {
            "id": str(uuid.uuid4()),
            "outlet_id": outlet_id,
            "current_owner_id": sales_a,
            "in_pool": False,
            "next_follow_up_at": f"{today}T09:00:00+07:00" if outlet_id == today_outlet else f"2020-01-{(index % 28) + 1:02d}T08:00:00+07:00",
        }
        for index, outlet_id in enumerate(outlet_ids)
    ])
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Cap A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            dashboard = await client.get("/api/sales-pwa/dashboard", params={"sales_user_id": "someone-else"})
            assert dashboard.status_code == 200, dashboard.text
            body = dashboard.json()
            assert body["metrics"]["visit_overdue"] == 31
            assert body["follow_up_overdue_total"] == 31
            assert len(body["follow_up_overdue"]) == 30
            assert body["agenda_today_total"] == 1
            assert [row["outlet"] for row in body["agenda_today"]] == ["Hari Ini"]
            assert body["metrics"]["revenue"] == 0
            assert body["metrics"]["receivables"] == 0
            assert body["metrics"]["active_po"] == 0
            assert "Hari Ini" not in [row["outlet"] for row in body["follow_up_overdue"]]
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": sales_a})
        await db.sales_outlets.delete_many({"id": {"$in": outlet_ids}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": outlet_ids}})


def test_prediction_value_never_turns_unknown_into_zero():
    from services.sales_pwa import _customer_order_value, _order_line_value

    complete = _order_line_value({"line_items": [{"quantity": 2, "unit_price": 5000}]})
    assert complete == {"value_status": "complete", "order_value": 10000.0, "known_value": 10000.0}

    missing_price = _order_line_value({"line_items": [{"quantity": 2, "name": "Cup"}]})
    assert missing_price == {"value_status": "unknown", "order_value": None, "known_value": None}

    missing_qty = _order_line_value({"line_items": [{"unit_price": 5000, "name": "Cup"}]})
    assert missing_qty["order_value"] is None
    assert missing_qty["known_value"] is None
    assert missing_qty["value_status"] == "unknown"

    blank = _order_line_value({"line_items": [{"quantity": "  ", "unit_price": 1000}]})
    assert blank["order_value"] is None
    assert blank["known_value"] is None

    negative = _order_line_value({"line_items": [{"quantity": 1, "unit_price": -5}]})
    assert negative["order_value"] is None

    mixed = _order_line_value({"line_items": [
        {"quantity": 2, "unit_price": 1000},
        {"quantity": 1, "name": "Tanpa harga"},
    ]})
    assert mixed["value_status"] == "partial"
    assert mixed["order_value"] is None
    assert mixed["known_value"] == 2000.0

    explicit_zero = _order_line_value({"line_items": [{"quantity": 2, "unit_price": 0}]})
    assert explicit_zero["value_status"] == "complete"
    assert explicit_zero["order_value"] == 0.0

    imported = _order_line_value({
        "history_source": "IMPORT HISTORIS",
        "line_items": [{"quantity": 1, "unit_price": 9}],
    })
    assert imported["order_value"] is None
    assert imported["known_value"] is None

    partial_customer = _customer_order_value([
        {"line_items": [{"quantity": 2, "unit_price": 1000}]},
        {"line_items": [{"quantity": 4}]},
        {},
    ])
    assert partial_customer["value_status"] == "partial"
    assert partial_customer["historical_order_value"] is None
    assert partial_customer["average_order_value"] is None
    assert partial_customer["known_value"] == 2000.0

    unknown_customer = _customer_order_value([{}, {"line_items": []}])
    assert unknown_customer == {
        "value_status": "unknown",
        "historical_order_value": None,
        "average_order_value": None,
        "known_value": None,
    }

    full_customer = _customer_order_value([
        {"line_items": [{"quantity": 10, "unit_price": 1000}]},
        {"line_items": [{"quantity": 5, "unit_price": 2000}]},
        {"line_items": [{"quantity": 2, "unit_price": 5000}]},
    ])
    assert full_customer["value_status"] == "complete"
    assert full_customer["historical_order_value"] == 30000.0
    assert full_customer["average_order_value"] == 10000.0


def test_prediction_products_use_stable_ids_and_skip_invalid_sources():
    from services.sales_pwa import _prediction_link_key, _prediction_products

    assert _prediction_link_key("CUST-1", {"customer_key": "id:CUST-1"}) == "id:CUST-1"
    assert _prediction_link_key("CUST-1", None) == ""
    assert _prediction_link_key("name:toko sama", None) == ""
    assert _prediction_link_key("ambiguous:toko sama", {"customer_key": "ambiguous:toko sama"}) == "ambiguous:toko sama"

    rows = _prediction_products([
        {
            "history_source": "ORDER MASUK",
            "order_date": "2026-09-01",
            "line_items": [
                {"accurate_item_id": "CUP", "name": "Paper Cup", "unit": "pcs", "quantity": 10},
                {"accurate_item_id": "CUP", "name": "Paper Cup lain", "unit": "pcs", "quantity": 5},
            ],
        },
        {
            "history_source": "ORDER MASUK",
            "order_date": "2026-09-10",
            "line_items": [
                {"accurate_item_id": "CUP", "name": "Paper Cup", "unit": "pcs", "quantity": 2},
                {"name": "Gelas", "unit": "pcs"},
            ],
        },
        {
            "history_source": "ORDER MASUK",
            "order_date": "2026-08-01",
            "line_items": [{"name": "Gelas", "unit": "pcs", "quantity": 4}],
        },
        {
            "history_source": "ORDER MASUK",
            "order_date": "2026-07-01",
            "line_items": [{"name": "Tanpa Qty", "unit": "dus"}],
        },
        {
            "history_source": "IMPORT HISTORIS",
            "order_date": "2026-09-20",
            "line_items": [
                {"accurate_item_id": "CUP", "name": "Paper Cup", "quantity": 999},
                {"name": "Barang Import", "quantity": 3},
            ],
        },
        {"history_source": "ORDER MASUK", "order_date": "2026-09-11", "line_items": []},
    ])
    by_name = {row["name"]: row for row in rows}
    assert "Barang Import" not in by_name
    cup = by_name["Paper Cup"]
    assert cup["product_key"] == "a:CUP"
    assert cup["purchase_count"] == 2
    assert cup["total_quantity"] == 17
    assert cup["quantity_complete"] is True
    assert cup["last_purchase_date"] == "2026-09-10"
    assert cup["last_quantity"] == 2
    gelas = by_name["Gelas"]
    assert gelas["purchase_count"] == 2
    assert gelas["quantity_complete"] is False
    assert gelas["total_quantity"] == 4
    assert by_name["Tanpa Qty"]["total_quantity"] is None
    assert by_name["Tanpa Qty"]["quantity_complete"] is False
    assert [row["name"] for row in rows[:2]] == ["Paper Cup", "Gelas"]


@pytest.mark.anyio
async def test_sales_pwa_prediction_workflow_scope_and_agenda():
    import server
    from services.order_prediction import wib_today

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    email_a = f"pred-{sales_a[:8]}@eternal.com"
    email_b = f"pred-{sales_b[:8]}@eternal.com"
    suffix = uuid.uuid4().hex[:8]
    cust = f"CUST-{suffix}"
    thin = f"THIN-{suffix}"
    bare = f"BARE-{suffix}"
    partial = f"PART-{suffix}"
    other_only = f"OTHER-{suffix}"
    today = wib_today()
    outlet_one = str(uuid.uuid4())
    outlet_two = str(uuid.uuid4())
    outlet_pool = str(uuid.uuid4())
    outlet_other = str(uuid.uuid4())
    outlet_name = str(uuid.uuid4())
    outlet_ids = [outlet_one, outlet_two, outlet_pool, outlet_other, outlet_name]
    order_ids = []
    finance_names = ["invoices", "sales_invoices", "receipts", "journal_entries", "customer_credits"]
    existing_names = set(await db.list_collection_names())
    finance_before = {}
    for name in finance_names:
        finance_before[name] = await db[name].count_documents({}) if name in existing_names else 0

    def on_day(offset):
        return (today + timedelta(days=offset)).isoformat()

    def lined(po, customer_id, customer, order_date, items, **flags):
        doc = _order(po, "Sales A", sales_a, customer, customer_id=customer_id, order_date=order_date)
        doc["outlet"] = "Gudang Utara"
        doc["line_items"] = items
        doc.update(flags)
        order_ids.append(doc["id"])
        return doc

    cup = lambda qty, price: {"accurate_item_id": "CUP", "name": "Paper Cup", "unit": "pcs", "quantity": qty, "unit_price": price}
    orders = [
        lined(f"PO-OLD-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-27), [cup(10, 1000), {"accurate_item_id": "DUS", "name": "Dus", "unit": "pcs", "quantity": 1, "unit_price": 0}]),
        lined(f"PO-MID-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-18), [cup(5, 2000)]),
        lined(f"PO-NEW-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-9), [cup(2, 5000)], notes="Catatan follow up"),
        lined(f"PO-CANCEL-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-1), [cup(9, 9000)], cancelled=True),
        lined(f"PO-TEST-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-1), [cup(8, 8000)], is_test=True),
        lined(f"PO-DEMO-{suffix}", cust, f"Toko Prediksi {suffix}", on_day(-1), [cup(7, 7000)], is_demo=True),
        lined(f"PO-RISK-{suffix}", f"RISK-{suffix}", f"Toko Risiko {suffix}", on_day(-43), [cup(1, 1000)]),
        lined(f"PO-RISK2-{suffix}", f"RISK-{suffix}", f"Toko Risiko {suffix}", on_day(-34), [cup(1, 1000)]),
        lined(f"PO-RISK3-{suffix}", f"RISK-{suffix}", f"Toko Risiko {suffix}", on_day(-25), [cup(1, 1000)]),
        lined(f"PO-LATE-{suffix}", f"LATE-{suffix}", f"Toko Telat {suffix}", on_day(-32), [cup(1, 1000)]),
        lined(f"PO-LATE2-{suffix}", f"LATE-{suffix}", f"Toko Telat {suffix}", on_day(-23), [cup(1, 1000)]),
        lined(f"PO-LATE3-{suffix}", f"LATE-{suffix}", f"Toko Telat {suffix}", on_day(-14), [cup(1, 1000)]),
        lined(f"PO-SOON-{suffix}", f"SOON-{suffix}", f"Toko Follow {suffix}", on_day(-24), [cup(1, 1000)]),
        lined(f"PO-SOON2-{suffix}", f"SOON-{suffix}", f"Toko Follow {suffix}", on_day(-15), [cup(1, 1000)]),
        lined(f"PO-SOON3-{suffix}", f"SOON-{suffix}", f"Toko Follow {suffix}", on_day(-6), [cup(1, 1000)]),
        lined(f"PO-IDLE-{suffix}", f"IDLE-{suffix}", f"Toko Diam {suffix}", on_day(-68), [cup(1, 1000)]),
        lined(f"PO-IDLE2-{suffix}", f"IDLE-{suffix}", f"Toko Diam {suffix}", on_day(-59), [cup(1, 1000)]),
        lined(f"PO-IDLE3-{suffix}", f"IDLE-{suffix}", f"Toko Diam {suffix}", on_day(-50), [cup(1, 1000)]),
        lined(f"PO-WAIT-{suffix}", f"WAIT-{suffix}", f"Toko Tunggu {suffix}", on_day(-20), [cup(1, 1000)]),
        lined(f"PO-WAIT2-{suffix}", f"WAIT-{suffix}", f"Toko Tunggu {suffix}", on_day(-11), [cup(1, 1000)]),
        lined(f"PO-WAIT3-{suffix}", f"WAIT-{suffix}", f"Toko Tunggu {suffix}", on_day(-2), [cup(1, 1000)]),
        lined(f"PO-THIN-{suffix}", thin, f"Toko Tipis {suffix}", on_day(-1), [cup(1, 1000)]),
        lined(f"PO-BARE-{suffix}", bare, f"Toko Kosong {suffix}", on_day(-27), []),
        lined(f"PO-BARE2-{suffix}", bare, f"Toko Kosong {suffix}", on_day(-18), []),
        lined(f"PO-BARE3-{suffix}", bare, f"Toko Kosong {suffix}", on_day(-9), []),
        lined(f"PO-PART-{suffix}", partial, f"Toko Sebagian {suffix}", on_day(-27), [{"name": "Gelas", "quantity": 2, "unit_price": 1000}]),
        lined(f"PO-PART2-{suffix}", partial, f"Toko Sebagian {suffix}", on_day(-18), [{"name": "Gelas", "quantity": 2}]),
        lined(f"PO-PART3-{suffix}", partial, f"Toko Sebagian {suffix}", on_day(-9), [{"name": "Gelas", "unit_price": 1000}]),
        lined(f"PO-NAME-{suffix}", "", f"Toko Nama {suffix}", on_day(-3), [cup(1, 1000)]),
    ]
    foreign = _order(f"PO-FOREIGN-{suffix}", "Sales B", sales_b, f"Toko Prediksi {suffix}", customer_id=cust, order_date=on_day(-1), outlet="Gudang Rahasia")
    foreign["line_items"] = [cup(4, 4000)]
    order_ids.append(foreign["id"])
    hidden = _order(f"PO-HIDDEN-{suffix}", "Sales B", sales_b, f"Toko Rahasia {suffix}", customer_id=other_only, order_date=on_day(-1))
    order_ids.append(hidden["id"])
    name_order = next(doc for doc in orders if doc["po_number"] == f"PO-NAME-{suffix}")
    name_order["customer_id"] = ""

    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Sales A", "role": "sales_eternal"},
        {"id": sales_b, "email": email_b, "name": "Sales B", "role": "sales_eternal"},
    ])
    await db.orders.insert_many(orders + [foreign, hidden])
    await db.sales_outlets.insert_many([
        {"id": outlet_one, "name": "Outlet Satu", "linked_customer_id": cust},
        {"id": outlet_two, "name": "Outlet Dua", "linked_customer_id": cust},
        {"id": outlet_pool, "name": "Outlet Pool", "linked_customer_id": cust},
        {"id": outlet_other, "name": "Outlet Orang", "linked_customer_id": cust},
        {"id": outlet_name, "name": "Outlet Nama", "linked_customer_id": f"Toko Nama {suffix}"},
    ])
    await db.sales_ownership.insert_many([
        {"id": str(uuid.uuid4()), "outlet_id": outlet_one, "current_owner_id": sales_a, "in_pool": False, "next_follow_up_at": "2026-10-02T02:00:00+00:00"},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_two, "current_owner_id": sales_a, "in_pool": False},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_pool, "current_owner_id": sales_a, "in_pool": True},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_other, "current_owner_id": sales_b, "in_pool": False},
        {"id": str(uuid.uuid4()), "outlet_id": outlet_name, "current_owner_id": sales_a, "in_pool": False},
    ])
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Sales A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-pwa/predictions", params={"sales_user_id": sales_b, "sales_id": sales_b})
            assert listed.status_code == 200, listed.text
            body = listed.json()
            own = [row for row in body["rows"] if suffix in str(row.get("customer_name") or "")]
            ranks = [row["status"] for row in own]
            assert ranks == [
                "RISIKO",
                "TERLAMBAT",
                "JATUH TEMPO",
                "JATUH TEMPO",
                "JATUH TEMPO",
                "WAKTU FOLLOW-UP",
                "TIDAK AKTIF",
                "BELUM WAKTUNYA",
                "DATA BELUM CUKUP",
                "DATA BELUM CUKUP",
            ]
            assert all(row.get("customer_id") != other_only for row in body["rows"])
            main = next(row for row in own if row.get("customer_id") == cust)
            assert main["customer_key"] == f"id:{cust}"
            assert main["customer_name"] == f"Toko Prediksi {suffix}"
            assert main["median_gap"] == 9
            assert main["average_gap"] == 9
            assert main["prediction_center"] == today.isoformat()
            assert main["prediction_start"] == on_day(-3)
            assert main["prediction_end"] == on_day(3)
            assert main["followup_start"] == on_day(-5)
            assert main["last_order_date"] == on_day(-9)
            assert main["history_count"] == 3
            assert main["status"] == "JATUH TEMPO"
            assert main.get("confidence")
            late = next(row for row in own if row["status"] == "TERLAMBAT")
            assert late["days_late"] == 2
            assert "Lewat 2 hari" in late["reason"]

            customers = await client.get("/api/sales-pwa/customers", params={"sales_user_id": sales_b})
            assert customers.status_code == 200, customers.text
            customer_row = next(row for row in customers.json()["customers"] if row["customer_key"] == cust)
            assert customer_row["prediction_key"] == f"id:{cust}"
            assert customer_row["customer_key"] == cust

            raw = await client.get(f"/api/sales-pwa/predictions/{cust}", params={"sales_user_id": sales_b})
            assert raw.status_code == 404
            detail = await client.get(f"/api/sales-pwa/predictions/id:{cust}", params={"sales_id": sales_b})
            assert detail.status_code == 200, detail.text
            payload = detail.json()
            history = payload["history"]
            assert [row["po_number"] for row in history] == [f"PO-NEW-{suffix}", f"PO-MID-{suffix}", f"PO-OLD-{suffix}"]
            assert history[0]["gap_days"] == 9
            assert history[0]["outlet"] == "Gudang Utara"
            assert history[0]["notes"] == "Catatan follow up"
            assert history[0]["order_value"] == 10000.0
            assert history[0]["value_status"] == "complete"
            assert f"PO-CANCEL-{suffix}" not in detail.text
            assert f"PO-TEST-{suffix}" not in detail.text
            assert f"PO-DEMO-{suffix}" not in detail.text
            assert f"PO-FOREIGN-{suffix}" not in detail.text
            assert f"PO-HIDDEN-{suffix}" not in detail.text
            products = payload["products"]
            assert products[0]["name"] == "Paper Cup"
            assert products[0]["purchase_count"] == 3
            assert products[0]["total_quantity"] == 17
            assert products[0]["last_quantity"] == 2
            assert products[1]["name"] == "Dus"
            assert products[1]["purchase_count"] == 1
            assert products[1]["total_quantity"] == 1
            assert payload["value"]["value_status"] == "complete"
            assert payload["value"]["historical_order_value"] == 30000.0
            assert payload["value"]["average_order_value"] == 10000.0
            agenda = payload["agenda"]
            assert agenda["linked"] is True
            assert agenda["customer_id"] == cust
            assert [row["name"] for row in agenda["outlets"]] == ["Outlet Dua", "Outlet Satu"]
            assert agenda["outlets"][1]["has_active_agenda"] is True
            assert agenda["outlets"][1]["next_follow_up_at"] == "2026-10-02T02:00:00+00:00"
            assert agenda["outlets"][0]["has_active_agenda"] is False
            stored_before = await db.sales_ownership.find_one({"outlet_id": outlet_one})
            assert stored_before["next_follow_up_at"] == "2026-10-02T02:00:00+00:00"

            bare_row = next(row for row in own if row.get("customer_id") == bare)
            bare_detail = await client.get(f"/api/sales-pwa/predictions/{bare_row['customer_key']}")
            assert bare_detail.status_code == 200, bare_detail.text
            bare_body = bare_detail.json()
            assert bare_body["products"] == []
            assert bare_body["value"]["historical_order_value"] is None
            assert bare_body["value"]["value_status"] == "unknown"
            assert len(bare_body["history"]) == 3

            part_row = next(row for row in own if row.get("customer_id") == partial)
            part_detail = await client.get(f"/api/sales-pwa/predictions/{part_row['customer_key']}")
            assert part_detail.status_code == 200, part_detail.text
            part_body = part_detail.json()
            assert part_body["value"]["value_status"] == "partial"
            assert part_body["value"]["historical_order_value"] is None
            assert part_body["value"]["average_order_value"] is None
            assert part_body["value"]["known_value"] == 2000.0
            assert [row.get("order_value") for row in part_body["history"]] == [None, None, 2000.0]

            thin_row = next(row for row in own if row.get("customer_id") == thin)
            thin_detail = await client.get(f"/api/sales-pwa/predictions/{thin_row['customer_key']}")
            assert thin_detail.status_code == 200, thin_detail.text
            assert thin_detail.json()["agenda"]["linked"] is False
            assert thin_detail.json()["agenda"]["outlets"] == []

            name_row = next(row for row in own if str(row.get("customer_name") or "").startswith("Toko Nama"))
            assert str(name_row["customer_key"]).startswith("name:")
            name_detail = await client.get(f"/api/sales-pwa/predictions/{name_row['customer_key']}")
            assert name_detail.status_code == 200, name_detail.text
            assert name_detail.json()["agenda"]["linked"] is False

            app.dependency_overrides[current_user] = lambda: _sales_user(sales_b, email_b, "Sales B")
            shared = await client.get(f"/api/sales-pwa/predictions/id:{cust}")
            assert shared.status_code == 200, shared.text
            assert f"PO-FOREIGN-{suffix}" in shared.text
            assert f"PO-NEW-{suffix}" not in shared.text
            assert f"PO-OLD-{suffix}" not in shared.text
            hidden_detail = await client.get(f"/api/sales-pwa/predictions/id:RISK-{suffix}")
            assert hidden_detail.status_code == 404
            stolen = await client.post("/api/sales-pwa/visits/schedule", json={"outlet_id": outlet_one, "next_follow_up_at": "2026-10-08T09:00"})
            assert stolen.status_code == 404
            assert (await db.sales_ownership.find_one({"outlet_id": outlet_one}))["next_follow_up_at"] == "2026-10-02T02:00:00+00:00"

            app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Sales A")
            pool = await client.post("/api/sales-pwa/visits/schedule", json={"outlet_id": outlet_pool, "next_follow_up_at": "2026-10-08T09:00"})
            assert pool.status_code == 404
            spoof = await client.post("/api/sales-pwa/visits/schedule", json={"outlet_id": outlet_other, "next_follow_up_at": "2026-10-08T09:00"})
            assert spoof.status_code == 404
            missing = await client.post("/api/sales-pwa/visits/schedule", json={"outlet_id": str(uuid.uuid4()), "next_follow_up_at": "2026-10-08T09:00"})
            assert missing.status_code == 404
            scheduled = await client.post("/api/sales-pwa/visits/schedule", json={"outlet_id": outlet_two, "next_follow_up_at": "2026-10-06T09:00"})
            assert scheduled.status_code == 200, scheduled.text
            assert scheduled.json()["next_follow_up_at"] == "2026-10-06T02:00:00+00:00"
            assert (await db.sales_ownership.find_one({"outlet_id": outlet_two}))["next_follow_up_at"] == "2026-10-06T02:00:00+00:00"
            assert (await db.sales_ownership.find_one({"outlet_id": outlet_one}))["next_follow_up_at"] == "2026-10-02T02:00:00+00:00"
            assert await db.sales_activities.count_documents({"outlet_id": {"$in": outlet_ids}}) == 0
            names_after = set(await db.list_collection_names())
            assert "sales_prediction_followups" not in names_after
            assert "sales_prediction_reminders" not in names_after
            for name in finance_names:
                after = await db[name].count_documents({}) if name in names_after else 0
                assert after == finance_before[name]
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.orders.delete_many({"id": {"$in": order_ids}})
        await db.sales_outlets.delete_many({"id": {"$in": outlet_ids}})
        await db.sales_ownership.delete_many({"outlet_id": {"$in": outlet_ids}})


@pytest.mark.anyio
async def test_prediction_identity_does_not_promote_name_and_keeps_one_agenda_slot():
    import server
    from services.order_prediction import wib_today

    db = server.db
    sales_a = str(uuid.uuid4())
    sales_b = str(uuid.uuid4())
    email_a = f"ident-{sales_a[:8]}@eternal.com"
    email_b = f"ident-{sales_b[:8]}@eternal.com"
    suffix = uuid.uuid4().hex[:8]
    twin = f"Toko Kembar {suffix}"
    cust_a = f"TWIN-A-{suffix}"
    cust_b = f"TWIN-B-{suffix}"
    known = f"KNOWN-{suffix}"
    known_name = f"Toko Dikenal {suffix}"
    today = wib_today()
    outlet_a = str(uuid.uuid4())
    order_ids = []

    def on_day(offset):
        return (today + timedelta(days=offset)).isoformat()

    def lined(po, sales_id, customer_id, customer, order_date):
        doc = _order(po, "Sales A", sales_id, customer, customer_id=customer_id, order_date=order_date)
        doc["line_items"] = [{"accurate_item_id": "CUP", "name": "Paper Cup", "unit": "pcs", "quantity": 1, "unit_price": 1000}]
        order_ids.append(doc["id"])
        return doc

    orders = [
        lined(f"PO-A1-{suffix}", sales_a, cust_a, twin, on_day(-20)),
        lined(f"PO-A2-{suffix}", sales_a, cust_a, twin, on_day(-10)),
        lined(f"PO-B1-{suffix}", sales_a, cust_b, twin, on_day(-18)),
        lined(f"PO-B2-{suffix}", sales_a, cust_b, twin, on_day(-9)),
        lined(f"PO-NAME-{suffix}", sales_a, "", twin, on_day(-3)),
        lined(f"PO-K1-{suffix}", sales_a, known, known_name, on_day(-27)),
        lined(f"PO-K2-{suffix}", sales_a, known, known_name, on_day(-18)),
        lined(f"PO-K3-{suffix}", sales_a, known, known_name, on_day(-9)),
        lined(f"PO-BARE-{suffix}", sales_a, "", known_name, on_day(-2)),
        lined(f"PO-FOREIGN-{suffix}", sales_b, cust_a, twin, on_day(-1)),
    ]
    bare = next(doc for doc in orders if doc["po_number"] == f"PO-NAME-{suffix}")
    bare["customer_id"] = ""
    bare_known = next(doc for doc in orders if doc["po_number"] == f"PO-BARE-{suffix}")
    bare_known["customer_id"] = ""

    await db.users.insert_many([
        {"id": sales_a, "email": email_a, "name": "Sales A", "role": "sales_eternal"},
        {"id": sales_b, "email": email_b, "name": "Sales B", "role": "sales_eternal"},
    ])
    await db.orders.insert_many(orders)
    await db.sales_customers.insert_one({"id": known, "name": known_name})
    await db.sales_outlets.insert_one({"id": outlet_a, "name": "Outlet Stabil", "linked_customer_id": known})
    await db.sales_ownership.insert_one({
        "id": str(uuid.uuid4()),
        "outlet_id": outlet_a,
        "current_owner_id": sales_a,
        "in_pool": False,
    })
    app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Sales A")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-pwa/predictions", params={"sales_user_id": sales_b})
            assert listed.status_code == 200, listed.text
            rows = [row for row in listed.json()["rows"] if suffix in str(row.get("customer_name") or "")]
            by_key = {row["customer_key"]: row for row in rows}
            assert f"id:{cust_a}" in by_key
            assert f"id:{cust_b}" in by_key
            assert by_key[f"id:{cust_a}"]["customer_id"] == cust_a
            assert by_key[f"id:{cust_b}"]["customer_id"] == cust_b
            assert by_key[f"id:{cust_a}"]["history_count"] == 2
            assert by_key[f"id:{cust_b}"]["history_count"] == 2
            ambiguous_key = f"ambiguous:{twin.lower()}"
            assert ambiguous_key in by_key
            assert by_key[ambiguous_key]["customer_id"] in (None, "")
            assert f"PO-A1-{suffix}" not in str(by_key[ambiguous_key])
            name_key = f"name:{known_name.lower()}"
            assert name_key in by_key
            assert by_key[name_key]["customer_id"] in (None, "")
            assert by_key[f"id:{known}"]["history_count"] == 3
            assert by_key[f"id:{known}"]["customer_id"] == known

            detail_a = await client.get(f"/api/sales-pwa/predictions/id:{cust_a}")
            assert detail_a.status_code == 200, detail_a.text
            assert [row["po_number"] for row in detail_a.json()["history"]] == [f"PO-A2-{suffix}", f"PO-A1-{suffix}"]
            assert f"PO-B1-{suffix}" not in detail_a.text
            assert f"PO-NAME-{suffix}" not in detail_a.text
            assert f"PO-FOREIGN-{suffix}" not in detail_a.text

            ambiguous = await client.get(f"/api/sales-pwa/predictions/{ambiguous_key}")
            assert ambiguous.status_code == 200, ambiguous.text
            ambiguous_body = ambiguous.json()
            assert ambiguous_body["agenda"]["linked"] is False
            assert ambiguous_body["agenda"]["identity"] == "ambiguous"
            assert ambiguous_body["agenda"]["outlets"] == []
            assert ambiguous_body["analysis"]["customer_id"] in (None, "")
            assert [row["po_number"] for row in ambiguous_body["history"]] == [f"PO-NAME-{suffix}"]

            bare_detail = await client.get(f"/api/sales-pwa/predictions/{name_key}")
            assert bare_detail.status_code == 200, bare_detail.text
            bare_body = bare_detail.json()
            assert bare_body["agenda"]["linked"] is False
            assert bare_body["agenda"]["identity"] == "name"
            assert bare_body["agenda"]["customer_id"] is None
            assert bare_body["analysis"]["customer_id"] in (None, "")
            assert [row["po_number"] for row in bare_body["history"]] == [f"PO-BARE-{suffix}"]

            stable = await client.get(f"/api/sales-pwa/predictions/id:{known}")
            assert stable.status_code == 200, stable.text
            stable_body = stable.json()
            assert stable_body["agenda"]["linked"] is True
            assert stable_body["agenda"]["identity"] == "stable"
            assert stable_body["agenda"]["customer_id"] == known
            assert f"PO-BARE-{suffix}" not in stable.text
            assert [row["po_number"] for row in stable_body["history"]] == [
                f"PO-K3-{suffix}", f"PO-K2-{suffix}", f"PO-K1-{suffix}",
            ]

            customers = await client.get("/api/sales-pwa/customers")
            assert customers.status_code == 200, customers.text
            customer_rows = [row for row in customers.json()["customers"] if suffix in str(row.get("customer") or "")]
            stable_customer = next(row for row in customer_rows if row["customer_key"] == known)
            assert stable_customer["prediction_key"] == f"id:{known}"
            bare_customer = next(row for row in customer_rows if row["customer_key"] == f"name:{known_name.lower()}")
            assert bare_customer["prediction_key"] == name_key
            ambiguous_customer = next(row for row in customer_rows if row["customer_key"] == f"name:{twin.lower()}")
            assert ambiguous_customer["prediction_key"] == ambiguous_key

            missing = await client.get(f"/api/sales-pwa/predictions/id:MISSING-{suffix}")
            assert missing.status_code == 404

            app.dependency_overrides[current_user] = lambda: _sales_user(sales_b, email_b, "Sales B")
            foreign = await client.get(f"/api/sales-pwa/predictions/id:{cust_a}", params={"sales_user_id": sales_a})
            assert foreign.status_code == 200, foreign.text
            assert [row["po_number"] for row in foreign.json()["history"]] == [f"PO-FOREIGN-{suffix}"]
            assert f"PO-A1-{suffix}" not in foreign.text
            stolen = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": outlet_a,
                "next_follow_up_at": "2026-10-08T09:00",
            })
            assert stolen.status_code == 404

            app.dependency_overrides[current_user] = lambda: _sales_user(sales_a, email_a, "Sales A")
            first = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": outlet_a,
                "next_follow_up_at": "2026-10-06T09:00",
            })
            assert first.status_code == 200, first.text
            assert first.json()["next_follow_up_at"] == "2026-10-06T02:00:00+00:00"
            second = await client.post("/api/sales-pwa/visits/schedule", json={
                "outlet_id": outlet_a,
                "next_follow_up_at": "2026-10-09T11:30",
            })
            assert second.status_code == 200, second.text
            stored = await db.sales_ownership.find({"outlet_id": outlet_a}).to_list(10)
            assert len(stored) == 1
            assert stored[0]["next_follow_up_at"] == "2026-10-09T04:30:00+00:00"
            assert await db.sales_activities.count_documents({"outlet_id": outlet_a}) == 0
            names_after = set(await db.list_collection_names())
            assert "sales_prediction_followups" not in names_after
            assert "sales_prediction_reminders" not in names_after
    finally:
        app.dependency_overrides.pop(current_user, None)
        await db.users.delete_many({"id": {"$in": [sales_a, sales_b]}})
        await db.orders.delete_many({"id": {"$in": order_ids}})
        await db.sales_outlets.delete_many({"id": outlet_a})
        await db.sales_ownership.delete_many({"outlet_id": outlet_a})
        await db.sales_customers.delete_many({"id": known})
