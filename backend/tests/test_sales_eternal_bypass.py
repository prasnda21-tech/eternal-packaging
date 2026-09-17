"""JWT login as Ayub ↔ Euis: global endpoints cannot leak the other sales person's data.

Uses real /api/auth/login (no current_user override). Authorization is session users.id.
"""
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

from server import app, current_user, hash_password
from services.sales_user_mapping import SURFACE_LOCK_DETAIL

_RUN = uuid.uuid4().hex[:8]
AYUB_ID = str(uuid.uuid4())
EUIS_ID = str(uuid.uuid4())
AYUB_EMAIL = f"ayub.bypass.{_RUN}@eternal.com"
EUIS_EMAIL = f"euis.bypass.{_RUN}@eternal.com"
ADMIN_EMAIL = f"admin.bypass.{_RUN}@eternal.com"
PASSWORD = "BypassSalesX24"
ACCURATE_AYUB = "Sholahudin Al - Ayubi"
ACCOUNT_AYUB = "Sholahuddin Al Ayyubi"
ACCURATE_EUIS = "Euis Risnawati"
AYUB_CUSTOMER = f"Kopi Ayub {_RUN}"
EUIS_CUSTOMER = f"Toko Euis {_RUN}"


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


def _assert_locked(resp, *secret_names):
    assert resp.status_code in (403, 404), resp.text
    body = resp.text
    assert SURFACE_LOCK_DETAIL in body or "tidak ditemukan" in body.lower() or "tidak punya permission" in body.lower() or "hanya dapat memakai" in body.lower()
    if resp.status_code == 403:
        assert SURFACE_LOCK_DETAIL in body
    for name in secret_names:
        assert name not in body


def _order(po, sales, sales_user_id, customer, **extra):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": extra.get("id") or str(uuid.uuid4()),
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "customer_id": extra.get("customer_id", f"cust-{customer}"),
        "sales": sales,
        "sales_user_id": sales_user_id,
        "sales_ownership_status": extra.get("sales_ownership_status", "matched"),
        "order_date": extra.get("order_date", "2026-01-01"),
        "cancelled": False,
        "current_stage": extra.get("current_stage", 5),
        "status_label": extra.get("status_label", "Penjadwalan"),
        "line_items": [{"name": extra.get("item", "Paper cup"), "quantity": 1000, "unit": "pcs"}],
        "created_at": now,
        "updated_at": now,
        "source_pdf": extra.get("source_pdf") or {"storage_path": extra.get("po_file", f"files/po-{po}.pdf")},
        "surat_jalan_files": extra.get("surat_jalan_files") or [],
        "delivery_proofs": extra.get("delivery_proofs") or [],
        "shipments": extra.get("shipments") or [],
    }


async def _seed(db):
    await db.users.delete_many({"email": {"$in": [AYUB_EMAIL, EUIS_EMAIL, ADMIN_EMAIL]}})
    await db.users.delete_many({"id": {"$in": [AYUB_ID, EUIS_ID, f"admin-{_RUN}"]}})
    hashed = hash_password(PASSWORD)
    await db.users.insert_many([
        {
            "id": AYUB_ID,
            "email": AYUB_EMAIL,
            "name": ACCOUNT_AYUB,
            "role": "sales_eternal",
            "employee_id": f"EMP-0049-{_RUN}",
            "status": "active",
            "password_hash": hashed,
            "permissions_override": {},
        },
        {
            "id": EUIS_ID,
            "email": EUIS_EMAIL,
            "name": ACCURATE_EUIS,
            "role": "sales_eternal",
            "employee_id": f"EMP-0052-{_RUN}",
            "status": "active",
            "password_hash": hashed,
            "permissions_override": {"po.view": True},
        },
        {
            "id": f"admin-{_RUN}",
            "email": ADMIN_EMAIL,
            "name": "Admin Bypass",
            "role": "admin",
            "status": "active",
            "password_hash": hashed,
        },
    ])
    ayub_po_id = f"po-ayub-{_RUN}"
    euis_po_id = f"po-euis-{_RUN}"
    ayub_file = f"storage/po-ayub-{_RUN}.pdf"
    euis_file = f"storage/po-euis-{_RUN}.pdf"
    euis_sj = f"storage/sj-euis-{_RUN}.pdf"
    euis_proof = f"storage/proof-euis-{_RUN}.jpg"
    await db.orders.delete_many({"id": {"$in": [ayub_po_id, euis_po_id]}})
    await db.orders.insert_many([
        _order(
            f"PO-AYU-{_RUN}", ACCURATE_AYUB, AYUB_ID, AYUB_CUSTOMER,
            id=ayub_po_id, po_file=ayub_file, customer_id=f"cust-ayub-{_RUN}",
        ),
        _order(
            f"PO-EUI-{_RUN}", ACCURATE_EUIS, EUIS_ID, EUIS_CUSTOMER,
            id=euis_po_id, po_file=euis_file, customer_id=f"cust-euis-{_RUN}",
            surat_jalan_files=[{"id": f"sjf-{_RUN}", "storage_path": euis_sj, "shipment_id": f"shp-{_RUN}"}],
            delivery_proofs=[{"id": f"prf-{_RUN}", "storage_path": euis_proof}],
            shipments=[{"id": f"shp-{_RUN}", "number": f"SJ-EUI-{_RUN}"}],
        ),
    ])
    await db.files.delete_many({"storage_path": {"$in": [ayub_file, euis_file, euis_sj, euis_proof]}})
    await db.files.insert_many([
        {"id": f"file-ayub-{_RUN}", "storage_path": ayub_file, "kind": "po_pdf", "order_id": ayub_po_id, "is_deleted": False},
        {"id": f"file-euis-{_RUN}", "storage_path": euis_file, "kind": "po_pdf", "order_id": euis_po_id, "is_deleted": False},
        {"id": f"file-sj-{_RUN}", "storage_path": euis_sj, "kind": "surat_jalan", "order_id": euis_po_id, "is_deleted": False},
        {"id": f"file-prf-{_RUN}", "storage_path": euis_proof, "kind": "delivery_proof", "order_id": euis_po_id, "is_deleted": False},
    ])
    await db.sales_customers.delete_many({"id": {"$in": [f"cust-ayub-{_RUN}", f"cust-euis-{_RUN}"]}})
    await db.sales_customers.insert_many([
        {"id": f"cust-ayub-{_RUN}", "name": AYUB_CUSTOMER, "normalized_name": AYUB_CUSTOMER.lower()},
        {"id": f"cust-euis-{_RUN}", "name": EUIS_CUSTOMER, "normalized_name": EUIS_CUSTOMER.lower()},
    ])
    await db.sales_deliveries.delete_many({"id": {"$in": [f"dlv-ayub-{_RUN}", f"dlv-euis-{_RUN}"]}})
    await db.sales_deliveries.insert_many([
        {"id": f"dlv-ayub-{_RUN}", "po_id": ayub_po_id, "sj_number": f"SJ-AYU-{_RUN}", "is_cancelled": False},
        {"id": f"dlv-euis-{_RUN}", "po_id": euis_po_id, "sj_number": f"SJ-EUI-{_RUN}", "is_cancelled": False},
    ])
    await db.sales_invoices.delete_many({"id": {"$in": [f"inv-ayub-{_RUN}", f"inv-euis-{_RUN}"]}})
    await db.sales_invoices.insert_many([
        {"id": f"inv-ayub-{_RUN}", "po_id": ayub_po_id, "invoice_number": f"INV-AYU-{_RUN}", "is_cancelled": False, "customer": AYUB_CUSTOMER},
        {"id": f"inv-euis-{_RUN}", "po_id": euis_po_id, "invoice_number": f"INV-EUI-{_RUN}", "is_cancelled": False, "customer": EUIS_CUSTOMER},
    ])
    return {
        "ayub_po": ayub_po_id,
        "euis_po": euis_po_id,
        "ayub_file": ayub_file,
        "euis_file": euis_file,
        "euis_sj": euis_sj,
        "euis_proof": euis_proof,
        "ayub_cust": f"cust-ayub-{_RUN}",
        "euis_cust": f"cust-euis-{_RUN}",
        "ayub_dlv": f"dlv-ayub-{_RUN}",
        "euis_dlv": f"dlv-euis-{_RUN}",
        "ayub_inv": f"inv-ayub-{_RUN}",
        "euis_inv": f"inv-euis-{_RUN}",
        "euis_sj_file": f"sjf-{_RUN}",
        "euis_proof_id": f"prf-{_RUN}",
    }


async def _login(client, email):
    resp = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def _global_cases(ids):
    euis_po = ids["euis_po"]
    ayub_po = ids["ayub_po"]
    return [
        ("GET", "/api/orders", {}),
        ("GET", "/api/orders", {"sales": ACCURATE_EUIS, "sales_user_id": EUIS_ID, "sales_id": EUIS_ID}),
        ("GET", f"/api/orders/{euis_po}", {}),
        ("GET", f"/api/orders/{ayub_po}", {}),
        ("GET", "/api/sales-orders", {"sales_user_id": EUIS_ID, "po_id": euis_po}),
        ("GET", "/api/sales-orders/options", {}),
        ("GET", "/api/dashboard/status-po", {"customer": EUIS_CUSTOMER}),
        ("GET", "/api/reports/order-prediction", {"sales": ACCURATE_EUIS, "sales_id": EUIS_ID}),
        ("GET", f"/api/reports/order-prediction/{EUIS_CUSTOMER}", {}),
        ("GET", "/api/sales-customers", {"customer_id": ids["euis_cust"]}),
        ("GET", f"/api/sales-customers/{ids['euis_cust']}", {}),
        ("GET", "/api/sales-deliveries", {"po_id": euis_po, "sales_user_id": EUIS_ID}),
        ("GET", f"/api/sales-deliveries/{ids['euis_dlv']}", {}),
        ("GET", f"/api/sales-deliveries/{ids['euis_dlv']}/pdf", {}),
        ("GET", "/api/sales-invoices", {"po_id": euis_po}),
        ("GET", f"/api/sales-invoices/{ids['euis_inv']}", {}),
        ("GET", f"/api/sales-invoices/{ids['euis_inv']}/pdf", {}),
        ("GET", f"/api/files/{ids['euis_file']}", {}),
        ("GET", f"/api/files/{ids['ayub_file']}", {}),
        ("GET", f"/api/files/{ids['euis_sj']}", {}),
        ("GET", f"/api/files/{ids['euis_proof']}", {}),
        ("GET", f"/api/orders/{euis_po}/surat-jalan/files/{ids['euis_sj_file']}", {}),
        ("GET", f"/api/orders/{ayub_po}/surat-jalan/accurate-preview", {}),
        ("GET", "/api/dashboard/today", {}),
        ("GET", "/api/dashboard/summary", {}),
        ("GET", "/api/chat/work/orders", {}),
        ("GET", "/api/chat/rooms", {}),
        ("POST", f"/api/orders/{euis_po}/tracking-link", {}),
        ("GET", "/api/settings/sales-user-mapping", {}),
    ]


@pytest.mark.anyio
async def test_ayub_cannot_read_euis_via_global_endpoints():
    import server
    db = server.db
    ids = await _seed(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login(client, AYUB_EMAIL)
        me = await client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        perms = me.json()["permissions"]
        assert "sales.pwa.view" in perms
        assert "po.view" not in perms
        secrets = (EUIS_CUSTOMER, ACCURATE_EUIS, ids["euis_po"], f"PO-EUI-{_RUN}")
        for method, path, params in _global_cases(ids):
            if method == "GET":
                resp = await client.get(path, headers=headers, params=params)
            else:
                resp = await client.post(path, headers=headers, json=params or {})
            _assert_locked(resp, *secrets)


@pytest.mark.anyio
async def test_euis_cannot_read_ayub_via_global_endpoints():
    import server
    db = server.db
    ids = await _seed(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login(client, EUIS_EMAIL)
        me = await client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        perms = me.json()["permissions"]
        assert "sales.pwa.view" in perms
        # Override po.view=True must not open global PO surfaces.
        assert "po.view" in perms
        secrets = (AYUB_CUSTOMER, ACCOUNT_AYUB, ACCURATE_AYUB, ids["ayub_po"], f"PO-AYU-{_RUN}")
        for method, path, params in _global_cases(ids):
            if method == "GET":
                resp = await client.get(path, headers=headers, params=params)
            else:
                resp = await client.post(path, headers=headers, json=params or {})
            _assert_locked(resp, *secrets)
        # Own PWA still works; spoofed sales_user_id is ignored.
        own = await client.get("/api/sales-pwa/orders", headers=headers, params={"sales_user_id": AYUB_ID, "limit": 50})
        assert own.status_code == 200, own.text
        pos = [o["po_number"] for o in own.json()["orders"]]
        assert f"PO-EUI-{_RUN}" in pos
        assert f"PO-AYU-{_RUN}" not in pos
        for row in own.json()["orders"]:
            assert AYUB_CUSTOMER not in str(row.get("customer") or "")


@pytest.mark.anyio
async def test_raw_file_and_pwa_document_cannot_bypass_ownership():
    import server
    db = server.db
    ids = await _seed(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ayub = await _login(client, AYUB_EMAIL)
        euis = await _login(client, EUIS_EMAIL)
        raw = await client.get(f"/api/files/{ids['euis_file']}", headers=ayub)
        _assert_locked(raw, EUIS_CUSTOMER, ids["euis_file"])
        sj = await client.get(f"/api/orders/{ids['euis_po']}/surat-jalan/files/{ids['euis_sj_file']}", headers=ayub)
        _assert_locked(sj, EUIS_CUSTOMER)
        pwa_other = await client.get(f"/api/sales-pwa/orders/{ids['euis_po']}/files/po", headers=ayub)
        assert pwa_other.status_code == 404
        assert EUIS_CUSTOMER not in pwa_other.text
        pwa_sj = await client.get(f"/api/sales-pwa/orders/{ids['euis_po']}/files/sj/{ids['euis_dlv']}", headers=ayub)
        assert pwa_sj.status_code == 404
        assert EUIS_CUSTOMER not in pwa_sj.text
        pwa_inv = await client.get(f"/api/sales-pwa/orders/{ids['euis_po']}/files/invoices/{ids['euis_inv']}", headers=ayub)
        assert pwa_inv.status_code == 404
        pwa_prf = await client.get(f"/api/sales-pwa/orders/{ids['euis_po']}/files/proofs/{ids['euis_proof_id']}", headers=ayub)
        assert pwa_prf.status_code == 404
        # Reverse: Euis cannot open Ayub PWA files.
        rev = await client.get(f"/api/sales-pwa/orders/{ids['ayub_po']}/files/po", headers=euis)
        assert rev.status_code == 404
        assert AYUB_CUSTOMER not in rev.text


@pytest.mark.anyio
async def test_existing_emp_accounts_get_pwa_view_on_login():
    import server
    db = server.db
    await _seed(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for email in (AYUB_EMAIL, EUIS_EMAIL):
            headers = await _login(client, email)
            me = await client.get("/api/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["role"] == "sales_eternal"
            assert "sales.pwa.view" in me.json()["permissions"]
            user = await db.users.find_one({"email": email}, {"_id": 0, "permissions_override": 1, "role": 1})
            assert user["role"] == "sales_eternal"
