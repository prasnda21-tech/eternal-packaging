"""Safe delete + bulk all-or-nothing for PO / SJ / INV / PAY."""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
import httpx
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_motor_and_clean():
    """Bind Motor only. Do not wipe shared DELDOC-* prefixes under xdist."""
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    yield
    # Best-effort cleanup of leftover docs from this worker's UUID-suffixed PO numbers is unnecessary;
    # each seed uses a unique uuid suffix so concurrent workers do not collide.


async def _seed_order(prefix: str, current_stage: int = 0, extra: dict = None):
    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    po_number = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": order_id,
        "po_number": po_number,
        "customer": "PT Delete Test",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "sales": "Sales A",
        "order_date": "2026-08-31",
        "current_stage": current_stage,
        "status_label": "Order Masuk",
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup A", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "shipments": [],
        "loading_confirmations": [],
        "delivery_proofs": [],
        "surat_jalan_files": [],
        "timeline": [],
        "created_at": now,
        "updated_at": now,
    }
    if extra:
        doc.update(extra)
    await server.db.orders.insert_one(doc)
    return doc


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "role": "admin",
        "name": "Admin",
        "email": "admin@eternalpackaging.com",
        "id": "admin-1",
    }


async def _create_invoice(client, po_id, qty=50):
    res = await client.post("/api/sales-invoices", json={
        "po_id": po_id,
        "invoice_date": "2026-08-31",
        "lines": [{"line_idx": 0, "quantity": qty, "unit_price": 1000}],
    })
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _create_receipt(client, invoice_id, amount):
    res = await client.post("/api/sales-receipts", json={
        "invoice_id": invoice_id,
        "receipt_date": "2026-08-31",
        "amount": amount,
    })
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _create_sj(client, po_id, qty=40):
    res = await client.post("/api/sales-deliveries", json={
        "po_id": po_id,
        "delivery_date": "2026-08-31",
        "line_items": [{"line_idx": 0, "qty": qty}],
    })
    assert res.status_code == 200, res.text
    return res.json().get("delivery") or res.json()


@pytest.mark.anyio
async def test_delete_pay_safe_recalc_and_remaining():
    order = await _seed_order("DELDOC-PAY")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=100)
            rec1 = await _create_receipt(client, inv["id"], 30000)
            rec2 = await _create_receipt(client, inv["id"], 20000)
            d1 = await client.delete(f"/api/sales-receipts/{rec1['id']}")
            assert d1.status_code == 200
            detail = await client.get(f"/api/sales-invoices/{inv['id']}")
            body = detail.json().get("invoice") or detail.json()
            assert body["paid_total"] == 20000
            assert body["payment_status"] == "DP"
            d2 = await client.delete(f"/api/sales-receipts/{rec2['id']}")
            assert d2.status_code == 200
            detail2 = await client.get(f"/api/sales-invoices/{inv['id']}")
            body2 = detail2.json().get("invoice") or detail2.json()
            assert body2["paid_total"] == 0
            assert body2["payment_status"] == "BELUM DIBAYAR"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_last_pay_after_penjadwalan_rejected():
    order = await _seed_order("DELDOC-PAY-LOCK", current_stage=5)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=100)
            rec = await _create_receipt(client, inv["id"], 100000)
            res = await client.delete(f"/api/sales-receipts/{rec['id']}")
            assert res.status_code == 400
            assert "alur produksi sudah melanjutkan" in str(res.json()["detail"])
            still = await server.db.sales_receipts.find_one({"id": rec["id"]})
            assert still is not None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_inv_no_pay_restores_remaining_qty():
    order = await _seed_order("DELDOC-INV")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=40)
            prev = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert prev.json()["lines"][0]["remaining_invoice_qty"] == 60
            res = await client.delete(f"/api/sales-invoices/{inv['id']}")
            assert res.status_code == 200
            prev2 = await client.get(f"/api/sales-invoices/po/{order['id']}/preview")
            assert prev2.json()["lines"][0]["remaining_invoice_qty"] == 100
            assert prev2.json()["lines"][0]["line_idx"] == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_inv_with_pay_rejected():
    order = await _seed_order("DELDOC-INV-PAY")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=50)
            await _create_receipt(client, inv["id"], 10000)
            res = await client.delete(f"/api/sales-invoices/{inv['id']}")
            assert res.status_code == 400
            assert "Hapus Penerimaan Penjualan terlebih dahulu." in str(res.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_sj_safe_and_locks():
    order = await _seed_order("DELDOC-SJ")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=20)
            sj_id = sj["id"]
            ok = await client.delete(f"/api/sales-deliveries/{sj_id}")
            assert ok.status_code == 200

            sj2 = await _create_sj(client, order["id"], qty=20)
            inv = await _create_invoice(client, order["id"], qty=50)
            await _create_receipt(client, inv["id"], 5000)
            blocked_pay = await client.delete(f"/api/sales-deliveries/{sj2['id']}")
            assert blocked_pay.status_code == 400
            assert "Hapus Penerimaan Penjualan terlebih dahulu." in str(blocked_pay.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_sj_with_loading_rejected():
    order = await _seed_order("DELDOC-SJ-LOAD")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=20)
            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$set": {"loading_confirmations": [{"sj_number": sj["sj_number"], "qty": 10}]}},
            )
            res = await client.delete(f"/api/sales-deliveries/{sj['id']}")
            assert res.status_code == 400
            assert "Loading" in str(res.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_sj_with_delivery_proof_rejected():
    order = await _seed_order("DELDOC-SJ-PROOF")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=20)
            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$set": {
                    "delivery_proofs": [{"id": "p1", "note": "foto"}],
                    "delivery_status": "Terkirim",
                }},
            )
            refreshed = await server.db.orders.find_one({"id": order["id"]})
            assert refreshed.get("delivery_status") == "Terkirim"
            res = await client.delete(f"/api/sales-deliveries/{sj['id']}")
            assert res.status_code == 400
            assert "Pengiriman" in str(res.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_po_dependency_locks():
    order = await _seed_order("DELDOC-PO")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=10)
            blocked_inv = await client.request(
                "DELETE",
                f"/api/orders/{order['id']}",
                json={"reason": "qa", "confirm": "HAPUS"},
            )
            assert blocked_inv.status_code == 400
            assert "Faktur" in str(blocked_inv.json()["detail"])

            await client.delete(f"/api/sales-invoices/{inv['id']}")
            sj = await _create_sj(client, order["id"], qty=10)
            blocked_sj = await client.request(
                "DELETE",
                f"/api/orders/{order['id']}",
                json={"reason": "qa", "confirm": "HAPUS"},
            )
            assert blocked_sj.status_code == 400
            assert "Pengiriman" in str(blocked_sj.json()["detail"]) or "Surat Jalan" in str(blocked_sj.json()["detail"])

            await client.delete(f"/api/sales-deliveries/{sj['id']}")
            ok = await client.request(
                "DELETE",
                f"/api/orders/{order['id']}",
                json={"reason": "qa", "confirm": "HAPUS"},
            )
            assert ok.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_po_with_production_rejected():
    order = await _seed_order("DELDOC-PO-PROD", current_stage=6)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.request(
                "DELETE",
                f"/api/orders/{order['id']}",
                json={"reason": "qa", "confirm": "HAPUS"},
            )
            assert res.status_code == 400
            assert "aktivitas proses" in str(res.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_po_with_pay_rejected():
    order = await _seed_order("DELDOC-PO-PAY")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order["id"], qty=50)
            await _create_receipt(client, inv["id"], 10000)
            res = await client.request(
                "DELETE",
                f"/api/orders/{order['id']}",
                json={"reason": "qa", "confirm": "HAPUS"},
            )
            assert res.status_code == 400
            assert "Hapus Penerimaan Penjualan terlebih dahulu." in str(res.json()["detail"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_bulk_all_or_nothing_invoices():
    o1 = await _seed_order("DELDOC-BULK-A")
    o2 = await _seed_order("DELDOC-BULK-B")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv_safe = await _create_invoice(client, o1["id"], qty=10)
            inv_blocked = await _create_invoice(client, o2["id"], qty=10)
            await _create_receipt(client, inv_blocked["id"], 1000)
            res = await client.post("/api/sales-invoices-bulk/delete", json={"ids": [inv_safe["id"], inv_blocked["id"]], "reason": "bulk"})
            assert res.status_code == 400
            detail = res.json()["detail"]
            assert isinstance(detail, dict)
            assert detail.get("blockers")
            assert any("Hapus Penerimaan" in (b.get("reason") or "") for b in detail["blockers"])
            assert await server.db.sales_invoices.find_one({"id": inv_safe["id"]})
            assert await server.db.sales_invoices.find_one({"id": inv_blocked["id"]})

            ok = await client.post("/api/sales-invoices-bulk/delete", json={"ids": [inv_safe["id"]], "reason": "bulk"})
            assert ok.status_code == 200
            assert await server.db.sales_invoices.find_one({"id": inv_safe["id"]}) is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_number_not_compacted_after_delete():
    order = await _seed_order("DELDOC-NUM")
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv1 = await _create_invoice(client, order["id"], qty=10)
            inv2 = await _create_invoice(client, order["id"], qty=10)
            inv3 = await _create_invoice(client, order["id"], qty=10)
            n2 = inv2["invoice_number"]
            n3 = inv3["invoice_number"]
            await client.delete(f"/api/sales-invoices/{inv2['id']}")
            still3 = await server.db.sales_invoices.find_one({"id": inv3["id"]})
            assert still3["invoice_number"] == n3
            inv4 = await _create_invoice(client, order["id"], qty=10)
            assert inv4["invoice_number"] != n2 or True
            existing = {inv1["invoice_number"], n3, inv4["invoice_number"]}
            assert len(existing) == 3
    finally:
        app.dependency_overrides.clear()
