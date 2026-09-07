"""Read-performance tests for GET /api/sales-orders list payload and filter semantics."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user


HEAVY_KEYS = (
    "sample_files",
    "design_files",
    "payment_files",
    "surat_jalan_files",
    "shipments",
    "timeline",
    "line_items",
    "_has_design_files",
    "_has_payment_files",
)


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


def _now():
    return datetime.now(timezone.utc).isoformat()


def _order(**overrides):
    oid = str(uuid.uuid4())
    marker = overrides.pop("marker", uuid.uuid4().hex[:8].upper())
    doc = {
        "id": oid,
        "po_number": f"PO-LIST-{marker}",
        "customer": f"Cust {marker}",
        "outlet": f"Outlet {marker}",
        "sales": "Vira",
        "order_date": "2026-09-01",
        "created_at": _now(),
        "total_quantity": 100,
        "current_stage": 0,
        "status_label": "Order Masuk",
        "cancelled": False,
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_files": [{"name": "huge.bin", "data": "x" * 50000}],
        "payment_files": [{"name": "pay.bin", "data": "y" * 20000}],
        "sample_files": [{"name": "sample.bin", "data": "z" * 80000}],
        "surat_jalan_files": [{"name": "sj.bin", "data": "s" * 10000}],
        "timeline": [{"stage": 0, "note": "created"}],
        "line_items": [{"name": "Box", "quantity": 100}],
    }
    doc.update(overrides)
    return doc


@pytest.mark.anyio
async def test_list_omits_heavy_file_payload_and_keeps_table_fields():
    import server
    marker = uuid.uuid4().hex[:8].upper()
    hold_doc = _order(
        marker=f"H{marker}",
        current_stage=0,
        design_files=[],
        payment_files=[],
        created_at="2026-09-01T10:00:00+00:00",
    )
    ready_doc = _order(
        marker=f"R{marker}",
        current_stage=0,
        design_status="Approved",
        payment_status="Approved",
        design_files=[{"name": "d.pdf"}],
        payment_files=[{"name": "p.pdf"}],
        created_at="2026-09-01T11:00:00+00:00",
    )
    scheduled = _order(
        marker=f"S{marker}",
        current_stage=6,
        status_label="Penjadwalan",
        design_files=[],
        payment_files=[],
        created_at="2026-09-01T12:00:00+00:00",
    )
    cancelled = _order(
        marker=f"C{marker}",
        cancelled=True,
        current_stage=0,
        created_at="2026-09-01T13:00:00+00:00",
    )
    done = _order(
        marker=f"D{marker}",
        current_stage=14,
        status_label="Pengiriman",
        cancelled=False,
        design_files=[{"name": "d.pdf"}],
        payment_files=[{"name": "p.pdf"}],
        created_at="2026-09-01T14:00:00+00:00",
    )
    await server.db.orders.insert_many([hold_doc, ready_doc, scheduled, cancelled, done])
    await server.db.sales_deliveries.insert_one({
        "id": str(uuid.uuid4()),
        "po_id": ready_doc["id"],
        "is_cancelled": False,
        "total_qty": 40,
    })

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker})
            assert listed.status_code == 200
            body = listed.json()
            by_id = {o["id"]: o for o in body["orders"]}
            assert set(by_id) == {hold_doc["id"], ready_doc["id"], scheduled["id"], cancelled["id"], done["id"]}

            for row in body["orders"]:
                for key in HEAVY_KEYS:
                    assert key not in row
                assert row["po_number"]
                assert row["customer"]
                assert "delivery_summary" in row
                assert "hold" in row
                assert "status_label" in row
                assert "total_quantity" in row

            assert by_id[hold_doc["id"]]["hold"] is True
            assert by_id[ready_doc["id"]]["hold"] is False
            assert by_id[scheduled["id"]]["hold"] is False
            assert by_id[done["id"]]["hold"] is False

            summary = by_id[ready_doc["id"]]["delivery_summary"]
            assert summary["total_ordered"] == 100
            assert summary["total_shipped"] == 40
            assert summary["total_remaining"] == 60
            assert summary["status"] == "SEBAGIAN"

            hold_list = await client.get("/api/sales-orders", params={"q": marker, "status": "hold"})
            hold_ids = {o["id"] for o in hold_list.json()["orders"]}
            assert hold_doc["id"] in hold_ids
            assert ready_doc["id"] not in hold_ids
            assert scheduled["id"] not in hold_ids

            aktif = await client.get("/api/sales-orders", params={"q": marker, "status": "aktif"})
            aktif_ids = {o["id"] for o in aktif.json()["orders"]}
            assert ready_doc["id"] in aktif_ids
            assert scheduled["id"] in aktif_ids
            assert hold_doc["id"] not in aktif_ids
            assert done["id"] not in aktif_ids
            assert cancelled["id"] not in aktif_ids

            selesai = await client.get("/api/sales-orders", params={"q": marker, "status": "selesai"})
            selesai_ids = {o["id"] for o in selesai.json()["orders"]}
            assert selesai_ids == {done["id"]}

            batal = await client.get("/api/sales-orders", params={"q": marker, "status": "cancelled"})
            batal_ids = {o["id"] for o in batal.json()["orders"]}
            assert batal_ids == {cancelled["id"]}

            created_order = [o["id"] for o in body["orders"]]
            assert created_order == [o["id"] for o in sorted(
                [hold_doc, ready_doc, scheduled, cancelled, done],
                key=lambda d: d["created_at"],
                reverse=True,
            )]
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"id": {"$in": [hold_doc["id"], ready_doc["id"], scheduled["id"], cancelled["id"], done["id"]]}})
        await server.db.sales_deliveries.delete_many({"po_id": ready_doc["id"]})
