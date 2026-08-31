import pytest
import asyncio
import uuid
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from permissions import ALL_PERMISSIONS
from server import app

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


async def _ensure_business_number_indexes():
    from pymongo.errors import DuplicateKeyError, OperationFailure
    for coll, field in (
        (server.db.orders, "po_number"),
        (server.db.sales_deliveries, "sj_number"),
        (server.db.sales_invoices, "invoice_number"),
        (server.db.sales_receipts, "receipt_number"),
    ):
        try:
            await coll.create_index(field, unique=True)
        except (DuplicateKeyError, OperationFailure):
            pass


@pytest.fixture(autouse=True)
async def ensure_unique_indexes(bind_motor):
    await _ensure_business_number_indexes()

def admin_token():
    import jwt
    from datetime import datetime, timedelta, timezone
    payload = {
        "sub": "admin_test",
        "email": "admin@example.com",
        "name": "Admin Test",
        "role": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(days=1)
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")

@pytest.mark.anyio
async def test_po_concurrency():
    po_num = f"PO-CONCUR-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "po_number": po_num,
        "work_date": "2026-08-30",
        "customer": "Test",
        "sales": "Sales",
        "outlet": "Outlet",
        "total_quantity": 100,
        "line_items": [{"name": "Item A", "quantity": 100}]
    }
    
    headers = {"Authorization": f"Bearer {admin_token()}"}
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        req1 = client.post("/api/sales-orders", json=payload, headers=headers)
        req2 = client.post("/api/sales-orders", json=payload, headers=headers)
        
        res1, res2 = await asyncio.gather(req1, req2)
        
        status_codes = [res1.status_code, res2.status_code]
        assert 200 in status_codes
        assert 409 in status_codes
        
        po_docs = await server.db.orders.find({"po_number": po_num}).to_list(100)
        assert len(po_docs) == 1, "There should be exactly one PO document."

@pytest.mark.anyio
async def test_sj_concurrency():
    po_num = f"PO-CONCUR-SJ-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "po_number": po_num,
        "work_date": "2026-08-30",
        "customer": "Test",
        "sales": "Sales",
        "outlet": "Outlet",
        "total_quantity": 100,
        "line_items": [{"name": "Item A", "quantity": 100}]
    }
    headers = {"Authorization": f"Bearer {admin_token()}"}
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/sales-orders", json=payload, headers=headers)
        assert resp.status_code == 200
        po_id = resp.json()["order"]["id"]
        
        await server.db.orders.update_one({"id": po_id}, {"$set": {"current_stage": 11, "status_label": "Packing", "packing_status": "Selesai"}})
        
        sj_payload = {
            "po_id": po_id,
            "delivery_date": "2026-08-31",
            "line_items": [{"name": "Item A", "qty": 50, "line_idx": 0}]
        }
        
        req1 = client.post("/api/sales-deliveries", json=sj_payload, headers=headers)
        req2 = client.post("/api/sales-deliveries", json=sj_payload, headers=headers)
        
        res1, res2 = await asyncio.gather(req1, req2)
        
        sj_docs = await server.db.sales_deliveries.find({"po_id": po_id}).to_list(100)
        # We expect 2 documents if both succeed under the qty limit, but with strictly distinct SJ numbers.
        assert len(sj_docs) in [1, 2], "SJ docs created should be 1 or 2."
        sj_numbers = [doc["sj_number"] for doc in sj_docs]
        assert len(set(sj_numbers)) == len(sj_numbers), "SJ numbers must be strictly unique!"

@pytest.mark.anyio
async def test_inv_concurrency():
    po_num = f"PO-CONCUR-INV-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "po_number": po_num,
        "work_date": "2026-08-30",
        "customer": "Test",
        "sales": "Sales",
        "outlet": "Outlet",
        "total_quantity": 100,
        "line_items": [{"name": "Item A", "quantity": 100}]
    }
    headers = {"Authorization": f"Bearer {admin_token()}"}
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/sales-orders", json=payload, headers=headers)
        assert resp.status_code == 200
        po_id = resp.json()["order"]["id"]
        
        inv_payload = {
            "po_id": po_id,
            "lines": [{"name": "Item A", "quantity": 50, "line_idx": 0, "unit_price": 10}]
        }
        
        req1 = client.post("/api/sales-invoices", json=inv_payload, headers=headers)
        req2 = client.post("/api/sales-invoices", json=inv_payload, headers=headers)
        
        res1, res2 = await asyncio.gather(req1, req2)
        
        inv_docs = await server.db.sales_invoices.find({"po_id": po_id}).to_list(100)
        assert len(inv_docs) in [1, 2], "Invoices created should be 1 or 2"
        inv_numbers = [doc["invoice_number"] for doc in inv_docs]
        assert len(set(inv_numbers)) == len(inv_numbers), "Invoice numbers must be strictly unique!"

@pytest.mark.anyio
async def test_pay_concurrency():
    po_num = f"PO-CONCUR-PAY-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "po_number": po_num,
        "work_date": "2026-08-30",
        "customer": "Test",
        "sales": "Sales",
        "outlet": "Outlet",
        "total_quantity": 100,
        "line_items": [{"name": "Item A", "quantity": 100}]
    }
    headers = {"Authorization": f"Bearer {admin_token()}"}
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/sales-orders", json=payload, headers=headers)
        assert resp.status_code == 200
        po_id = resp.json()["order"]["id"]
        
        inv_payload = {
            "po_id": po_id,
            "lines": [{"name": "Item A", "quantity": 100, "line_idx": 0, "unit_price": 10}]
        }
        resp = await client.post("/api/sales-invoices", json=inv_payload, headers=headers)
        assert resp.status_code == 200
        inv_id = resp.json()["invoice"]["id"]
        
        pay_payload = {
            "invoice_id": inv_id,
            "payment_date": "2026-08-30",
            "amount": 500,
            "payment_method": "Transfer",
            "notes": ""
        }
        
        req1 = client.post("/api/sales-receipts", json=pay_payload, headers=headers)
        req2 = client.post("/api/sales-receipts", json=pay_payload, headers=headers)
        
        res1, res2 = await asyncio.gather(req1, req2)
        
        pay_docs = await server.db.sales_receipts.find({"invoice_id": inv_id}).to_list(100)
        assert len(pay_docs) in [1, 2], "Receipts created should be 1 or 2"
        pay_numbers = [doc["receipt_number"] for doc in pay_docs]
        assert len(set(pay_numbers)) == len(pay_numbers), "Receipt numbers must be strictly unique!"
