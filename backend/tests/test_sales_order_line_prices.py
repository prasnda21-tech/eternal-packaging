"""Selling price snapshot on sales orders and the invoice that follows."""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
# Dedicated DB: this module asserts global journal_entries / stock_movements
# counts. Sharing eternal_packaging_test with xdist siblings (e.g. invoices)
# races those counts (CI: expected 17, actual 18).
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
_TEST_DB = f"eternal_packaging_sales_order_line_prices_test_{_worker}"
os.environ["DB_NAME"] = _TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    # Pin against sibling suites that mutate os.environ["DB_NAME"].
    os.environ["DB_NAME"] = _TEST_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[_TEST_DB]


def _user(role):
    return {"email": f"{role}@eternalpackaging.com", "name": role, "role": role}


async def _seed_master(db, *, accurate_item_id: str, name: str, unit: str = "pcs") -> dict:
    """Preview-only Master Produk row so P1 create PO resolver can resolve the line."""
    await db.branches.update_one({"code": "YGY"}, {"$setOnInsert": {"id": "test-ygy", "name": "Yogyakarta", "active": True}}, upsert=True)
    mid = f"mst-{uuid.uuid4().hex[:10]}"
    doc = {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": accurate_item_id,
        "item_code": f"SKU-{uuid.uuid4().hex[:6].upper()}",
        "item_name": name,
        "name": name,
        "unit": unit,
        "item_type": "INVENTORY",
        "is_active": True,
    }
    await db.master_items.insert_one(dict(doc))
    return doc


async def _seed_price(db, aid, unit_price, unit="pcs", hpp=111):
    from services.customer_pricing import GROUPS, VERSIONS
    version_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    await db[GROUPS].insert_one({
        "id": group_id,
        "enabled": True,
        "group_name": f"Harga {aid}",
        "active_accurate_item_ids": [aid],
        "current_published_version_id": version_id,
    })
    await db[VERSIONS].insert_one({
        "id": version_id,
        "status": "PUBLISHED",
        "tiers": [{"min_qty": 1, "max_qty": None}],
        "products": [{
            "accurate_item_id": aid,
            "unit": unit,
            "tier_prices": [unit_price],
            "hpp_snapshot": hpp,
            "manual_hpp": hpp,
        }],
    })
    return version_id


@pytest.mark.anyio
async def test_quote_returns_selling_price_only_and_blocks_holding_team():
    import server
    aid = f"aid-quote-{uuid.uuid4().hex[:8]}"
    await _seed_price(server.db, aid, 700, hpp=321)
    app.dependency_overrides[current_user] = lambda: _user("holding_team")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/api/sales-orders/price-quote", json={
                "accurate_item_id": aid, "unit": "pcs", "quantity": 1000,
            })
            assert denied.status_code == 403
        app.dependency_overrides[current_user] = lambda: _user("admin")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            matched = await client.post("/api/sales-orders/price-quote", json={
                "accurate_item_id": aid, "unit": "pcs", "quantity": 1000,
            })
            assert matched.status_code == 200
            body = matched.json()
            assert body["status"] == "matched"
            assert body["unit_price"] == 700
            assert body["source"] == "pengaturan_harga"
            assert "hpp" not in matched.text
            mismatch = await client.post("/api/sales-orders/price-quote", json={
                "accurate_item_id": aid, "unit": "dus", "quantity": 1000,
            })
            assert mismatch.json()["status"] == "unit_mismatch"
            assert mismatch.json()["unit_price"] is None
            missing = await client.post("/api/sales-orders/price-quote", json={
                "accurate_item_id": "tidak-ada", "unit": "pcs", "quantity": 1,
            })
            assert missing.json()["status"] == "missing"
            assert missing.json()["unit_price"] is None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_order_snapshots_selling_price_and_partial_invoice_keeps_it():
    import server
    cup = f"aid-cup-{uuid.uuid4().hex[:8]}"
    lid = f"aid-lid-{uuid.uuid4().hex[:8]}"
    await _seed_master(server.db, accurate_item_id=cup, name="Cup PET")
    await _seed_master(server.db, accurate_item_id=lid, name="Lid")
    cup_version = await _seed_price(server.db, cup, 700)
    lid_version = await _seed_price(server.db, lid, 150)
    po_number = f"TEST-PRICE-{uuid.uuid4().hex[:6].upper()}"
    journals_before = await server.db.journal_entries.count_documents({})
    movements_before = await server.db.stock_movements.count_documents({})
    app.dependency_overrides[current_user] = lambda: _user("admin")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/sales-orders", json={
                "po_number": po_number,
                "customer": "GOHO COFFEE",
                "outlet": "Yogyakarta",
                "line_items": [
                    {
                        "name": "Cup PET",
                        "quantity": 1000,
                        "unit": "pcs",
                        "accurate_item_id": cup,
                        "unit_price": 700,
                        "price_source": "pengaturan_harga",
                    },
                    {
                        "name": "Lid",
                        "quantity": 1000,
                        "unit": "pcs",
                        "accurate_item_id": lid,
                        "unit_price": 150,
                        "price_source": "pengaturan_harga",
                    },
                ],
            })
            assert created.status_code == 200, created.text
            order = created.json()["order"]
            assert order["selling_total"] == 850000
            assert order["line_items"][0]["line_subtotal"] == 700000
            assert order["line_items"][1]["line_subtotal"] == 150000
            assert order["line_items"][0]["price_source"] == "pengaturan_harga"
            assert await server.db.journal_entries.count_documents({}) == journals_before
            assert await server.db.stock_movements.count_documents({}) == movements_before

            await server.db.customer_pricing_versions.update_one(
                {"id": cup_version}, {"$set": {"products.0.tier_prices": [999]}}
            )
            await server.db.customer_pricing_versions.update_one(
                {"id": lid_version}, {"$set": {"products.0.tier_prices": [50]}}
            )
            stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert stored["line_items"][0]["unit_price"] == 700
            assert stored["selling_total"] == 850000

            invoice = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{
                    "line_idx": 0,
                    "line_id": order["line_items"][0]["line_id"],
                    "quantity": 400,
                    "unit_price": 999,
                }],
            })
            assert invoice.status_code == 200, invoice.text
            inv = invoice.json()["invoice"]
            assert inv["lines"][0]["unit_price"] == 700
            assert inv["lines"][0]["line_subtotal"] == 280000
            assert inv["subtotal"] == 280000

            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$set": {"line_items.0.unit_price": 1, "selling_total": 1}},
            )
            untouched = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert untouched["lines"][0]["unit_price"] == 700
            assert untouched["subtotal"] == 280000
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": po_number})
        await server.db.sales_invoices.delete_many({"po_number": po_number})


@pytest.mark.anyio
async def test_same_item_lines_invoice_their_own_price_and_blank_is_not_zero():
    import server
    aid = f"aid-same-{uuid.uuid4().hex[:8]}"
    await _seed_master(server.db, accurate_item_id=aid, name="Cup PET")
    blank_master = await _seed_master(
        server.db, accurate_item_id=f"aid-blank-{uuid.uuid4().hex[:8]}", name="Tanpa harga"
    )
    await _seed_price(server.db, aid, 700)
    po_number = f"TEST-SAME-{uuid.uuid4().hex[:6].upper()}"
    app.dependency_overrides[current_user] = lambda: _user("admin")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/sales-orders", json={
                "po_number": po_number,
                "customer": "GOHO COFFEE",
                "line_items": [
                    {"name": "Cup PET", "quantity": 10, "unit": "pcs", "accurate_item_id": aid, "unit_price": 700, "price_source": "manual"},
                    {"name": "Cup PET", "quantity": 10, "unit": "pcs", "accurate_item_id": aid, "unit_price": 900, "price_source": "manual"},
                    {
                        "name": "Tanpa harga",
                        "quantity": 2,
                        "unit": "pcs",
                        "unit_price": "",
                        "master_item_id": blank_master["id"],
                        "accurate_item_id": blank_master["accurate_item_id"],
                    },
                ],
            })
            assert created.status_code == 200, created.text
            order = created.json()["order"]
            assert order["line_items"][0]["unit_price"] == 700
            assert order["line_items"][1]["unit_price"] == 900
            assert order["line_items"][2]["unit_price"] is None
            assert order["line_items"][2]["line_subtotal"] is None
            assert order["selling_total"] == 16000

            second = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{
                    "line_idx": 1,
                    "line_id": order["line_items"][1]["line_id"],
                    "quantity": 4,
                    "unit_price": 700,
                }],
            })
            assert second.status_code == 200, second.text
            inv = second.json()["invoice"]
            assert inv["lines"][0]["line_idx"] == 1
            assert inv["lines"][0]["unit_price"] == 900
            assert inv["lines"][0]["line_subtotal"] == 3600
            wrong_id = await client.post("/api/sales-invoices", json={
                "po_id": order["id"],
                "lines": [{
                    "line_idx": 0,
                    "line_id": order["line_items"][1]["line_id"],
                    "quantity": 1,
                    "unit_price": 1,
                }],
            })
            assert wrong_id.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": po_number})
        await server.db.sales_invoices.delete_many({"po_id": {"$exists": True}, "po_number": po_number})


@pytest.mark.anyio
async def test_old_order_without_price_does_not_invoice_zero():
    import server
    from datetime import datetime, timezone
    order_id = str(uuid.uuid4())
    po_number = f"TEST-OLD-{uuid.uuid4().hex[:6].upper()}"
    await server.db.orders.insert_one({
        "id": order_id,
        "po_number": po_number,
        "customer": "Lama",
        "cancelled": False,
        "line_items": [{"name": "Cup lama", "quantity": 5, "unit": "pcs"}],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    app.dependency_overrides[current_user] = lambda: _user("admin")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            preview = await client.get(f"/api/sales-invoices/po/{order_id}/preview")
            assert preview.status_code == 200
            line = preview.json()["lines"][0]
            assert line["unit_price"] is None
            assert line["price_from_order"] is False
            missing = await client.post("/api/sales-invoices", json={
                "po_id": order_id,
                "lines": [{"line_idx": 0, "quantity": 2}],
            })
            assert missing.status_code == 400
            blank = await client.post("/api/sales-invoices", json={
                "po_id": order_id,
                "lines": [{"line_idx": 0, "quantity": 2, "unit_price": ""}],
            })
            assert blank.status_code == 400
            explicit = await client.post("/api/sales-invoices", json={
                "po_id": order_id,
                "lines": [{"line_idx": 0, "quantity": 2, "unit_price": 500}],
            })
            assert explicit.status_code == 200, explicit.text
            assert explicit.json()["invoice"]["lines"][0]["unit_price"] == 500
            assert explicit.json()["invoice"]["lines"][0]["line_subtotal"] == 1000
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"id": order_id})
        await server.db.sales_invoices.delete_many({"po_id": order_id})
