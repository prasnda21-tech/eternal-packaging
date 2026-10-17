"""Server-side pagination for GET /api/sales-deliveries."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
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

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {
        "id": "admin-1",
        "email": "admin@eternal.test",
        "role": "admin",
        "name": "Admin",
        "permissions": ["sales.delivery.view", "sales_order.view", "po.view"],
    }


async def _seed_deliveries(db, *, prefix: str, n: int, po_ordered_qty: int = 1000):
    """n deliveries of 1 qty each on one PO → SEBAGIAN until PO fully shipped."""
    po_id = str(uuid.uuid4())
    await db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO-{prefix}",
        "customer": f"Cust {prefix}",
        "cancelled": False,
        "line_items": [{"quantity": po_ordered_qty, "name": "Item"}],
    })
    docs = []
    for i in range(n):
        docs.append({
            "id": f"{prefix}-{i:04d}",
            "sj_number": f"SJ-{prefix}-{i:04d}",
            "po_id": po_id,
            "po_number": f"PO-{prefix}",
            "customer": f"Cust {prefix}",
            "outlet": "Outlet",
            "sales": "Sales A" if i % 2 == 0 else "Sales B",
            "delivery_date": date(2026, 10, 17).isoformat(),
            "total_qty": 1,
            "created_by": "admin@eternal.test",
            "created_by_name": "Admin",
            "is_cancelled": False,
            "created_at": f"2026-10-17T{i % 24:02d}:{(i * 3) % 60:02d}:00+00:00",
            "line_items": [{"line_idx": 0, "qty": 1}],
        })
    await db.sales_deliveries.insert_many(docs)
    return po_id, [d["id"] for d in docs]


@pytest.mark.anyio
async def test_sales_delivery_list_pagination_pages_and_counts():
    import server

    prefix = uuid.uuid4().hex[:8].upper()
    app.dependency_overrides[current_user] = _admin
    po_id = None
    ids = []
    try:
        po_id, ids = await _seed_deliveries(server.db, prefix=prefix, n=120, po_ordered_qty=1000)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 1,
                "page_size": 50,
            })
            assert r1.status_code == 200, r1.text
            body1 = r1.json()
            assert body1["page"] == 1
            assert body1["page_size"] == 50
            assert body1["total"] == 120
            assert body1["total_pages"] == 3
            assert body1["count"] == 50
            assert len(body1["deliveries"]) == 50
            assert body1["counts"]["all"] == 120
            assert body1["counts"]["SEBAGIAN"] == 120
            assert body1["counts"]["SELESAI"] == 0

            r2 = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 2,
                "page_size": 50,
            })
            assert r2.status_code == 200
            body2 = r2.json()
            assert body2["page"] == 2
            assert body2["count"] == 50
            ids1 = {d["id"] for d in body1["deliveries"]}
            ids2 = {d["id"] for d in body2["deliveries"]}
            assert ids1.isdisjoint(ids2)

            r3 = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 3,
                "page_size": 50,
            })
            assert r3.status_code == 200
            body3 = r3.json()
            assert body3["page"] == 3
            assert body3["count"] == 20
            assert len(body3["deliveries"]) == 20

            # Stable sort: page1 first id is newest by created_at then id
            assert body1["deliveries"][0]["id"] == f"{prefix}-0119"

            r_size = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 1,
                "page_size": 25,
            })
            assert r_size.status_code == 200
            assert r_size.json()["page_size"] == 25
            assert r_size.json()["count"] == 25
            assert r_size.json()["total_pages"] == 5

            r_bad = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 0,
                "page_size": 50,
            })
            assert r_bad.status_code == 400

            r_over = await client.get("/api/sales-deliveries", params={
                "q": prefix,
                "page": 99,
                "page_size": 50,
            })
            assert r_over.status_code == 200
            assert r_over.json()["page"] == 3
            assert len(r_over.json()["deliveries"]) == 20
    finally:
        app.dependency_overrides.pop(current_user, None)
        if ids:
            await server.db.sales_deliveries.delete_many({"id": {"$in": ids}})
        if po_id:
            await server.db.orders.delete_many({"id": po_id})


@pytest.mark.anyio
async def test_sales_delivery_list_status_filter_counts_are_filter_context():
    import server

    prefix = uuid.uuid4().hex[:8].upper()
    app.dependency_overrides[current_user] = _admin
    po_partial = str(uuid.uuid4())
    po_done = str(uuid.uuid4())
    ids = []
    try:
        await server.db.orders.insert_many([
            {
                "id": po_partial,
                "po_number": f"PO-{prefix}-P",
                "customer": f"Cust {prefix}",
                "cancelled": False,
                "line_items": [{"quantity": 10, "name": "A"}],
            },
            {
                "id": po_done,
                "po_number": f"PO-{prefix}-D",
                "customer": f"Cust {prefix}",
                "cancelled": False,
                "line_items": [{"quantity": 5, "name": "B"}],
            },
        ])
        docs = [
            {
                "id": f"{prefix}-p1",
                "sj_number": f"SJ-{prefix}-P1",
                "po_id": po_partial,
                "po_number": f"PO-{prefix}-P",
                "customer": f"Cust {prefix}",
                "sales": "Vira",
                "delivery_date": "2026-10-17",
                "total_qty": 3,
                "created_by": "admin@eternal.test",
                "is_cancelled": False,
                "created_at": "2026-10-17T10:00:00+00:00",
                "line_items": [{"line_idx": 0, "qty": 3}],
            },
            {
                "id": f"{prefix}-d1",
                "sj_number": f"SJ-{prefix}-D1",
                "po_id": po_done,
                "po_number": f"PO-{prefix}-D",
                "customer": f"Cust {prefix}",
                "sales": "Vira",
                "delivery_date": "2026-10-17",
                "total_qty": 5,
                "created_by": "admin@eternal.test",
                "is_cancelled": False,
                "created_at": "2026-10-17T11:00:00+00:00",
                "line_items": [{"line_idx": 0, "qty": 5}],
            },
        ]
        ids = [d["id"] for d in docs]
        await server.db.sales_deliveries.insert_many(docs)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("services.sales_date_presets.wib_today", return_value=date(2026, 10, 17)):
                r = await client.get("/api/sales-deliveries", params={
                    "q": prefix,
                    "status": "SEBAGIAN",
                    "page": 1,
                    "page_size": 50,
                })
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 1
            assert len(body["deliveries"]) == 1
            assert body["deliveries"][0]["status"] == "SEBAGIAN"
            # Counts ignore status filter (same q/date/created_by context).
            assert body["counts"] == {"all": 2, "SEBAGIAN": 1, "SELESAI": 1}
    finally:
        app.dependency_overrides.pop(current_user, None)
        if ids:
            await server.db.sales_deliveries.delete_many({"id": {"$in": ids}})
        await server.db.orders.delete_many({"id": {"$in": [po_partial, po_done]}})


@pytest.mark.anyio
async def test_sales_delivery_list_search_spans_pages():
    import server

    prefix = uuid.uuid4().hex[:8].upper()
    app.dependency_overrides[current_user] = _admin
    po_id = None
    ids = []
    try:
        po_id, ids = await _seed_deliveries(server.db, prefix=prefix, n=60, po_ordered_qty=1000)
        # Rename one older row's customer so search finds it outside page 1 of default sort.
        target = f"{prefix}-0000"
        await server.db.sales_deliveries.update_one(
            {"id": target},
            {"$set": {"customer": f"UNIQUE-{prefix}-HIT"}},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/sales-deliveries", params={
                "q": f"UNIQUE-{prefix}-HIT",
                "page": 1,
                "page_size": 10,
            })
            assert r.status_code == 200
            body = r.json()
            assert body["total"] == 1
            assert body["deliveries"][0]["id"] == target
    finally:
        app.dependency_overrides.pop(current_user, None)
        if ids:
            await server.db.sales_deliveries.delete_many({"id": {"$in": ids}})
        if po_id:
            await server.db.orders.delete_many({"id": po_id})
