"""PWA Sales read-only scope: session users.id only. Role sales_eternal."""
import os
import sys
import uuid
from datetime import datetime, timezone
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
