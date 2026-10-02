"""PWA Sales today's deliveries follow Jadwal Pengiriman and session ownership."""
import json
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
from services.sales_pwa_deliveries import (
    build_sales_today_deliveries,
    jakarta_today_key,
    schedule_clock,
)

DAY = "2026-10-02"
YESTERDAY = "2026-10-01"
TOMORROW = "2026-10-03"


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


def _user(uid, role="sales_eternal"):
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@example.com",
        "name": uid,
        "role": role,
        "permissions": list(ROLE_DEFAULT_PERMISSIONS.get(role, [])),
    }


def _line(name, qty, **extra):
    row = {"line_id": name, "line_idx": extra.pop("line_idx", 0), "name": name, "qty": qty}
    row.update(extra)
    return row


def _shipment(sid, number, day, lines, **extra):
    doc = {
        "id": sid,
        "number": number,
        "sales_delivery_id": sid,
        "shipment_index": extra.pop("shipment_index", 1),
        "delivery_date": day,
        "address": extra.pop("address", "Jl. Melati 8"),
        "lines": lines,
    }
    doc.update(extra)
    return doc


def _order(oid, po, owner, customer, shipments, **extra):
    doc = {
        "id": oid,
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "sales_user_id": owner,
        "cancelled": extra.pop("cancelled", False),
        "current_stage": extra.pop("current_stage", 12),
        "delivery_status": extra.pop("delivery_status", "Belum Terkirim"),
        "total_quantity": extra.pop("total_quantity", 10000),
        "quantity": 10000,
        "line_items": extra.pop("line_items", [{"name": "Cup PO Utuh", "quantity": 10000}]),
        "shipments": shipments,
        "created_at": extra.pop("created_at", f"{DAY}T02:00:00+00:00"),
        "order_date": extra.pop("order_date", DAY),
        "estimated_delivery": extra.pop("estimated_delivery", DAY),
        "deadline": extra.pop("deadline", DAY),
        "address": extra.pop("address", ""),
    }
    doc.update(extra)
    return doc


def _blob(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_jakarta_day_boundary_matches_delivery_schedule_clock():
    still_today = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)
    next_day = datetime(2026, 10, 2, 17, 30, tzinfo=timezone.utc)
    assert jakarta_today_key(still_today) == DAY
    assert jakarta_today_key(next_day) == TOMORROW
    assert schedule_clock(f"{DAY}T07:45:00+07:00") == "07:45"
    assert schedule_clock(f"{DAY}T17:30:00Z") == "00:30"
    assert schedule_clock(DAY) is None
    assert schedule_clock("15:05") == "15:05"


def test_sales_sees_only_own_today_schedule_qty_and_status():
    owner_a = "sales-a"
    owner_b = "sales-b"
    mixed = _shipment(
        "sh-mix",
        "SJ-MIX",
        DAY,
        [
            _line("Cup A", 12, line_idx=0),
            _line("RAHASIA-B", 7777, line_idx=1, po_id="order-b", sales_user_id=owner_b),
        ],
        delivery_time="08:15",
        address="Gudang A",
    )
    foreign_only = _shipment(
        "sh-foreign",
        "SJ-FOREIGN",
        DAY,
        [_line("ONLY-B", 9, po_id="order-b", sales_user_id=owner_b)],
        shipment_index=2,
    )
    order_a = _order(
        "order-a",
        "PO-A",
        owner_a,
        "Pelanggan A",
        [mixed, foreign_only],
        total_quantity=10000,
    )
    order_b = _order(
        "order-b",
        "PO-RAHASIA-B",
        owner_b,
        "Pelanggan Rahasia",
        [_shipment("sh-b", "SJ-B", DAY, [_line("Item B", 40)], address="Toko B")],
    )
    yesterday = _order(
        "order-y",
        "PO-KEMARIN",
        owner_a,
        "Pelanggan Kemarin",
        [_shipment("sh-y", "SJ-Y", YESTERDAY, [_line("Kemarin", 3)])],
    )
    tomorrow = _order(
        "order-t",
        "PO-BESOK",
        owner_a,
        "Pelanggan Besok",
        [_shipment("sh-t", "SJ-T", TOMORROW, [_line("Besok", 4)])],
    )
    estimated_only = _order(
        "order-est",
        "PO-ESTIMASI",
        owner_a,
        "Pelanggan Estimasi",
        [],
        estimated_delivery=DAY,
        created_at=f"{DAY}T01:00:00+07:00",
    )
    cancelled_sj = _order(
        "order-cancel-sj",
        "PO-BATAL-SJ",
        owner_a,
        "Pelanggan Batal SJ",
        [_shipment("sh-c", "SJ-C", DAY, [_line("Batal", 5)], is_cancelled=True)],
    )
    cancelled_po = _order(
        "order-cancel-po",
        "PO-BATAL",
        owner_a,
        "Pelanggan Batal",
        [_shipment("sh-cp", "SJ-CP", DAY, [_line("Batal PO", 6)])],
        cancelled=True,
    )
    moved = _order(
        "order-move",
        "PO-PINDAH",
        owner_a,
        "Pelanggan Pindah",
        [_shipment("sh-m", "SJ-M", TOMORROW, [_line("Pindah", 7)])],
    )
    delivered = _order(
        "order-done",
        "PO-TERKIRIM",
        owner_a,
        "Pelanggan Terkirim",
        [_shipment("sh-d", "SJ-D", DAY, [_line("Sudah", 8)], delivery_time="11:00")],
        delivery_status="Terkirim",
        current_stage=14,
    )
    no_number = _order(
        "order-nonum",
        "PO-TANPA-NOMOR",
        owner_a,
        "Pelanggan Tanpa Nomor",
        [_shipment("sh-n", "", DAY, [_line("Tanpa", 2)])],
    )
    orders = [order_a, order_b, yesterday, tomorrow, estimated_only, cancelled_sj, cancelled_po, moved, delivered, no_number]

    payload = build_sales_today_deliveries(orders, _user(owner_a), today=DAY)
    text = _blob(payload)
    numbers = [row["po_number"] for row in payload["rows"]]
    assert numbers == ["PO-A", "PO-TERKIRIM"]
    assert payload["total"] == 2
    assert payload["po_count"] == 2
    assert payload["qty"] == 20
    assert payload["timezone"] == "Asia/Jakarta"
    assert payload["source"] == "delivery_schedule.schedule_rows"
    assert payload["sales_user_id"] == owner_a
    assert "PO-RAHASIA-B" not in text
    assert "Pelanggan Rahasia" not in text
    assert "RAHASIA-B" not in text
    assert "ONLY-B" not in text
    assert "7777" not in text
    assert "10000" not in text
    assert "PO-KEMARIN" not in text
    assert "PO-BESOK" not in text
    assert "PO-ESTIMASI" not in text
    assert "PO-BATAL" not in text
    assert "PO-PINDAH" not in text
    own = payload["rows"][0]
    assert own["customer"] == "Pelanggan A"
    assert own["schedule_date"] == DAY
    assert own["schedule_time"] == "08:15"
    assert own["destination"] == "Gudang A"
    assert own["status"] == "hari_ini"
    assert own["status_label"] == "Hari Ini"
    assert own["qty"] == 12
    assert own["items"] == [{"name": "Cup A", "qty": 12}]
    done = payload["rows"][1]
    assert done["status_label"] == "Terkirim"
    assert done["qty"] == 8
    assert "qty_total" not in own
    assert "total_quantity" not in own

    other = build_sales_today_deliveries(orders, _user(owner_b), today=DAY)
    other_text = _blob(other)
    assert [row["po_number"] for row in other["rows"]] == ["PO-RAHASIA-B"]
    assert other["total"] == 1
    assert other["qty"] == 40
    assert other["rows"][0]["items"] == [{"name": "Item B", "qty": 40}]
    assert "Cup A" not in other_text
    assert "PO-A" not in other_text
    assert "7777" not in other_text
    assert other["sales_user_id"] == owner_b


def test_date_move_and_jakarta_rollover_follow_shipment_delivery_date():
    owner = "sales-a"
    order = _order(
        "order-a",
        "PO-A",
        owner,
        "Pelanggan A",
        [_shipment("sh-a", "SJ-A", DAY, [_line("Cup", 15)])],
    )
    user = _user(owner)
    assert build_sales_today_deliveries([order], user, today=DAY)["total"] == 1
    order["shipments"][0]["delivery_date"] = TOMORROW
    assert build_sales_today_deliveries([order], user, today=DAY)["total"] == 0

    boundary = _order(
        "order-b",
        "PO-BATAS",
        owner,
        "Pelanggan Batas",
        [
            _shipment("sh-y", "SJ-Y", YESTERDAY, [_line("Kemarin", 1)]),
            _shipment("sh-d", "SJ-D", DAY, [_line("Hari", 2)], shipment_index=2),
            _shipment("sh-t", "SJ-T", TOMORROW, [_line("Besok", 3)], shipment_index=3),
        ],
    )
    still = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)
    rolled = datetime(2026, 10, 2, 17, 30, tzinfo=timezone.utc)
    before = build_sales_today_deliveries([boundary], user, today=jakarta_today_key(still))
    after = build_sales_today_deliveries([boundary], user, today=jakarta_today_key(rolled))
    assert [row["po_number"] for row in before["rows"]] == ["PO-BATAS"]
    assert before["rows"][0]["items"] == [{"name": "Hari", "qty": 2}]
    assert before["qty"] == 2
    assert after["rows"][0]["items"] == [{"name": "Besok", "qty": 3}]
    assert "Kemarin" not in _blob(before)
    assert "Kemarin" not in _blob(after)


@pytest.mark.anyio
async def test_http_scope_spoof_cancel_and_direct_po_access():
    import server
    db = server.db
    suffix = uuid.uuid4().hex[:8]
    owner_a = f"sales-a-{suffix}"
    owner_b = f"sales-b-{suffix}"
    secret = f"RAHASIA-{suffix}"
    po_a = f"PO-A-{suffix}"
    po_b = f"PO-B-{suffix}"
    order_a = _order(
        f"order-a-{suffix}",
        po_a,
        owner_a,
        f"Pelanggan A {suffix}",
        [_shipment(
            f"sh-a-{suffix}",
            f"SJ-A-{suffix}",
            DAY,
            [
                _line(f"Cup {suffix}", 12),
                _line(secret, 7777, line_idx=1, po_id=f"order-b-{suffix}", sales_user_id=owner_b),
            ],
            delivery_time="09:00",
            address=f"Tujuan A {suffix}",
        )],
        total_quantity=10000,
    )
    # The HTTP clock is the real Jakarta day, so pin the fixture to that day.
    live_day = jakarta_today_key()
    live_yesterday = (datetime.strptime(live_day, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    order_a["shipments"][0]["delivery_date"] = live_day
    order_b = _order(
        f"order-b-{suffix}",
        po_b,
        owner_b,
        f"Pelanggan {secret}",
        [_shipment(
            f"sh-b-{suffix}",
            f"SJ-B-{suffix}",
            live_day,
            [_line(f"Item {secret}", 40)],
            address=f"Tujuan {secret}",
        )],
    )
    yesterday = _order(
        f"order-y-{suffix}",
        f"PO-Y-{suffix}",
        owner_a,
        f"Kemarin {suffix}",
        [_shipment(f"sh-y-{suffix}", f"SJ-Y-{suffix}", live_yesterday, [_line("Kemarin", 3)])],
    )
    await db.orders.delete_many({"id": {"$regex": suffix}})
    await db.delivery_batches.delete_many({"id": {"$regex": suffix}})
    await db.orders.insert_many([order_a, order_b, yesterday])
    await db.delivery_batches.insert_one({
        "id": f"batch-{suffix}",
        "delivery_date": live_day,
        "status": "published",
        "stops": [
            {"order_id": order_a["id"], "po_number": po_a, "customer": order_a["customer"], "qty": 12},
            {"order_id": order_b["id"], "po_number": po_b, "customer": secret, "qty": 7777},
        ],
    })

    app.dependency_overrides[current_user] = lambda: _user(owner_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(
            "/api/sales-pwa/deliveries/today",
            params={
                "sales_user_id": owner_b,
                "sales_id": owner_b,
                "date": TOMORROW,
                "order_id": order_b["id"],
                "po_id": po_b,
            },
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        text = _blob(body)
        assert body["date"] == live_day
        assert body["timezone"] == "Asia/Jakarta"
        assert body["sales_user_id"] == owner_a
        assert body["total"] == 1
        assert body["qty"] == 12
        assert body["po_count"] == 1
        assert body["rows"][0]["po_number"] == po_a
        assert body["rows"][0]["qty"] == 12
        assert body["rows"][0]["items"] == [{"name": f"Cup {suffix}", "qty": 12}]
        assert body["rows"][0]["status_label"] == "Hari Ini"
        assert body["rows"][0]["schedule_time"] == "09:00"
        assert secret not in text
        assert po_b not in text
        assert "7777" not in text
        assert "10000" not in text
        assert f"batch-{suffix}" not in text

        denied = await client.get(f"/api/sales-pwa/orders/{order_b['id']}")
        assert denied.status_code == 404
        assert secret not in denied.text
        assert po_b not in denied.text

        blocked = await client.post("/api/sales-pwa/deliveries/today", json={"delivery_date": TOMORROW})
        assert blocked.status_code == 405

        moved_day = (datetime.strptime(live_day, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        order_a["shipments"][0]["delivery_date"] = moved_day
        await db.orders.update_one({"id": order_a["id"]}, {"$set": {"shipments": order_a["shipments"]}})
        moved = await client.get("/api/sales-pwa/deliveries/today")
        assert moved.status_code == 200
        assert moved.json()["total"] == 0
        assert moved.json()["qty"] == 0
        assert po_a not in moved.text

        order_a["shipments"][0]["delivery_date"] = live_day
        order_a["shipments"][0]["is_cancelled"] = True
        await db.orders.update_one({"id": order_a["id"]}, {"$set": {"shipments": order_a["shipments"]}})
        cancelled = await client.get("/api/sales-pwa/deliveries/today")
        assert cancelled.json()["total"] == 0
        assert po_a not in cancelled.text

    app.dependency_overrides[current_user] = lambda: _user(owner_b)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        own_b = await client.get("/api/sales-pwa/deliveries/today", params={"sales_user_id": owner_a})
        assert own_b.status_code == 200, own_b.text
        body_b = own_b.json()
        assert [row["po_number"] for row in body_b["rows"]] == [po_b]
        assert body_b["qty"] == 40
        assert body_b["sales_user_id"] == owner_b
        assert po_a not in own_b.text
        assert f"Cup {suffix}" not in own_b.text
        other = await client.get(f"/api/sales-pwa/orders/{order_a['id']}")
        assert other.status_code == 404

    app.dependency_overrides[current_user] = lambda: _user("admin-x", role="admin")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        forbidden = await client.get("/api/sales-pwa/deliveries/today")
        assert forbidden.status_code == 403

    await db.orders.delete_many({"id": {"$regex": suffix}})
    await db.delivery_batches.delete_many({"id": {"$regex": suffix}})
