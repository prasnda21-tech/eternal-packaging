"""Server-side header filters for sales orders and deliveries."""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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
from services.sales_date_presets import match_shipping_date, shipping_date_preset_bounds


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
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }


def _order(**overrides):
    oid = str(uuid.uuid4())
    marker = overrides.pop("marker", uuid.uuid4().hex[:8].upper())
    doc = {
        "id": oid,
        "po_number": f"PO-HF-{marker}",
        "customer": f"Cust {marker}",
        "outlet": "Outlet",
        "sales": "Vira",
        "order_date": "2026-09-01",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_quantity": 100,
        "current_stage": 0,
        "status_label": "Order Masuk",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-09-01T00:00:00+00:00",
        "payment_approved_at": "2026-09-01T00:00:00+00:00",
        "line_items": [{"name": "Box", "quantity": 100}],
    }
    doc.update(overrides)
    return doc


@pytest.mark.anyio
async def test_sales_order_shipping_presets_sales_delivery_and_pagination():
    import server

    today = date(2026, 10, 6)
    prefix = uuid.uuid4().hex[:8].upper()
    docs = [
        _order(
            marker=f"T{prefix}",
            sales="Andi",
            scheduled_delivery_date=today.isoformat(),
            delivery_deadline_manual=f"{today.isoformat()}T00:00:00+07:00",
            created_at="2026-10-06T01:00:00+00:00",
        ),
        _order(
            marker=f"M{prefix}",
            sales="Budi",
            scheduled_delivery_date=(today + timedelta(days=1)).isoformat(),
            delivery_deadline_manual=f"{(today + timedelta(days=1)).isoformat()}T00:00:00+07:00",
            created_at="2026-10-06T02:00:00+00:00",
        ),
        _order(
            marker=f"N3{prefix}",
            sales="Andi",
            scheduled_delivery_date=(today + timedelta(days=2)).isoformat(),
            delivery_deadline_manual=f"{(today + timedelta(days=2)).isoformat()}T00:00:00+07:00",
            created_at="2026-10-06T03:00:00+00:00",
        ),
        _order(
            marker=f"OV{prefix}",
            sales="Andi",
            scheduled_delivery_date=(today - timedelta(days=1)).isoformat(),
            delivery_deadline_manual=f"{(today - timedelta(days=1)).isoformat()}T00:00:00+07:00",
            current_stage=2,
            created_at="2026-10-06T04:00:00+00:00",
        ),
        _order(
            marker=f"OC{prefix}",
            sales="Andi",
            scheduled_delivery_date=(today - timedelta(days=2)).isoformat(),
            delivery_deadline_manual=f"{(today - timedelta(days=2)).isoformat()}T00:00:00+07:00",
            cancelled=True,
            created_at="2026-10-06T05:00:00+00:00",
        ),
        _order(
            marker=f"OS{prefix}",
            sales="Andi",
            scheduled_delivery_date=(today - timedelta(days=3)).isoformat(),
            delivery_deadline_manual=f"{(today - timedelta(days=3)).isoformat()}T00:00:00+07:00",
            current_stage=14,
            status_label="Pengiriman",
            created_at="2026-10-06T06:00:00+00:00",
        ),
        _order(
            marker=f"UN{prefix}",
            sales="Andi",
            design_status="Waiting",
            payment_status="Waiting",
            design_approved_at=None,
            payment_approved_at=None,
            scheduled_delivery_date=None,
            delivery_deadline_manual=None,
            created_at="2026-10-06T07:00:00+00:00",
        ),
    ]
    ids = [d["id"] for d in docs]
    app.dependency_overrides[current_user] = _admin
    try:
        await server.db.orders.insert_many(docs)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("services.sales_date_presets.wib_today", return_value=today):
                async def fetch(params):
                    r = await client.get("/api/sales-orders", params=params)
                    assert r.status_code == 200, r.text
                    return r.json()

                today_rows = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "today",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert {o["id"] for o in today_rows["orders"]} == {docs[0]["id"]}

                tom_rows = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "tomorrow",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert {o["id"] for o in tom_rows["orders"]} == {docs[1]["id"]}

                n3 = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "next_3",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert {o["id"] for o in n3["orders"]} == {docs[0]["id"], docs[1]["id"], docs[2]["id"]}

                n7 = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "next_7",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert docs[0]["id"] in {o["id"] for o in n7["orders"]}

                week = shipping_date_preset_bounds("this_week", today=today)
                assert week["kind"] == "range"
                this_week = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "this_week",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert docs[0]["id"] in {o["id"] for o in this_week["orders"]}

                next_week = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "next_week",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert docs[0]["id"] not in {o["id"] for o in next_week["orders"]}

                overdue = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "overdue",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                overdue_ids = {o["id"] for o in overdue["orders"]}
                assert docs[3]["id"] in overdue_ids
                assert docs[4]["id"] not in overdue_ids  # cancelled
                assert docs[5]["id"] not in overdue_ids  # completed

                unset = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "unset",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert docs[6]["id"] in {o["id"] for o in unset["orders"]}

                sales = await fetch({
                    "status": "all",
                    "sales": "Budi",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert {o["id"] for o in sales["orders"]} == {docs[1]["id"]}

                # delivery status before pagination: seed one shipped PO
                po_ship = docs[0]
                await server.db.sales_deliveries.insert_one({
                    "id": str(uuid.uuid4()),
                    "po_id": po_ship["id"],
                    "sj_number": f"SJ-{prefix}",
                    "total_qty": 100,
                    "is_cancelled": False,
                    "created_at": "2026-10-06T08:00:00+00:00",
                    "line_items": [{"line_idx": 0, "qty": 100}],
                })
                done = await fetch({
                    "status": "all",
                    "delivery_status": "SELESAI",
                    "page": 1,
                    "page_size": 50,
                    "q": prefix,
                })
                assert po_ship["id"] in {o["id"] for o in done["orders"]}

                combined = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "today",
                    "sales": "Andi",
                    "delivery_status": "SELESAI",
                    "q": prefix,
                    "page": 1,
                    "page_size": 1,
                })
                assert combined["total"] == 1
                assert combined["orders"][0]["id"] == docs[0]["id"]
                assert combined["page_size"] == 1
                page2 = await fetch({
                    "status": "all",
                    "tanggal_pengiriman_preset": "today",
                    "sales": "Andi",
                    "delivery_status": "SELESAI",
                    "q": prefix,
                    "page": 2,
                    "page_size": 1,
                })
                assert page2["orders"] == []
                assert page2["total"] == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"id": {"$in": ids}})
        await server.db.sales_deliveries.delete_many({"po_id": {"$in": ids}})


@pytest.mark.anyio
async def test_sales_delivery_date_created_by_and_options():
    import server

    today = date(2026, 10, 6)
    prefix = uuid.uuid4().hex[:8].upper()
    po_id = str(uuid.uuid4())
    await server.db.orders.insert_one({
        "id": po_id,
        "po_number": f"PO-SJ-{prefix}",
        "customer": "Cust SJ",
        "line_items": [{"name": "Box", "quantity": 10}],
        "total_quantity": 10,
        "cancelled": False,
        "current_stage": 0,
    })
    d1 = {
        "id": str(uuid.uuid4()),
        "sj_number": f"SJ-{prefix}-1",
        "po_id": po_id,
        "po_number": f"PO-SJ-{prefix}",
        "customer": "Cust SJ",
        "delivery_date": today.isoformat(),
        "total_qty": 4,
        "created_by": "alice@eternal.test",
        "created_by_name": "Alice",
        "is_cancelled": False,
        "created_at": "2026-10-06T01:00:00+00:00",
        "line_items": [{"line_idx": 0, "qty": 4}],
    }
    d2 = {
        "id": str(uuid.uuid4()),
        "sj_number": f"SJ-{prefix}-2",
        "po_id": po_id,
        "po_number": f"PO-SJ-{prefix}",
        "customer": "Cust SJ",
        "delivery_date": (today - timedelta(days=1)).isoformat(),
        "total_qty": 3,
        "created_by": "bob@eternal.test",
        "created_by_name": "Bob",
        "is_cancelled": False,
        "created_at": "2026-10-06T02:00:00+00:00",
        "line_items": [{"line_idx": 0, "qty": 3}],
    }
    d3 = {
        "id": str(uuid.uuid4()),
        "sj_number": f"SJ-{prefix}-3",
        "po_id": po_id,
        "po_number": f"PO-SJ-{prefix}",
        "customer": "Cust SJ",
        "delivery_date": today.isoformat(),
        "total_qty": 1,
        "created_by": "alice@eternal.test",
        "created_by_name": "Alice Alias",
        "is_cancelled": False,
        "created_at": "2026-10-06T03:00:00+00:00",
        "line_items": [{"line_idx": 0, "qty": 1}],
    }
    app.dependency_overrides[current_user] = _admin
    try:
        await server.db.sales_deliveries.insert_many([d1, d2, d3])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("services.sales_date_presets.wib_today", return_value=today):
                r = await client.get("/api/sales-deliveries", params={
                    "q": prefix,
                    "delivery_date_preset": "today",
                })
                assert r.status_code == 200
                ids = {d["id"] for d in r.json()["deliveries"]}
                assert ids == {d1["id"], d3["id"]}

                r2 = await client.get("/api/sales-deliveries", params={
                    "q": prefix,
                    "created_by": "alice@eternal.test",
                })
                assert r2.status_code == 200
                by_alice = r2.json()["deliveries"]
                assert {d["id"] for d in by_alice} == {d1["id"], d3["id"]}
                assert all(d["created_by"] == "alice@eternal.test" for d in by_alice)

                # name must not be the authority
                r3 = await client.get("/api/sales-deliveries", params={
                    "q": prefix,
                    "created_by": "Alice",
                })
                assert r3.status_code == 200
                assert r3.json()["deliveries"] == []

                r4 = await client.get("/api/sales-deliveries", params={
                    "q": prefix,
                    "status": "SEBAGIAN",
                    "delivery_date_preset": "today",
                    "created_by": "alice@eternal.test",
                })
                assert r4.status_code == 200
                assert {d["id"] for d in r4.json()["deliveries"]} == {d1["id"], d3["id"]}

            opt = await client.get("/api/sales-deliveries/filter-options")
            assert opt.status_code == 200
            values = [o["value"] for o in opt.json()["created_by_options"]]
            assert values.count("alice@eternal.test") == 1
            assert "bob@eternal.test" in values
            alice = next(o for o in opt.json()["created_by_options"] if o["value"] == "alice@eternal.test")
            assert alice["label"] == "Alice"
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.sales_deliveries.delete_many({"id": {"$in": [d1["id"], d2["id"], d3["id"]]}})
        await server.db.orders.delete_many({"id": po_id})


@pytest.mark.anyio
async def test_sales_order_header_filter_finds_match_outside_first_page():
    """Header filter must run before pagination — not only on page-1 rows."""
    import server

    today = date(2026, 10, 6)
    prefix = uuid.uuid4().hex[:8].upper()
    filler = [
        _order(
            marker=f"F{i}{prefix}",
            sales="Filler",
            scheduled_delivery_date=(today + timedelta(days=1)).isoformat(),
            delivery_deadline_manual=f"{(today + timedelta(days=1)).isoformat()}T00:00:00+07:00",
            created_at=f"2026-10-06T1{i}:00:00+00:00",
        )
        for i in range(3)
    ]
    target = _order(
        marker=f"TARGET{prefix}",
        sales="ZTargetSales",
        scheduled_delivery_date=today.isoformat(),
        delivery_deadline_manual=f"{today.isoformat()}T00:00:00+07:00",
        created_at="2026-10-06T09:00:00+00:00",  # older → later page when sorted newest-first
    )
    docs = [*filler, target]
    ids = [d["id"] for d in docs]
    app.dependency_overrides[current_user] = _admin
    try:
        await server.db.orders.insert_many(docs)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("services.sales_date_presets.wib_today", return_value=today):
                unfiltered = await client.get("/api/sales-orders", params={
                    "status": "all",
                    "q": prefix,
                    "page": 1,
                    "page_size": 3,
                })
                assert unfiltered.status_code == 200
                page1_ids = {o["id"] for o in unfiltered.json()["orders"]}
                assert target["id"] not in page1_ids
                assert unfiltered.json()["total"] == 4

                filtered = await client.get("/api/sales-orders", params={
                    "status": "all",
                    "q": prefix,
                    "sales": "ZTargetSales",
                    "tanggal_pengiriman_preset": "today",
                    "page": 1,
                    "page_size": 3,
                })
                assert filtered.status_code == 200
                body = filtered.json()
                assert body["total"] == 1
                assert body["orders"][0]["id"] == target["id"]
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"id": {"$in": ids}})


def test_match_shipping_date_helpers():
    today = date(2026, 10, 6)
    assert match_shipping_date(None, "unset", today=today)
    assert not match_shipping_date("2026-10-06", "unset", today=today)
    assert match_shipping_date("2026-10-05", "overdue", today=today, is_active_order=True)
    assert not match_shipping_date("2026-10-05", "overdue", today=today, is_active_order=False)
