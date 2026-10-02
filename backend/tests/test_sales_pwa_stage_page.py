"""Lapangan stage totals page through every owned PO, not only the first four."""
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
from services.sales_pwa import _list_sort_pair, _sort_value, serialize_list_row

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


def _sales(uid):
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@eternal.test",
        "name": uid,
        "role": "sales_eternal",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["sales_eternal"]),
    }


def _order(po, sales_id, customer, day, **extra):
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "sales_user_id": sales_id,
        "order_date": day,
        "cancelled": False,
        "hold": False,
        "current_stage": 2,
        "line_items": [],
        "design_files": [],
        "created_at": now,
        "updated_at": now,
    }
    doc.update(extra)
    return doc


def test_list_sort_pair_matches_the_serialized_row():
    order = {
        "id": "sort-1",
        "po_number": "PO-9",
        "customer": "Kopi ABC",
        "order_date": "2026-09-18",
        "cancelled": False,
        "current_stage": 2,
    }
    row = serialize_list_row(order, [], [])
    for key in ("order_date", "customer", "progress", "po", "updated_at"):
        assert _list_sort_pair(order, key) == (_sort_value(row, key), row.get("po_number") or "")


@pytest.mark.anyio
async def test_stage_pages_cover_every_owned_po_and_hide_other_sales(monkeypatch):
    import server
    import services.sales_pwa as sales_pwa
    db = server.db
    suffix = uuid.uuid4().hex[:8]
    owner = f"stage-owner-{suffix}"
    other = f"stage-other-{suffix}"
    days = ["2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14", "2026-09-13"]
    design = [
        _order(f"PO-D{index}-{suffix}", owner, f"Pelanggan {index}", day)
        for index, day in enumerate(days, start=1)
    ]
    design[-1]["customer"] = f"Cari Khusus {suffix}"
    design[-1]["customer_name"] = design[-1]["customer"]
    payment = _order(
        f"PO-PAY-{suffix}",
        owner,
        "Pelanggan Bayar",
        "2026-09-20",
        payment_status="Approved",
        current_stage=4,
    )
    held = _order(f"PO-HOLD-{suffix}", owner, "Pelanggan Hold", "2026-09-19", hold=True)
    foreign = _order(f"PO-FOREIGN-{suffix}", other, "Pelanggan Lain", "2026-09-18")
    docs = design + [payment, held, foreign]
    ids = [doc["id"] for doc in docs]
    loaded = []
    original = sales_pwa._load_docs_for_pos

    async def _record(db_arg, po_ids):
        loaded.append(list(po_ids))
        return await original(db_arg, po_ids)

    monkeypatch.setattr(sales_pwa, "_load_docs_for_pos", _record)
    await db.orders.delete_many({"id": {"$in": ids}})
    await db.orders.insert_many(docs)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[current_user] = lambda: _sales(owner)
            first = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Design",
                "status": "semua",
                "sort": "order_date",
                "direction": "desc",
                "limit": 4,
                "q": suffix,
            })
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["total"] == 6
            assert body["counts"]["semua"] == 6
            assert [row["po_number"] for row in body["orders"]] == [doc["po_number"] for doc in design[:4]]
            assert {row["status"] for row in body["orders"]} == {"Approval Design"}
            assert all(row["design_approvals"] == [] for row in body["orders"])
            assert "token" not in first.text
            assert loaded == [[doc["id"] for doc in design[:4]]]

            rest = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Design",
                "status": "semua",
                "sort": "order_date",
                "direction": "desc",
                "skip": 4,
                "limit": 4,
                "q": suffix,
            })
            assert rest.status_code == 200, rest.text
            rest_body = rest.json()
            assert rest_body["total"] == 6
            assert [row["po_number"] for row in rest_body["orders"]] == [doc["po_number"] for doc in design[4:]]
            seen = [row["id"] for row in body["orders"] + rest_body["orders"]]
            assert seen == [doc["id"] for doc in design]
            assert len(seen) == len(set(seen))

            found = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Design",
                "q": f"Cari Khusus {suffix}",
                "limit": 4,
            })
            assert found.status_code == 200, found.text
            assert found.json()["total"] == 1
            assert found.json()["orders"][0]["po_number"] == design[-1]["po_number"]

            payment_page = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Payment",
                "q": suffix,
                "limit": 4,
            })
            assert payment_page.json()["total"] == 1
            assert payment_page.json()["orders"][0]["po_number"] == payment["po_number"]
            assert payment_page.json()["orders"][0]["status"] == "Approval Payment"

            app.dependency_overrides[current_user] = lambda: _sales(other)
            foreign_list = await client.get("/api/sales-pwa/orders", params={"q": suffix, "limit": 20})
            assert foreign_list.status_code == 200, foreign_list.text
            foreign_pos = [row["po_number"] for row in foreign_list.json()["orders"]]
            assert foreign_pos == [foreign["po_number"]]
            assert foreign_list.json()["total"] == 1
            denied = await client.get(f"/api/sales-pwa/orders/{design[0]['id']}")
            assert denied.status_code == 404
    finally:
        await db.orders.delete_many({"id": {"$in": ids}})


@pytest.mark.anyio
async def test_field_actions_match_the_list_and_scan_orders_once():
    import server
    db = server.db
    suffix = uuid.uuid4().hex[:8]
    owner = f"field-owner-{suffix}"
    other = f"field-other-{suffix}"
    days = ["2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14", "2026-09-13"]
    design = [
        _order(f"PO-FD{index}-{suffix}", owner, f"Pelanggan {index}", day)
        for index, day in enumerate(days, start=1)
    ]
    design[0]["source_pdf"] = {"storage_path": f"po/{suffix}.pdf"}
    design[0]["delivery_proofs"] = [{"id": f"proof-{suffix}", "storage_path": f"proof/{suffix}.jpg"}]
    design[0]["timeline"] = [{"note": f"timeline-rahasia-{suffix}", "stage": 2}]
    payment = _order(
        f"PO-FPAY-{suffix}",
        owner,
        "Pelanggan Bayar",
        "2026-09-20",
        payment_status="Approved",
        current_stage=4,
    )
    done = _order(
        f"PO-FDONE-{suffix}",
        owner,
        "Pelanggan Selesai",
        "2026-10-02",
        current_stage=14,
        delivery_status="Terkirim",
        qty_delivered=10,
        line_items=[{"name": "Gelas", "quantity": 10, "unit": "pcs"}],
    )
    foreign = _order(f"PO-FFOREIGN-{suffix}", other, "Pelanggan Lain", "2026-09-18")
    docs = design + [payment, done, foreign]
    ids = [doc["id"] for doc in docs]

    class _Orders:
        def __init__(self, inner):
            self._inner = inner
            self.scans = 0
            self.pages = 0

        def find(self, filt=None, *args, **kwargs):
            query = filt or {}
            ident = query.get("id")
            if isinstance(ident, dict) and "$in" in ident:
                self.pages += 1
            else:
                self.scans += 1
            return self._inner.find(query, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    proxy = _Orders(db.orders)
    db.orders = proxy
    await db.orders.delete_many({"id": {"$in": ids}})
    await db.orders.insert_many(docs)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[current_user] = lambda: _sales(owner)
            proxy.scans = 0
            proxy.pages = 0
            actions = await client.get("/api/sales-pwa/field-actions", params={"month": "2026-10", "sales_user_id": other})
            assert actions.status_code == 200, actions.text
            body = actions.json()
            assert proxy.scans == 1
            assert proxy.pages == 1
            listed = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Design",
                "status": "semua",
                "sort": "order_date",
                "direction": "desc",
                "limit": 4,
            })
            paid = await client.get("/api/sales-pwa/orders", params={
                "stage": "Approval Payment",
                "status": "semua",
                "sort": "order_date",
                "direction": "desc",
                "limit": 4,
            })
            finished = await client.get("/api/sales-pwa/orders", params={
                "status": "selesai",
                "date_from": "2026-10-01",
                "date_to": "2026-10-31",
                "limit": 1,
            })
            assert listed.status_code == 200, listed.text
            assert [row["id"] for row in body["design"]["orders"]] == [row["id"] for row in listed.json()["orders"]]
            assert body["design"]["total"] == listed.json()["total"]
            assert [row["id"] for row in body["payment"]["orders"]] == [row["id"] for row in paid.json()["orders"]]
            assert body["payment"]["total"] == paid.json()["total"]
            assert body["selesai"] == finished.json()["counts"]["selesai"]
            assert body["design"]["total"] == 6
            assert body["payment"]["total"] == 1
            first = body["design"]["orders"][0]
            assert first["documents"]["po"] == 1
            assert first["documents"]["bukti_kirim"] == 1
            assert f"timeline-rahasia-{suffix}" not in actions.text
            assert foreign["po_number"] not in actions.text
            app.dependency_overrides[current_user] = lambda: _sales(other)
            hidden = await client.get("/api/sales-pwa/field-actions", params={"month": "2026-10"})
            assert hidden.status_code == 200, hidden.text
            assert design[0]["po_number"] not in hidden.text
            assert hidden.json()["design"]["total"] == 1
    finally:
        db.orders = proxy._inner
        await db.orders.delete_many({"id": {"$in": ids}})
